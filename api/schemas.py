from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid")

class ExtentView(Schema):
    width: float
    height: float


class StandView(Schema):
    category: str
    jet_bridge: bool
    gpu_available: bool


class NodeView(Schema):
    id: str
    name: str
    short: str
    type: str
    type_name: str
    x: float
    y: float
    description: str
    has_vehicle_parking: bool
    stand: StandView | None = None


class EdgeView(Schema):
    id: str
    from_node_id: str
    to_node_id: str
    type: str
    type_name: str
    name: str
    distance_m: float
    modes: list[str]
    hint: str


class MapView(Schema):
    extent: ExtentView
    nodes: list[NodeView]
    edges: list[EdgeView]
    warehouse_node_id: str


class QualificationView(Schema):
    id: str
    short: str
    name: str
    description: str
    color: str
    can_release: bool


class FaultTypeView(Schema):
    id: str
    ata: str
    name: str
    description: str
    label: str
    required_qualifications: list[str]
    required_qualifications_text: str
    support_text: str | None
    default_priority: str
    default_priority_name: str
    estimated_work_min: int
    requires_equipment: bool
    requires_vehicle: bool
    mel_deferrable: bool
    routing_note: str | None


class PriorityView(Schema):
    id: str
    name: str
    description: str
    weight: int
    color: str
    can_preempt: bool


class AircraftTypeView(Schema):
    id: str
    name: str
    icao: str
    stand_category: str
    map_scale: float


class VehicleTypeView(Schema):
    id: str
    name: str
    speed_mps: float
    seats: int
    carries_equipment: bool
    glyph: str


class NamedView(Schema):
    id: str
    name: str
    color: str | None = None


class TimingView(Schema):
    regulation_response_limit_s: int
    regulation_response_limit_text: str
    acknowledge_s: int
    equipment_pickup_s: int
    max_compute_time_ms: int
    target_compute_time_ms: int


class ScenarioView(Schema):
    code: str
    title: str
    purpose: str
    covers: list[str]
    criteria: list[int]
    given: str
    expected: str
    steps_text: list[str]


class ReferenceView(Schema):
    app_name: str
    version: str
    map: MapView
    qualifications: list[QualificationView]
    fault_types: list[FaultTypeView]
    priorities: list[PriorityView]
    aircraft_types: list[AircraftTypeView]
    vehicle_types: list[VehicleTypeView]
    movement_modes: list[NamedView]
    employee_statuses: list[NamedView]
    request_statuses: list[NamedView]
    vehicle_statuses: list[NamedView]
    reject_reasons: list[NamedView]
    timing: TimingView
    scenarios: list[ScenarioView]

class PointView(Schema):
    x: float
    y: float


class EmployeeView(Schema):
    id: str
    name: str
    surname: str
    initials: str
    qualifications: list[QualificationView]
    qualification_text: str
    type_ratings: list[str]
    x: float
    y: float
    node_id: str | None
    status: NamedView
    busy_until_ts: float | None
    busy_until_clock: str | None
    current_request_id: str | None
    tasks_completed_in_shift: int
    walked_meters_in_shift: float
    busy_label: str = "Свободен"
    tooltip: str


class AircraftView(Schema):
    id: str
    type_id: str
    type_name: str
    stand_node_id: str
    stand_short: str
    stand_name: str
    flight_number: str | None
    status: NamedView
    map_scale: float
    label: str
    departure_clock: str | None
    tooltip: str


class VehicleView(Schema):
    id: str
    type_id: str
    type_name: str
    x: float
    y: float
    status: NamedView
    glyph: str
    operator_id: str | None
    current_operator_id: str | None
    available: bool
    tooltip: str


class RouteLegView(Schema):
    mode: str
    mode_name: str
    from_point: PointView
    to_point: PointView
    from_node_id: str | None
    to_node_id: str | None
    edge_id: str | None
    distance_m: float
    seconds: float
    text: str
    note: str | None


class RouteView(Schema):
    reachable: bool
    points: list[PointView]
    legs: list[RouteLegView]
    node_sequence: list[str]
    total_distance_m: float
    total_seconds: float
    overhead_seconds: float
    vehicle_id: str | None
    via_warehouse: bool
    mode: str
    mode_text: str
    total_distance_text: str
    total_seconds_text: str


class CandidateView(Schema):
    employee_id: str
    employee_name: str
    eligible: bool
    is_selected: bool
    reject_reason: str | None
    reject_reason_name: str | None
    eta_seconds: float | None
    eta_text: str
    arrival_clock: str | None
    departure_clock: str | None
    within_regulation: bool | None
    mode: str | None
    mode_text: str
    vehicle_id: str | None
    straight_distance_m: float
    tasks_completed: int
    tie_break_note: str | None
    route: RouteView | None
    verdict: str


class NotificationView(Schema):
    channel: str
    channel_name: str
    employee_id: str
    request_id: str
    title: str
    body: str
    created_clock: str
    requires_acknowledge: bool


class AssignmentView(Schema):
    id: str
    request_id: str
    employee_id: str
    employee_name: str
    vehicle_id: str | None
    method: str
    method_name: str
    departure_ts: float
    departure_clock: str
    arrival_ts: float
    arrival_clock: str
    deadline_ts: float
    deadline_clock: str
    regulation_margin_s: float
    regulation_margin_text: str
    within_regulation: bool
    compute_ms: float | None
    compute_text: str


class OptimalityView(Schema):
    ok: bool
    checked: int
    proof: str
    violations: list[str]


class IntuitiveView(Schema):
    employee_id: str
    employee_name: str
    matches_system: bool
    straight_distance_m: float
    eta_seconds: float
    saved_seconds: float
    summary: str


class EscalationView(Schema):
    reason: str
    suggestions: list[str]
    rejection_summary: list[str]


class PolicyView(Schema):
    consider_busy: bool = True
    allow_preemption: bool = False
    consider_break: bool = False


class RequestView(Schema):
    id: str
    aircraft_id: str
    stand_node_id: str
    stand_short: str
    fault_type_id: str
    fault_name: str
    ata: str
    priority: str
    priority_name: str
    priority_color: str
    status: NamedView
    created_ts: float
    created_clock: str
    deadline_clock: str
    reported_by: str
    escalation_reason: str | None
    assignment: AssignmentView | None


class ProposalView(Schema):
    request: RequestView
    committed: bool
    policy: PolicyView
    selected: CandidateView | None
    candidates: list[CandidateView]
    assignment: AssignmentView | None
    notification: NotificationView | None
    optimality: OptimalityView
    intuitive: IntuitiveView | None
    escalation: EscalationView | None
    compute_ms: float
    compute_text: str
    within_compute_limit: bool
    headline: str
    preempted_request_id: str | None


class FormView(Schema):
    aircraft_id: str | None
    fault_type_id: str | None
    policy: PolicyView


class LogEntryView(Schema):
    clock: str
    text: str
    kind: str


class LoadedScenarioView(Schema):
    code: str
    title: str
    purpose: str
    given: str
    expected: str


class StateView(Schema):
    now_ts: float
    clock: str
    shift_end_clock: str
    employees: list[EmployeeView]
    aircraft: list[AircraftView]
    vehicles: list[VehicleView]
    closed_edge_ids: list[str]
    requests: list[RequestView]
    notifications: list[NotificationView]
    proposal: ProposalView | None
    form: FormView
    log: list[LogEntryView]
    scenario: LoadedScenarioView | None
    #: Сводка по смене для верхней панели.
    summary: list[str]

class CallRequest(Schema):
    aircraft_id: str = Field(min_length=1)
    fault_type_id: str = Field(min_length=1)
    policy: PolicyView = PolicyView()


class RecalculateRequest(Schema):
    policy: PolicyView = PolicyView()


class CommitRequest(Schema):
    employee_id: str | None = None


class PositionRequest(Schema):
    x: float
    y: float


