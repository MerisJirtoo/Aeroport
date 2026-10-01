from __future__ import annotations

from typing import Mapping, Sequence

from core import apron as apron_module
from core import catalog, dispatcher, scenarios
from core.apron import ApronMap
from core.catalog import FOOT, VEHICLE, format_clock, format_compute_ms, format_duration, plural
from core.models import (
    Aircraft,
    Assignment,
    CandidateEvaluation,
    Employee,
    MaintenanceRequest,
    Notification,
    RoutePlan,
    ShiftState,
    Vehicle,
)
from core.scenarios import Scenario
from . import schemas as s

CHANNEL_NAMES: Mapping[str, str] = {"CORP_BOT": "Корпоративный бот ОТО"}
METHOD_NAMES: Mapping[str, str] = {
    "AUTO": "Принято предложение системы",
    "OVERRIDE": "Выбор диспетчера вместо предложенного",
    "MANUAL": "Назначено диспетчером вручную",
}


def surname(full_name: str) -> str:
    return full_name.split(" ")[0]


def initials(full_name: str) -> str:
    return "".join(full_name.split(" ")[1:])


def mode_text(mode: str | None, vehicle_id: str | None) -> str:
    if mode == VEHICLE:
        return f"на спецтранспорте {vehicle_id}" if vehicle_id else "на спецтранспорте"
    if mode == FOOT:
        return "пешком"
    return "—"


def _view(schema, obj=None, **extra):
    data = {}
    for name in schema.model_fields:
        if name in extra:
            data[name] = extra[name]
        elif obj is not None and hasattr(obj, name):
            data[name] = getattr(obj, name)
    return schema(**data)


def _named(info) -> s.NamedView:
    color = info.color if info.color != "#607d8b" else None
    return s.NamedView(id=info.id, name=info.name, color=color)


def _clock(ts) -> str | None:
    return format_clock(ts) if ts else None


def _tip(*lines: str) -> str:
    return "\n".join(lines)


def _quals(codes: Sequence[str]) -> list[s.QualificationView]:
    return [_view(s.QualificationView, catalog.QUALIFICATIONS[code]) for code in codes]


def _edge_hint(edge) -> str:
    parts = []
    for mode, label in ((FOOT, "пешком"), (VEHICLE, "спецтранспорт")):
        if mode not in edge.modes:
            continue
        factor = edge.factor_for(mode)
        parts.append(label if factor == 1.0 else f"{label} ({factor:g} скорости)")
    return f"{edge.distance_m:g} м · " + ", ".join(parts)


def _map_view(apron: ApronMap) -> s.MapView:
    return s.MapView(
        extent=s.ExtentView(width=apron.extent.width, height=apron.extent.height),
        nodes=[
            s.NodeView(
                id=node.id, name=node.name, short=node.short, type=node.type,
                type_name=apron_module.NODE_TYPES[node.type], x=node.x, y=node.y,
                description=node.description, has_vehicle_parking=node.has_vehicle_parking,
                stand=_view(s.StandView, node.stand) if node.stand else None,
            )
            for node in apron.nodes
        ],
        edges=[
            s.EdgeView(
                id=edge.id, from_node_id=edge.from_node_id, to_node_id=edge.to_node_id,
                type=edge.type, type_name=apron_module.EDGE_TYPES[edge.type].name,
                name=edge.name, distance_m=edge.distance_m, modes=list(edge.modes),
                hint=_edge_hint(edge),
            )
            for edge in apron.edges
        ],
        warehouse_node_id=apron_module.WAREHOUSE_NODE_ID,
    )


def _fault_view(fault: catalog.FaultType) -> s.FaultTypeView:
    primary = fault.requirement.primary.any_of
    support = fault.requirement.support
    support_text = None
    if support is not None:
        codes = " или ".join(catalog.QUALIFICATIONS[q].short for q in support.any_of)
        support_text = f"второй номер {codes}" + (" обязателен" if support.required else " по обстановке")
    notes = []
    if fault.requires_equipment:
        notes.append("обязателен заезд на склад ЗИП за специнструментом")
    if fault.requires_vehicle:
        notes.append("работа невозможна без грузоспособного спецтранспорта")
    if fault.mel_deferrable:
        notes.append("допускает отложенное устранение по MEL")
    return s.FaultTypeView(
        id=fault.id, ata=fault.ata, name=fault.name, description=fault.description,
        label=f"ATA {fault.ata} · {fault.name}",
        required_qualifications=list(primary),
        required_qualifications_text=" или ".join(catalog.QUALIFICATIONS[q].short for q in primary),
        support_text=support_text,
        default_priority=fault.default_priority,
        default_priority_name=catalog.PRIORITIES[fault.default_priority].name,
        estimated_work_min=fault.estimated_work_min,
        requires_equipment=fault.requires_equipment,
        requires_vehicle=fault.requires_vehicle,
        mel_deferrable=fault.mel_deferrable,
        routing_note="; ".join(notes) if notes else None,
    )


def reference_view(apron: ApronMap) -> s.ReferenceView:
    timing = catalog.TIMING
    return s.ReferenceView(
        app_name="Ramp Dispatch",
        version="1.0.0",
        map=_map_view(apron),
        qualifications=[_view(s.QualificationView, q) for q in catalog.QUALIFICATIONS.values()],
        fault_types=[_fault_view(f) for f in catalog.FAULT_TYPES.values()],
        priorities=[_view(s.PriorityView, p) for p in catalog.PRIORITIES.values()],
        aircraft_types=[_view(s.AircraftTypeView, t) for t in catalog.AIRCRAFT_TYPES.values()],
        vehicle_types=[_view(s.VehicleTypeView, v) for v in catalog.VEHICLE_TYPES.values()],
        movement_modes=[s.NamedView(id=m.id, name=m.name) for m in catalog.MOVEMENT_MODES.values()],
        employee_statuses=[
            _named(catalog.EMPLOYEE_STATUS["AVAILABLE"]),
            s.NamedView(id="ON_TASK", name="Занят", color="#ef6c00"),
        ],
        request_statuses=[_named(v) for v in catalog.REQUEST_STATUS.values()],
        vehicle_statuses=[_named(v) for v in catalog.VEHICLE_STATUS.values()],
        reject_reasons=[_named(v) for v in catalog.REJECT_REASONS.values()],
        timing=s.TimingView(
            regulation_response_limit_s=timing.regulation_response_limit_s,
            regulation_response_limit_text=format_duration(timing.regulation_response_limit_s),
            acknowledge_s=timing.acknowledge_s,
            equipment_pickup_s=timing.equipment_pickup_s,
            max_compute_time_ms=timing.max_compute_time_ms,
            target_compute_time_ms=timing.target_compute_time_ms,
        ),
        scenarios=[
            _view(s.ScenarioView, item, covers=list(item.covers), criteria=list(item.criteria),
                  steps_text=[scenarios.describe_step(step) for step in item.steps])
            for item in scenarios.ALL
        ],
    )


def duty_label(employee: Employee, state: ShiftState | None = None) -> str:
    request = state.find_request(employee.current_request_id) if state and employee.current_request_id else None
    if request is not None and request.status not in {"COMPLETED", "CANCELLED"}:
        return f"Занят: обслуживание {request.aircraft_id}, {request.fault.name}"
    if employee.status in {"ON_TASK", "EN_ROUTE"}:
        return "Занят"
    return "Свободен"


def employee_view(employee: Employee, state: ShiftState | None = None) -> s.EmployeeView:
    quals = _quals(employee.qualifications)
    label = duty_label(employee, state)
    lines = [
        f"{employee.id} · {employee.name}",
        "Квалификации:",
        *[f"  {q.id} — {q.name}" for q in quals],
        f"Допуски к типам ВС: {', '.join(employee.type_ratings) or 'нет'}",
        f"Статус: {label}",
    ]
    if employee.busy_until_ts and label != "Свободен":
        lines.append(f"Освободится в {format_clock(employee.busy_until_ts)}")
    lines.append(f"Координаты: {employee.position.x:.0f}, {employee.position.y:.0f}")
    named = _named(employee.status_info)
    return s.EmployeeView(
        id=employee.id, name=employee.name, surname=surname(employee.name), initials=initials(employee.name),
        qualifications=quals, qualification_text="/".join(q.short for q in quals),
        type_ratings=list(employee.type_ratings), x=employee.position.x, y=employee.position.y,
        node_id=employee.position.node_id, status=s.NamedView(id=named.id, name=label, color=named.color),
        busy_until_ts=employee.busy_until_ts, busy_until_clock=_clock(employee.busy_until_ts),
        current_request_id=employee.current_request_id,
        tasks_completed_in_shift=employee.tasks_completed_in_shift,
        walked_meters_in_shift=round(employee.walked_meters_in_shift, 1),
        busy_label=label, tooltip=_tip(*lines),
    )


def aircraft_view(aircraft: Aircraft, apron: ApronMap, now_ts: float) -> s.AircraftView:
    stand = aircraft.stand(apron)
    minutes = aircraft.minutes_to_departure(now_ts)
    lines = [
        f"{aircraft.id} · {aircraft.type_info.name}",
        f"Рейс {aircraft.flight_number or '—'}",
        f"{stand.name} (категория {stand.stand.category if stand.stand else '—'})",
        f"Статус: {catalog.AIRCRAFT_STATUS[aircraft.status].name}",
    ]
    if aircraft.scheduled_departure_ts is not None:
        extra = f" (через {minutes:.0f} мин)" if minutes is not None else ""
        lines.append(f"Вылет по расписанию {format_clock(aircraft.scheduled_departure_ts)}{extra}")
    if aircraft.captain_name:
        lines.append(f"КВС {aircraft.captain_name}")
    return s.AircraftView(
        id=aircraft.id, type_id=aircraft.type_id, type_name=aircraft.type_info.name,
        stand_node_id=aircraft.stand_node_id, stand_short=stand.short, stand_name=stand.name,
        flight_number=aircraft.flight_number, status=_named(catalog.AIRCRAFT_STATUS[aircraft.status]),
        map_scale=aircraft.type_info.map_scale, label=f"{aircraft.id} · {aircraft.type_info.icao}",
        departure_clock=_clock(aircraft.scheduled_departure_ts), tooltip=_tip(*lines),
    )


def vehicle_view(vehicle: Vehicle) -> s.VehicleView:
    info = vehicle.type_info
    lines = [
        f"{vehicle.id} · {info.name}",
        f"Скорость {info.speed_mps:g} м/с, мест {info.seats}",
        "Перевозит специнструмент" if info.carries_equipment else "Без грузового отсека",
        f"Статус: {catalog.VEHICLE_STATUS[vehicle.status].name}",
    ]
    if vehicle.operator_id:
        lines.append(f"Закреплён за {vehicle.operator_id}")
    if vehicle.current_operator_id:
        lines.append(f"За рулём {vehicle.current_operator_id}")
    return s.VehicleView(
        id=vehicle.id, type_id=vehicle.type_id, type_name=info.name,
        x=vehicle.position.x, y=vehicle.position.y, status=_named(catalog.VEHICLE_STATUS[vehicle.status]),
        glyph=info.glyph, operator_id=vehicle.operator_id, current_operator_id=vehicle.current_operator_id,
        available=vehicle.is_available, tooltip=_tip(*lines),
    )


def _point(point) -> s.PointView:
    return s.PointView(x=point.x, y=point.y)


def route_view(route: RoutePlan, apron: ApronMap) -> s.RouteView:
    mode = VEHICLE if route.vehicle_id else FOOT
    return s.RouteView(
        reachable=route.reachable,
        points=[_point(p) for p in route.polyline(apron)],
        legs=[
            s.RouteLegView(
                mode=leg.mode, mode_name=catalog.MOVEMENT_MODES[leg.mode].name,
                from_point=_point(leg.start_xy(apron)), to_point=_point(leg.end_xy(apron)),
                from_node_id=leg.from_node_id, to_node_id=leg.to_node_id, edge_id=leg.edge_id,
                distance_m=round(leg.distance_m, 1), seconds=round(leg.seconds, 1),
                text=(
                    f"{'идти до' if leg.mode == FOOT else 'ехать до'} "
                    f"«{apron.node(leg.to_node_id).short if leg.to_node_id else 'точка перрона'}» — "
                    f"{leg.distance_m:.0f} м, {format_duration(leg.seconds)}"
                ),
                note=leg.note,
            )
            for leg in route.legs
        ],
        node_sequence=list(route.node_sequence),
        total_distance_m=round(route.total_distance_m, 1),
        total_seconds=round(route.total_seconds, 1),
        overhead_seconds=route.overhead_seconds,
        vehicle_id=route.vehicle_id,
        via_warehouse=route.via_warehouse,
        mode=mode,
        mode_text=mode_text(mode, route.vehicle_id),
        total_distance_text=f"{route.total_distance_m:.0f} м",
        total_seconds_text=format_duration(route.total_seconds),
    )


def _verdict(evaluation: CandidateEvaluation) -> str:
    if evaluation.is_selected:
        return (
            f"Назначен: прибудет за {format_duration(evaluation.eta_seconds)} "
            f"{mode_text(evaluation.mode, evaluation.vehicle_id)}"
        )
    if evaluation.eligible and evaluation.eta_seconds is not None:
        return f"{evaluation.reject_reason_name}: {format_duration(evaluation.eta_seconds)}"
    return evaluation.reject_reason_name or "Не рассматривался"


def candidate_view(evaluation: CandidateEvaluation, state: ShiftState) -> s.CandidateView:
    employee = state.find_employee(evaluation.employee_id)
    return s.CandidateView(
        employee_id=evaluation.employee_id,
        employee_name=employee.name if employee else evaluation.employee_id,
        eligible=evaluation.eligible,
        is_selected=evaluation.is_selected,
        reject_reason=evaluation.reject_reason,
        reject_reason_name=evaluation.reject_reason_name,
        eta_seconds=round(evaluation.eta_seconds, 1) if evaluation.eta_seconds is not None else None,
        eta_text=format_duration(evaluation.eta_seconds),
        arrival_clock=_clock(evaluation.arrival_ts),
        departure_clock=_clock(evaluation.departure_ts),
        within_regulation=evaluation.within_regulation,
        mode=evaluation.mode,
        mode_text=mode_text(evaluation.mode, evaluation.vehicle_id),
        vehicle_id=evaluation.vehicle_id,
        straight_distance_m=round(evaluation.straight_distance_m, 1),
        tasks_completed=evaluation.tasks_completed,
        tie_break_note=evaluation.tie_break_note,
        route=route_view(evaluation.route, state.apron) if evaluation.route else None,
        verdict=_verdict(evaluation),
    )


def _optimality_proof(proof: dispatcher.OptimalityProof, selected: CandidateEvaluation | None) -> str:
    if selected is None:
        return "Пригодных кандидатов в пределах регламента нет — предложение не выдано."
    if not proof.ok:
        return "Нарушен инвариант оптимальности: раньше прибыли бы " + ", ".join(
            emp_id for emp_id, _delta in proof.violations
        )
    if proof.checked <= 1:
        return (
            "Проверен единственный кандидат, уложившийся в регламент: "
            "альтернатив с меньшим временем прибытия не существует."
        )
    word = plural(proof.checked, ("кандидата", "кандидатов", "кандидатов"))
    return (
        f"Сравнено {proof.checked} {word} в пределах регламента: ни один не прибывает "
        f"раньше {selected.employee_id} ({format_duration(selected.eta_seconds)})."
    )


def _intuitive_summary(baseline: dispatcher.IntuitiveBaseline) -> str:
    if baseline.matches_system:
        return (
            "Визуально ближайший кандидат совпал с расчётным: "
            f"{baseline.employee_id} и по прямой ближе всех "
            f"({baseline.straight_distance_m:.0f} м), и по времени быстрее всех."
        )
    return (
        f"Диспетчер «на глаз» выбрал бы {baseline.employee_id} — он ближе по прямой "
        f"({baseline.straight_distance_m:.0f} м), но фактически ехал бы по перрону "
        f"на {format_duration(baseline.saved_seconds)} дольше. "
        "Расчёт по графу перрона экономит это время."
    )


def assignment_view(assignment: Assignment, state: ShiftState) -> s.AssignmentView:
    employee = state.find_employee(assignment.employee_id)
    return s.AssignmentView(
        id=assignment.id, request_id=assignment.request_id, employee_id=assignment.employee_id,
        employee_name=employee.name if employee else assignment.employee_id,
        vehicle_id=assignment.vehicle_id, method=assignment.method,
        method_name=METHOD_NAMES.get(assignment.method, assignment.method),
        departure_ts=assignment.departure_ts, departure_clock=format_clock(assignment.departure_ts),
        arrival_ts=assignment.arrival_ts, arrival_clock=format_clock(assignment.arrival_ts),
        deadline_ts=assignment.deadline_ts, deadline_clock=format_clock(assignment.deadline_ts),
        regulation_margin_s=round(assignment.regulation_margin_s, 1),
        regulation_margin_text=format_duration(assignment.regulation_margin_s),
        within_regulation=assignment.within_regulation,
        compute_ms=round(assignment.compute_ms, 3) if assignment.compute_ms is not None else None,
        compute_text=format_compute_ms(assignment.compute_ms or 0.0),
    )


def notification_view(notification: Notification) -> s.NotificationView:
    return s.NotificationView(
        channel=notification.channel,
        channel_name=CHANNEL_NAMES.get(notification.channel, notification.channel),
        employee_id=notification.employee_id, request_id=notification.request_id,
        title=notification.title, body=notification.body,
        created_clock=format_clock(notification.created_ts),
        requires_acknowledge=notification.requires_acknowledge,
    )


def request_view(request: MaintenanceRequest, state: ShiftState) -> s.RequestView:
    stand = state.apron.node(request.stand_node_id)
    return s.RequestView(
        id=request.id, aircraft_id=request.aircraft_id, stand_node_id=request.stand_node_id,
        stand_short=stand.short, fault_type_id=request.fault_type_id, fault_name=request.fault.name,
        ata=request.fault.ata, priority=request.priority, priority_name=request.priority_info.name,
        priority_color=request.priority_info.color, status=_named(request.status_info),
        created_ts=request.created_at_ts, created_clock=format_clock(request.created_at_ts),
        deadline_clock=format_clock(request.deadline_ts), reported_by=request.reported_by,
        escalation_reason=request.escalation_reason,
        assignment=assignment_view(request.assignment, state) if request.assignment else None,
    )


def proposal_view(
    proposal: dispatcher.Proposal,
    request: MaintenanceRequest,
    state: ShiftState,
    *,
    committed: bool,
    notification: Notification | None,
    preempted_request_id: str | None,
) -> s.ProposalView:
    candidates = [candidate_view(item, state) for item in proposal.evaluations]
    candidates.sort(
        key=lambda c: (
            0 if c.is_selected else 1,
            0 if c.eligible else 1,
            c.eta_seconds if c.eta_seconds is not None else float("inf"),
            c.employee_id,
        )
    )
    intuitive = None
    if proposal.intuitive is not None:
        employee = state.find_employee(proposal.intuitive.employee_id)
        name = employee.name if employee else proposal.intuitive.employee_id
        intuitive = s.IntuitiveView(
            employee_id=proposal.intuitive.employee_id, employee_name=name,
            matches_system=proposal.intuitive.matches_system,
            straight_distance_m=round(proposal.intuitive.straight_distance_m, 1),
            eta_seconds=round(proposal.intuitive.eta_seconds, 1),
            saved_seconds=round(proposal.intuitive.saved_seconds, 1),
            summary=_intuitive_summary(proposal.intuitive),
        )
    escalation = None
    if proposal.escalation is not None:
        escalation = s.EscalationView(
            reason=proposal.escalation.reason,
            suggestions=list(proposal.escalation.suggestions),
            rejection_summary=[
                f"{catalog.REJECT_REASONS[reason].name}: {count} " + plural(count, ("чел.", "чел.", "чел."))
                for reason, count in sorted(proposal.escalation.rejection_summary.items())
            ],
        )
    stand = state.apron.node(request.stand_node_id)
    if proposal.selected is None:
        headline = f"Эскалация по борту {request.aircraft_id}: исполнитель не найден"
    else:
        employee = state.find_employee(proposal.selected.employee_id)
        name = employee.name if employee else proposal.selected.employee_id
        headline = (
            f"{name} → стоянка {stand.short}, прибытие через "
            f"{format_duration(proposal.selected.eta_seconds)} "
            f"{mode_text(proposal.selected.mode, proposal.selected.vehicle_id)}"
        )
    assignment = request.assignment or proposal.assignment
    return s.ProposalView(
        request=request_view(request, state),
        committed=committed,
        policy=_view(s.PolicyView, proposal.policy),
        selected=next((c for c in candidates if c.is_selected), None),
        candidates=candidates,
        assignment=assignment_view(assignment, state) if assignment else None,
        notification=notification_view(notification) if notification else None,
        optimality=s.OptimalityView(
            ok=proposal.optimality.ok,
            checked=proposal.optimality.checked,
            proof=_optimality_proof(proposal.optimality, proposal.selected),
            violations=[
                f"{emp_id} прибыл бы на {format_duration(delta)} раньше"
                for emp_id, delta in proposal.optimality.violations
            ],
        ),
        intuitive=intuitive,
        escalation=escalation,
        compute_ms=round(proposal.compute_ms, 3),
        compute_text=format_compute_ms(proposal.compute_ms),
        within_compute_limit=proposal.within_compute_limit,
        headline=headline,
        preempted_request_id=preempted_request_id,
    )


def _summary(state: ShiftState) -> list[str]:
    available = sum(1 for e in state.employees if e.status == "AVAILABLE")
    busy = sum(1 for e in state.employees if e.status in {"ON_TASK", "EN_ROUTE"})
    escalated = sum(1 for r in state.requests if r.status == "ESCALATED")
    return [
        f"Смена: {len(state.employees)} " + plural(len(state.employees), ("сотрудник", "сотрудника", "сотрудников")),
        f"Свободны: {available}, заняты: {busy}",
        f"Свободный спецтранспорт: {len(state.free_vehicles)} из {len(state.vehicles)}",
        f"Заявок за смену: {len(state.requests)}" + (f", эскалаций: {escalated}" if escalated else ""),
    ]


def state_view(
    state: ShiftState,
    *,
    proposal: s.ProposalView | None,
    form: s.FormView,
    log: Sequence[s.LogEntryView],
    scenario: Scenario | None,
) -> s.StateView:
    return s.StateView(
        now_ts=state.now_ts,
        clock=format_clock(state.now_ts),
        shift_end_clock=format_clock(state.shift_end_ts),
        employees=[employee_view(e, state) for e in state.employees],
        aircraft=[aircraft_view(a, state.apron, state.now_ts) for a in state.aircraft],
        vehicles=[vehicle_view(v) for v in state.vehicles],
        closed_edge_ids=list(state.apron.closed_edge_ids()),
        requests=[request_view(r, state) for r in state.requests],
        notifications=[notification_view(n) for n in state.notifications],
        proposal=proposal,
        form=form,
        log=list(log),
        scenario=_view(s.LoadedScenarioView, scenario) if scenario else None,
        summary=_summary(state),
    )
