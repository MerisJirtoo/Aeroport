"""Авторизация и экраны ролей: борт, сотрудник, кабинет администратора."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from core import catalog, scenarios
from core.catalog import format_clock, format_duration
from db import UserRow, get_session_factory
from api import presenters
from api.routes import (
    SESSION_IMPERSONATOR,
    SESSION_USER,
    Actor,
    actor_from_row,
    authenticate,
    current_actor,
    require_admin,
    require_aircraft,
    require_employee,
    router,
    session,
)
from api.session import SessionError


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginRequest(Schema):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class ImpersonateRequest(Schema):
    user_id: int


class FaultReportRequest(Schema):
    fault_type_id: str = Field(min_length=1)


class EmployeePatch(Schema):
    name: str | None = None
    qualifications: list[str] | None = None
    type_ratings: list[str] | None = None
    status: str | None = None


class EmployeeCreate(Schema):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    qualifications: list[str] = Field(min_length=1)
    type_ratings: list[str] = Field(default_factory=list)
    status: str = "AVAILABLE"
    home_node_id: str = "TC"


class AircraftPatch(Schema):
    type_id: str | None = None
    stand_node_id: str | None = None
    flight_number: str | None = None
    captain_name: str | None = None
    status: str | None = None


class AircraftCreate(Schema):
    id: str = Field(min_length=1)
    type_id: str
    stand_node_id: str
    flight_number: str | None = None
    captain_name: str | None = None
    status: str = "ON_STAND"


class VehiclePatch(Schema):
    status: str | None = None
    type_id: str | None = None
    operator_id: str | None = None


class FaultCreate(Schema):
    id: str = Field(min_length=1)
    ata: str = "00"
    name: str = Field(min_length=1)
    description: str = ""
    primary_any_of: list[str] = Field(min_length=1)
    default_priority: str = "NORMAL"
    estimated_work_min: int = 30
    requires_equipment: bool = False
    requires_vehicle: bool = False
    mel_deferrable: bool = False


ACTIVE_REQUEST = {
    "NEW",
    "PROPOSED",
    "ASSIGNED",
    "PENDING",
    "EN_ROUTE",
    "IN_PROGRESS",
    "ESCALATED",
}


def _actor_view(actor: Actor) -> dict:
    return {
        "id": actor.id,
        "username": actor.username,
        "role": actor.role,
        "display_name": actor.display_name,
        "employee_id": actor.employee_id,
        "aircraft_id": actor.aircraft_id,
        "impersonating": actor.impersonating,
        "home": {"admin": "/#/admin", "employee": "/#/employee", "aircraft": "/#/aircraft"}[
            actor.role
        ],
    }


def _conflict(action):
    try:
        return action()
    except SessionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    
@router.post("/auth/login")
def login(payload: LoginRequest, request: Request) -> dict:
    row = authenticate(payload.username.strip(), payload.password)
    request.session[SESSION_USER] = row.id
    request.session.pop(SESSION_IMPERSONATOR, None)
    return {"ok": True, "me": _actor_view(actor_from_row(row))}


@router.post("/auth/logout")
def logout(request: Request) -> dict:
    request.session.clear()
    return {"ok": True}


@router.get("/auth/me")
def me(actor: Actor = Depends(current_actor)) -> dict:
    return _actor_view(actor)


@router.get("/auth/directory")
def directory() -> dict:
    """Список демо-аккаунтов для страницы входа (пароли не отдаём)."""
    session.ensure()
    with get_session_factory()() as db:
        rows = db.query(UserRow).order_by(UserRow.role, UserRow.username).all()
        return {
            "accounts": [
                {
                    "id": row.id,
                    "username": row.username,
                    "role": row.role,
                    "display_name": row.display_name,
                    "hint": "admin" if row.role == "admin" else "demo",
                }
                for row in rows
            ]
        }


@router.post("/auth/impersonate")
def impersonate(payload: ImpersonateRequest, request: Request, actor: Actor = Depends(require_admin)) -> dict:
    if actor.impersonating:
        raise HTTPException(status_code=403, detail="Сначала вернитесь в свою учётную запись")
    with get_session_factory()() as db:
        target = db.get(UserRow, payload.user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        if target.role == "admin":
            raise HTTPException(status_code=409, detail="Нельзя войти как другой администратор")
        request.session[SESSION_IMPERSONATOR] = actor.id
        request.session[SESSION_USER] = target.id
        return {"ok": True, "me": _actor_view(actor_from_row(target, actor.id))}


@router.post("/auth/stop-impersonation")
def stop_impersonation(request: Request) -> dict:
    impersonator_id = request.session.get(SESSION_IMPERSONATOR)
    if not impersonator_id:
        raise HTTPException(status_code=409, detail="Сейчас нет режима подмены")
    with get_session_factory()() as db:
        admin = db.get(UserRow, int(impersonator_id))
        if admin is None:
            request.session.clear()
            raise HTTPException(status_code=401, detail="Сессия администратора потеряна")
        request.session.pop(SESSION_IMPERSONATOR, None)
        request.session[SESSION_USER] = admin.id
        return {"ok": True, "me": _actor_view(actor_from_row(admin))}

@router.get("/me/aircraft")
def aircraft_home(actor: Actor = Depends(require_aircraft)) -> dict:
    session.ensure()
    state = session.state
    aircraft = state.aircraft_by_id(actor.aircraft_id or "")
    request = next(
        (
            item
            for item in reversed(state.requests)
            if item.aircraft_id == aircraft.id and item.status in ACTIVE_REQUEST
        ),
        None,
    )
    assignment = request.assignment if request else None
    employee = state.find_employee(assignment.employee_id) if assignment else None
    return {
        "me": _actor_view(actor),
        "aircraft": presenters.aircraft_view(aircraft, state.apron, state.now_ts),
        "clock": format_clock(state.now_ts),
        "fault_types": [
            {
                "id": item.id,
                "label": f"ATA {item.ata} · {item.name}",
                "description": item.description,
                "priority_name": catalog.PRIORITIES[item.default_priority].name,
                "required": " или ".join(item.requirement.primary.any_of),
            }
            for item in catalog.FAULT_TYPES.values()
        ],
        "request": None
        if request is None
        else {
            "id": request.id,
            "fault_name": request.fault.name,
            "status_name": request.status_info.name,
            "priority_name": request.priority_info.name,
            "deadline": format_clock(request.deadline_ts),
            "employee_name": employee.name if employee else None,
            "eta": format_clock(assignment.arrival_ts) if assignment else None,
        },
    }


@router.post("/me/aircraft/requests")
def aircraft_report(payload: FaultReportRequest, actor: Actor = Depends(require_aircraft)) -> dict:
    session.ensure()
    aircraft_id = actor.aircraft_id or ""
    existing = next(
        (
            item
            for item in session.state.requests
            if item.aircraft_id == aircraft_id and item.status in ACTIVE_REQUEST
        ),
        None,
    )
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"По борту уже есть активная заявка {existing.id}",
        )
    _conflict(lambda: session.report_from_aircraft(aircraft_id, payload.fault_type_id))
    return aircraft_home(actor)


@router.post("/me/aircraft/requests/cancel")
def aircraft_cancel(actor: Actor = Depends(require_aircraft)) -> dict:
    session.ensure()
    aircraft_id = actor.aircraft_id or ""
    request = next(
        (
            item
            for item in reversed(session.state.requests)
            if item.aircraft_id == aircraft_id and item.status in ACTIVE_REQUEST
        ),
        None,
    )
    if request is None:
        raise HTTPException(status_code=409, detail="Активной заявки нет")
    _conflict(lambda: session.cancel_request(request.id))
    return aircraft_home(actor)

@router.get("/me/employee")
def employee_home(actor: Actor = Depends(require_employee)) -> dict:
    session.ensure()
    state = session.state
    employee = state.employee(actor.employee_id or "")
    request = None
    if employee.current_request_id:
        request = state.find_request(employee.current_request_id)
    if request is None:
        request = next(
            (
                item
                for item in reversed(state.requests)
                if item.assignment and item.assignment.employee_id == employee.id
                and item.status in ACTIVE_REQUEST
            ),
            None,
        )
    assignment = request.assignment if request else None
    aircraft = state.find_aircraft(request.aircraft_id) if request else None
    route = None
    if assignment is not None:
        route = presenters.route_view(assignment.route, state.apron)
    return {
        "me": _actor_view(actor),
        "clock": format_clock(state.now_ts),
        "employee": presenters.employee_view(employee, state),
        "closed_edge_ids": list(state.apron.closed_edge_ids()),
        "aircraft": [presenters.aircraft_view(aircraft, state.apron, state.now_ts)] if aircraft else [],
        "vehicles": [
            presenters.vehicle_view(v)
            for v in state.vehicles
            if assignment and v.id == assignment.vehicle_id
        ],
        "employees": [presenters.employee_view(employee, state)],
        "call": None
        if request is None
        else {
            "id": request.id,
            "fault_name": request.fault.name,
            "fault_description": request.fault.description,
            "aircraft_id": request.aircraft_id,
            "stand_name": state.apron.node(request.stand_node_id).name,
            "stand_node_id": request.stand_node_id,
            "priority_name": request.priority_info.name,
            "status_name": request.status_info.name,
            "departure": format_clock(assignment.departure_ts) if assignment else None,
            "arrival": format_clock(assignment.arrival_ts) if assignment else None,
            "deadline": format_clock(request.deadline_ts),
            "mode_text": presenters.mode_text(
                "VEHICLE" if assignment and assignment.vehicle_id else "FOOT",
                assignment.vehicle_id if assignment else None,
            ),
            "notification": next(
                (
                    {"title": note.title, "body": note.body}
                    for note in state.notifications
                    if note.employee_id == employee.id and note.request_id == request.id
                ),
                None,
            ),
        },
        "route": route,
    }


@router.post("/me/employee/complete")
def employee_complete(actor: Actor = Depends(require_employee)) -> dict:
    session.ensure()
    employee = session.state.employee(actor.employee_id or "")
    request_id = employee.current_request_id
    if not request_id:
        raise HTTPException(status_code=409, detail="Нет активной задачи")
    _conflict(lambda: session.complete_request(request_id))
    return employee_home(actor)


@router.post("/me/employee/cancel")
def employee_cancel(actor: Actor = Depends(require_employee)) -> dict:
    session.ensure()
    employee = session.state.employee(actor.employee_id or "")
    request_id = employee.current_request_id
    if not request_id:
        raise HTTPException(status_code=409, detail="Нет активной задачи")
    _conflict(lambda: session.cancel_request(request_id))
    return employee_home(actor)

@router.get("/admin/monitor")
def monitor(_actor: Actor = Depends(require_admin)) -> dict:
    session.ensure()
    state = session.state
    rows = []
    for employee in state.employees:
        request = (
            state.find_request(employee.current_request_id)
            if employee.current_request_id
            else None
        )
        assignment = request.assignment if request else None
        worked = None
        if assignment is not None:
            worked = max(0.0, state.now_ts - assignment.departure_ts)
        rows.append(
            {
                "id": employee.id,
                "name": employee.name,
                "qualifications": list(employee.qualifications),
                "status": employee.status,
                "status_name": presenters.duty_label(employee, state),
                "status_color": employee.status_info.color,
                "busy_label": presenters.duty_label(employee, state),
                "request_id": employee.current_request_id,
                "task": request.fault.name if request else None,
                "aircraft_id": request.aircraft_id if request else None,
                "worked_text": format_duration(worked) if worked is not None else None,
                "busy_until": (
                    format_clock(employee.busy_until_ts) if employee.busy_until_ts else None
                ),
                "user_id": _user_id(employee_id=employee.id),
            }
        )
    pending = []
    for item in state.requests:
        if item.status not in ACTIVE_REQUEST:
            continue
        pending.append(
            {
                "id": item.id,
                "aircraft_id": item.aircraft_id,
                "fault_name": item.fault.name,
                "status": item.status,
                "status_name": item.status_info.name,
                "priority_name": item.priority_info.name,
                "employee_id": item.assignment.employee_id if item.assignment else None,
            }
        )
    counts = {
        "available": sum(1 for e in state.employees if e.status == "AVAILABLE"),
        "busy": sum(1 for e in state.employees if e.status in {"ON_TASK", "EN_ROUTE"}),
        "pending": sum(1 for item in state.requests if item.status == "PENDING"),
    }
    aircraft = [
        {
            **presenters.aircraft_view(ac, state.apron, state.now_ts).model_dump(),
            "user_id": _user_id(aircraft_id=ac.id),
            "captain_name": ac.captain_name,
            "type_id": ac.type_id,
            "stand_node_id": ac.stand_node_id,
            "status_id": ac.status,
        }
        for ac in state.aircraft
    ]
    return {
        "clock": format_clock(state.now_ts),
        "counts": counts,
        "employees": rows,
        "requests": pending,
        "aircraft": aircraft,
        "vehicles": [presenters.vehicle_view(v).model_dump() for v in state.vehicles],
        "catalog": {
            "qualifications": [
                {"id": q.id, "short": q.short, "name": q.name}
                for q in catalog.QUALIFICATIONS.values()
            ],
            "aircraft_types": [
                {"id": t.id, "name": t.name} for t in catalog.AIRCRAFT_TYPES.values()
            ],
            "aircraft_statuses": [
                {"id": s.id, "name": s.name} for s in catalog.AIRCRAFT_STATUS.values()
            ],
            "employee_statuses": [
                {"id": "AVAILABLE", "name": "Свободен"},
                {"id": "ON_TASK", "name": "Занят"},
            ],
            "fault_types": [
                {
                    "id": item.id,
                    "ata": item.ata,
                    "name": item.name,
                    "label": f"ATA {item.ata} · {item.name}",
                    "primary_any_of": list(item.requirement.primary.any_of),
                    "default_priority": item.default_priority,
                    "estimated_work_min": item.estimated_work_min,
                }
                for item in catalog.FAULT_TYPES.values()
            ],
            "priorities": [
                {"id": p.id, "name": p.name} for p in catalog.PRIORITIES.values()
            ],
            "stands": [
                {"id": n.id, "name": n.name} for n in state.apron.nodes if n.is_stand
            ],
            "vehicle_types": [
                {"id": t.id, "name": t.name} for t in catalog.VEHICLE_TYPES.values()
            ],
            "vehicle_statuses": [
                {"id": s.id, "name": s.name} for s in catalog.VEHICLE_STATUS.values()
            ],
        },
        "scenarios": [
            {
                "code": item.code,
                "title": item.title,
                "purpose": item.purpose,
                "given": item.given,
                "expected": item.expected,
                "covers": list(item.covers),
            }
            for item in scenarios.ALL
        ],
        "active_scenario": None
        if session.scenario is None
        else {
            "code": session.scenario.code,
            "title": session.scenario.title,
            "given": session.scenario.given,
            "expected": session.scenario.expected,
        },
        "open_proposal": session.active is not None,
    }


def _user_id(*, employee_id: str | None = None, aircraft_id: str | None = None) -> int | None:
    with get_session_factory()() as db:
        query = db.query(UserRow)
        if employee_id:
            query = query.filter(UserRow.employee_id == employee_id)
        elif aircraft_id:
            query = query.filter(UserRow.aircraft_id == aircraft_id)
        else:
            return None
        row = query.one_or_none()
        return row.id if row else None


@router.patch("/admin/employees/{employee_id}")
def patch_employee(employee_id: str, payload: EmployeePatch, _actor: Actor = Depends(require_admin)) -> dict:
    fields = payload.model_dump(exclude_none=True)
    employee = _conflict(lambda: session.update_employee(employee_id, fields))
    return presenters.employee_view(employee, session.state).model_dump()


@router.post("/admin/employees")
def create_employee(payload: EmployeeCreate, _actor: Actor = Depends(require_admin)) -> dict:
    employee = _conflict(lambda: session.create_employee(payload.model_dump()))
    return presenters.employee_view(employee, session.state).model_dump()


@router.delete("/admin/employees/{employee_id}")
def delete_employee(employee_id: str, _actor: Actor = Depends(require_admin)) -> dict:
    _conflict(lambda: session.delete_employee(employee_id))
    return {"ok": True}


@router.patch("/admin/aircraft/{aircraft_id}")
def patch_aircraft(aircraft_id: str, payload: AircraftPatch, _actor: Actor = Depends(require_admin)) -> dict:
    aircraft = _conflict(
        lambda: session.update_aircraft(aircraft_id, payload.model_dump(exclude_none=True))
    )
    return presenters.aircraft_view(aircraft, session.state.apron, session.state.now_ts).model_dump()


@router.post("/admin/aircraft")
def create_aircraft(payload: AircraftCreate, _actor: Actor = Depends(require_admin)) -> dict:
    aircraft = _conflict(lambda: session.create_aircraft(payload.model_dump()))
    return presenters.aircraft_view(aircraft, session.state.apron, session.state.now_ts).model_dump()


@router.patch("/admin/vehicles/{vehicle_id}")
def patch_vehicle(vehicle_id: str, payload: VehiclePatch, _actor: Actor = Depends(require_admin)) -> dict:
    vehicle = _conflict(
        lambda: session.update_vehicle(vehicle_id, payload.model_dump(exclude_none=True))
    )
    return presenters.vehicle_view(vehicle).model_dump()


@router.delete("/admin/vehicles/{vehicle_id}")
def delete_vehicle(vehicle_id: str, _actor: Actor = Depends(require_admin)) -> dict:
    _conflict(lambda: session.delete_vehicle(vehicle_id))
    return {"ok": True}


@router.post("/admin/faults")
def create_fault(payload: FaultCreate, _actor: Actor = Depends(require_admin)) -> dict:
    fault = _conflict(lambda: session.create_fault_type(payload.model_dump()))
    return {
        "id": fault.id,
        "ata": fault.ata,
        "name": fault.name,
        "label": f"ATA {fault.ata} · {fault.name}",
    }


@router.delete("/admin/faults/{fault_id}")
def delete_fault(fault_id: str, _actor: Actor = Depends(require_admin)) -> dict:
    _conflict(lambda: session.delete_fault_type(fault_id))
    return {"ok": True}


@router.post("/admin/requests/{request_id}/cancel")
def admin_cancel_request(request_id: str, _actor: Actor = Depends(require_admin)) -> dict:
    _conflict(lambda: session.cancel_request(request_id))
    return monitor(_actor)


@router.post("/admin/requests/{request_id}/complete")
def admin_complete_request(request_id: str, _actor: Actor = Depends(require_admin)) -> dict:
    _conflict(lambda: session.complete_request(request_id))
    return monitor(_actor)
