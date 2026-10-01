"""
Маршрутизация по аэропорту
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Sequence

from . import catalog
from .apron import WAREHOUSE_NODE_ID, ApronMap, Node
from .catalog import FOOT, VEHICLE, FaultType, euclidean, format_duration
from .models import (
    Employee,
    Point,
    Position,
    RouteChoice,
    RouteLeg,
    RoutePlan,
    Vehicle,
)

APPROACH_DETOUR_FACTOR = 1.15
TIE_TOLERANCE_S = 0.5


@dataclass(frozen=True, slots=True)
class Path:
    node_ids: tuple[str, ...]
    edge_ids: tuple[str, ...]


@dataclass(slots=True)
class ShortestPaths:
    #Матрица кратчайших путей

    cost: dict[str, dict[str, float]]
    prev_node: dict[str, dict[str, str]]
    prev_edge: dict[str, dict[str, str]]


@dataclass(slots=True)
class RouterStats:
    matrix_builds: int = 0
    path_queries: int = 0


class Router:
    __slots__ = ("_apron", "_cache", "_cache_version", "stats")

    def __init__(self, apron: ApronMap) -> None:
        self._apron = apron
        self._cache: dict[str, ShortestPaths] = {}
        self._cache_version = -1
        self.stats = RouterStats()

    @property
    def apron(self) -> ApronMap:
        return self._apron

    def invalidate(self) -> None:
        self._cache.clear()
        self._cache_version = -1

    def matrix(self, mode: str) -> ShortestPaths:
        version = self._apron.version
        if self._cache_version != version:
            self._cache.clear()
            self._cache_version = version
        if mode not in self._cache:
            self._cache[mode] = self._compute_matrix(mode)
        return self._cache[mode]

    @staticmethod
    def _edge_weight(edge, mode: str) -> float:
        return edge.distance_m / edge.factor_for(mode)

    def _compute_matrix(self, mode: str) -> ShortestPaths:
        adjacency = self._apron.adjacency(mode)
        node_ids = [node.id for node in self._apron.nodes]

        cost: dict[str, dict[str, float]] = {}
        prev_node: dict[str, dict[str, str]] = {}
        prev_edge: dict[str, dict[str, str]] = {}

        for source in node_ids:
            dist: dict[str, float] = {node_id: float("inf") for node_id in node_ids}
            dist[source] = 0.0
            came_from: dict[str, str] = {}
            came_by: dict[str, str] = {}
            visited: set[str] = set()
            queue: list[tuple[float, str]] = [(0.0, source)]

            while queue:
                current_cost, current = heapq.heappop(queue)
                if current in visited:
                    continue
                visited.add(current)
                for link in adjacency[current]:
                    candidate = current_cost + self._edge_weight(link.edge, mode)
                    if candidate < dist[link.to_node_id] - 1e-9:
                        dist[link.to_node_id] = candidate
                        came_from[link.to_node_id] = current
                        came_by[link.to_node_id] = link.edge.id
                        heapq.heappush(queue, (candidate, link.to_node_id))

            cost[source] = dist
            prev_node[source] = came_from
            prev_edge[source] = came_by

        self.stats.matrix_builds += 1
        return ShortestPaths(cost=cost, prev_node=prev_node, prev_edge=prev_edge)

    def path_between(self, mode: str, from_node_id: str, to_node_id: str) -> Path | None:
        self.stats.path_queries += 1
        if from_node_id == to_node_id:
            return Path((from_node_id,), ())

        matrix = self.matrix(mode)
        if matrix.cost[from_node_id][to_node_id] == float("inf"):
            return None

        node_ids = [to_node_id]
        edge_ids: list[str] = []
        current = to_node_id
        guard = len(self._apron.nodes) + 1

        while current != from_node_id:
            guard -= 1
            if guard <= 0:
                raise RuntimeError("Зацикливание при восстановлении маршрута")
            edge_ids.insert(0, matrix.prev_edge[from_node_id][current])
            current = matrix.prev_node[from_node_id][current]
            node_ids.insert(0, current)

        return Path(tuple(node_ids), tuple(edge_ids))

    # Построение участков маршрута
    def entry_node_for(self, position: Position) -> str:
        if position.node_id:
            return position.node_id
        return self._apron.nearest_node(position.x, position.y)[0].id

    def _approach_leg(self, position: Position, node_id: str) -> RouteLeg | None:
        node = self._apron.node(node_id)
        straight = euclidean(position.x, position.y, node.x, node.y)
        if straight < 1:
            return None

        distance_m = straight * APPROACH_DETOUR_FACTOR
        return RouteLeg(
            mode=FOOT,
            distance_m=distance_m,
            seconds=distance_m / catalog.MOVEMENT_MODES[FOOT].base_speed_mps,
            to_node_id=node_id,
            from_point=Point(position.x, position.y),
            note=f"Выход к контрольной точке «{node.short}»",
        )

    def _legs_for_path(self, path: Path, mode: str, speed_mps: float) -> list[RouteLeg]:
        legs: list[RouteLeg] = []
        for index, edge_id in enumerate(path.edge_ids):
            edge = self._apron.edge(edge_id)
            legs.append(
                RouteLeg(
                    mode=mode,
                    distance_m=edge.distance_m,
                    seconds=self._apron.traversal_seconds(edge, mode, speed_mps),
                    from_node_id=path.node_ids[index],
                    to_node_id=path.node_ids[index + 1],
                    edge_id=edge_id,
                )
            )
        return legs

    def _traverse(self, waypoints: Sequence[str], mode: str, speed_mps: float) -> list[RouteLeg] | None:
        legs: list[RouteLeg] = []
        for start, finish in zip(waypoints, waypoints[1:]):
            path = self.path_between(mode, start, finish)
            if path is None:
                return None
            legs.extend(self._legs_for_path(path, mode, speed_mps))
        return legs

    @staticmethod
    def _annotate_warehouse_stop(legs: list[RouteLeg]) -> None:
        pickup = catalog.TIMING.equipment_pickup_s
        for leg in reversed(legs):
            if leg.to_node_id == WAREHOUSE_NODE_ID:
                leg.note = ("Заезд на склад ЗИП: получение инструмента, "f"+{format_duration(pickup)}")
                return

    # Варианты маршрута
    def plan_on_foot(self, position: Position, to_node_id: str, via_warehouse: bool = False) -> RoutePlan:
        speed = catalog.MOVEMENT_MODES[FOOT].base_speed_mps
        entry = self.entry_node_for(position)

        legs: list[RouteLeg] = []
        approach = self._approach_leg(position, entry)
        if approach is not None:
            legs.append(approach)

        waypoints = (
            (entry, WAREHOUSE_NODE_ID, to_node_id) if via_warehouse else (entry, to_node_id)
        )
        traversal = self._traverse(waypoints, FOOT, speed)
        if traversal is None:
            return RoutePlan.unreachable()
        legs.extend(traversal)

        overhead = 0.0
        if via_warehouse:
            overhead += catalog.TIMING.equipment_pickup_s
            self._annotate_warehouse_stop(legs)

        return RoutePlan(legs=legs, overhead_seconds=overhead, via_warehouse=via_warehouse)

    def plan_with_vehicle(
        self,
        position: Position,
        vehicle: Vehicle,
        to_node_id: str,
        via_warehouse: bool = False,
    ) -> RoutePlan:
        #Маршрут со спецтранспортом: сотрудник пешком доходит до машины, садится, едет по транспортному подграфу и паркуется у ВС

        if vehicle.position.node_id:
            vehicle_node_id = vehicle.position.node_id
        else:
            vehicle_node_id = self._apron.nearest_node(
                vehicle.position.x,
                vehicle.position.y,
                lambda node: node.has_vehicle_parking,
            )[0].id

        to_vehicle = self.plan_on_foot(position, vehicle_node_id)
        if not to_vehicle.reachable:
            return RoutePlan.unreachable()

        legs = list(to_vehicle.legs)
        if legs:
            legs[-1].note = (
                f"Посадка в «{vehicle.type_info.name}» {vehicle.id}, "
                f"+{format_duration(vehicle.type_info.boarding_s)}"
            )

        waypoints = (
            (vehicle_node_id, WAREHOUSE_NODE_ID, to_node_id)
            if via_warehouse
            else (vehicle_node_id, to_node_id)
        )
        drive_legs = self._traverse(waypoints, VEHICLE, vehicle.speed_mps)
        if drive_legs is None:
            return RoutePlan.unreachable()

        if drive_legs:
            drive_legs[-1].note = (
                "Парковка в зоне обслуживания ВС, "
                f"+{format_duration(vehicle.type_info.parking_s)}"
            )
        legs.extend(drive_legs)

        overhead = float(vehicle.overhead_seconds)
        if via_warehouse:
            overhead += catalog.TIMING.equipment_pickup_s
            self._annotate_warehouse_stop(legs)

        return RoutePlan(
            legs=legs,
            overhead_seconds=overhead,
            vehicle_id=vehicle.id,
            via_warehouse=via_warehouse,
        )

    @staticmethod
    def is_vehicle_usable_by(vehicle: Vehicle, employee: Employee, requires_equipment: bool) -> bool:
        #Может ли сотрудник воспользоваться данной единицей спецтранспорта.
        if not vehicle.is_available:
            return False
        if vehicle.operator_id and vehicle.operator_id != employee.id:
            return False
        if requires_equipment and not vehicle.type_info.carries_equipment:
            return False
        return True

    def best_route(
        self,
        employee: Employee,
        vehicles: Sequence[Vehicle],
        to_node_id: str,
        fault: FaultType,
    ) -> tuple[RouteChoice, tuple[RouteChoice, ...]] | None:
        #Выбор оптимального способа перемещения: сравниваются пеший вариант и каждая доступная единица спецтранспорта
        via_warehouse = fault.requires_equipment
        options: list[RouteChoice] = []

        if not fault.requires_vehicle:
            foot_plan = self.plan_on_foot(employee.position, to_node_id, via_warehouse)
            if foot_plan.reachable:
                options.append(RouteChoice(route=foot_plan, mode=FOOT, vehicle_id=None))

        needs_cargo = via_warehouse or fault.requires_vehicle
        for vehicle in vehicles:
            if not self.is_vehicle_usable_by(vehicle, employee, needs_cargo):
                continue
            plan = self.plan_with_vehicle(
                employee.position, vehicle, to_node_id, via_warehouse
            )
            if plan.reachable:
                options.append(
                    RouteChoice(route=plan, mode=VEHICLE, vehicle_id=vehicle.id)
                )

        if not options:
            return None

        def rank(option: RouteChoice) -> tuple[float, int, str]:
            bucket = round(option.route.total_seconds / TIE_TOLERANCE_S)
            mode_rank = 0 if option.mode == FOOT else 1
            return (bucket, mode_rank, option.vehicle_id or "")

        options.sort(key=rank)
        return options[0], tuple(options)
