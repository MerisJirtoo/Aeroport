from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Callable, Iterable, Mapping

from . import catalog
from .catalog import FOOT, VEHICLE, euclidean

NODE_TYPES: Mapping[str, str] = MappingProxyType(
    {
        "STAND": "Стоянка ВС",
        "TECH_CENTER": "Технический центр",
        "WAREHOUSE": "Склад ЗИП",
        "JUNCTION": "Перекрёсток служебного проезда",
    }
)


@dataclass(frozen=True, slots=True)
class EdgeType:
    id: str
    name: str
    description: str


EDGE_TYPES: Mapping[str, EdgeType] = MappingProxyType(
    {
        t.id: t
        for t in (
            EdgeType(
                "SERVICE_ROAD",
                "Служебный проезд",
                "Основная транспортная артерия перрона, движение без ограничений "
                "скорости сверх перронных.",
            ),
            EdgeType(
                "STAND_LANE",
                "Подъездная полоса к стоянке",
                "Заезд в зону обслуживания ВС: скорость транспорта снижена "
                "из соображений безопасности.",
            ),
            EdgeType(
                "FOOT_PATH",
                "Служебная пешеходная дорожка",
                "Проход только для персонала, транспорт не проходит по габаритам.",
            ),
            EdgeType(
                "APRON_CROSSING",
                "Пешеходный проход между стоянками",
                "Размеченный проход по перрону с обходом зон обслуживания ВС.",
            ),
        )
    }
)

@dataclass(frozen=True, slots=True)
class Extent:
    width: float
    height: float


MAP_EXTENT = Extent(width=900, height=520)


@dataclass(frozen=True, slots=True)
class StandInfo:
    category: str
    jet_bridge: bool
    gpu_available: bool


@dataclass(frozen=True, slots=True)
class Node:
    id: str
    name: str
    short: str
    type: str
    x: float
    y: float
    description: str
    has_vehicle_parking: bool = False
    stand: StandInfo | None = None

    @property
    def is_stand(self) -> bool:
        return self.type == "STAND"


NODES: tuple[Node, ...] = (
    Node(
        id="TC",
        name="Технический центр ОТО",
        short="ТЦ",
        type="TECH_CENTER",
        x=60,
        y=440,
        description=(
            "База инженерно-технического персонала смены, комната отдыха, "
            "стоянка перронного транспорта."
        ),
        has_vehicle_parking=True,
    ),
    Node(
        id="WH",
        name="Склад ЗИП и инструментальная",
        short="Склад",
        type="WAREHOUSE",
        x=60,
        y=250,
        description=(
            "Выдача расходных материалов и специнструмента. Обязательный заезд "
            "для работ с признаком requires_equipment."
        ),
        has_vehicle_parking=True,
    ),
    Node(
        id="CP1",
        name="Перекрёсток «Западный»",
        short="П-З",
        type="JUNCTION",
        x=200,
        y=440,
        description="Развязка служебного проезда у выезда из технической зоны.",
    ),
    Node(
        id="CP2",
        name="Перекрёсток «Центральный»",
        short="П-Ц",
        type="JUNCTION",
        x=480,
        y=440,
        description="Центральная развязка служебного проезда, точка ожидания дежурного транспорта.",
        has_vehicle_parking=True,
    ),
    Node(
        id="CP3",
        name="Перекрёсток «Восточный»",
        short="П-В",
        type="JUNCTION",
        x=760,
        y=440,
        description="Развязка служебного проезда у восточной группы стоянок.",
    ),
    Node(
        id="A1",
        name="Стоянка A1",
        short="A1",
        type="STAND",
        x=200,
        y=140,
        description="Контактная стоянка с телетрапом, ближняя к технической зоне.",
        has_vehicle_parking=True,
        stand=StandInfo("C", jet_bridge=True, gpu_available=True),
    ),
    Node(
        id="A2",
        name="Стоянка A2",
        short="A2",
        type="STAND",
        x=340,
        y=120,
        description=(
            "Контактная стоянка с телетрапом. Подъезд транспорта только в объезд, "
            "через центральную развязку."
        ),
        has_vehicle_parking=True,
        stand=StandInfo("C", jet_bridge=True, gpu_available=True),
    ),
    Node(
        id="A3",
        name="Стоянка A3",
        short="A3",
        type="STAND",
        x=480,
        y=140,
        description="Удалённая стоянка, обслуживание с перронным автобусом.",
        has_vehicle_parking=True,
        stand=StandInfo("C", jet_bridge=False, gpu_available=False),
    ),
    Node(
        id="B1",
        name="Стоянка B1",
        short="B1",
        type="STAND",
        x=620,
        y=120,
        description="Удалённая стоянка восточной группы.",
        has_vehicle_parking=True,
        stand=StandInfo("D", jet_bridge=False, gpu_available=True),
    ),
    Node(
        id="B2",
        name="Стоянка B2",
        short="B2",
        type="STAND",
        x=760,
        y=140,
        description="Стоянка для широкофюзеляжных ВС, самая удалённая от технического центра.",
        has_vehicle_parking=True,
        stand=StandInfo("E", jet_bridge=False, gpu_available=True),
    ),
)

@dataclass(frozen=True, slots=True)
class Edge:
    id: str
    from_node_id: str
    to_node_id: str
    type: str
    name: str
    distance_m: float
    modes: tuple[str, ...]
    speed_factor: Mapping[str, float] = field(default_factory=dict)

    def supports(self, mode: str) -> bool:
        return mode in self.modes

    def factor_for(self, mode: str) -> float:
        return self.speed_factor.get(mode, 1.0)

    def other_end(self, node_id: str) -> str:
        return self.to_node_id if node_id == self.from_node_id else self.from_node_id

def _edge(
    edge_id: str,
    from_node_id: str,
    to_node_id: str,
    edge_type: str,
    name: str,
    distance_m: float,
    modes: tuple[str, ...],
    **factors: float,
) -> Edge:
    return Edge(
        id=edge_id,
        from_node_id=from_node_id,
        to_node_id=to_node_id,
        type=edge_type,
        name=name,
        distance_m=distance_m,
        modes=modes,
        speed_factor=MappingProxyType(dict(factors)),
    )


BOTH = (FOOT, VEHICLE)
ON_FOOT = (FOOT,)

EDGES: tuple[Edge, ...] = (
    _edge("E-TC-WH", "TC", "WH", "SERVICE_ROAD",
          "Внутренний проезд технической зоны", 190, BOTH),
    _edge("E-TC-CP1", "TC", "CP1", "SERVICE_ROAD",
          "Выезд из технического центра на служебный проезд", 145, BOTH),
    _edge("E-WH-CP1", "WH", "CP1", "SERVICE_ROAD",
          "Проезд вдоль склада к западной развязке", 245, BOTH),
    _edge("E-WH-A1", "WH", "A1", "FOOT_PATH",
          "Служебная дорожка «Склад — A1»", 190, ON_FOOT),
    _edge("E-CP1-CP2", "CP1", "CP2", "SERVICE_ROAD",
          "Служебный проезд, западный участок", 280, BOTH),
    _edge("E-CP2-CP3", "CP2", "CP3", "SERVICE_ROAD",
          "Служебный проезд, восточный участок", 280, BOTH),
    _edge("E-CP1-A1", "CP1", "A1", "STAND_LANE",
          "Подъезд к стоянке A1", 300, BOTH, VEHICLE=0.6),
    _edge("E-CP2-A2", "CP2", "A2", "STAND_LANE",
          "Подъезд к стоянке A2 (в объезд зоны обслуживания A3)", 350, BOTH, VEHICLE=0.6),
    _edge("E-CP2-A3", "CP2", "A3", "STAND_LANE",
          "Подъезд к стоянке A3", 300, BOTH, VEHICLE=0.6),
    _edge("E-CP3-B1", "CP3", "B1", "STAND_LANE",
          "Подъезд к стоянке B1 (в объезд зоны обслуживания B2)", 350, BOTH, VEHICLE=0.6),
    _edge("E-CP3-B2", "CP3", "B2", "STAND_LANE",
          "Подъезд к стоянке B2", 300, BOTH, VEHICLE=0.6),
    _edge("E-A1-A2", "A1", "A2", "APRON_CROSSING",
          "Проход A1 — A2", 145, ON_FOOT, FOOT=0.85),
    _edge("E-A2-A3", "A2", "A3", "APRON_CROSSING",
          "Проход A2 — A3", 145, ON_FOOT, FOOT=0.85),
    _edge("E-A3-B1", "A3", "B1", "APRON_CROSSING",
          "Проход A3 — B1", 145, ON_FOOT, FOOT=0.85),
    _edge("E-B1-B2", "B1", "B2", "APRON_CROSSING",
          "Проход B1 — B2", 145, ON_FOOT, FOOT=0.85),
)

WAREHOUSE_NODE_ID = "WH"

@dataclass(frozen=True, slots=True)
class Link:
    to_node_id: str
    edge: Edge


@dataclass(frozen=True, slots=True)
class ValidationResult:
    ok: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


class ApronMap:
    __slots__ = ("_nodes", "_edges", "_node_index", "_edge_index", "_closed", "_version")

    def __init__(
        self,
        nodes: Iterable[Node] = NODES,
        edges: Iterable[Edge] = EDGES,
    ) -> None:
        self._nodes = tuple(nodes)
        self._edges = tuple(edges)
        self._node_index = {node.id: node for node in self._nodes}
        self._edge_index = {edge.id: edge for edge in self._edges}
        self._closed: set[str] = set()
        self._version = 1

    @property
    def nodes(self) -> tuple[Node, ...]:
        return self._nodes

    @property
    def edges(self) -> tuple[Edge, ...]:
        return self._edges

    @property
    def extent(self) -> Extent:
        return MAP_EXTENT

    @property
    def version(self) -> int:
        return self._version

    def node(self, node_id: str) -> Node:
        try:
            return self._node_index[node_id]
        except KeyError:
            raise KeyError(f"Неизвестный узел карты: {node_id}") from None

    def edge(self, edge_id: str) -> Edge:
        try:
            return self._edge_index[edge_id]
        except KeyError:
            raise KeyError(f"Неизвестное ребро карты: {edge_id}") from None

    def stands(self) -> tuple[Node, ...]:
        return tuple(node for node in self._nodes if node.is_stand)

    def is_closed(self, edge_id: str) -> bool:
        return edge_id in self._closed

    def closed_edge_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._closed))

    def set_edge_closed(self, edge_id: str, closed: bool) -> Edge:
        edge = self.edge(edge_id)
        already = edge_id in self._closed
        if already == closed:
            return edge
        if closed:
            self._closed.add(edge_id)
        else:
            self._closed.discard(edge_id)
        self._version += 1
        return edge

    def reset_closures(self) -> bool:
        if not self._closed:
            return False
        self._closed.clear()
        self._version += 1
        return True

    def adjacency(self, mode: str, include_closed: bool = False) -> dict[str, list[Link]]:
        adjacency: dict[str, list[Link]] = {node.id: [] for node in self._nodes}
        for edge in self._edges:
            if not edge.supports(mode):
                continue
            if not include_closed and edge.id in self._closed:
                continue
            adjacency[edge.from_node_id].append(Link(edge.to_node_id, edge))
            adjacency[edge.to_node_id].append(Link(edge.from_node_id, edge))
        return adjacency

    def traversal_seconds(self, edge: Edge, mode: str, speed_mps: float | None = None) -> float:
        base = speed_mps or catalog.MOVEMENT_MODES[mode].base_speed_mps
        return edge.distance_m / (base * edge.factor_for(mode))

    def nearest_node(
        self, x: float, y: float, predicate: Callable[[Node], bool] | None = None
    ) -> tuple[Node, float]:
        best: Node | None = None
        best_distance = float("inf")
        for node in self._nodes:
            if predicate is not None and not predicate(node):
                continue
            distance = euclidean(x, y, node.x, node.y)
            if distance < best_distance:
                best_distance = distance
                best = node
        if best is None:
            raise ValueError("Не найден ни один узел, удовлетворяющий условию")
        return best, best_distance

    # -- проверка целостности ----------------------------------------------

    def validate(self) -> ValidationResult:
        errors: list[str] = []
        warnings: list[str] = []

        seen_nodes: set[str] = set()
        for node in self._nodes:
            if node.id in seen_nodes:
                errors.append(f"Дублирующийся id узла: {node.id}")
            seen_nodes.add(node.id)
            if node.type not in NODE_TYPES:
                errors.append(f"Неизвестный тип узла {node.type} у {node.id}")
            if not (0 <= node.x <= MAP_EXTENT.width and 0 <= node.y <= MAP_EXTENT.height):
                errors.append(f"Узел {node.id} выходит за границы карты")
            if node.is_stand and node.stand is None:
                errors.append(f"Стоянка {node.id} без параметров стоянки")

        seen_edges: set[str] = set()
        for edge in self._edges:
            if edge.id in seen_edges:
                errors.append(f"Дублирующийся id ребра: {edge.id}")
            seen_edges.add(edge.id)
            if edge.from_node_id not in seen_nodes:
                errors.append(f"Ребро {edge.id}: нет узла {edge.from_node_id}")
            if edge.to_node_id not in seen_nodes:
                errors.append(f"Ребро {edge.id}: нет узла {edge.to_node_id}")
            if edge.from_node_id == edge.to_node_id:
                errors.append(f"Ребро {edge.id}: петля")
            if edge.type not in EDGE_TYPES:
                errors.append(f"Ребро {edge.id}: неизвестный тип {edge.type}")
            if edge.distance_m <= 0:
                errors.append(f"Ребро {edge.id}: некорректная длина")
            if not edge.modes:
                errors.append(f"Ребро {edge.id}: не задан способ перемещения")

            if edge.from_node_id in seen_nodes and edge.to_node_id in seen_nodes:
                a = self._node_index[edge.from_node_id]
                b = self._node_index[edge.to_node_id]
                straight = euclidean(a.x, a.y, b.x, b.y)
                if edge.distance_m + 0.5 < straight:
                    errors.append(
                        f"Ребро {edge.id}: длина {edge.distance_m} м меньше расстояния "
                        f"по прямой {straight:.1f} м"
                    )

        for mode in () if errors else catalog.MOVEMENT_MODES:
            adjacency = self.adjacency(mode)
            start = self._nodes[0].id
            visited = {start}
            queue = [start]
            while queue:
                current = queue.pop()
                for link in adjacency[current]:
                    if link.to_node_id not in visited:
                        visited.add(link.to_node_id)
                        queue.append(link.to_node_id)
            unreachable = [node.id for node in self._nodes if node.id not in visited]
            if unreachable:
                errors.append(f"Режим {mode}: недостижимые узлы — {', '.join(unreachable)}")

        for stand in self.stands():
            has_vehicle_access = any(
                edge.supports(VEHICLE) and stand.id in (edge.from_node_id, edge.to_node_id)
                for edge in self._edges
            )
            if not has_vehicle_access:
                warnings.append(f"К стоянке {stand.id} нет подъезда для спецтранспорта")

        return ValidationResult(
            ok=not errors, errors=tuple(errors), warnings=tuple(warnings)
        )
