from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

from . import catalog
from .apron import ApronMap
from .catalog import FOOT, VEHICLE, Reject, euclidean, seconds_to_minutes

@dataclass(slots=True)
class Position:
    x: float
    y: float
    node_id: str | None = None

    @classmethod
    def at_node(cls, apron: ApronMap, node_id: str, dx: float = 0.0, dy: float = 0.0) -> "Position":
        node = apron.node(node_id)
        return cls(node.x + dx, node.y + dy, node_id if dx == 0 and dy == 0 else None)

    def move_to(self, x: float, y: float, node_id: str | None = None) -> "Position":
        self.x = x
        self.y = y
        self.node_id = node_id
        return self

    def straight_distance_to(self, other: "Position") -> float:
        return euclidean(self.x, self.y, other.x, other.y)

    def clone(self) -> "Position":
        return Position(self.x, self.y, self.node_id)

@dataclass(frozen=True, slots=True)
class Eligibility:
    eligible: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class Policy:
    """Политика подбора исполнителя."""
    consider_busy: bool = True
    allow_preemption: bool = False
    consider_break: bool = False

    @property
    def is_default(self) -> bool:
        return self == DEFAULT_POLICY

DEFAULT_POLICY = Policy()

@dataclass(slots=True)
class Employee:
    id: str
    name: str
    qualifications: list[str]
    type_ratings: list[str] = field(default_factory=list)
    position: Position = field(default_factory=lambda: Position(0, 0))
    status: str = "AVAILABLE"
    busy_until_ts: float | None = None
    current_request_id: str | None = None
    shift_end_ts: float | None = None
    home_node_id: str = "TC"
    vehicle_id: str | None = None
    tasks_completed_in_shift: int = 0
    walked_meters_in_shift: float = 0.0

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("Employee: не задан id")
        if not self.qualifications:
            raise ValueError(f"Employee {self.id}: не заданы квалификации")
        for code in self.qualifications:
            if code not in catalog.QUALIFICATIONS:
                raise ValueError(f"Employee {self.id}: неизвестная квалификация {code}")
        if self.status not in catalog.EMPLOYEE_STATUS:
            raise ValueError(f"Employee {self.id}: неизвестный статус {self.status}")

    @property
    def status_info(self) -> catalog.StatusInfo:
        return catalog.EMPLOYEE_STATUS[self.status]

    @property
    def primary_qualification(self) -> catalog.Qualification:
        return catalog.QUALIFICATIONS[self.qualifications[0]]

    def has_qualification(self, code: str) -> bool:
        return code in self.qualifications

    def has_type_rating(self, aircraft_type_id: str) -> bool:
        return aircraft_type_id in self.type_ratings

    def departure_ts(self, now_ts: float) -> float:
        blocked_until = self.busy_until_ts or 0 if self.status == "ON_TASK" else 0
        return max(now_ts, blocked_until) + catalog.TIMING.acknowledge_s

    def check_eligibility(
        self,
        *,
        fault: catalog.FaultType,
        aircraft: "Aircraft | None",
        now_ts: float,
        policy: Policy = DEFAULT_POLICY,
    ) -> Eligibility:
        if self.status == "OFF_SHIFT":
            return Eligibility(False, Reject.OFF_SHIFT)
        if self.status == "BREAK" and not policy.consider_break:
            return Eligibility(False, Reject.ON_BREAK)
        if self.status == "EN_ROUTE" and not policy.allow_preemption:
            return Eligibility(False, Reject.ALREADY_DISPATCHED)
        if self.status == "ON_TASK" and not policy.consider_busy:
            return Eligibility(False, Reject.ALREADY_DISPATCHED)
        if not catalog.matches_requirement(self.qualifications, fault.requirement.primary):
            return Eligibility(False, Reject.NO_QUALIFICATION)
        if aircraft is not None and not self.has_type_rating(aircraft.type_id):
            return Eligibility(False, Reject.NO_TYPE_RATING)
        if self.shift_end_ts is not None:
            work_ends_ts = self.departure_ts(now_ts) + fault.estimated_work_s
            if work_ends_ts > self.shift_end_ts:
                return Eligibility(False, Reject.SHIFT_ENDING)
        return Eligibility(True)

@dataclass(slots=True)
class Aircraft:
    id: str
    type_id: str
    stand_node_id: str
    flight_number: str | None = None
    scheduled_departure_ts: float | None = None
    status: str = "ON_STAND"
    captain_name: str | None = None

    def __post_init__(self) -> None:
        catalog.aircraft_type(self.type_id)
        if self.status not in catalog.AIRCRAFT_STATUS:
            raise ValueError(f"Aircraft {self.id}: неизвестный статус {self.status}")

    @property
    def type_info(self) -> catalog.AircraftType:
        return catalog.aircraft_type(self.type_id)

    def stand(self, apron: ApronMap):
        stand = apron.node(self.stand_node_id)
        if not stand.is_stand:
            raise ValueError(
                f"Aircraft {self.id}: узел {self.stand_node_id} не является стоянкой"
            )
        return stand

    def position(self, apron: ApronMap) -> Position:
        stand = self.stand(apron)
        return Position(stand.x, stand.y, stand.id)

    def minutes_to_departure(self, now_ts: float) -> float | None:
        """Запас времени до вылета — определяет реальную критичность заявки."""
        if self.scheduled_departure_ts is None:
            return None
        return seconds_to_minutes(self.scheduled_departure_ts - now_ts)

@dataclass(slots=True)
class Vehicle:
    id: str
    type_id: str
    position: Position
    status: str = "FREE"
    operator_id: str | None = None
    current_operator_id: str | None = None
    busy_until_ts: float | None = None

    def __post_init__(self) -> None:
        catalog.vehicle_type(self.type_id)
        if self.status not in catalog.VEHICLE_STATUS:
            raise ValueError(f"Vehicle {self.id}: неизвестный статус {self.status}")

    @property
    def type_info(self) -> catalog.VehicleType:
        return catalog.vehicle_type(self.type_id)

    @property
    def is_available(self) -> bool:
        return self.status == "FREE"

    @property
    def speed_mps(self) -> float:
        return self.type_info.speed_mps

    @property
    def overhead_seconds(self) -> int:
        return self.type_info.boarding_s + self.type_info.parking_s

@dataclass(slots=True)
class MaintenanceRequest:
    id: str
    aircraft_id: str
    stand_node_id: str
    fault_type_id: str
    created_at_ts: float
    priority: str = ""
    reported_by: str = "КВС"
    notes: str = ""
    status: str = "NEW"
    assignment: "Assignment | None" = None
    evaluations: list["CandidateEvaluation"] = field(default_factory=list)
    escalation_reason: str | None = None

    def __post_init__(self) -> None:
        fault = catalog.fault_type(self.fault_type_id)
        if not self.priority:
            self.priority = fault.default_priority
        if self.priority not in catalog.PRIORITIES:
            raise ValueError(f"Request {self.id}: неизвестный приоритет {self.priority}")

    @property
    def fault(self) -> catalog.FaultType:
        return catalog.fault_type(self.fault_type_id)

    @property
    def priority_info(self) -> catalog.Priority:
        return catalog.PRIORITIES[self.priority]

    @property
    def status_info(self) -> catalog.StatusInfo:
        return catalog.REQUEST_STATUS[self.status]

    @property
    def required_qualifications(self) -> tuple[str, ...]:
        return self.fault.requirement.primary.any_of

    @property
    def deadline_ts(self) -> float:
        return self.created_at_ts + catalog.TIMING.regulation_response_limit_s

    def remaining_seconds(self, now_ts: float) -> float:
        return self.deadline_ts - now_ts

    @property
    def requires_equipment(self) -> bool:
        return self.fault.requires_equipment

@dataclass(frozen=True, slots=True)
class Point:
    x: float
    y: float


@dataclass(slots=True)
class RouteLeg:
    mode: str
    distance_m: float
    seconds: float
    from_node_id: str | None = None
    to_node_id: str | None = None
    edge_id: str | None = None
    note: str | None = None
    from_point: Point | None = None
    to_point: Point | None = None

    def start_xy(self, apron: ApronMap) -> Point:
        if self.from_point is not None:
            return self.from_point
        node = apron.node(self.from_node_id or "")
        return Point(node.x, node.y)

    def end_xy(self, apron: ApronMap) -> Point:
        if self.to_point is not None:
            return self.to_point
        node = apron.node(self.to_node_id or "")
        return Point(node.x, node.y)


@dataclass(slots=True)
class RoutePlan:
    legs: list[RouteLeg] = field(default_factory=list)
    overhead_seconds: float = 0.0
    vehicle_id: str | None = None
    via_warehouse: bool = False
    reachable: bool = True

    @classmethod
    def unreachable(cls) -> "RoutePlan":
        return cls(legs=[], reachable=False)

    @property
    def total_distance_m(self) -> float:
        return sum(leg.distance_m for leg in self.legs)

    @property
    def total_seconds(self) -> float:
        return sum(leg.seconds for leg in self.legs) + self.overhead_seconds

    @property
    def modes(self) -> tuple[str, ...]:
        seen: list[str] = []
        for leg in self.legs:
            if leg.mode not in seen:
                seen.append(leg.mode)
        return tuple(seen)

    @property
    def node_sequence(self) -> tuple[str, ...]:
        sequence: list[str] = []
        for leg in self.legs:
            for node_id in (leg.from_node_id, leg.to_node_id):
                if node_id and (not sequence or sequence[-1] != node_id):
                    sequence.append(node_id)
        return tuple(sequence)

    def polyline(self, apron: ApronMap) -> tuple[Point, ...]:
        if not self.legs:
            return ()
        points = [self.legs[0].start_xy(apron)]
        points.extend(leg.end_xy(apron) for leg in self.legs)
        return tuple(points)


@dataclass(frozen=True, slots=True)
class RouteChoice:
    route: RoutePlan
    mode: str
    vehicle_id: str | None

@dataclass(slots=True)
class CandidateEvaluation:
    employee_id: str
    eligible: bool
    reject_reason: str | None = None
    departure_ts: float | None = None
    travel_seconds: float | None = None
    arrival_ts: float | None = None
    eta_seconds: float | None = None
    within_regulation: bool | None = None
    route: RoutePlan | None = None
    mode: str | None = None
    vehicle_id: str | None = None
    straight_distance_m: float = 0.0
    tasks_completed: int = 0
    is_selected: bool = False
    tie_break_note: str | None = None

    @property
    def reject_reason_name(self) -> str | None:
        if self.reject_reason is None:
            return None
        return catalog.REJECT_REASONS[self.reject_reason].name

@dataclass(slots=True)
class Assignment:
    id: str
    request_id: str
    employee_id: str
    route: RoutePlan
    departure_ts: float
    arrival_ts: float
    deadline_ts: float
    vehicle_id: str | None = None
    method: str = "AUTO"
    compute_ms: float | None = None
    notified_ts: float | None = None
    acknowledged_ts: float | None = None

    @property
    def regulation_margin_s(self) -> float:
        return self.deadline_ts - self.arrival_ts

    @property
    def within_regulation(self) -> bool:
        return self.regulation_margin_s >= 0

    @property
    def within_compute_limit(self) -> bool:
        return self.compute_ms is None or self.compute_ms <= catalog.TIMING.max_compute_time_ms

@dataclass(frozen=True, slots=True)
class Notification:
    channel: str
    employee_id: str
    request_id: str
    created_ts: float
    title: str
    body: str
    requires_acknowledge: bool = True


@dataclass(slots=True)
class ShiftState:
    apron: ApronMap
    router: object
    ids: object
    now_ts: float
    sim_start_ts: float
    shift_end_ts: float
    employees: list[Employee] = field(default_factory=list)
    aircraft: list[Aircraft] = field(default_factory=list)
    vehicles: list[Vehicle] = field(default_factory=list)
    requests: list[MaintenanceRequest] = field(default_factory=list)
    notifications: list[Notification] = field(default_factory=list)

    def _by_id(self, items, key):
        return next((item for item in items if item.id == key), None)

    def find_employee(self, employee_id: str) -> Employee | None:
        return self._by_id(self.employees, employee_id)

    def employee(self, employee_id: str) -> Employee:
        found = self.find_employee(employee_id)
        if found is None:
            raise KeyError(f"Не найден сотрудник {employee_id}")
        return found

    def find_aircraft(self, aircraft_id: str) -> Aircraft | None:
        return self._by_id(self.aircraft, aircraft_id)

    def aircraft_by_id(self, aircraft_id: str) -> Aircraft:
        found = self.find_aircraft(aircraft_id)
        if found is None:
            raise KeyError(f"Не найдено ВС с бортовым номером {aircraft_id}")
        return found

    def find_vehicle(self, vehicle_id: str) -> Vehicle | None:
        return self._by_id(self.vehicles, vehicle_id)

    def find_request(self, request_id: str) -> MaintenanceRequest | None:
        return self._by_id(self.requests, request_id)

    def request(self, request_id: str) -> MaintenanceRequest:
        found = self.find_request(request_id)
        if found is None:
            raise KeyError(f"Не найдена заявка {request_id}")
        return found

    @property
    def free_vehicles(self) -> list[Vehicle]:
        return [v for v in self.vehicles if v.is_available]

    def active_requests(self) -> list[MaintenanceRequest]:
        return [
            r
            for r in self.requests
            if r.assignment is not None and r.status in ("ASSIGNED", "EN_ROUTE", "IN_PROGRESS")
        ]
