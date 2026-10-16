"""Прокси → проверка сети → вход → проверка TikTok → регистрация аккаунта."""
import json
import re
from datetime import datetime
from uuid import uuid4

from sqlalchemy import select

from app.config import SESSION_DIR
from app.database import SessionLocal
from app.models import Account, AccountConnection, TaskRun, TaskStatus
from app.services import account_sessions, auto_comments, session_import, settings


def submit(data):
    from app.services import tasks
    proxy = settings.normalize_proxy(data.proxy, data.scheme)
    sid = None
    if data.sessionid is not None:
        sid = data.sessionid.get_secret_value().strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,512}", sid):
            raise ValueError("Вставьте только значение sessionid из колонки Value, без имени куки и кавычек")
    with auto_comments.lock, SessionLocal() as db:
        if db.scalar(select(TaskRun.id).where(TaskRun.kind == "connect_account", TaskRun.status.in_(["queued", "running"]))):
            raise ValueError("Подключение уже идёт. Завершите вход или отмените его")
        connection = AccountConnection(id=uuid4().hex, proxy_url=proxy, use_system_chrome=data.use_system_chrome)
        db.add(connection)
        db.commit()
        pending_state = SESSION_DIR / "pending" / f"{connection.id}.json"
        try:
            if sid is not None:
                pending_state.parent.mkdir(parents=True, exist_ok=True)
                pending_state.write_text(json.dumps({"cookies": session_import.parse(sid), "origins": []}), encoding="utf-8")
            return tasks.submit("connect_account", {"connection_id": connection.id, "method": "sessionid" if sid else "browser", "stop_requested": False})
        except Exception:
            pending_state.unlink(missing_ok=True)
            db.delete(connection)
            db.commit()
            raise ValueError("Не удалось запустить подключение. Перезапустите приложение") from None


def stopped(task_id):
    with SessionLocal() as db:
        task = db.get(TaskRun, task_id)
        return not task or json.loads(task.params).get("stop_requested", False)


def request_stop(task_id):
    with auto_comments.lock, SessionLocal() as db:
        task = db.get(TaskRun, task_id)
        if not task or task.kind != "connect_account":
            raise ValueError("Подключение не найдено")
        if task.status in ("queued", "running"):
            params = json.loads(task.params)
            params["stop_requested"] = True
            task.params = json.dumps(params)
            task.result = "Отменяем подключение…"
            db.commit()
        return task


async def run(task_id, params):
    from app.services import tasks
    from app.workers import tiktok_search as ts
    connection_id = params["connection_id"]
    temporary = SESSION_DIR / "pending" / f"{connection_id}.json"
    temporary.parent.mkdir(parents=True, exist_ok=True)
    try:
        with SessionLocal() as db:
            connection = db.get(AccountConnection, connection_id)
            if not connection:
                raise ValueError("Подключение прервано. Начните заново")
            proxy, chrome = connection.proxy_url, connection.use_system_chrome
        if stopped(task_id):
            return "Подключение отменено", 0, True
        connect = ts.connect_with_session if params.get("method") == "sessionid" else ts.connect_account
        username = await connect(
            proxy, temporary, chrome,
            on_stage=lambda stage: tasks._set(task_id, result=stage),
            should_stop=lambda: stopped(task_id),
        )
        with auto_comments.lock, SessionLocal() as db:
            if stopped(task_id):
                return "Подключение отменено", 0, True
            account = db.scalars(select(Account).where(Account.username == username.lower())).first()
            if account and db.scalar(select(TaskRun.id).where(
                TaskRun.kind == "auto_comment", TaskRun.status.in_(["queued", "running"]),
            )):
                raise ValueError("Дождитесь завершения автоотправки перед переподключением аккаунта")
            if not account:
                account = Account(username=username.lower())
                db.add(account)
                db.flush()
            destination = account_sessions.state_path(account.id)
            destination.parent.mkdir(parents=True, exist_ok=True)
            previous_state = destination.read_bytes() if destination.exists() else None
            try:
                temporary.replace(destination)
                account.proxy_url, account.use_system_chrome = proxy, chrome
                account.authenticated_at = datetime.now()
                db.commit()
            except Exception:
                db.rollback()
                if previous_state is not None:
                    destination.write_bytes(previous_state)
                else:
                    destination.unlink(missing_ok=True)
                raise
            account_sessions.select_account(account)
        return f"Аккаунт @{username} подключён через прокси", 1, False
    except ts.ConnectionCancelled:
        return "Подключение отменено", 0, True
    finally:
        temporary.unlink(missing_ok=True)
        with SessionLocal() as db:
            connection = db.get(AccountConnection, connection_id)
            if connection:
                db.delete(connection)
                db.commit()
