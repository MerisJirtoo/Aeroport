#Логика обработки заявок и назначения сотрудников

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Mapping, Sequence

from . import catalog
from .catalog import FOOT, VEHICLE, Reject
from .models import (
    Aircraft,
    Assignment,
    CandidateEvaluation,
    DEFAULT_POLICY,
    Employee,
    MaintenanceRequest,
    Notification,
    Policy,
    RoutePlan,
    ShiftState,
)
from .router import TIE_TOLERANCE_S
from .catalog import euclidean, format_clock, format_duration

@dataclass(frozen=True, slots=True)
class OptimalityProof:
    ok: bool
    checked: int
    violations: tuple[tuple[str, float], ...] = ()


@dataclass(frozen=True, slots=True)
class IntuitiveBaseline:
    employee_id: str
    eta_seconds: float
    straight_distance_m: float
    matches_system: bool
    saved_seconds: float


@dataclass(frozen=True, slots=True)
class Escalation:
    reason: str
    rejection_summary: Mapping[str, int]
    suggestions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Proposal:
    request_id: str
    now_ts: float
    policy: Policy
    evaluations: tuple[CandidateEvaluation, ...]
    selected: CandidateEvaluation | None
    assignment: Assignment | None
    optimality: OptimalityProof
    intuitive: IntuitiveBaseline | None
    escalation: Escalation | None
    compute_ms: float

    @property
    def within_compute_limit(self) -> bool:
        return self.compute_ms <= catalog.TIMING.max_compute_time_ms


@dataclass(frozen=True, slots=True)
class CommitResult:
    assignment: Assignment
    notification: Notification
    preempted_request_id: str | None

def create_request(
    state: ShiftState,
    *,
    aircraft_id: str,
    fault_type_id: str,
    priority: str | None = None,
    reported_by: str | None = None,
    notes: str = "",
    created_at_ts: float | None = None,
    stand_node_id: str | None = None,
) -> MaintenanceRequest:
    aircraft = state.aircraft_by_id(aircraft_id)
    request = MaintenanceRequest(
        id=state.ids.next("R"),
        aircraft_id=aircraft.id,
        stand_node_id=stand_node_id or aircraft.stand_node_id,
        fault_type_id=fault_type_id,
        created_at_ts=state.now_ts if created_at_ts is None else created_at_ts,
        priority=priority or "",
        reported_by=reported_by or "КВС",
        notes=notes,
    )
    state.requests.append(request)
    return request

@dataclass(slots=True)
class _Context:
    state: ShiftState
    request: MaintenanceRequest
    aircraft: Aircraft
    now_ts: float
    policy: Policy


def evaluate_candidate(context: _Context, employee: Employee) -> CandidateEvaluation:
    state = context.state
    request = context.request
    fault = request.fault
    stand = state.apron.node(request.stand_node_id)

    straight_distance_m = euclidean(employee.position.x, employee.position.y, stand.x, stand.y)

    def rejected(reason: str) -> CandidateEvaluation:
        return CandidateEvaluation(
            employee_id=employee.id,
            eligible=False,
            reject_reason=reason,
            straight_distance_m=straight_distance_m,
            tasks_completed=employee.tasks_completed_in_shift,
        )

    eligibility = employee.check_eligibility(
        fault=fault,
        aircraft=context.aircraft,
        now_ts=context.now_ts,
        policy=context.policy,
    )
    if not eligibility.eligible:
        return rejected(eligibility.reason or Reject.NO_QUALIFICATION)

    if employee.status == "EN_ROUTE" and context.policy.allow_preemption:
        current = (
            state.find_request(employee.current_request_id)
            if employee.current_request_id
            else None
        )
        new_weight = catalog.PRIORITIES[request.priority].weight
        current_weight = (
            catalog.PRIORITIES[current.priority].weight if current else float("inf")
        )
        if new_weight <= current_weight:
            return rejected(Reject.ALREADY_DISPATCHED)

    choice = state.router.best_route(
        employee=employee,
        vehicles=state.vehicles,
        to_node_id=request.stand_node_id,
        fault=fault,
    )
    if choice is None:
        return rejected(Reject.UNREACHABLE)

    best, _options = choice
    route = best.route
    departure_ts = employee.departure_ts(context.now_ts)
    arrival_ts = departure_ts + route.total_seconds

    return CandidateEvaluation(
        employee_id=employee.id,
        eligible=True,
        departure_ts=departure_ts,
        travel_seconds=route.total_seconds,
        arrival_ts=arrival_ts,
        eta_seconds=arrival_ts - request.created_at_ts,
        within_regulation=arrival_ts <= request.deadline_ts,
        route=route,
        mode=best.mode,
        vehicle_id=best.vehicle_id,
        straight_distance_m=straight_distance_m,
        tasks_completed=employee.tasks_completed_in_shift,
    )

def candidate_sort_key(evaluation: CandidateEvaluation) -> tuple[int, int, int, str]:
    """
    Порядок предпочтения кандидатов. Основной критерий — время прибытия;
    остальные включаются только при фактическом равенстве и нужны для
    воспроизводимости результата (одинаковый вход — одинаковый выбор).

    Время сравнивается с точностью до порога равенства TIE_TOLERANCE_S, поэтому
    отличия в доли секунды не считаются преимуществом.
    """
    eta = evaluation.eta_seconds or 0.0
    return (
        round(eta / TIE_TOLERANCE_S),
        evaluation.tasks_completed,
        0 if evaluation.mode == FOOT else 1,
        evaluation.employee_id,
    )


def describe_tie_break(
    selected: CandidateEvaluation, runner_up: CandidateEvaluation | None
) -> str | None:
    if runner_up is None:
        return None
    if abs((selected.eta_seconds or 0) - (runner_up.eta_seconds or 0)) > TIE_TOLERANCE_S:
        return None
    if selected.tasks_completed != runner_up.tasks_completed:
        return (
            f"Равное время прибытия с {runner_up.employee_id}; "
            "выбран менее загруженный за смену"
        )
    if selected.mode != runner_up.mode:
        return (
            f"Равное время прибытия с {runner_up.employee_id}; "
            "выбран пеший вариант, спецтранспорт оставлен свободным"
        )
    return f"Равное время прибытия с {runner_up.employee_id}; выбран по табельному номеру"


def verify_optimality(evaluations: Sequence[CandidateEvaluation], selected: CandidateEvaluation | None) -> OptimalityProof:
    pool = [e for e in evaluations if e.eligible and e.within_regulation]
    if selected is None:
        return OptimalityProof(ok=True, checked=len(pool))
    selected_eta = selected.eta_seconds or 0.0
    violations = tuple(
        (e.employee_id, selected_eta - (e.eta_seconds or 0.0))
        for e in pool
        if e.employee_id != selected.employee_id
        and (e.eta_seconds or 0.0) < selected_eta - TIE_TOLERANCE_S
    )
    return OptimalityProof(ok=not violations, checked=len(pool), violations=violations)


def intuitive_baseline(evaluations: Sequence[CandidateEvaluation], selected: CandidateEvaluation | None) -> IntuitiveBaseline | None:
    pool = [e for e in evaluations if e.eligible and e.eta_seconds is not None]
    if not pool or selected is None:
        return None

    pick = min(
        pool,
        key=lambda e: (round(e.straight_distance_m * 2), e.employee_id),
    )
    return IntuitiveBaseline(
        employee_id=pick.employee_id,
        eta_seconds=pick.eta_seconds or 0.0,
        straight_distance_m=pick.straight_distance_m,
        matches_system=pick.employee_id == selected.employee_id,
        saved_seconds=(pick.eta_seconds or 0.0) - (selected.eta_seconds or 0.0),
    )


def build_escalation(context: _Context, evaluations: Sequence[CandidateEvaluation]) -> Escalation:
    eligible = sorted(
        (e for e in evaluations if e.eligible and e.eta_seconds is not None),
        key=lambda e: e.eta_seconds or 0.0,
    )
    best = eligible[0] if eligible else None

    reasons: dict[str, int] = {}
    for e in evaluations:
        if not e.eligible and e.reject_reason:
            reasons[e.reject_reason] = reasons.get(e.reject_reason, 0) + 1

    suggestions: list[str] = []
    free_vehicles = context.state.free_vehicles

    if best is not None and best.mode == FOOT and free_vehicles:
        ids = ", ".join(v.id for v in free_vehicles)
        suggestions.append(f"Выделить спецтранспорт: свободны {ids}")
    if reasons.get(Reject.ON_BREAK):
        suggestions.append(
            "Привлечь сотрудника с регламентированного перерыва "
            f"({reasons[Reject.ON_BREAK]} чел.)"
        )
    if reasons.get(Reject.ALREADY_DISPATCHED) and context.request.priority_info.can_preempt:
        suggestions.append(
            f"Рассмотреть перераспределение: {reasons[Reject.ALREADY_DISPATCHED]} чел. "
            "заняты заявками более низкого приоритета"
        )
    if best is None:
        suggestions.append(
            "На смене нет ни одного сотрудника с требуемой квалификацией "
            + "/".join(context.request.required_qualifications)
            + " и допуском на "
            + context.aircraft.type_info.name
            + " — вызвать резерв"
        )
    suggestions.append(
        "Уведомить ЦУП о риске нарушения регламента 15 минут по борту "
        f"{context.aircraft.id}"
    )

    if best is not None:
        overrun = (best.eta_seconds or 0.0) - catalog.TIMING.regulation_response_limit_s
        reason = (
            f"Лучший доступный кандидат {best.employee_id} прибудет за "
            f"{format_duration(best.eta_seconds)}, превышение регламента на "
            f"{format_duration(overrun)}"
        )
    else:
        reason = "Нет ни одного пригодного кандидата"

    return Escalation(
        reason=reason,
        rejection_summary=reasons,
        suggestions=tuple(suggestions),
    )

def propose(
    state: ShiftState,
    request: MaintenanceRequest,
    *,
    policy: Policy = DEFAULT_POLICY,
    now_ts: float | None = None,
) -> Proposal:
    started = perf_counter()

    context = _Context(
        state=state,
        request=request,
        aircraft=state.aircraft_by_id(request.aircraft_id),
        now_ts=state.now_ts if now_ts is None else now_ts,
        policy=policy,
    )

    evaluations = [evaluate_candidate(context, employee) for employee in state.employees]

    feasible = sorted(
        (e for e in evaluations if e.eligible and e.within_regulation),
        key=candidate_sort_key,
    )
    selected = feasible[0] if feasible else None
    if selected is not None:
        selected.is_selected = True
        selected.tie_break_note = describe_tie_break(
            selected, feasible[1] if len(feasible) > 1 else None
        )

    for e in evaluations:
        if not e.eligible or e.is_selected:
            continue
        e.reject_reason = (
            Reject.SLOWER_THAN_BEST if e.within_regulation else Reject.OVER_REGULATION
        )

    optimality = verify_optimality(evaluations, selected)
    intuitive = intuitive_baseline(evaluations, selected)
    escalation = None if selected else build_escalation(context, evaluations)

    assignment: Assignment | None = None
    if selected is not None and selected.route is not None:
        assignment = Assignment(
            id=state.ids.next("AS"),
            request_id=request.id,
            employee_id=selected.employee_id,
            vehicle_id=selected.vehicle_id,
            route=selected.route,
            departure_ts=selected.departure_ts or 0.0,
            arrival_ts=selected.arrival_ts or 0.0,
            deadline_ts=request.deadline_ts,
            method="AUTO",
        )

    compute_ms = (perf_counter() - started) * 1000
    if assignment is not None:
        assignment.compute_ms = compute_ms

    request.evaluations = evaluations
    request.status = "PROPOSED" if selected else "ESCALATED"
    request.escalation_reason = escalation.reason if escalation else None

    return Proposal(
        request_id=request.id,
        now_ts=context.now_ts,
        policy=policy,
        evaluations=tuple(evaluations),
        selected=selected,
        assignment=assignment,
        optimality=optimality,
        intuitive=intuitive,
        escalation=escalation,
        compute_ms=compute_ms,
    )

def build_notification(
    state: ShiftState,
    request: MaintenanceRequest,
    assignment: Assignment,
    employee: Employee,
    aircraft: Aircraft,
) -> Notification:
    stand = state.apron.node(request.stand_node_id)
    fault = request.fault
    mode_text = (
        f"на спецтранспорте {assignment.vehicle_id}" if assignment.vehicle_id else "пешком"
    )
    route_text = " → ".join(assignment.route.node_sequence) or stand.short

    body = " ".join(
        (
            f"{employee.name}, вызов от экипажа: {fault.name} (ATA {fault.ata}).",
            f"Борт {aircraft.id} ({aircraft.type_info.name}), {stand.name}.",
            f"Выход в {format_clock(assignment.departure_ts)} {mode_text},"
            f" расчётное прибытие {format_clock(assignment.arrival_ts)}.",
            f"Регламентный срок {format_clock(assignment.deadline_ts)},"
            f" запас {format_duration(assignment.regulation_margin_s)}.",
            f"Маршрут: {route_text}.",
        )
    )

    return Notification(
        channel="CORP_BOT",
        employee_id=assignment.employee_id,
        request_id=request.id,
        created_ts=state.now_ts,
        title=f"Вызов на стоянку {stand.short} — борт {aircraft.id}",
        body=body,
        requires_acknowledge=True,
    )

def commit(
    state: ShiftState,
    request: MaintenanceRequest,
    proposal: Proposal,
    *,
    employee_id: str | None = None,
    method: str | None = None,
) -> CommitResult:
    assignment = proposal.assignment

    if employee_id and (assignment is None or employee_id != assignment.employee_id):
        override = next(
            (
                e
                for e in proposal.evaluations
                if e.employee_id == employee_id and e.eligible and e.route is not None
            ),
            None,
        )
        if override is None:
            raise ValueError(f"Кандидат {employee_id} недоступен для назначения")
        assignment = Assignment(
            id=state.ids.next("AS"),
            request_id=request.id,
            employee_id=override.employee_id,
            vehicle_id=override.vehicle_id,
            route=override.route or RoutePlan.unreachable(),
            departure_ts=override.departure_ts or 0.0,
            arrival_ts=override.arrival_ts or 0.0,
            deadline_ts=request.deadline_ts,
            method="OVERRIDE",
            compute_ms=proposal.compute_ms,
        )

    if assignment is None:
        raise ValueError(f"Нет предложения для подтверждения по заявке {request.id}")
    if method:
        assignment.method = method

    employee = state.employee(assignment.employee_id)
    aircraft = state.aircraft_by_id(request.aircraft_id)
    work_seconds = request.fault.estimated_work_s

    preempted_request_id: str | None = None
    if employee.current_request_id and employee.current_request_id != request.id:
        previous = state.find_request(employee.current_request_id)
        if previous is not None and previous.status != "COMPLETED":
            previous.status = "NEW"
            previous.assignment = None
            previous.escalation_reason = (
                "Исполнитель снят на заявку более высокого приоритета "
                f"{request.id} ({request.priority_info.name})"
            )
            preempted_request_id = previous.id

    employee.status = "EN_ROUTE"
    employee.current_request_id = request.id
    employee.busy_until_ts = assignment.arrival_ts + work_seconds
    employee.walked_meters_in_shift += assignment.route.total_distance_m

    if assignment.vehicle_id:
        vehicle = state.find_vehicle(assignment.vehicle_id)
        if vehicle is not None:
            vehicle.status = "IN_USE"
            vehicle.current_operator_id = employee.id
            vehicle.busy_until_ts = employee.busy_until_ts

    assignment.notified_ts = state.now_ts
    request.assignment = assignment
    request.status = "ASSIGNED"

    notification = build_notification(state, request, assignment, employee, aircraft)
    state.notifications.append(notification)

    return CommitResult(
        assignment=assignment,
        notification=notification,
        preempted_request_id=preempted_request_id,
    )


BUSY_STATUSES = frozenset({"ON_TASK", "EN_ROUTE"})

def has_busy_qualified(state: ShiftState, request: MaintenanceRequest) -> bool:
    aircraft = state.find_aircraft(request.aircraft_id)
    for employee in state.employees:
        if employee.status not in BUSY_STATUSES:
            continue
        if not catalog.matches_requirement(
            employee.qualifications, request.fault.requirement.primary
        ):
            continue
        if aircraft is not None and not employee.has_type_rating(aircraft.type_id):
            continue
        return True
    return False


def release_assignment(state: ShiftState, request: MaintenanceRequest) -> None:
    assignment = request.assignment
    if assignment is None:
        return
    employee = state.find_employee(assignment.employee_id)
    if employee is not None and employee.current_request_id == request.id:
        employee.status = "AVAILABLE"
        employee.current_request_id = None
        employee.busy_until_ts = None
    if assignment.vehicle_id:
        vehicle = state.find_vehicle(assignment.vehicle_id)
        if vehicle is not None and vehicle.current_operator_id == assignment.employee_id:
            vehicle.status = "FREE"
            vehicle.current_operator_id = None
            vehicle.busy_until_ts = None
    request.assignment = None


def cancel_request(state: ShiftState, request: MaintenanceRequest) -> None:
    release_assignment(state, request)
    request.status = "CANCELLED"
    request.escalation_reason = None


def complete_request(state: ShiftState, request: MaintenanceRequest) -> None:
    employee = None
    if request.assignment:
        employee = state.find_employee(request.assignment.employee_id)
    release_assignment(state, request)
    request.status = "COMPLETED"
    if employee is not None:
        employee.tasks_completed_in_shift += 1


def try_assign_pending(state: ShiftState, *, policy: Policy = DEFAULT_POLICY) -> list[CommitResult]:
    live = Policy(
        consider_busy=False,
        allow_preemption=False,
        consider_break=False,
    )
    pending = [
        item
        for item in state.requests
        if item.status == "PENDING"
    ]
    pending.sort(
        key=lambda item: (-catalog.PRIORITIES[item.priority].weight, item.created_at_ts)
    )
    results: list[CommitResult] = []
    for request in pending:
        proposal = propose(state, request, policy=live)
        selected = proposal.selected
        if selected is None:
            request.status = "PENDING"
            request.escalation_reason = None
            continue
        employee = state.employee(selected.employee_id)
        if employee.status != "AVAILABLE":
            request.status = "PENDING"
            request.escalation_reason = None
            continue
        results.append(commit(state, request, proposal, method="AUTO"))
    return results
