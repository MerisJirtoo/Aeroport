"""SQLite: таблицы, сид, загрузка смены и справочников."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import asdict, fields
from pathlib import Path
from types import MappingProxyType

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import JSON

from core import catalog
from core.apron import EDGES, NODES, ApronMap, Edge, Node, StandInfo
from core.catalog import (
    AircraftType,
    FaultRequirement,
    FaultType,
    IdGenerator,
    Priority,
    Qualification,
    RequirementSlot,
    VehicleType,
)
from core.models import (
    Aircraft,
    Assignment,
    Employee,
    MaintenanceRequest,
    Point,
    Position,
    RouteLeg,
    RoutePlan,
    ShiftState,
    Vehicle,
)
from core.router import Router
from core.scenarios import create_initial_state

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin"
DEMO_PASSWORD = "demo"
_ITERATIONS = 210_000
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "ramp.db"

_engine: Engine | None = None
_factory: sessionmaker[Session] | None = None


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), _ITERATIONS)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, expected = stored.split("$", 1)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), _ITERATIONS)
    return hmac.compare_digest(digest.hex(), expected)


class Base(DeclarativeBase):
    pass


class UserRow(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(16), index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    employee_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    aircraft_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


class QualificationRow(Base):
    __tablename__ = "qualifications"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    short: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text)
    color: Mapped[str] = mapped_column(String(16))
    can_release: Mapped[bool] = mapped_column(Boolean)


class AircraftTypeRow(Base):
    __tablename__ = "aircraft_types"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    icao: Mapped[str] = mapped_column(String(8))
    stand_category: Mapped[str] = mapped_column(String(8))
    engines: Mapped[int] = mapped_column(Integer)
    map_scale: Mapped[float] = mapped_column(Float)


class FaultTypeRow(Base):
    __tablename__ = "fault_types"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    ata: Mapped[str] = mapped_column(String(8))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    primary_any_of: Mapped[list] = mapped_column(JSON)
    support_any_of: Mapped[list | None] = mapped_column(JSON, nullable=True)
    support_required: Mapped[bool] = mapped_column(Boolean, default=False)
    default_priority: Mapped[str] = mapped_column(String(16))
    estimated_work_min: Mapped[int] = mapped_column(Integer)
    requires_equipment: Mapped[bool] = mapped_column(Boolean, default=False)
    requires_vehicle: Mapped[bool] = mapped_column(Boolean, default=False)
    mel_deferrable: Mapped[bool] = mapped_column(Boolean, default=False)


class PriorityRow(Base):
    __tablename__ = "priorities"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text)
    weight: Mapped[int] = mapped_column(Integer)
    color: Mapped[str] = mapped_column(String(16))
    can_preempt: Mapped[bool] = mapped_column(Boolean)


class VehicleTypeRow(Base):
    __tablename__ = "vehicle_types"
    id: Mapped[str] = mapped_column(String(24), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    speed_mps: Mapped[float] = mapped_column(Float)
    seats: Mapped[int] = mapped_column(Integer)
    carries_equipment: Mapped[bool] = mapped_column(Boolean)
    boarding_s: Mapped[int] = mapped_column(Integer)
    parking_s: Mapped[int] = mapped_column(Integer)
    glyph: Mapped[str] = mapped_column(String(8))


class MapNodeRow(Base):
    __tablename__ = "map_nodes"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    short: Mapped[str] = mapped_column(String(16))
    type: Mapped[str] = mapped_column(String(24))
    x: Mapped[float] = mapped_column(Float)
    y: Mapped[float] = mapped_column(Float)
    description: Mapped[str] = mapped_column(Text)
    has_vehicle_parking: Mapped[bool] = mapped_column(Boolean, default=False)
    stand_category: Mapped[str | None] = mapped_column(String(8), nullable=True)
    jet_bridge: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    gpu_available: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


class MapEdgeRow(Base):
    __tablename__ = "map_edges"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    from_node_id: Mapped[str] = mapped_column(String(16), ForeignKey("map_nodes.id"))
    to_node_id: Mapped[str] = mapped_column(String(16), ForeignKey("map_nodes.id"))
    type: Mapped[str] = mapped_column(String(24))
    name: Mapped[str] = mapped_column(String(160))
    distance_m: Mapped[float] = mapped_column(Float)
    modes: Mapped[list] = mapped_column(JSON)
    speed_factor: Mapped[dict] = mapped_column(JSON)
    closed: Mapped[bool] = mapped_column(Boolean, default=False)


class EmployeeRow(Base):
    __tablename__ = "employees"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    qualifications: Mapped[list] = mapped_column(JSON)
    type_ratings: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24))
    x: Mapped[float] = mapped_column(Float)
    y: Mapped[float] = mapped_column(Float)
    node_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    busy_until_ts: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_request_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    shift_end_ts: Mapped[float | None] = mapped_column(Float, nullable=True)
    home_node_id: Mapped[str] = mapped_column(String(16), default="TC")
    vehicle_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tasks_completed_in_shift: Mapped[int] = mapped_column(Integer, default=0)
    walked_meters_in_shift: Mapped[float] = mapped_column(Float, default=0.0)


class AircraftRow(Base):
    __tablename__ = "aircraft"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    type_id: Mapped[str] = mapped_column(String(16), ForeignKey("aircraft_types.id"))
    stand_node_id: Mapped[str] = mapped_column(String(16), ForeignKey("map_nodes.id"))
    flight_number: Mapped[str | None] = mapped_column(String(24), nullable=True)
    scheduled_departure_ts: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="ON_STAND")
    captain_name: Mapped[str | None] = mapped_column(String(120), nullable=True)


class VehicleRow(Base):
    __tablename__ = "vehicles"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    type_id: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24), default="FREE")
    x: Mapped[float] = mapped_column(Float)
    y: Mapped[float] = mapped_column(Float)
    node_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    operator_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    current_operator_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    busy_until_ts: Mapped[float | None] = mapped_column(Float, nullable=True)


class RequestRow(Base):
    __tablename__ = "requests"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    aircraft_id: Mapped[str] = mapped_column(String(32), ForeignKey("aircraft.id"))
    stand_node_id: Mapped[str] = mapped_column(String(16))
    fault_type_id: Mapped[str] = mapped_column(String(32), ForeignKey("fault_types.id"))
    created_at_ts: Mapped[float] = mapped_column(Float)
    priority: Mapped[str] = mapped_column(String(16))
    reported_by: Mapped[str] = mapped_column(String(80), default="КВС")
    notes: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(24), default="NEW")
    escalation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    assignment_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class ShiftMetaRow(Base):
    __tablename__ = "shift_meta"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    now_ts: Mapped[float] = mapped_column(Float)
    sim_start_ts: Mapped[float] = mapped_column(Float)
    shift_end_ts: Mapped[float] = mapped_column(Float)
    next_request_n: Mapped[int] = mapped_column(Integer, default=1)
    next_assignment_n: Mapped[int] = mapped_column(Integer, default=1)


def database_url() -> str:
    return os.environ.get("RAMP_DATABASE_URL") or f"sqlite:///{DEFAULT_DB_PATH}"


def get_engine() -> Engine:
    global _engine
    if _engine is not None:
        return _engine
    url = database_url()
    kwargs: dict = {"future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if url in {"sqlite://", "sqlite:///:memory:"}:
            kwargs["poolclass"] = StaticPool
        else:
            DEFAULT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _fk(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    _engine = engine
    return engine


def get_session_factory() -> sessionmaker[Session]:
    global _factory
    if _factory is None:
        _factory = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    return _factory


def init_db() -> None:
    Base.metadata.create_all(get_engine())


def reset_engine() -> None:
    global _engine, _factory
    if _engine is not None:
        _engine.dispose()
    _engine, _factory = None, None


def _cols(row, *skip: str) -> dict:
    return {c.key: getattr(row, c.key) for c in row.__table__.columns if c.key not in skip}


def _take(cls, data: dict, **overrides):
    names = {item.name for item in fields(cls)}
    payload = {key: value for key, value in data.items() if key in names}
    payload.update(overrides)
    return cls(**payload)


def _pos(row) -> Position:
    return Position(row.x, row.y, row.node_id)


def _sync(session: Session, model, items, payload_fn) -> None:
    keep = {item.id for item in items}
    for row in session.query(model).all():
        if row.id not in keep:
            session.delete(row)
    for item in items:
        payload = dict(payload_fn(item))
        payload.pop("id", None)
        session.merge(model(id=item.id, **payload))


def _cols_from(obj, *, skip=(), extra=None) -> dict:
    data = {item.name: getattr(obj, item.name) for item in fields(obj) if item.name not in skip}
    if extra:
        data.update(extra)
    return data


def _point(data: dict | None) -> Point | None:
    if not data:
        return None
    return Point(float(data["x"]), float(data["y"]))


def _load_route(data: dict) -> RoutePlan:
    legs = [
        _take(RouteLeg, item, from_point=_point(item.get("from_point")), to_point=_point(item.get("to_point")))
        for item in data.get("legs") or []
    ]
    return _take(RoutePlan, data, legs=legs)


def _load_assignment(data: dict) -> Assignment:
    return _take(Assignment, data, route=_load_route(data.get("route") or {}))


def load_apron(session: Session) -> ApronMap:
    nodes = []
    for row in session.query(MapNodeRow).all():
        stand = (
            StandInfo(row.stand_category, bool(row.jet_bridge), bool(row.gpu_available))
            if row.stand_category
            else None
        )
        nodes.append(
            Node(row.id, row.name, row.short, row.type, row.x, row.y, row.description, row.has_vehicle_parking, stand)
        )
    edges = [
        Edge(
            row.id, row.from_node_id, row.to_node_id, row.type, row.name,
            row.distance_m, tuple(row.modes), dict(row.speed_factor or {}),
        )
        for row in session.query(MapEdgeRow).all()
    ]
    apron = ApronMap(nodes, edges)
    for row in session.query(MapEdgeRow).filter(MapEdgeRow.closed.is_(True)).all():
        apron.set_edge_closed(row.id, True)
    return apron


def load_state(session: Session | None = None) -> ShiftState:
    own = session is None
    session = session or get_session_factory()()
    try:
        meta = session.get(ShiftMetaRow, 1)
        if meta is None:
            raise RuntimeError("В базе нет метаданных смены — вызовите seed_db()")
        ids = IdGenerator()
        ids._counters["R"] = meta.next_request_n - 1
        ids._counters["AS"] = meta.next_assignment_n - 1
        apron = load_apron(session)
        return ShiftState(
            apron=apron,
            router=Router(apron),
            ids=ids,
            now_ts=meta.now_ts,
            sim_start_ts=meta.sim_start_ts,
            shift_end_ts=meta.shift_end_ts,
            employees=[
                _take(Employee, _cols(row, "x", "y", "node_id"), position=_pos(row))
                for row in session.query(EmployeeRow).all()
            ],
            aircraft=[_take(Aircraft, _cols(row)) for row in session.query(AircraftRow).all()],
            vehicles=[
                _take(Vehicle, _cols(row, "x", "y", "node_id"), position=_pos(row))
                for row in session.query(VehicleRow).all()
            ],
            requests=[
                _take(
                    MaintenanceRequest,
                    {**_cols(row, "assignment_json"), "notes": row.notes or ""},
                    assignment=_load_assignment(row.assignment_json) if row.assignment_json else None,
                )
                for row in session.query(RequestRow).all()
            ],
        )
    finally:
        if own:
            session.close()


def save_state(state: ShiftState, session: Session | None = None) -> None:
    own = session is None
    session = session or get_session_factory()()
    try:
        _sync(
            session, EmployeeRow, state.employees,
            lambda emp: _cols_from(
                emp, skip=("position",),
                extra={"x": emp.position.x, "y": emp.position.y, "node_id": emp.position.node_id},
            ),
        )
        _sync(session, AircraftRow, state.aircraft, lambda ac: _cols_from(ac))
        _sync(
            session, VehicleRow, state.vehicles,
            lambda veh: _cols_from(
                veh, skip=("position",),
                extra={"x": veh.position.x, "y": veh.position.y, "node_id": veh.position.node_id},
            ),
        )
        _sync(
            session, RequestRow, state.requests,
            lambda req: _cols_from(
                req, skip=("assignment", "evaluations"),
                extra={"assignment_json": asdict(req.assignment) if req.assignment else None},
            ),
        )
        closed = set(state.apron.closed_edge_ids())
        for row in session.query(MapEdgeRow).all():
            row.closed = row.id in closed
        counters = getattr(state.ids, "_counters", {})
        session.merge(
            ShiftMetaRow(
                id=1,
                now_ts=state.now_ts,
                sim_start_ts=state.sim_start_ts,
                shift_end_ts=state.shift_end_ts,
                next_request_n=counters.get("R", 0) + 1,
                next_assignment_n=counters.get("AS", 0) + 1,
            )
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        if own:
            session.close()


def _cast(cls, row, **override):
    data = {
        item.name: override[item.name] if item.name in override else getattr(row, item.name)
        for item in fields(cls)
        if item.name in override or hasattr(row, item.name)
    }
    return cls(**data)


def hydrate_catalog(session: Session | None = None) -> bool:
    own = session is None
    session = session or get_session_factory()()
    try:
        qualifications = session.query(QualificationRow).all()
        if not qualifications:
            return False
        catalog.QUALIFICATIONS = MappingProxyType({row.id: _cast(Qualification, row) for row in qualifications})
        types = {row.id: _cast(AircraftType, row) for row in session.query(AircraftTypeRow).all()}
        if types:
            catalog.AIRCRAFT_TYPES = MappingProxyType(types)
        faults = {}
        for row in session.query(FaultTypeRow).all():
            support = (
                RequirementSlot(tuple(row.support_any_of), bool(row.support_required))
                if row.support_any_of
                else None
            )
            faults[row.id] = _cast(
                FaultType, row,
                requirement=FaultRequirement(RequirementSlot(tuple(row.primary_any_of or ())), support),
            )
        if faults:
            catalog.FAULT_TYPES = MappingProxyType(faults)
        priorities = {row.id: _cast(Priority, row) for row in session.query(PriorityRow).all()}
        if priorities:
            catalog.PRIORITIES = MappingProxyType(priorities)
        vehicles = {row.id: _cast(VehicleType, row) for row in session.query(VehicleTypeRow).all()}
        if vehicles:
            catalog.VEHICLE_TYPES = MappingProxyType(vehicles)
        return True
    finally:
        if own:
            session.close()


def _add_rows(session: Session, model, items, adapt=None) -> None:
    if session.query(model).count():
        return
    cols = {c.key for c in model.__table__.columns}
    session.add_all(
        [model(**{key: data[key] for key in cols if key in data})
         for data in ((adapt(item) if adapt else asdict(item)) for item in items)]
    )


def seed_db(*, force: bool = False) -> bool:
    init_db()
    with get_session_factory()() as session:
        if not force and session.query(UserRow).count():
            _add_rows(session, QualificationRow, catalog.stock_qualifications())
            _add_rows(session, AircraftTypeRow, catalog.stock_aircraft_types())
            _add_rows(session, PriorityRow, catalog.stock_priorities())
            _add_rows(session, FaultTypeRow, catalog.stock_fault_types(), _fault_payload)
            _add_rows(session, VehicleTypeRow, catalog.stock_vehicle_types())
            session.commit()
            hydrate_catalog(session)
            return False
        if force:
            for model in (
                RequestRow, UserRow, EmployeeRow, AircraftRow, VehicleRow, ShiftMetaRow,
                MapEdgeRow, MapNodeRow, FaultTypeRow, QualificationRow, AircraftTypeRow,
                PriorityRow, VehicleTypeRow,
            ):
                session.query(model).delete()
        _add_rows(session, QualificationRow, catalog.stock_qualifications())
        _add_rows(session, AircraftTypeRow, catalog.stock_aircraft_types())
        _add_rows(session, PriorityRow, catalog.stock_priorities())
        _add_rows(session, FaultTypeRow, catalog.stock_fault_types(), _fault_payload)
        _add_rows(session, VehicleTypeRow, catalog.stock_vehicle_types())
        if session.query(MapNodeRow).count() == 0:
            session.add_all([
                MapNodeRow(
                    id=node.id, name=node.name, short=node.short, type=node.type,
                    x=node.x, y=node.y, description=node.description,
                    has_vehicle_parking=node.has_vehicle_parking,
                    stand_category=node.stand.category if node.stand else None,
                    jet_bridge=node.stand.jet_bridge if node.stand else None,
                    gpu_available=node.stand.gpu_available if node.stand else None,
                )
                for node in NODES
            ])
        if session.query(MapEdgeRow).count() == 0:
            session.add_all([
                MapEdgeRow(
                    id=edge.id, from_node_id=edge.from_node_id, to_node_id=edge.to_node_id,
                    type=edge.type, name=edge.name, distance_m=edge.distance_m,
                    modes=list(edge.modes), speed_factor=dict(edge.speed_factor), closed=False,
                )
                for edge in EDGES
            ])
        save_state(create_initial_state(), session)
        session.query(UserRow).delete()
        admin_hash = hash_password(ADMIN_PASSWORD)
        demo_hash = hash_password(DEMO_PASSWORD)
        session.add(UserRow(username=ADMIN_USERNAME, password_hash=admin_hash, role="admin", display_name="Диспетчер смены"))
        for emp in session.query(EmployeeRow).all():
            session.add(UserRow(username=emp.id, password_hash=demo_hash, role="employee", display_name=emp.name, employee_id=emp.id))
        for ac in session.query(AircraftRow).all():
            session.add(UserRow(
                username=ac.id, password_hash=demo_hash, role="aircraft",
                display_name=f"{ac.id} · {ac.flight_number or ac.type_id}", aircraft_id=ac.id,
            ))
        session.commit()
        hydrate_catalog(session)
        return True


def _fault_payload(item) -> dict:
    support = item.requirement.support
    data = asdict(item)
    data.pop("requirement", None)
    data["primary_any_of"] = list(item.requirement.primary.any_of)
    data["support_any_of"] = list(support.any_of) if support else None
    data["support_required"] = bool(support and support.required)
    return data
