from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import SEARCH_STATE_PATH
from app.database import get_db
from app.models import ReplyTemplate, TaskRun, TaskStatus
from app.schemas import AutoCommentIn, SearchTaskIn, SessionInfo, TaskOut
from app.services import account_sessions, auto_comments, connections, session_import, tasks
from app.services import settings as app_settings
from app.services.logs import log_event

router = APIRouter(prefix="/api", tags=["tasks"])


class SessionImportIn(BaseModel):
    data: str = Field(min_length=1, max_length=200_000)


def _ensure_not_pending(db: Session, kind: str) -> None:
    pending = db.scalar(
        select(TaskRun.id).where(TaskRun.kind == kind, TaskRun.status.in_([TaskStatus.QUEUED, TaskStatus.RUNNING]))
    )
    if pending:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Задача #{pending} ({kind}) уже в работе")


@router.get("/tasks", response_model=list[TaskOut])
def list_tasks(limit: int = Query(30, ge=1, le=200), db: Session = Depends(get_db)):
    return db.scalars(select(TaskRun).order_by(TaskRun.id.desc()).limit(limit)).all()


@router.post("/tasks/search", response_model=TaskOut, status_code=status.HTTP_202_ACCEPTED)
def start_search(data: SearchTaskIn, db: Session = Depends(get_db)):
    if data.template_id and not db.get(ReplyTemplate, data.template_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Шаблон не найден")
    try:
        profile = account_sessions.profile(data.account_id)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    params = data.model_dump()
    params["account_id"] = profile.account_id
    return tasks.submit("search", params)


@router.post("/tasks/login", response_model=TaskOut, status_code=status.HTTP_202_ACCEPTED)
def start_login(db: Session = Depends(get_db)):
    raise HTTPException(409, "Откройте «Аккаунты» → «Добавить аккаунт». Сначала укажите прокси")


@router.post("/tasks/auto-comment", response_model=TaskOut, status_code=status.HTTP_202_ACCEPTED)
def start_auto_comment(data: AutoCommentIn):
    try:
        return auto_comments.submit(data)
    except auto_comments.QueueError as e:
        raise HTTPException(409, str(e)) from e


@router.get("/tasks/auto-comment", response_model=TaskOut | None)
def auto_comment_status(db: Session = Depends(get_db)):
    return db.scalars(select(TaskRun).where(TaskRun.kind == "auto_comment").order_by(TaskRun.id.desc()).limit(1)).first()


@router.post("/tasks/{task_id}/stop", response_model=TaskOut)
def stop_auto_comment(task_id: int, db: Session = Depends(get_db)):
    task = db.get(TaskRun, task_id)
    if task and task.kind == "connect_account":
        return connections.request_stop(task_id)
    try:
        return auto_comments.request_stop(task_id)
    except auto_comments.QueueError as e:
        raise HTTPException(404, str(e)) from e


@router.post("/tasks/check-session", response_model=TaskOut, status_code=status.HTTP_202_ACCEPTED)
def start_check(db: Session = Depends(get_db)):
    _ensure_not_pending(db, "check_session")
    try:
        profile = account_sessions.profile()
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return tasks.submit("check_session", {"account_id": profile.account_id})


@router.get("/session", response_model=SessionInfo)
def session_info(db: Session = Depends(get_db)):
    from app.models import Account
    selected = app_settings.load().get("active_account_id")
    account = db.get(Account, selected) if selected else None
    path = account_sessions.state_path(selected) if selected else SEARCH_STATE_PATH
    exists = bool(account and account_sessions.ready(account))
    last_check = db.scalars(
        select(TaskRun).where(TaskRun.kind == "check_session").order_by(TaskRun.id.desc()).limit(1)
    ).first()
    return SessionInfo(
        state_saved=exists,
        username=account.username if exists else None,
        saved_at=datetime.fromtimestamp(path.stat().st_mtime) if exists else None,
        proxy_configured=bool(account and account.proxy_url),
        last_check=last_check,
    )


@router.post("/session/import", response_model=TaskOut, status_code=status.HTTP_202_ACCEPTED)
def import_session(data: SessionImportIn, db: Session = Depends(get_db)):
    raise HTTPException(409, "Подключение выполняется через прокси во вкладке «Аккаунты». Импорт общей сессии отключён")


@router.delete("/session", status_code=status.HTTP_204_NO_CONTENT)
def clear_session(db: Session = Depends(get_db)):
    raise HTTPException(409, "Управляйте подключениями во вкладке «Аккаунты»")
