from __future__ import annotations

from dataclasses import dataclass
from threading import RLock

from core import catalog, dispatcher, scenarios
from core.catalog import format_clock, format_duration
from core.models import (
    DEFAULT_POLICY,
    Aircraft,
    Employee,
    MaintenanceRequest,
    Notification,
    Policy,
    Position,
    ShiftState,
    Vehicle,
)
from core.scenarios import Scenario, create_initial_state
from db import (
    DEMO_PASSWORD,
    UserRow,
    get_session_factory,
    hash_password,
    hydrate_catalog,
    load_state,
    save_state,
    seed_db,
)
from api import presenters
from api import schemas as s


class SessionError(Exception):
    pass


@dataclass(slots=True)
class ActiveCall:
    request: MaintenanceRequest
    proposal: dispatcher.Proposal
    committed: bool = False
    notification: Notification | None = None
    preempted_request_id: str | None = None


@dataclass(slots=True)
class LogEntry:
    ts: float
    text: str
    kind: str


class DispatcherSession:
    def __init__(self) -> None:
        self._lock = RLock()
        self.state: ShiftState | None = None
        self.active: ActiveCall | None = None
        self.scenario: Scenario | None = None
        self.log: list[LogEntry] = []
        self.form_aircraft_id: str | None = None
        self.form_fault_type_id: str | None = None
        self.policy: Policy = DEFAULT_POLICY
        self._ready = False

    def ensure(self) -> None:
        if self._ready and self.state is not None:
            return
        with self._lock:
            if self._ready and self.state is not None:
                return
            seed_db()
            hydrate_catalog()
            self.state = load_state()
            self._ready = True
            if not self.log:
                self._note("Смена загружена из базы данных", "system")

    def _persist(self) -> None:
        assert self.state is not None
        save_state(self.state)

    # -- журнал -------------------------------------------------------------

    def _note(self, text: str, kind: str = "action") -> None:
        self.log.append(LogEntry(ts=self.state.now_ts, text=text, kind=kind))
        del self.log[:-60]

    # -- операции -----------------------------------------------------------

    def reset(self) -> None:
        with self._lock:
            seed_db(force=True)
            hydrate_catalog()
            self.state = load_state()
            self.active = None
            self.scenario = None
            self.log = []
            self.form_aircraft_id = None
            self.form_fault_type_id = None
            self.policy = DEFAULT_POLICY
            self._ready = True
            self._note("Состояние смены сброшено в исходную расстановку", "system")
            self._persist()

    def load_scenario(self, code: str) -> None:
        try:
            scenario = scenarios.get(code)
        except KeyError as exc:
            raise SessionError(str(exc)) from None

        with self._lock:
            self.ensure()
            state = create_initial_state()
            scenario.build(state)
            last_call, log = scenarios.prepare(state, scenario)

            self.state = state
            self.active = None
            self.scenario = scenario
            self.log = []
            self._note(f"Загружен сценарий {scenario.code}: {scenario.title}", "system")
            for line in log:
                self._note(line, "scenario")

            if last_call is not None:
                if last_call.get("at") is not None:
                    state.now_ts = state.sim_start_ts + last_call["at"]
                self.form_aircraft_id = last_call["aircraft_id"]
                self.form_fault_type_id = last_call["fault_type_id"]
                self.policy = Policy(**last_call["policy"]) if last_call.get("policy") else DEFAULT_POLICY
                self._note(
                    "Форма вызова предзаполнена — нажмите «Вызвать специалиста»",
                    "hint",
                )
            else:
                self.form_aircraft_id = None
                self.form_fault_type_id = None
                self.policy = DEFAULT_POLICY
            self._persist()
            self._sync_accounts_to_state()

    def call(self, aircraft_id: str, fault_type_id: str, policy: Policy) -> None:
        with self._lock:
            self.ensure()
            state = self.state
            if state.find_aircraft(aircraft_id) is None:
                raise SessionError(f"Не найдено ВС с бортовым номером {aircraft_id}")
            try:
                request = dispatcher.create_request(
                    state, aircraft_id=aircraft_id, fault_type_id=fault_type_id
                )
            except KeyError as exc:
                raise SessionError(str(exc)) from None

            proposal = dispatcher.propose(state, request, policy=policy)
            self.active = ActiveCall(request=request, proposal=proposal)
            self.form_aircraft_id = aircraft_id
            self.form_fault_type_id = fault_type_id
            self.policy = policy
            self._apply_auto_assignment(request, proposal)
            self._persist()

    def _apply_auto_assignment(self, request, proposal) -> None:
        stand = self.state.apron.node(request.stand_node_id)
        selected = proposal.selected
        if selected is not None:
            employee = self.state.employee(selected.employee_id)
            if employee.status == "AVAILABLE":
                result = dispatcher.commit(self.state, request, proposal)
                self.active.committed = True
                self.active.notification = result.notification
                self.active.preempted_request_id = result.preempted_request_id
                self._note(
                    f"{request.id}: назначен {result.assignment.employee_id}, "
                    f"борт {request.aircraft_id} на {stand.short}, "
                    f"прибытие {format_clock(result.assignment.arrival_ts)}",
                    "commit",
                )
                return
        if dispatcher.has_busy_qualified(self.state, request):
            request.status = "PENDING"
            request.escalation_reason = None
            self._note(
                f"{request.id}: {request.fault.name}, борт {request.aircraft_id} "
                f"на {stand.short} — в очереди ожидания",
                "call",
            )
            return
        self._note(
            f"{request.id}: {request.fault.name}, борт {request.aircraft_id} "
            f"на {stand.short} — эскалация, исполнитель не найден",
            "call",
        )

    def recalculate(self, policy: Policy) -> None:
        with self._lock:
            self.ensure()
            active = self._require_active()
            if active.committed:
                raise SessionError("Назначение уже подтверждено, пересчёт невозможен")
            proposal = dispatcher.propose(self.state, active.request, policy=policy)
            self.active = ActiveCall(request=active.request, proposal=proposal)
            self.policy = policy
            outcome = (
                f"предложен {proposal.selected.employee_id}"
                if proposal.selected
                else "по-прежнему эскалация"
            )
            self._note(f"{active.request.id}: пересчёт по новой политике — {outcome}", "call")
            self._persist()

    def commit(self, employee_id: str | None) -> None:
        with self._lock:
            self.ensure()
            active = self._require_active()
            if active.committed:
                raise SessionError("Назначение по этому вызову уже подтверждено")
            if active.proposal.selected is None and employee_id is None:
                raise SessionError(
                    "Система не нашла исполнителя в пределах регламента. "
                    "Измените политику подбора или выберите кандидата вручную."
                )
            try:
                result = dispatcher.commit(
                    self.state, active.request, active.proposal, employee_id=employee_id
                )
            except ValueError as exc:
                raise SessionError(str(exc)) from None

            active.committed = True
            active.notification = result.notification
            active.preempted_request_id = result.preempted_request_id

            self._note(
                f"{active.request.id}: назначен {result.assignment.employee_id}, "
                f"прибытие {format_clock(result.assignment.arrival_ts)}, "
                f"уведомление отправлено",
                "commit",
            )
            if result.preempted_request_id:
                self._note(
                    f"{result.preempted_request_id} возвращена в очередь: "
                    "исполнитель снят на заявку более высокого приоритета",
                    "warning",
                )
            self._persist()

    def cancel(self) -> None:
        with self._lock:
            self.ensure()
            active = self._require_active()
            dispatcher.cancel_request(self.state, active.request)
            self._note(f"{active.request.id}: заявка отменена, исполнитель свободен", "action")
            self.active = None
            self._drain_pending()
            self._persist()

    def cancel_request(self, request_id: str) -> None:
        with self._lock:
            self.ensure()
            request = self.state.find_request(request_id)
            if request is None:
                raise SessionError(f"Заявка {request_id} не найдена")
            if request.status in {"COMPLETED", "CANCELLED"}:
                raise SessionError(f"Заявка {request_id} уже закрыта")
            dispatcher.cancel_request(self.state, request)
            if self.active and self.active.request.id == request_id:
                self.active = None
            self._note(f"{request.id}: заявка отменена", "action")
            self._drain_pending()
            self._persist()

    def complete_request(self, request_id: str) -> None:
        with self._lock:
            self.ensure()
            request = self.state.find_request(request_id)
            if request is None:
                raise SessionError(f"Заявка {request_id} не найдена")
            if request.status in {"COMPLETED", "CANCELLED"}:
                raise SessionError(f"Заявка {request_id} уже закрыта")
            dispatcher.complete_request(self.state, request)
            if self.active and self.active.request.id == request_id:
                self.active = None
            self._note(f"{request.id}: работы завершены, инженер свободен", "action")
            self._drain_pending()
            self._persist()

    def _drain_pending(self) -> None:
        for result in dispatcher.try_assign_pending(self.state):
            self._note(
                f"{result.assignment.request_id}: снята с очереди, "
                f"назначен {result.assignment.employee_id}",
                "commit",
            )

    def move_employee(self, employee_id: str, x: float, y: float) -> None:
        with self._lock:
            self.ensure()
            employee = self.state.find_employee(employee_id)
            if employee is None:
                raise SessionError(f"Не найден сотрудник {employee_id}")

            extent = self.state.apron.extent
            employee.position.move_to(
                round(min(max(x, 0.0), extent.width), 1),
                round(min(max(y, 0.0), extent.height), 1),
                None,
            )
            self._note(
                f"{employee.id} перемещён в точку "
                f"({employee.position.x:.0f}, {employee.position.y:.0f})",
                "move",
            )

            active = self.active
            if active is not None and not active.committed:
                proposal = dispatcher.propose(
                    self.state, active.request, policy=active.proposal.policy
                )
                self.active = ActiveCall(request=active.request, proposal=proposal)
                if proposal.selected is not None:
                    outcome = (
                        f"теперь предложен {proposal.selected.employee_id}, "
                        f"прибытие за {format_duration(proposal.selected.eta_seconds)}"
                    )
                else:
                    outcome = "исполнитель по-прежнему не найден"
                self._note(f"{active.request.id}: подбор пересчитан — {outcome}", "call")
            self._persist()

    def report_from_aircraft(self, aircraft_id: str, fault_type_id: str) -> None:
        self.call(aircraft_id, fault_type_id, DEFAULT_POLICY)

    def update_employee(self, employee_id: str, fields: dict) -> Employee:
        with self._lock:
            self.ensure()
            employee = self.state.find_employee(employee_id)
            if employee is None:
                raise SessionError(f"Не найден сотрудник {employee_id}")
            if "name" in fields and fields["name"]:
                employee.name = fields["name"]
            if "qualifications" in fields:
                quals = list(fields["qualifications"])
                if not quals:
                    raise SessionError("У сотрудника должна быть хотя бы одна квалификация")
                for code in quals:
                    if code not in catalog.QUALIFICATIONS:
                        raise SessionError(f"Неизвестная квалификация {code}")
                employee.qualifications = quals
            if "type_ratings" in fields:
                ratings = list(fields["type_ratings"])
                for code in ratings:
                    catalog.aircraft_type(code)
                employee.type_ratings = ratings
            if "status" in fields and fields["status"]:
                if fields["status"] not in catalog.EMPLOYEE_STATUS:
                    raise SessionError(f"Неизвестный статус {fields['status']}")
                employee.status = fields["status"]
                if fields["status"] == "AVAILABLE":
                    employee.current_request_id = None
                    employee.busy_until_ts = None
            self._note(f"{employee.id}: карточка сотрудника обновлена", "action")
            if employee.status == "AVAILABLE":
                self._drain_pending()
            self._persist()
            self._sync_user_display(employee_id=employee.id, display_name=employee.name)
            return employee

    def create_employee(self, data: dict) -> Employee:
        with self._lock:
            self.ensure()
            employee_id = data["id"].strip()
            if self.state.find_employee(employee_id):
                raise SessionError(f"Сотрудник {employee_id} уже есть в смене")
            stand = data.get("home_node_id") or "TC"
            try:
                pos = Position.at_node(self.state.apron, stand, 10, 10)
            except KeyError as exc:
                raise SessionError(str(exc)) from None
            employee = Employee(
                id=employee_id,
                name=data["name"],
                qualifications=list(data["qualifications"]),
                type_ratings=list(data.get("type_ratings") or []),
                position=pos,
                status=data.get("status") or "AVAILABLE",
                shift_end_ts=self.state.shift_end_ts,
                home_node_id=stand,
            )
            self.state.employees.append(employee)
            self._create_user(
                username=employee.id,
                role="employee",
                display_name=employee.name,
                employee_id=employee.id,
            )
            self._note(f"В смену добавлен {employee.id} {employee.name}", "action")
            self._persist()
            return employee

    def update_aircraft(self, aircraft_id: str, fields: dict) -> Aircraft:
        with self._lock:
            self.ensure()
            aircraft = self.state.find_aircraft(aircraft_id)
            if aircraft is None:
                raise SessionError(f"Не найдено ВС {aircraft_id}")
            if "type_id" in fields and fields["type_id"]:
                catalog.aircraft_type(fields["type_id"])
                aircraft.type_id = fields["type_id"]
            if "stand_node_id" in fields and fields["stand_node_id"]:
                node = self.state.apron.node(fields["stand_node_id"])
                if not node.is_stand:
                    raise SessionError(f"{fields['stand_node_id']} не является стоянкой")
                aircraft.stand_node_id = fields["stand_node_id"]
            if "flight_number" in fields:
                aircraft.flight_number = fields["flight_number"]
            if "captain_name" in fields:
                aircraft.captain_name = fields["captain_name"]
            if "status" in fields and fields["status"]:
                if fields["status"] not in catalog.AIRCRAFT_STATUS:
                    raise SessionError(f"Неизвестный статус ВС {fields['status']}")
                aircraft.status = fields["status"]
            self._note(f"Борт {aircraft.id}: карточка обновлена", "action")
            self._persist()
            self._sync_user_display(
                aircraft_id=aircraft.id,
                display_name=f"{aircraft.id} · {aircraft.flight_number or aircraft.type_id}",
            )
            return aircraft

    def create_aircraft(self, data: dict) -> Aircraft:
        with self._lock:
            self.ensure()
            aircraft_id = data["id"].strip()
            if self.state.find_aircraft(aircraft_id):
                raise SessionError(f"Борт {aircraft_id} уже стоит на перроне")
            aircraft = Aircraft(
                id=aircraft_id,
                type_id=data["type_id"],
                stand_node_id=data["stand_node_id"],
                flight_number=data.get("flight_number"),
                captain_name=data.get("captain_name"),
                status=data.get("status") or "ON_STAND",
                scheduled_departure_ts=self.state.now_ts + 3600,
            )
            self.state.aircraft.append(aircraft)
            self._create_user(
                username=aircraft.id,
                role="aircraft",
                display_name=f"{aircraft.id} · {aircraft.flight_number or aircraft.type_id}",
                aircraft_id=aircraft.id,
            )
            self._note(f"На перрон поставлен борт {aircraft.id}", "action")
            self._persist()
            return aircraft

    def delete_employee(self, employee_id: str) -> None:
        with self._lock:
            self.ensure()
            employee = self.state.find_employee(employee_id)
            if employee is None:
                raise SessionError(f"Не найден сотрудник {employee_id}")
            if employee.current_request_id:
                current = self.state.find_request(employee.current_request_id)
                if current is not None:
                    dispatcher.cancel_request(self.state, current)
            self.state.employees = [item for item in self.state.employees if item.id != employee_id]
            with get_session_factory()() as db:
                for row in db.query(UserRow).filter(UserRow.employee_id == employee_id).all():
                    db.delete(row)
                db.commit()
            self._note(f"{employee_id}: сотрудник удалён из смены", "action")
            self._drain_pending()
            self._persist()

    def delete_vehicle(self, vehicle_id: str) -> None:
        with self._lock:
            self.ensure()
            vehicle = self.state.find_vehicle(vehicle_id)
            if vehicle is None:
                raise SessionError(f"Не найден спецтранспорт {vehicle_id}")
            for employee in self.state.employees:
                if employee.vehicle_id == vehicle_id:
                    employee.vehicle_id = None
            self.state.vehicles = [item for item in self.state.vehicles if item.id != vehicle_id]
            self._note(f"{vehicle_id}: единица техники удалена", "action")
            self._persist()

    def create_fault_type(self, data: dict) -> catalog.FaultType:
        from db import FaultTypeRow

        fault_id = data["id"].strip()
        if fault_id in catalog.FAULT_TYPES:
            raise SessionError(f"Тип неисправности {fault_id} уже есть в справочнике")
        primary = list(data["primary_any_of"])
        if not primary:
            raise SessionError("Укажите хотя бы одну квалификацию")
        for code in primary:
            if code not in catalog.QUALIFICATIONS:
                raise SessionError(f"Неизвестная квалификация {code}")
        priority = data.get("default_priority") or "NORMAL"
        if priority not in catalog.PRIORITIES:
            raise SessionError(f"Неизвестный приоритет {priority}")
        support = data.get("support_any_of") or None
        with get_session_factory()() as db:
            db.add(
                FaultTypeRow(
                    id=fault_id,
                    ata=str(data.get("ata") or "00"),
                    name=data["name"],
                    description=data.get("description") or "",
                    primary_any_of=primary,
                    support_any_of=list(support) if support else None,
                    support_required=bool(data.get("support_required")),
                    default_priority=priority,
                    estimated_work_min=int(data.get("estimated_work_min") or 30),
                    requires_equipment=bool(data.get("requires_equipment")),
                    requires_vehicle=bool(data.get("requires_vehicle")),
                    mel_deferrable=bool(data.get("mel_deferrable")),
                )
            )
            db.commit()
            hydrate_catalog(db)
        self._note(f"В справочник добавлена неисправность {fault_id}", "action")
        return catalog.fault_type(fault_id)

    def delete_fault_type(self, fault_id: str) -> None:
        from db import FaultTypeRow

        if fault_id not in catalog.FAULT_TYPES:
            raise SessionError(f"Неизвестная неисправность {fault_id}")
        self.ensure()
        used = any(item.fault_type_id == fault_id for item in self.state.requests)
        if used:
            raise SessionError("Нельзя удалить неисправность: по ней уже есть заявки")
        with get_session_factory()() as db:
            row = db.get(FaultTypeRow, fault_id)
            if row is None:
                raise SessionError(f"Неисправность {fault_id} не найдена в базе")
            db.delete(row)
            db.commit()
            hydrate_catalog(db)
        self._note(f"Из справочника удалена неисправность {fault_id}", "action")

    def update_vehicle(self, vehicle_id: str, fields: dict) -> Vehicle:
        with self._lock:
            self.ensure()
            vehicle = self.state.find_vehicle(vehicle_id)
            if vehicle is None:
                raise SessionError(f"Не найден спецтранспорт {vehicle_id}")
            if "status" in fields and fields["status"]:
                if fields["status"] not in catalog.VEHICLE_STATUS:
                    raise SessionError(f"Неизвестный статус техники {fields['status']}")
                vehicle.status = fields["status"]
            if "type_id" in fields and fields["type_id"]:
                catalog.vehicle_type(fields["type_id"])
                vehicle.type_id = fields["type_id"]
            if "operator_id" in fields:
                vehicle.operator_id = fields["operator_id"] or None
            self._note(f"{vehicle.id}: карточка техники обновлена", "action")
            self._persist()
            return vehicle

    def _sync_accounts_to_state(self) -> None:
        assert self.state is not None
        employees = {emp.id: emp for emp in self.state.employees}
        aircraft = {ac.id: ac for ac in self.state.aircraft}
        with get_session_factory()() as db:
            for row in db.query(UserRow).filter(UserRow.role == "employee").all():
                if row.employee_id not in employees:
                    db.delete(row)
            for row in db.query(UserRow).filter(UserRow.role == "aircraft").all():
                if row.aircraft_id not in aircraft:
                    db.delete(row)
            db.flush()
            existing_emp = {
                row.employee_id
                for row in db.query(UserRow).filter(UserRow.role == "employee").all()
            }
            existing_ac = {
                row.aircraft_id
                for row in db.query(UserRow).filter(UserRow.role == "aircraft").all()
            }
            demo_hash = hash_password(DEMO_PASSWORD)
            for emp in employees.values():
                if emp.id in existing_emp:
                    continue
                db.add(
                    UserRow(
                        username=emp.id,
                        password_hash=demo_hash,
                        role="employee",
                        display_name=emp.name,
                        employee_id=emp.id,
                    )
                )
            for ac in aircraft.values():
                if ac.id in existing_ac:
                    continue
                db.add(
                    UserRow(
                        username=ac.id,
                        password_hash=demo_hash,
                        role="aircraft",
                        display_name=f"{ac.id} · {ac.flight_number or ac.type_id}",
                        aircraft_id=ac.id,
                    )
                )
            db.commit()

    def _create_user(
        self,
        username: str,
        role: str,
        display_name: str,
        employee_id: str | None = None,
        aircraft_id: str | None = None,
    ) -> None:
        with get_session_factory()() as db:
            if db.query(UserRow).filter(UserRow.username == username).one_or_none():
                return
            db.add(
                UserRow(
                    username=username,
                    password_hash=hash_password(DEMO_PASSWORD),
                    role=role,
                    display_name=display_name,
                    employee_id=employee_id,
                    aircraft_id=aircraft_id,
                )
            )
            db.commit()

    def _sync_user_display(
        self,
        *,
        employee_id: str | None = None,
        aircraft_id: str | None = None,
        display_name: str,
    ) -> None:
        with get_session_factory()() as db:
            query = db.query(UserRow)
            if employee_id:
                query = query.filter(UserRow.employee_id == employee_id)
            elif aircraft_id:
                query = query.filter(UserRow.aircraft_id == aircraft_id)
            else:
                return
            for row in query.all():
                row.display_name = display_name
            db.commit()

    def _require_active(self) -> ActiveCall:
        if self.active is None:
            raise SessionError("Нет открытого вызова")
        return self.active

    def snapshot(self) -> s.StateView:
        """Полный снимок состояния для визуального слоя."""
        with self._lock:
            self.ensure()
            active = self.active
            proposal_view = None
            if active is not None:
                proposal_view = presenters.proposal_view(
                    active.proposal,
                    active.request,
                    self.state,
                    committed=active.committed,
                    notification=active.notification,
                    preempted_request_id=active.preempted_request_id,
                )

            form = s.FormView(
                aircraft_id=self.form_aircraft_id,
                fault_type_id=self.form_fault_type_id,
                policy=s.PolicyView(
                    consider_busy=self.policy.consider_busy,
                    allow_preemption=self.policy.allow_preemption,
                    consider_break=self.policy.consider_break,
                ),
            )

            log = [
                s.LogEntryView(clock=format_clock(entry.ts), text=entry.text, kind=entry.kind)
                for entry in self.log
            ]

            return presenters.state_view(
                self.state,
                proposal=proposal_view,
                form=form,
                log=log,
                scenario=self.scenario,
            )
