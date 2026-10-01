from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status

from core.catalog import format_clock
from core.models import Policy
from core.scenarios import SIM_START_TS
from db import UserRow, get_session_factory, verify_password
from api import presenters
from api import schemas as s
from api.session import DispatcherSession, SessionError

SESSION_USER = "user_id"
SESSION_IMPERSONATOR = "impersonator_id"


@dataclass(frozen=True, slots=True)
class Actor:
    id: int
    username: str
    role: str
    display_name: str
    employee_id: str | None
    aircraft_id: str | None
    impersonator_id: int | None = None

    @property
    def impersonating(self) -> bool:
        return self.impersonator_id is not None


def actor_from_row(row: UserRow, impersonator_id: int | None = None) -> Actor:
    return Actor(
        id=row.id, username=row.username, role=row.role, display_name=row.display_name,
        employee_id=row.employee_id, aircraft_id=row.aircraft_id, impersonator_id=impersonator_id,
    )


def authenticate(username: str, password: str) -> UserRow:
    with get_session_factory()() as db:
        row = db.query(UserRow).filter(UserRow.username == username).one_or_none()
        if row is None or not verify_password(password, row.password_hash):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный логин или пароль")
        db.expunge(row)
        return row


def current_actor(request: Request) -> Actor:
    user_id = request.session.get(SESSION_USER)
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Требуется вход в систему")
    with get_session_factory()() as db:
        row = db.get(UserRow, int(user_id))
    if row is None:
        request.session.clear()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Учётная запись больше не существует")
    impersonator = request.session.get(SESSION_IMPERSONATOR)
    return actor_from_row(row, int(impersonator) if impersonator else None)


def require_roles(*roles: str):
    def dependency(actor: Actor = Depends(current_actor)) -> Actor:
        if actor.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Недостаточно прав для этого действия")
        return actor
    return dependency


require_admin = require_roles("admin")
require_employee = require_roles("employee")
require_aircraft = require_roles("aircraft")

router = APIRouter(prefix="/api")
session = DispatcherSession()


def _policy(view: s.PolicyView) -> Policy:
    return Policy(
        consider_busy=view.consider_busy,
        allow_preemption=view.allow_preemption,
        consider_break=view.consider_break,
    )


def _guard(action) -> s.StateView:
    try:
        action()
    except SessionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    return session.snapshot()


# ---------------------------------------------------------------------------
# Справочные данные и состояние
# ---------------------------------------------------------------------------


@router.get(
    "/reference",
    response_model=s.ReferenceView,
    summary="Справочники и карта перрона",
    description="Неизменяемые данные: граф перрона, каталог неисправностей, "
    "квалификации, приоритеты, нормативные тайминги и список сценариев. "
    "Достаточно запросить один раз при загрузке интерфейса.",
)
def get_reference(_actor: Actor = Depends(current_actor)) -> s.ReferenceView:
    session.ensure()
    return presenters.reference_view(session.state.apron)


@router.get(
    "/state",
    response_model=s.StateView,
    summary="Текущее состояние смены",
    description="Полный снимок: сотрудники с координатами, ВС на стоянках, "
    "спецтранспорт, заявки, открытое предложение системы и журнал действий.",
)
def get_state(_actor: Actor = Depends(require_admin)) -> s.StateView:
    return session.snapshot()


# ---------------------------------------------------------------------------
# Работа диспетчера
# ---------------------------------------------------------------------------


@router.post(
    "/calls",
    response_model=s.StateView,
    summary="Зафиксировать вызов и подобрать исполнителя",
    description="Создаёт заявку по борту и типу неисправности, оценивает всю смену "
    "и возвращает предложение с полным протоколом рассмотрения кандидатов. "
    "Назначение при этом ещё не применяется.",
)
def create_call(payload: s.CallRequest, _actor: Actor = Depends(require_admin)) -> s.StateView:
    return _guard(
        lambda: session.call(
            payload.aircraft_id, payload.fault_type_id, _policy(payload.policy)
        )
    )


@router.post(
    "/calls/current/recalculate",
    response_model=s.StateView,
    summary="Пересчитать подбор с другой политикой",
    description="Тот же вызов рассматривается заново: можно разрешить снимать "
    "сотрудников с перерыва или вытеснять с менее приоритетных заявок.",
)
def recalculate_call(
    payload: s.RecalculateRequest, _actor: Actor = Depends(require_admin)
) -> s.StateView:
    return _guard(lambda: session.recalculate(_policy(payload.policy)))


@router.post(
    "/calls/current/commit",
    response_model=s.StateView,
    summary="Подтвердить назначение",
    description="Переводит сотрудника в путь, занимает спецтранспорт и отправляет "
    "уведомление. Поле employee_id позволяет диспетчеру назначить другого "
    "кандидата из протокола — это фиксируется как OVERRIDE.",
)
def commit_call(payload: s.CommitRequest, _actor: Actor = Depends(require_admin)) -> s.StateView:
    return _guard(lambda: session.commit(payload.employee_id))


@router.post(
    "/calls/current/cancel",
    response_model=s.StateView,
    summary="Отменить открытый вызов",
)
def cancel_call(_actor: Actor = Depends(require_admin)) -> s.StateView:
    return _guard(session.cancel)


@router.post(
    "/employees/{employee_id}/position",
    response_model=s.StateView,
    summary="Обновить координаты сотрудника",
    description="Вызывается при перетаскивании иконки по карте. Координаты "
    "фиксируются в модели немедленно; если по текущему вызову решение ещё "
    "не принято, подбор автоматически пересчитывается.",
)
def move_employee(
    payload: s.PositionRequest,
    employee_id: str = Path(description="Табельный идентификатор, например E-03"),
    _actor: Actor = Depends(require_admin),
) -> s.StateView:
    return _guard(lambda: session.move_employee(employee_id, payload.x, payload.y))

@router.post(
    "/scenarios/{code}/load",
    response_model=s.StateView,
    summary="Загрузить контрольный сценарий как исходные данные",
    description="Выполняет подготовительные шаги сценария и предзаполняет форму "
    "вызова. Сам вызов остаётся за диспетчером, чтобы работа алгоритма была видна.",
)
def load_scenario(
    code: str = Path(description="Код сценария, например TS-7"),
    _actor: Actor = Depends(require_admin),
) -> s.StateView:
    return _guard(lambda: session.load_scenario(code))


@router.post(
    "/reset",
    response_model=s.StateView,
    summary="Сбросить смену в исходную расстановку",
)
def reset(_actor: Actor = Depends(require_admin)) -> s.StateView:
    return _guard(session.reset)


@router.get(
    "/health",
    summary="Проверка работоспособности",
    description="Валидация конфигурации карты перрона и версия приложения.",
)
def health() -> dict:
    validation = session.state.apron.validate()
    return {
        "ok": validation.ok,
        "map_errors": list(validation.errors),
        "map_warnings": list(validation.warnings),
        "sim_start_clock": format_clock(SIM_START_TS),
    }
