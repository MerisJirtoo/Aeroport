"""Cценарии для тестировния"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Mapping

from . import catalog, dispatcher
from .apron import ApronMap
from .catalog import format_clock, format_duration, minutes_to_seconds, IdGenerator
from .models import DEFAULT_POLICY, Aircraft, Employee, Policy, Position, ShiftState, Vehicle
from .router import Router

SIM_START_TS = int(datetime(2026, 9, 5, 11, 0, 0, tzinfo=timezone.utc).timestamp())
SHIFT_END_TS = SIM_START_TS + minutes_to_seconds(360)


def create_initial_state() -> ShiftState:
    apron = ApronMap()
    check = apron.validate()
    if not check.ok:
        raise RuntimeError("Конфигурация карты перрона некорректна: " + "; ".join(check.errors))
    at = lambda node, dx=0.0, dy=0.0: Position.at_node(apron, node, dx, dy)
    return ShiftState(
        apron=apron,
        router=Router(apron),
        ids=IdGenerator(),
        now_ts=SIM_START_TS,
        sim_start_ts=SIM_START_TS,
        shift_end_ts=SHIFT_END_TS,
        employees=[
            Employee("E-01", "Иванов А. С.", ["B1"], ["SU95", "A320", "B738"], at("TC", 18, -12), shift_end_ts=SHIFT_END_TS, home_node_id="TC"),
            Employee("E-02", "Петров Д. М.", ["B2"], ["A320", "B738"], at("A3", -15, 20), shift_end_ts=SHIFT_END_TS, home_node_id="TC"),
            Employee("E-03", "Сидоров К. В.", ["B2"], ["SU95", "A320", "B738", "A333"], at("WH", 25, 10), shift_end_ts=SHIFT_END_TS, home_node_id="WH"),
            Employee("E-04", "Кузнецов И. П.", ["B1", "A"], ["A320", "B738", "A333"], at("CP3", -20, -18), shift_end_ts=SHIFT_END_TS, home_node_id="TC", vehicle_id="V-02"),
            Employee("E-05", "Морозова Е. А.", ["A"], ["SU95", "A320"], at("TC", -14, 22), shift_end_ts=SHIFT_END_TS, home_node_id="TC"),
            Employee("E-06", "Волков С. Н.", ["B1", "NDT"], ["B738", "A333"], at("B2", 12, 25), shift_end_ts=SHIFT_END_TS, home_node_id="TC"),
            Employee("E-07", "Николаев П. Р.", ["B2"], ["SU95"], at("TC", 5, 30), shift_end_ts=SHIFT_END_TS, home_node_id="TC"),
            Employee("E-08", "Орлов В. В.", ["B1", "STRUCT"], ["SU95", "A320", "B738", "A333"], at("TC", 30, 8), shift_end_ts=SHIFT_END_TS, home_node_id="TC"),
        ],
        aircraft=[
            Aircraft("RA-89142", "SU95", "A1", "SU 1402", SIM_START_TS + minutes_to_seconds(40), captain_name="Ковалёв А. И."),
            Aircraft("VP-BKC", "A320", "A3", "SU 1108", SIM_START_TS + minutes_to_seconds(25), "BOARDING", "Лебедев М. Ю."),
            Aircraft("RA-73745", "B738", "B1", "SU 1633", SIM_START_TS + minutes_to_seconds(90), captain_name="Соколов Р. Н."),
            Aircraft("VQ-BQX", "A333", "B2", "SU 274", SIM_START_TS + minutes_to_seconds(120), captain_name="Гусев Д. А."),
            Aircraft("RA-89096", "SU95", "A2", "SU 1319", SIM_START_TS + minutes_to_seconds(55), captain_name="Тихонов Е. С."),
        ],
        vehicles=[
            Vehicle("V-01", "RAMP_CAR", at("TC", -20, -20)),
            Vehicle("V-02", "E_CART", at("CP2", 10, -10), operator_id="E-04"),
            Vehicle("V-03", "RAMP_CAR", at("B2", -25, 15), "IN_USE", "E-06", busy_until_ts=SIM_START_TS + minutes_to_seconds(20)),
            Vehicle("V-04", "LADDER_TRUCK", at("WH", -18, 18)),
        ],
    )

ALL_RATINGS = ("SU95", "A320", "B738", "A333")


@dataclass(frozen=True, slots=True)
class Scenario:
    code: str
    title: str
    purpose: str
    covers: tuple[str, ...]
    criteria: tuple[int, ...]
    given: str
    expected: str
    build: Callable[[ShiftState], None]
    steps: tuple[dict, ...]


def _at(state: ShiftState, node_id: str, dx: float = 0.0, dy: float = 0.0) -> Position:
    return Position.at_node(state.apron, node_id, dx, dy)


def _people(state: ShiftState, rows: tuple) -> list[Employee]:
    out = []
    for row in rows:
        extra = row[-1] if row and isinstance(row[-1], dict) else {}
        base = row[:-1] if extra else row
        eid, name, quals, node, *xy = base
        dx, dy = (tuple(xy) + (0.0, 0.0))[:2]
        extra = dict(extra)
        ratings = extra.pop("ratings", ALL_RATINGS)
        if "busy_min" in extra:
            extra["busy_until_ts"] = state.sim_start_ts + minutes_to_seconds(extra.pop("busy_min"))
        if "tasks" in extra:
            extra["tasks_completed_in_shift"] = extra.pop("tasks")
        out.append(
            Employee(
                id=eid,
                name=name,
                qualifications=[quals] if isinstance(quals, str) else list(quals),
                type_ratings=list(ratings),
                position=_at(state, node, dx, dy),
                shift_end_ts=state.shift_end_ts,
                **extra,
            )
        )
    return out


def _cars(state: ShiftState, rows: tuple) -> list[Vehicle]:
    out = []
    for row in rows:
        extra = row[-1] if row and isinstance(row[-1], dict) else {}
        base = row[:-1] if extra else row
        vid, type_id, node, *xy = base
        dx, dy = (tuple(xy) + (0.0, 0.0))[:2]
        out.append(Vehicle(id=vid, type_id=type_id, position=_at(state, node, dx, dy), **extra))
    return out


def _layout(crew, fleet=()):
    def build(state: ShiftState) -> None:
        state.employees = _people(state, crew)
        state.vehicles = _cars(state, fleet)
    return build


def apply_step(state: ShiftState, step: dict, log: list[str]) -> None:
    """Минимальный исполнитель шагов предзагрузки (без assert-раннера)."""
    op = step["op"]
    if op == "call":
        if step.get("at") is not None:
            state.now_ts = state.sim_start_ts + step["at"]
        policy = Policy(**step["policy"]) if step.get("policy") else DEFAULT_POLICY
        request = dispatcher.create_request(
            state,
            aircraft_id=step["aircraft_id"],
            fault_type_id=step["fault_type_id"],
            priority=step.get("priority"),
            reported_by=step.get("reported_by"),
        )
        proposal = dispatcher.propose(state, request, policy=policy)
        if step.get("commit", True) and proposal.selected is not None:
            dispatcher.commit(state, request, proposal, employee_id=step.get("assign_to"))
        outcome = proposal.selected.employee_id if proposal.selected else "ЭСКАЛАЦИЯ"
        log.append(
            f"Вызов: {request.fault.name} | борт {request.aircraft_id} "
            f"на {request.stand_node_id} | {request.priority_info.name} → {outcome}"
        )
        return
    if op == "move":
        employee = state.employee(step["employee_id"])
        if step.get("node_id"):
            node = state.apron.node(step["node_id"])
            dx, dy = step.get("dx", 0.0), step.get("dy", 0.0)
            employee.position.move_to(node.x + dx, node.y + dy, None if dx or dy else step["node_id"])
            log.append(f"Перемещение: {employee.id} → {node.name}")
        else:
            employee.position.move_to(step.get("x") or 0.0, step.get("y") or 0.0, None)
            log.append(f"Перемещение: {employee.id} → ({step.get('x')}, {step.get('y')})")
        return
    if op == "status":
        employee = state.employee(step["employee_id"])
        if step.get("status"):
            employee.status = step["status"]
        if step.get("busy_for_min") is not None:
            employee.busy_until_ts = state.now_ts + minutes_to_seconds(step["busy_for_min"])
        if step.get("tasks_completed") is not None:
            employee.tasks_completed_in_shift = step["tasks_completed"]
        suffix = f", занят ещё {step['busy_for_min']:g} мин" if step.get("busy_for_min") is not None else ""
        log.append(f"Статус: {employee.id} → {employee.status_info.name}{suffix}")
        return
    if op == "qualify":
        employee = state.employee(step["employee_id"])
        if step.get("qualifications"):
            employee.qualifications = list(step["qualifications"])
        if step.get("type_ratings"):
            employee.type_ratings = list(step["type_ratings"])
        log.append(
            f"Квалификация: {employee.id} → {', '.join(employee.qualifications)} "
            f"| допуски: {', '.join(employee.type_ratings)}"
        )
        return
    if op == "vehicle":
        vehicle = state.find_vehicle(step["vehicle_id"])
        if vehicle is None:
            raise KeyError(f"Сценарий ссылается на несуществующий транспорт {step['vehicle_id']}")
        if step.get("status"):
            vehicle.status = step["status"]
        if step.get("node_id"):
            vehicle.position = Position.at_node(state.apron, step["node_id"])
        log.append(f"Спецтранспорт: {vehicle.id} → {catalog.VEHICLE_STATUS[vehicle.status].name}")
        return
    if op == "close":
        edge = state.apron.set_edge_closed(step["edge_id"], True)
        log.append(f"Внештатное условие: перекрыт участок «{edge.name}»")
        return
    if op == "open":
        edge = state.apron.set_edge_closed(step["edge_id"], False)
        log.append(f"Участок «{edge.name}» снова открыт")
        return
    if op == "wait":
        state.now_ts += step["seconds"]
        log.append(f"Прошло {format_duration(step['seconds'])}, текущее время {format_clock(state.now_ts)}")
        return
    raise TypeError(f"Неизвестный тип шага сценария: {op}")


def describe_step(step: dict) -> str:
    op = step["op"]
    if op == "call":
        fault = catalog.fault_type(step["fault_type_id"])
        suffix = "" if step.get("commit", True) else " (без подтверждения)"
        offset = f" через {step['at']} с" if step.get("at") else ""
        policy = step.get("policy") or {}
        if policy.get("consider_break"):
            policy_note = ", разрешено снимать с перерыва"
        elif policy.get("allow_preemption"):
            policy_note = ", разрешено вытеснение"
        else:
            policy_note = ""
        return f"Вызов{offset}: {fault.name}, борт {step['aircraft_id']}{policy_note}{suffix}"
    if op == "move":
        target = step.get("node_id") or f"({step.get('x')}, {step.get('y')})"
        return f"Перемещение {step['employee_id']} → {target}"
    if op == "status":
        bits = []
        if step.get("status"):
            bits.append(catalog.EMPLOYEE_STATUS[step["status"]].name)
        if step.get("busy_for_min") is not None:
            bits.append(f"занят ещё {step['busy_for_min']:g} мин")
        return f"Статус {step['employee_id']}: {', '.join(bits)}"
    if op == "qualify":
        return f"Квалификация {step['employee_id']} → {', '.join(step.get('qualifications') or ())}"
    if op == "vehicle":
        return f"Спецтранспорт {step['vehicle_id']}"
    if op == "close":
        return f"Перекрытие участка {step['edge_id']}"
    if op == "open":
        return f"Открытие участка {step['edge_id']}"
    if op == "wait":
        return f"Ожидание {format_duration(step['seconds'])}"
    return op


def prepare(state: ShiftState, scenario: Scenario) -> tuple[dict | None, list[str]]:
    """Расстановка + все шаги, кроме последнего вызова (его делает диспетчер)."""
    steps = list(scenario.steps)
    last = None
    cut = len(steps)
    for index in range(len(steps) - 1, -1, -1):
        if steps[index]["op"] == "call":
            last, cut = steps[index], index
            break
    log: list[str] = []
    for step in steps[:cut]:
        apply_step(state, step, log)
    return last, log


def _sc(code, title, purpose, covers, criteria, given, expected, crew, steps, fleet=(), build=None) -> Scenario:
    return Scenario(
        code=code,
        title=title,
        purpose=purpose,
        covers=covers,
        criteria=criteria,
        given=given,
        expected=expected,
        build=build or _layout(crew, fleet),
        steps=steps,
    )


def _build_ts8(state: ShiftState) -> None:
    quals = (["B1"], ["B2"], ["A"], ["B1", "NDT"], ["B2", "A"], ["B1", "STRUCT"])
    nodes = state.apron.nodes
    state.employees = [
        Employee(
            id=f"E-{101 + i}",
            name=f"Техник {101 + i}",
            qualifications=list(quals[i % 6]),
            type_ratings=list(ALL_RATINGS),
            position=Position(
                nodes[i % len(nodes)].x + (i % 7) * 7 - 21,
                nodes[i % len(nodes)].y + (i % 5) * 7 - 14,
            ),
            shift_end_ts=state.shift_end_ts,
        )
        for i in range(26)
    ]
    state.vehicles = _cars(
        state,
        (
            ("V-01", "RAMP_CAR", "TC", -20, -20),
            ("V-02", "E_CART", "CP2", 10, -10),
            ("V-03", "RAMP_CAR", "CP3", -14, 16),
            ("V-04", "LADDER_TRUCK", "WH", -18, 18),
        ),
    )


ALL: tuple[Scenario, ...] = (
    _sc(
        "TS-1", "Базовое назначение",
        "Штатный вызов при полностью свободной смене: система должна найти ближайшего по времени техника нужного профиля, построить маршрут и уложиться в регламент.",
        ("FR-1", "FR-2", "FR-3", "FR-4", "CT-2"), (4, 5),
        "Пять свободных сотрудников на разных точках перрона. Вызов: борт VP-BKC (A320) на стоянке A3, отказ инерциальной навигационной системы.",
        "Назначен ближайший по времени техник по авионике, маршрут построен, время прибытия не превышает 15 минут.",
        (
            ("E-01", "Иванов А. С.", ["B1"], "TC", 18, -12),
            ("E-02", "Петров Д. М.", ["B2"], "A1", -12, 18),
            ("E-03", "Сидоров К. В.", ["B2"], "CP3", 20, -14),
            ("E-04", "Кузнецов И. П.", ["B1", "A"], "CP2", -16, 12),
            ("E-05", "Морозова Е. А.", ["A"], "WH", 14, 16),
        ),
        ({"op": "call", "aircraft_id": "VP-BKC", "fault_type_id": "NAV_IRS_FAIL", "reported_by": "КВС Лебедев М. Ю."},),
        fleet=(("V-01", "RAMP_CAR", "TC", -20, -20),),
    ),
    _sc(
        "TS-2", "Квалификация важнее расстояния",
        "Самый близкий к борту сотрудник не имеет нужной специализации. Система обязана его отклонить и объяснить причину диспетчеру.",
        ("FR-2",), (3, 5),
        "Техник по планеру и двигателям стоит непосредственно у ВС. Вызов требует техника по авионике, который находится на соседней стоянке.",
        "Назначен специалист нужного профиля; ближайший отклонён с причиной «нет требуемой квалификации».",
        (
            ("E-01", "Иванов А. С.", ["B1"], "A3", 8, 10),
            ("E-02", "Петров Д. М.", ["B2"], "A2", -10, 14),
            ("E-05", "Морозова Е. А.", ["A"], "A3", -12, 6),
        ),
        ({"op": "call", "aircraft_id": "VP-BKC", "fault_type_id": "NAV_IRS_FAIL"},),
    ),
    _sc(
        "TS-3", "Учёт загруженности и времени убытия",
        "Занятый сотрудник не исключается из рассмотрения: система считает его время убытия и сравнивает итоговое время прибытия наравне со свободными. Проверяются оба исхода такого сравнения.",
        ("FR-1", "FR-3"), (3, 5),
        "Техник по авионике стоит у самого ВС, но занят работами. Второй техник свободен, однако находится на складе в семи минутах ходьбы. Вызов подаётся дважды: при остатке работ 12 минут и при остатке 2 минуты.",
        "В первом случае назначается свободный, во втором — занятый: он освобождается раньше и всё равно прибывает быстрее.",
        (
            ("E-02", "Петров Д. М.", ["B2"], "A3", -14, 18, {"status": "ON_TASK", "busy_min": 12}),
            ("E-03", "Сидоров К. В.", ["B2"], "WH", 20, 12),
        ),
        (
            {"op": "call", "aircraft_id": "VP-BKC", "fault_type_id": "NAV_IRS_FAIL", "commit": False},
            {"op": "status", "employee_id": "E-02", "busy_for_min": 2},
            {"op": "call", "aircraft_id": "VP-BKC", "fault_type_id": "NAV_IRS_FAIL", "commit": False},
        ),
    ),
    _sc(
        "TS-4", "Несколько вызовов подряд",
        "Три заявки поступают с интервалом 30 секунд на разные стоянки при ограниченном составе смены. Каждое следующее назначение обязано учитывать уже занятых.",
        ("CT-1",), (3, 5, 8),
        "Четыре сотрудника: два по авионике, два по планеру. Вызовы в 14:00:00, 14:00:30 и 14:01:00 на стоянки A3, A1 и B1.",
        "Три разных исполнителя, ни одного двойного назначения, все прибытия в пределах регламента.",
        (
            ("E-01", "Иванов А. С.", ["B1"], "TC", 18, -12),
            ("E-02", "Петров Д. М.", ["B2"], "A1", -12, 18),
            ("E-03", "Сидоров К. В.", ["B2"], "CP3", 20, -14),
            ("E-04", "Кузнецов И. П.", ["B1", "A"], "CP2", -16, 12),
        ),
        (
            {"op": "call", "at": 0, "aircraft_id": "VP-BKC", "fault_type_id": "NAV_IRS_FAIL"},
            {"op": "call", "at": 30, "aircraft_id": "RA-89142", "fault_type_id": "PACK_FAIL"},
            {"op": "call", "at": 60, "aircraft_id": "RA-73745", "fault_type_id": "COM_VHF_FAIL"},
        ),
        fleet=(("V-01", "RAMP_CAR", "TC", -20, -20),),
    ),
    _sc(
        "TS-5", "Нехватка квалифицированного персонала",
        "Ни один кандидат не укладывается в регламент. Система обязана не назначать молча, а сообщить о нарушении и предложить варианты действий.",
        ("CT-1",), (3, 5),
        "Вызов на дальнюю стоянку B2 по борту VQ-BQX (A330). Из техников по авионике: один без допуска на этот тип ВС, второй занят работами на полчаса, третий на регламентированном перерыве.",
        "Назначение не выдаётся, заявка переводится в эскалацию со списком предложений. Повторный подбор с разрешением снимать с перерыва даёт исполнителя.",
        (
            ("E-01", "Иванов А. С.", ["B1"], "B2", 10, 12),
            ("E-02", "Петров Д. М.", ["B2"], "A1", -12, 18, {"ratings": ("A320", "B738")}),
            ("E-03", "Сидоров К. В.", ["B2"], "TC", 16, 10, {"status": "ON_TASK", "busy_min": 30}),
            ("E-07", "Николаев П. Р.", ["B2"], "TC", 5, 30, {"status": "BREAK", "busy_min": 12}),
        ),
        (
            {"op": "call", "aircraft_id": "VQ-BQX", "fault_type_id": "NAV_IRS_FAIL", "commit": False},
            {"op": "call", "aircraft_id": "VQ-BQX", "fault_type_id": "NAV_IRS_FAIL", "commit": False, "policy": {"consider_break": True}},
        ),
        fleet=(("V-01", "RAMP_CAR", "TC", -20, -20),),
    ),
    _sc(
        "TS-6", "Влияние спецтранспорта на маршрут",
        "Контролируемый эксперимент: два техника одной квалификации стоят в одной точке, но только за одним закреплён перронный автомобиль. Проверяется, что система сравнивает способы перемещения, а не только расстояния.",
        ("FR-3",), (3, 5, 8),
        "Оба техника по планеру находятся у технического центра, поэтому расстояние по прямой до цели у них совпадает. За E-04 закреплён автомобиль V-01. Вызовы подаются на ближнюю стоянку A2 и на дальнюю B2.",
        "В обоих случаях назначен E-04 на спецтранспорте. На стоянку A2 пеший и транспортный маршруты идут физически разными дорогами: пешеход — служебной дорожкой через склад и проходом между стоянками, транспорт — служебным проездом.",
        (
            ("E-01", "Иванов А. С.", ["B1"], "TC"),
            ("E-04", "Кузнецов И. П.", ["B1"], "TC"),
        ),
        (
            {"op": "call", "aircraft_id": "RA-89096", "fault_type_id": "PACK_FAIL", "commit": False},
            {"op": "call", "aircraft_id": "VQ-BQX", "fault_type_id": "PACK_FAIL", "commit": False},
        ),
        fleet=(("V-01", "RAMP_CAR", "TC", -20, -20, {"operator_id": "E-04"}),),
    ),
    _sc(
        "TS-7", "Сравнение с интуитивным выбором диспетчера",
        "Обязательная проверка ТЗ: система не должна назначать сотрудника, который будет в пути дольше другого доступного кандидата. Перекрытие прохода делает визуально ближайшего кандидата фактически более медленным.",
        ("CT-1", "CT-3"), (5,),
        "Два техника по авионике: на складе (178 м до стоянки A1 по прямой) и на западной развязке (300 м по прямой). Тот же вызов подаётся до и после перекрытия служебной дорожки «Склад — A1».",
        "До перекрытия оба способа выбора совпадают. После перекрытия система выбирает более далёкого по прямой кандидата, потому что визуально ближайшему приходится идти в обход.",
        (
            ("E-11", "Складской К. О.", ["B2"], "WH"),
            ("E-12", "Развязкин Ц. Е.", ["B2"], "CP1"),
        ),
        (
            {"op": "call", "aircraft_id": "RA-89142", "fault_type_id": "NAV_IRS_FAIL", "commit": False},
            {"op": "close", "edge_id": "E-WH-A1"},
            {"op": "call", "aircraft_id": "RA-89142", "fault_type_id": "NAV_IRS_FAIL", "commit": False},
        ),
    ),
    _sc(
        "TS-8", "Производительность на максимальной конфигурации",
        "Нагрузочная проверка норматива ТЗ: время расчёта и выдачи предложения по маршруту не должно превышать 10 секунд.",
        ("CT-2", "TR-5"), (4,),
        "10 контрольных точек, 26 сотрудников, 4 единицы спецтранспорта, пять заявок подряд, включая работы с заездом на склад ЗИП.",
        "Каждое предложение выдаётся заметно быстрее норматива; инвариант оптимальности соблюдается на всех пяти расчётах.",
        (),
        (
            {"op": "call", "at": 0, "aircraft_id": "VP-BKC", "fault_type_id": "NAV_IRS_FAIL"},
            {"op": "call", "at": 20, "aircraft_id": "RA-89142", "fault_type_id": "HYD_LEAK"},
            {"op": "call", "at": 40, "aircraft_id": "RA-73745", "fault_type_id": "TIRE_DAMAGE"},
            {"op": "call", "at": 60, "aircraft_id": "VQ-BQX", "fault_type_id": "BIRD_STRIKE"},
            {"op": "call", "at": 80, "aircraft_id": "RA-89096", "fault_type_id": "PITOT_DISAGREE"},
        ),
        build=_build_ts8,
    ),
    _sc(
        "TS-9", "Изменение параметров проверяющим",
        "Жюри должно иметь возможность самостоятельно менять исходные данные и немедленно проверять корректность назначений: перемещать сотрудников, менять квалификацию, добавлять вызовы.",
        ("TR-10",), (9,),
        "Исходно единственный техник по авионике находится на восточной развязке. Затем он перемещается ближе к борту, после чего второму сотруднику добавляется допуск по авионике и он ставится прямо у ВС.",
        "После каждого изменения система пересчитывает назначение, результат меняется предсказуемо, инвариант оптимальности сохраняется.",
        (
            ("E-01", "Иванов А. С.", ["B1"], "TC", 18, -12),
            ("E-02", "Петров Д. М.", ["B2"], "CP3", 20, -14),
        ),
        (
            {"op": "call", "aircraft_id": "VP-BKC", "fault_type_id": "NAV_IRS_FAIL", "commit": False},
            {"op": "move", "employee_id": "E-02", "node_id": "A2"},
            {"op": "call", "aircraft_id": "VP-BKC", "fault_type_id": "NAV_IRS_FAIL", "commit": False},
            {"op": "qualify", "employee_id": "E-01", "qualifications": ("B1", "B2")},
            {"op": "move", "employee_id": "E-01", "node_id": "A3"},
            {"op": "call", "aircraft_id": "VP-BKC", "fault_type_id": "NAV_IRS_FAIL", "commit": False},
        ),
    ),
    _sc(
        "TS-10", "Приоритизация и адаптивное перераспределение",
        "Авторское расширение задания. Инженер уже направлен на отложенную заявку, когда поступает вызов AOG, и он единственный, кто успевает. Система предлагает перераспределение с обоснованием, а не отказывает.",
        ("CT-1",), (3, 8),
        "Единственный техник по авионике направлен на замену проблескового маяка (отложенная заявка по MEL). Следом поступает отказ инерциальной системы — вылет заблокирован.",
        "Без разрешения на вытеснение — эскалация. С разрешением — инженер снимается с отложенной заявки, она возвращается в очередь.",
        (
            ("E-01", "Иванов А. С.", ["B2"], "A3", 10, 12),
            ("E-05", "Морозова Е. А.", ["A"], "TC", -14, 22),
        ),
        (
            {"op": "call", "aircraft_id": "VP-BKC", "fault_type_id": "NAV_LIGHT_FAIL"},
            {"op": "call", "at": 30, "aircraft_id": "RA-89096", "fault_type_id": "NAV_IRS_FAIL", "commit": False},
            {"op": "call", "at": 30, "aircraft_id": "RA-89096", "fault_type_id": "NAV_IRS_FAIL", "policy": {"allow_preemption": True}},
        ),
    ),
)

BY_CODE: Mapping[str, Scenario] = {item.code: item for item in ALL}


def get(code: str) -> Scenario:
    try:
        return BY_CODE[code]
    except KeyError:
        raise KeyError(f"Неизвестный сценарий: {code}") from None


def codes() -> tuple[str, ...]:
    return tuple(item.code for item in ALL)
