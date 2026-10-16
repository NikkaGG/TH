from datetime import datetime, time

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Account, Draft, DraftStatus
from app.schemas import AccountConnectIn, AccountCreate, AccountOut, AccountUpdate, TaskOut
from app.services import account_sessions, auto_comments, connections, settings, tasks
from app.services.logs import log_event

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


def _replies_today(db: Session, account_ids: list[int]) -> dict[int, int]:
    if not account_ids:
        return {}
    start = datetime.combine(datetime.now().date(), time.min)
    rows = db.execute(
        select(Draft.account_id, func.count())
        .where(Draft.account_id.in_(account_ids), Draft.status == DraftStatus.SENT, Draft.sent_at >= start)
        .group_by(Draft.account_id)
    ).all()
    return dict(rows)


def _out(acc: Account, replies_today: int) -> AccountOut:
    out = AccountOut.model_validate(acc)
    out.replies_today = replies_today
    out.proxy_label = settings.mask_proxy(acc.proxy_url)
    out.session_ready = account_sessions.ready(acc)
    out.selected = settings.load().get("active_account_id") == acc.id
    return out


def _get_or_404(db: Session, account_id: int) -> Account:
    acc = db.get(Account, account_id)
    if not acc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Аккаунт не найден")
    return acc


@router.get("", response_model=list[AccountOut])
def list_accounts(status_filter: str | None = None, db: Session = Depends(get_db)):
    q = select(Account).order_by(Account.username)
    if status_filter:
        q = q.where(Account.status == status_filter)
    accounts = db.scalars(q).all()
    counts = _replies_today(db, [a.id for a in accounts])
    return [_out(a, counts.get(a.id, 0)) for a in accounts]


@router.get("/{account_id}", response_model=AccountOut)
def get_account(account_id: int, db: Session = Depends(get_db)):
    acc = _get_or_404(db, account_id)
    return _out(acc, _replies_today(db, [acc.id]).get(acc.id, 0))


@router.post("", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
def create_account(data: AccountCreate, db: Session = Depends(get_db)):
    raise HTTPException(409, "Аккаунт добавляется только после входа через прокси. Используйте «Добавить аккаунт»")


@router.post("/connect", response_model=TaskOut, status_code=202)
def connect_account(data: AccountConnectIn):
    try:
        return connections.submit(data)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e


@router.get("/connection/latest", response_model=TaskOut | None)
def latest_connection(db: Session = Depends(get_db)):
    from app.models import TaskRun
    return db.scalars(select(TaskRun).where(TaskRun.kind == "connect_account").order_by(TaskRun.id.desc()).limit(1)).first()


@router.post("/{account_id}/select", response_model=AccountOut)
@auto_comments.serialized
def select_account(account_id: int, db: Session = Depends(get_db)):
    acc = _get_or_404(db, account_id)
    if not account_sessions.ready(acc) or acc.status != "active":
        raise HTTPException(409, "Сначала подключите активный аккаунт через прокси")
    account_sessions.select_account(acc)
    return _out(acc, _replies_today(db, [acc.id]).get(acc.id, 0))


@router.post("/{account_id}/check", response_model=TaskOut, status_code=202)
def check_account(account_id: int, db: Session = Depends(get_db)):
    acc = _get_or_404(db, account_id)
    if not account_sessions.ready(acc):
        raise HTTPException(409, "Подключите аккаунт через прокси")
    return tasks.submit("check_session", {"account_id": account_id})


@router.patch("/{account_id}", response_model=AccountOut)
def update_account(account_id: int, data: AccountUpdate, db: Session = Depends(get_db)):
    acc = _get_or_404(db, account_id)
    changes = data.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(acc, field, value)
    db.commit()
    if changes:
        log_event("accounts", f"@{acc.username}: изменены поля {', '.join(changes)}", db=db)
    return _out(acc, _replies_today(db, [acc.id]).get(acc.id, 0))


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
@auto_comments.serialized
def delete_account(account_id: int, db: Session = Depends(get_db)):
    acc = _get_or_404(db, account_id)
    if db.scalar(select(Draft.id).where(
        Draft.account_id == account_id, Draft.status.in_((*auto_comments.BUSY, DraftStatus.REVIEW)),
    ).limit(1)):
        raise HTTPException(409, "У аккаунта есть активная или непроверенная публикация")
    username = acc.username
    db.delete(acc)
    db.commit()
    account_sessions.state_path(account_id).unlink(missing_ok=True)
    saved = settings.load()
    if saved.get("active_account_id") == account_id:
        saved.pop("active_account_id", None)
        saved.pop("connected_username", None)
        settings.save(saved)
    log_event("accounts", f"Удалён аккаунт @{username}", level="WARNING", db=db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
