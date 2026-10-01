from __future__ import annotations

import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Sequence

SECONDS_PER_MINUTE = 60
TIMEZONE_OFFSET_S = 3 * 3600


def minutes_to_seconds(minutes: float) -> int:
    return round(minutes * SECONDS_PER_MINUTE)


def seconds_to_minutes(seconds: float) -> float:
    return seconds / SECONDS_PER_MINUTE


def format_duration(seconds: float | None) -> str:
    if seconds is None or not math.isfinite(seconds):
        return "—"
    sign = "-" if seconds < 0 else ""
    total = round(abs(seconds))
    minutes, rest = divmod(total, SECONDS_PER_MINUTE)
    if minutes == 0:
        return f"{sign}{rest} с"
    if rest == 0:
        return f"{sign}{minutes} мин"
    return f"{sign}{minutes} мин {rest} с"


def format_clock(timestamp_s: float | None) -> str:
    if timestamp_s is None:
        return "—"
    total = int(timestamp_s + TIMEZONE_OFFSET_S) % 86400
    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, SECONDS_PER_MINUTE)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def format_compute_ms(milliseconds: float) -> str:
    if milliseconds < 0.1:
        return "менее 0,1 мс"
    return f"{milliseconds:.1f}".replace(".", ",") + " мс"


def euclidean(ax: float, ay: float, bx: float, by: float) -> float:
    return math.hypot(ax - bx, ay - by)


def plural(count: int, forms: tuple[str, str, str]) -> str:
    tail = abs(count) % 100
    last = tail % 10
    if 10 < tail < 20:
        return forms[2]
    if 1 < last < 5:
        return forms[1]
    if last == 1:
        return forms[0]
    return forms[2]


class IdGenerator:
    __slots__ = ("_counters",)

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}

    def next(self, prefix: str) -> str:
        value = self._counters.get(prefix, 0) + 1
        self._counters[prefix] = value
        return f"{prefix}-{value:03d}"

    def reset(self) -> None:
        self._counters.clear()


@dataclass(frozen=True, slots=True)
class Timing:
    regulation_response_limit_s: int = 15 * 60
    acknowledge_s: int = 30
    vehicle_boarding_s: int = 60
    vehicle_parking_s: int = 45
    equipment_pickup_s: int = 180
    max_compute_time_ms: int = 10_000
    target_compute_time_ms: int = 200


TIMING = Timing()

@dataclass(frozen=True, slots=True)
class Qualification:
    id: str
    short: str
    name: str
    description: str
    color: str
    can_release: bool


QUALIFICATIONS: Mapping[str, Qualification] = MappingProxyType(
    {
        q.id: q
        for q in (
            Qualification(
                id="B1",
                short="ПиД",
                name="Авиационный техник по планеру и двигателям",
                description=(
                    "Планер, силовые установки, гидравлика, шасси, механические системы. "
                    "Имеет право выпуска ВС по механической части."
                ),
                color="#2e7d32",
                can_release=True,
            ),
            Qualification(
                id="B2",
                short="АиРЭО",
                name="Авиационный техник по авионике и электрооборудованию",
                description=(
                    "Авионика, радиосвязь, навигация, электроснабжение, приборное "
                    "оборудование. Имеет право выпуска ВС по электрической части."
                ),
                color="#1565c0",
                can_release=True,
            ),
            Qualification(
                id="A",
                short="ЛМ",
                name="Линейный механик оперативного обслуживания",
                description=(
                    "Простые операции оперативного ТО по утверждённому перечню: замена "
                    "ламп, долив жидкостей, визуальный осмотр. Работает под контролем B1/B2."
                ),
                color="#ef6c00",
                can_release=False,
            ),
            Qualification(
                id="NDT",
                short="НК",
                name="Специалист по неразрушающему контролю",
                description=(
                    "Вихретоковый, ультразвуковой и визуально-оптический контроль "
                    "конструкции. Привлекается вторым номером при повреждениях планера."
                ),
                color="#6a1b9a",
                can_release=False,
            ),
            Qualification(
                id="STRUCT",
                short="КОНСТР",
                name="Инженер по конструкции планера",
                description=(
                    "Оценка допустимости повреждений по SRM, принятие решения о вылете "
                    "с повреждением или о постановке ВС на ремонт."
                ),
                color="#ad1457",
                can_release=True,
            ),
        )
    }
)

@dataclass(frozen=True, slots=True)
class AircraftType:
    id: str
    name: str
    icao: str
    stand_category: str
    engines: int
    map_scale: float


AIRCRAFT_TYPES: Mapping[str, AircraftType] = MappingProxyType(
    {
        t.id: t
        for t in (
            AircraftType("SU95", "SSJ-100 (RRJ-95)", "SU95", "C", 2, 1.00),
            AircraftType("A320", "Airbus A320neo", "A20N", "C", 2, 1.15),
            AircraftType("B738", "Boeing 737-800", "B738", "C", 2, 1.15),
            AircraftType("A333", "Airbus A330-300", "A333", "E", 2, 1.55),
        )
    }
)

@dataclass(frozen=True, slots=True)
class Priority:
    id: str
    name: str
    description: str
    weight: int
    color: str
    can_preempt: bool


PRIORITIES: Mapping[str, Priority] = MappingProxyType(
    {
        p.id: p
        for p in (
            Priority(
                id="AOG",
                name="AOG — вылет невозможен",
                description="Aircraft On Ground: неисправность блокирует вылет, рейс под срывом.",
                weight=100,
                color="#c62828",
                can_preempt=True,
            ),
            Priority(
                id="HIGH",
                name="Высокий",
                description="Устранение необходимо до вылета, резерв времени ограничен.",
                weight=60,
                color="#ef6c00",
                can_preempt=True,
            ),
            Priority(
                id="NORMAL",
                name="Обычный",
                description="Штатное оперативное обслуживание в пределах стояночного времени.",
                weight=30,
                color="#1565c0",
                can_preempt=False,
            ),
            Priority(
                id="DEFERRED",
                name="Отложенный (MEL)",
                description=(
                    "Отказ допускает вылет по перечню минимального оборудования, "
                    "устранение можно перенести на базовую стоянку."
                ),
                weight=10,
                color="#607d8b",
                can_preempt=False,
            ),
        )
    }
)

@dataclass(frozen=True, slots=True)
class RequirementSlot:
    any_of: tuple[str, ...]
    required: bool = True


@dataclass(frozen=True, slots=True)
class FaultRequirement:
    primary: RequirementSlot
    support: RequirementSlot | None = None


@dataclass(frozen=True, slots=True)
class FaultType:
    id: str
    ata: str
    name: str
    description: str
    requirement: FaultRequirement
    default_priority: str
    estimated_work_min: int
    requires_equipment: bool = False
    requires_vehicle: bool = False
    mel_deferrable: bool = False

    @property
    def estimated_work_s(self) -> int:
        return minutes_to_seconds(self.estimated_work_min)


FAULT_TYPES: Mapping[str, FaultType] = MappingProxyType(
    {
        f.id: f
        for f in (
            FaultType(
                id="NAV_IRS_FAIL",
                ata="34",
                name="Отказ инерциальной навигационной системы",
                description="Расхождение показаний IRS, отказ канала ILS. Вылет по ППП невозможен.",
                requirement=FaultRequirement(primary=RequirementSlot(("B2",))),
                default_priority="AOG",
                estimated_work_min=45,
            ),
            FaultType(
                id="COM_VHF_FAIL",
                ata="23",
                name="Отказ радиостанции VHF-2",
                description="Второй комплект радиосвязи не выходит на передачу.",
                requirement=FaultRequirement(primary=RequirementSlot(("B2",))),
                default_priority="HIGH",
                estimated_work_min=30,
                mel_deferrable=True,
            ),
            FaultType(
                id="HYD_LEAK",
                ata="29",
                name="Утечка жидкости гидросистемы",
                description=(
                    "Течь по панели ниши шасси, падение уровня в баке гидросистемы «Зелёная»."
                ),
                requirement=FaultRequirement(primary=RequirementSlot(("B1",))),
                default_priority="AOG",
                estimated_work_min=60,
                requires_equipment=True,
            ),
            FaultType(
                id="ENG_OIL_LOW",
                ata="79",
                name="Низкий уровень масла двигателя",
                description="Уровень масла двигателя №1 ниже нормы, требуется дозаправка и осмотр.",
                requirement=FaultRequirement(primary=RequirementSlot(("B1",))),
                default_priority="HIGH",
                estimated_work_min=35,
                requires_equipment=True,
            ),
            FaultType(
                id="APU_NO_START",
                ata="49",
                name="Отказ запуска ВСУ",
                description=(
                    "Вспомогательная силовая установка не выходит на режим, "
                    "требуется наземный источник питания и воздушный стартер."
                ),
                requirement=FaultRequirement(
                    primary=RequirementSlot(("B1",)),
                    support=RequirementSlot(("B2",), required=False),
                ),
                default_priority="NORMAL",
                estimated_work_min=50,
                requires_equipment=True,
                mel_deferrable=True,
            ),
            FaultType(
                id="TIRE_DAMAGE",
                ata="32",
                name="Повреждение пневматика основной опоры шасси",
                description=(
                    "Порез протектора сверх допуска, требуется замена колеса. "
                    "Обязателен подъём на домкрате — работа невозможна без спецтранспорта."
                ),
                requirement=FaultRequirement(
                    primary=RequirementSlot(("B1",)),
                    support=RequirementSlot(("A",), required=True),
                ),
                default_priority="AOG",
                estimated_work_min=75,
                requires_equipment=True,
                requires_vehicle=True,
            ),
            FaultType(
                id="BIRD_STRIKE",
                ata="53",
                name="Следы столкновения с птицей",
                description=(
                    "Повреждение обтекателя РЛС. Требуется осмотр конструкции "
                    "и заключение о допустимости повреждения по SRM."
                ),
                requirement=FaultRequirement(
                    primary=RequirementSlot(("B1",)),
                    support=RequirementSlot(("NDT", "STRUCT"), required=True),
                ),
                default_priority="AOG",
                estimated_work_min=90,
            ),
            FaultType(
                id="PITOT_DISAGREE",
                ata="34",
                name="Расхождение показаний приёмников воздушного давления",
                description="Разница показаний скорости между каналами КВС и второго пилота.",
                requirement=FaultRequirement(primary=RequirementSlot(("B2",))),
                default_priority="AOG",
                estimated_work_min=40,
                requires_equipment=True,
            ),
            FaultType(
                id="PACK_FAIL",
                ata="21",
                name="Отказ системы кондиционирования (PACK 1)",
                description="Левая установка кондиционирования не поддерживает расход воздуха.",
                requirement=FaultRequirement(primary=RequirementSlot(("B1",))),
                default_priority="HIGH",
                estimated_work_min=55,
                mel_deferrable=True,
            ),
            FaultType(
                id="NAV_LIGHT_FAIL",
                ata="33",
                name="Отказ проблескового маяка",
                description="Не работает верхний проблесковый маяк, требуется замена лампы.",
                requirement=FaultRequirement(primary=RequirementSlot(("B2", "A"))),
                default_priority="DEFERRED",
                estimated_work_min=20,
                mel_deferrable=True,
            ),
        )
    }
)
FOOT = "FOOT"
VEHICLE = "VEHICLE"

@dataclass(frozen=True, slots=True)
class MovementMode:
    id: str
    name: str
    base_speed_mps: float


MOVEMENT_MODES: Mapping[str, MovementMode] = MappingProxyType(
    {
        m.id: m
        for m in (
            MovementMode(FOOT, "Пешком", 1.25),
            MovementMode(VEHICLE, "На спецтранспорте", 5.0),
        )
    }
)


@dataclass(frozen=True, slots=True)
class VehicleType:
    id: str
    name: str
    speed_mps: float
    seats: int
    carries_equipment: bool
    boarding_s: int
    parking_s: int
    glyph: str


VEHICLE_TYPES: Mapping[str, VehicleType] = MappingProxyType(
    {
        v.id: v
        for v in (
            VehicleType(
                id="RAMP_CAR",
                name="Перронный автомобиль",
                speed_mps=5.0,
                seats=4,
                carries_equipment=True,
                boarding_s=TIMING.vehicle_boarding_s,
                parking_s=TIMING.vehicle_parking_s,
                glyph="А",
            ),
            VehicleType(
                id="E_CART",
                name="Электрокар",
                speed_mps=3.3,
                seats=2,
                carries_equipment=False,
                boarding_s=30,
                parking_s=20,
                glyph="Э",
            ),
            VehicleType(
                id="LADDER_TRUCK",
                name="Автовышка / стремянка на шасси",
                speed_mps=3.9,
                seats=2,
                carries_equipment=True,
                boarding_s=90,
                parking_s=90,
                glyph="В",
            ),
        )
    }
)

@dataclass(frozen=True, slots=True)
class StatusInfo:
    id: str
    name: str
    color: str = "#607d8b"
    assignable: bool = False


EMPLOYEE_STATUS: Mapping[str, StatusInfo] = MappingProxyType(
    {
        s.id: s
        for s in (
            StatusInfo("AVAILABLE", "Свободен", "#2e7d32", True),
            StatusInfo("EN_ROUTE", "В пути к ВС", "#1565c0", False),
            StatusInfo("ON_TASK", "Занят на работах", "#ef6c00", False),
            StatusInfo("BREAK", "Регламентированный перерыв", "#757575", False),
            StatusInfo("OFF_SHIFT", "Вне смены", "#bdbdbd", False),
        )
    }
)

REQUEST_STATUS: Mapping[str, StatusInfo] = MappingProxyType(
    {
        s.id: s
        for s in (
            StatusInfo("NEW", "Новая"),
            StatusInfo("PROPOSED", "Предложено назначение"),
            StatusInfo("ASSIGNED", "Назначено"),
            StatusInfo("PENDING", "В ожидании"),
            StatusInfo("EN_ROUTE", "Инженер в пути"),
            StatusInfo("IN_PROGRESS", "Работы выполняются"),
            StatusInfo("COMPLETED", "Выполнено"),
            StatusInfo("ESCALATED", "Эскалация — регламент не выполняется"),
            StatusInfo("CANCELLED", "Отменена"),
        )
    }
)

VEHICLE_STATUS: Mapping[str, StatusInfo] = MappingProxyType(
    {
        s.id: s
        for s in (
            StatusInfo("FREE", "Свободен"),
            StatusInfo("IN_USE", "Занят"),
            StatusInfo("MAINTENANCE", "На обслуживании"),
        )
    }
)

AIRCRAFT_STATUS: Mapping[str, StatusInfo] = MappingProxyType(
    {
        s.id: s
        for s in (
            StatusInfo("ON_STAND", "На стоянке"),
            StatusInfo("BOARDING", "Посадка пассажиров"),
            StatusInfo("AOG", "AOG — вылет заблокирован"),
            StatusInfo("DEPARTED", "Вылетел"),
        )
    }
)


class Reject:
    NO_QUALIFICATION = "NO_QUALIFICATION"
    NO_TYPE_RATING = "NO_TYPE_RATING"
    OFF_SHIFT = "OFF_SHIFT"
    ON_BREAK = "ON_BREAK"
    ALREADY_DISPATCHED = "ALREADY_DISPATCHED"
    SHIFT_ENDING = "SHIFT_ENDING"
    OVER_REGULATION = "OVER_REGULATION"
    UNREACHABLE = "UNREACHABLE"
    SLOWER_THAN_BEST = "SLOWER_THAN_BEST"


REJECT_REASONS: Mapping[str, StatusInfo] = MappingProxyType(
    {
        s.id: s
        for s in (
            StatusInfo(Reject.NO_QUALIFICATION, "Нет требуемой квалификации"),
            StatusInfo(Reject.NO_TYPE_RATING, "Нет допуска на данный тип ВС"),
            StatusInfo(Reject.OFF_SHIFT, "Вне смены"),
            StatusInfo(Reject.ON_BREAK, "Регламентированный перерыв"),
            StatusInfo(Reject.ALREADY_DISPATCHED, "Уже направлен на другую заявку"),
            StatusInfo(Reject.SHIFT_ENDING, "Смена заканчивается раньше срока работ"),
            StatusInfo(Reject.OVER_REGULATION, "Время в пути превышает регламент 15 минут"),
            StatusInfo(Reject.UNREACHABLE, "Маршрут до стоянки недоступен"),
            StatusInfo(Reject.SLOWER_THAN_BEST, "Прибудет позже лучшего кандидата"),
        )
    }
)

def matches_requirement(
    employee_qualifications: Sequence[str], slot: RequirementSlot | None
) -> bool:
    if slot is None or not slot.any_of:
        return True
    return any(code in employee_qualifications for code in slot.any_of)


def primary_qualifications_for(fault_type_id: str) -> tuple[str, ...]:
    return fault_type(fault_type_id).requirement.primary.any_of


def fault_type(fault_type_id: str) -> FaultType:
    try:
        return FAULT_TYPES[fault_type_id]
    except KeyError:
        raise KeyError(f"Неизвестный тип неисправности: {fault_type_id}") from None


def aircraft_type(aircraft_type_id: str) -> AircraftType:
    try:
        return AIRCRAFT_TYPES[aircraft_type_id]
    except KeyError:
        raise KeyError(f"Неизвестный тип ВС: {aircraft_type_id}") from None


def vehicle_type(vehicle_type_id: str) -> VehicleType:
    try:
        return VEHICLE_TYPES[vehicle_type_id]
    except KeyError:
        raise KeyError(f"Неизвестный тип спецтранспорта: {vehicle_type_id}") from None

_STOCK_QUALIFICATIONS = dict(QUALIFICATIONS)
_STOCK_AIRCRAFT_TYPES = dict(AIRCRAFT_TYPES)
_STOCK_FAULT_TYPES = dict(FAULT_TYPES)
_STOCK_PRIORITIES = dict(PRIORITIES)
_STOCK_VEHICLE_TYPES = dict(VEHICLE_TYPES)


def stock_qualifications() -> tuple[Qualification, ...]:
    return tuple(_STOCK_QUALIFICATIONS.values())


def stock_aircraft_types() -> tuple[AircraftType, ...]:
    return tuple(_STOCK_AIRCRAFT_TYPES.values())


def stock_fault_types() -> tuple[FaultType, ...]:
    return tuple(_STOCK_FAULT_TYPES.values())


def stock_priorities() -> tuple[Priority, ...]:
    return tuple(_STOCK_PRIORITIES.values())


def stock_vehicle_types() -> tuple[VehicleType, ...]:
    return tuple(_STOCK_VEHICLE_TYPES.values())
