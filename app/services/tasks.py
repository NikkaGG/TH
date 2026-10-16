"""Фоновые задачи браузера.

Playwright работает в отдельном потоке со своим event loop. На Windows uvicorn
(особенно с --reload) может включить SelectorEventLoop, а он не умеет запускать
subprocess, который нужен Playwright, поэтому loop создаём явно (Proactor).
Потоков один: задачи выполняются по очереди и не открывают несколько браузеров.
"""
import asyncio
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from sqlalchemy import select, update

from app.database import SessionLocal
from app.models import Account, AccountConnection, Draft, DraftStatus, ReplyTemplate, TaskRun, TaskStatus, Video, VideoStatus
from app.services import spintax
from app.services.logs import log_event
from app.workers import tiktok_search as ts

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="browser")


def _run_coro(coro):
    loop = asyncio.ProactorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def submit(kind: str, params: dict | None = None) -> TaskRun:
    with SessionLocal() as db:
        task = TaskRun(kind=kind, params=json.dumps(params or {}, ensure_ascii=False))
        db.add(task)
        db.commit()
        db.refresh(task)
    try:
        enqueue(task.id)
    except Exception:
        _set(task.id, status=TaskStatus.FAILED, error="Очередь задач недоступна", finished_at=datetime.now())
        raise RuntimeError("Очередь задач недоступна. Перезапустите приложение") from None
    log_event("tasks", f"Задача #{task.id} ({kind}) поставлена в очередь")
    return task


def enqueue(task_id: int) -> None:
    _executor.submit(_execute, task_id)


def recover_interrupted() -> None:
    with SessionLocal() as db:
        res = db.execute(
            update(TaskRun)
            .where(TaskRun.status.in_([TaskStatus.QUEUED, TaskStatus.RUNNING]))
            .values(status=TaskStatus.FAILED, error="Прервано перезапуском приложения", finished_at=datetime.now())
        )
        db.commit()
        # Незавершённые подключения не считаются добавленными аккаунтами.
        for connection in db.scalars(select(AccountConnection)).all():
            from app.config import SESSION_DIR
            (SESSION_DIR / "pending" / f"{connection.id}.json").unlink(missing_ok=True)
            db.delete(connection)
        db.commit()
        # До клика можно вернуть в очередь. Начатую попытку после сбоя не повторяем.
        db.execute(update(Draft).where(Draft.status == DraftStatus.PENDING_POST).values(status=DraftStatus.DRAFT))
        db.execute(update(Draft).where(Draft.status == DraftStatus.SENDING).values(status=DraftStatus.REVIEW))
        db.commit()
    if res.rowcount:
        log_event("tasks", f"Помечено прерванными: {res.rowcount}", level="WARNING")


def shutdown() -> None:
    from app.services import auto_comments, connections
    with SessionLocal() as db:
        ids = list(db.scalars(select(TaskRun.id).where(
            TaskRun.kind.in_(["auto_comment", "connect_account"]), TaskRun.status.in_([TaskStatus.QUEUED, TaskStatus.RUNNING]),
        )))
    for task_id in ids:
        with SessionLocal() as db:
            kind = db.get(TaskRun, task_id).kind
        (auto_comments.request_stop if kind == "auto_comment" else connections.request_stop)(task_id)
    _executor.shutdown(wait=False, cancel_futures=True)


def _set(task_id: int, **fields) -> None:
    with SessionLocal() as db:
        db.execute(update(TaskRun).where(TaskRun.id == task_id).values(**fields))
        db.commit()


def _save_videos(query: str, videos: list[ts.FoundVideo]) -> list[int]:
    """Сохраняет новые ролики, возвращает их id в базе."""
    if not videos:
        return []
    with SessionLocal() as db:
        existing = set(db.scalars(select(Video.video_id).where(Video.video_id.in_([v.video_id for v in videos]))))
        new = [
            Video(video_id=v.video_id, url=v.url, author=v.author, description=v.description,
                  views=v.views, query=query)
            for v in videos if v.video_id not in existing
        ]
        db.add_all(new)
        db.commit()
        return [v.id for v in new]


def _prepare_drafts(video_ids: list[int], template_id: int) -> int:
    """Запуск «найти и подготовить»: новые ролики сразу помечаются подходящими и получают черновик."""
    if not video_ids:
        return 0
    with SessionLocal() as db:
        tpl = db.get(ReplyTemplate, template_id)
        if not tpl:
            raise RuntimeError("Шаблон для черновиков удалён, выберите другой")
        videos = db.scalars(select(Video).where(Video.id.in_(video_ids))).all()
        for v in videos:
            v.status = VideoStatus.RELEVANT
            db.add(Draft(video_id=v.id, text=spintax.spin(tpl.body, spintax.video_variables(v))))
        db.commit()
        return len(videos)


def _execute(task_id: int) -> None:
    with SessionLocal() as db:
        task = db.get(TaskRun, task_id)
        kind, params = task.kind, json.loads(task.params)
    _set(task_id, status=TaskStatus.RUNNING)
    log_event("tasks", f"Задача #{task_id} ({kind}) запущена")

    try:
        cancelled = False
        if kind == "search":
            result, found = _run_search(task_id, params)
        elif kind == "login":
            raise ValueError("Подключайте аккаунт через прокси во вкладке «Аккаунты»")
        elif kind == "check_session":
            ok, result, username = _run_coro(ts.check_session(params.get("account_id")))
            if not ok and params.get("account_id"):
                with SessionLocal() as db:
                    account = db.get(Account, params["account_id"])
                    if account:
                        account.authenticated_at = None
                        db.commit()
            found = int(ok)
        elif kind == "connect_account":
            from app.services import connections
            result, found, cancelled = _run_coro(connections.run(task_id, params))
        elif kind == "test_proxy":
            result, found = _run_coro(ts.test_proxy()), 0
        elif kind == "auto_comment":
            from app.services import auto_comments
            result, found, cancelled = _run_coro(auto_comments.run(task_id, params))
        else:
            raise ValueError(f"Неизвестный тип задачи: {kind}")
    except Exception as e:
        msg = str(e) or type(e).__name__
        _set(task_id, status=TaskStatus.FAILED, error=msg, finished_at=datetime.now())
        log_event("tasks", f"Задача #{task_id} ({kind}) завершилась ошибкой: {msg}", level="ERROR")
        return

    _set(task_id, status=TaskStatus.CANCELLED if cancelled else TaskStatus.DONE,
         result=result, found=found, finished_at=datetime.now())
    log_event("tasks", f"Задача #{task_id} ({kind}) выполнена: {result}")


def _run_search(task_id: int, params: dict) -> tuple[str, int]:
    queries, limit = params["queries"], params.get("limit_per_query", 30)
    visible = params.get("visible", False)
    template_id = params.get("template_id")
    stats = {"seen": 0, "new": 0, "drafts": 0, "errors": 0, "last_error": ""}

    def on_results(query: str, videos: list[ts.FoundVideo]) -> None:
        new_ids = _save_videos(query, videos)
        stats["seen"] += len(videos)
        stats["new"] += len(new_ids)
        if template_id:
            stats["drafts"] += _prepare_drafts(new_ids, template_id)
        _set(task_id, found=stats["new"])
        log_event("search", f"«{query}»: найдено {len(videos)}, новых {len(new_ids)}")

    def on_error(query: str, e: Exception) -> None:
        stats["errors"] += 1
        stats["last_error"] = str(e)
        level = "WARNING" if isinstance(e, ts.EmptyResultError) else "ERROR"
        log_event("search", f"«{query}»: {e}", level=level)

    _run_coro(ts.search_many(queries, limit, on_results, on_error, visible=visible, account_id=params.get("account_id")))
    if stats["errors"] == len(queries):
        raise RuntimeError(stats["last_error"] if len(queries) == 1 else
                           f"Ни один запрос не дал результатов. Последняя ошибка: {stats['last_error']}")
    result = f"Запросов: {len(queries)}, найдено: {stats['seen']}, новых: {stats['new']}"
    if template_id:
        result += f", черновиков: {stats['drafts']}"
    if stats["errors"]:
        result += f", ошибок: {stats['errors']}"
    return result, stats["new"]
