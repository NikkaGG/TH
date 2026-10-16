"""Последовательная автоотправка из одной сохранённой сессии TikTok.

Блокировка общая с ручными операциями очереди. Резервирование черновиков и
создание задачи происходят в одной транзакции; повторный запуск не дублирует их.
Неопределённый результат никогда не повторяется автоматически.
"""
import asyncio
import json
from datetime import datetime, time
from functools import wraps
from threading import RLock

from sqlalchemy import func, or_, select, update

from app.database import SessionLocal
from app.models import Account, AccountStatus, Draft, DraftStatus, TaskRun, TaskStatus, VideoStatus
from app.services import account_sessions
from app.services.logs import log_event
from app.workers import tiktok_search as ts

lock = RLock()
BUSY = (DraftStatus.PENDING_POST, DraftStatus.SENDING)


def serialized(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with lock:
            return fn(*args, **kwargs)
    return wrapped


class QueueError(ValueError):
    pass


class StopRequested(RuntimeError):
    pass


def reply_usage(db, account_id: int) -> int:
    """Отправленные сегодня + резервы + все ещё не выясненные публикации."""
    start = datetime.combine(datetime.now().date(), time.min)
    return db.scalar(select(func.count()).select_from(Draft).where(
        Draft.account_id == account_id,
        or_(Draft.status.in_((*BUSY, DraftStatus.REVIEW)),
            (Draft.status == DraftStatus.SENT) & (Draft.sent_at >= start)),
    )) or 0


def _account(db, account_id):
    account = db.get(Account, account_id)
    if not account or account.status != AccountStatus.ACTIVE:
        raise QueueError("Выбранный аккаунт отсутствует или неактивен")
    if not account_sessions.ready(account):
        raise QueueError("Подключите этот аккаунт через прокси во вкладке «Аккаунты»")
    return account


def submit(data):
    from app.services import tasks

    with lock, SessionLocal() as db:
        if db.scalar(select(TaskRun.id).where(
            TaskRun.kind == "auto_comment",
            TaskRun.status.in_([TaskStatus.QUEUED, TaskStatus.RUNNING]),
        )):
            raise QueueError("Автоотправка уже запущена. Дождитесь завершения или остановите её")
        account = _account(db, data.account_id)
        query = select(Draft).where(Draft.status == DraftStatus.DRAFT)
        if data.draft_ids:
            ids = list(dict.fromkeys(data.draft_ids))
            drafts = list(db.scalars(query.where(Draft.id.in_(ids)).order_by(Draft.id)))
            if len(drafts) != len(ids):
                raise QueueError("Часть черновиков уже отправлена, занята или удалена. Обновите очередь")
        else:
            drafts = list(db.scalars(query.where(
                or_(Draft.account_id.is_(None), Draft.account_id == account.id)
            ).order_by(Draft.created_at, Draft.id).limit(data.limit)))
        if not drafts:
            raise QueueError("Нет черновиков для выбранного аккаунта")
        if any(d.account_id not in (None, account.id) for d in drafts):
            raise QueueError("Черновик назначен другому аккаунту")
        if any(not d.text.strip() for d in drafts):
            raise QueueError("У одного из черновиков пустой текст")
        available = max(0, account.daily_reply_limit - reply_usage(db, account.id))
        if len(drafts) > available:
            raise QueueError(f"Недостаточно дневного лимита: осталось {available}, выбрано {len(drafts)}. "
                             "Уменьшите количество или проверьте неопределённые публикации")
        params = data.model_dump()
        params["draft_ids"] = [d.id for d in drafts]
        params["username"] = account.username
        params["stop_requested"] = False
        task = TaskRun(kind="auto_comment", params=json.dumps(params, ensure_ascii=False))
        db.add(task)
        for draft in drafts:
            draft.status, draft.account_id = DraftStatus.PENDING_POST, account.id
        db.commit()
        db.refresh(task)
        try:
            tasks.enqueue(task.id)
        except Exception:
            for draft in drafts:
                draft.status = DraftStatus.DRAFT
            task.status, task.error, task.finished_at = TaskStatus.FAILED, "Не удалось запустить очередь", datetime.now()
            db.commit()
            raise QueueError("Очередь не запущена. Перезапустите приложение") from None
        log_event("drafts", f"Автоотправка #{task.id}: @{account.username}, {len(drafts)} комментариев")
        return task


def request_stop(task_id):
    with lock, SessionLocal() as db:
        task = db.get(TaskRun, task_id)
        if not task or task.kind != "auto_comment":
            raise QueueError("Задача автоотправки не найдена")
        if task.status in (TaskStatus.QUEUED, TaskStatus.RUNNING):
            params = json.loads(task.params)
            params["stop_requested"] = True
            task.params = json.dumps(params, ensure_ascii=False)
            in_flight = db.scalar(select(Draft.id).where(
                Draft.id.in_(params["draft_ids"]), Draft.status == DraftStatus.SENDING,
            ))
            task.result = ("Остановка запрошена. Ожидаем подтверждение уже начатой публикации" if in_flight
                           else "Останавливаем подготовку. Публикация ещё не начата")
            db.commit()
        db.refresh(task)
        return task


def _stopped(task_id):
    with SessionLocal() as db:
        task = db.get(TaskRun, task_id)
        return not task or json.loads(task.params).get("stop_requested", False)


def _before_post(task_id, draft_id, account_id):
    """Последняя проверка и запись состояния до клика, в том числе при перезапуске."""
    with lock, SessionLocal() as db:
        if _stopped(task_id):
            raise StopRequested("Автоотправка остановлена")
        account = _account(db, account_id)
        draft = db.get(Draft, draft_id)
        if not draft or draft.status != DraftStatus.PENDING_POST or draft.account_id != account_id:
            raise QueueError("Черновик изменён или уже обработан")
        if reply_usage(db, account_id) > account.daily_reply_limit:
            raise QueueError("Дневной лимит уменьшен или исчерпан; автоотправка остановлена")
        draft.status = DraftStatus.SENDING
        db.commit()


def _progress(task_id, message):
    with lock, SessionLocal() as db:
        task = db.get(TaskRun, task_id)
        if task and not json.loads(task.params).get("stop_requested"):
            task.result = message
            db.commit()


def _in_flight(task_id):
    with SessionLocal() as db:
        task = db.get(TaskRun, task_id)
        if not task:
            return False
        return bool(db.scalar(select(Draft.id).where(
            Draft.id.in_(json.loads(task.params)["draft_ids"]), Draft.status == DraftStatus.SENDING,
        )))


async def run(task_id, params):
    job = asyncio.create_task(_run_queue(task_id, params))
    cancel_requested = False
    try:
        while not job.done():
            await asyncio.wait([job], timeout=0.25)
            # Only preparation can be interrupted. Once before_post marks a
            # draft as sending, keep waiting for the single publication result.
            if not job.done() and not cancel_requested and _stopped(task_id) and not _in_flight(task_id):
                job.cancel()
                cancel_requested = True
        return await job
    finally:
        if not job.done():
            job.cancel()
            await asyncio.gather(job, return_exceptions=True)


async def _run_queue(task_id, params):
    sent = 0
    cancelled = False
    draft_ids = params["draft_ids"]
    try:
        if _stopped(task_id):
            raise StopRequested("Автоотправка остановлена")
        _progress(task_id, "Открываем браузер через прокси аккаунта")
        async with ts.open_context(headless=not params["visible"], account_id=params["account_id"]) as ctx:
            for index, draft_id in enumerate(draft_ids):
                if _stopped(task_id):
                    raise StopRequested("Автоотправка остановлена")
                with SessionLocal() as db:
                    draft = db.get(Draft, draft_id)
                    if not draft or draft.status != DraftStatus.PENDING_POST:
                        raise QueueError("Зарезервированный черновик изменён или удалён")
                    url, text = draft.video.url, draft.text
                try:
                    comment_id = await ts.post_comment_on_video(
                        ctx, url, text, visible=params["visible"], expected_username=params["username"],
                        before_post=lambda: _before_post(task_id, draft_id, params["account_id"]),
                        on_stage=lambda message: _progress(task_id, message),
                    )
                except ts.PostUncertainError:
                    with lock, SessionLocal() as db:
                        db.execute(update(Draft).where(Draft.id == draft_id).values(status=DraftStatus.REVIEW))
                        db.commit()
                    raise
                except Exception:
                    # Ошибка до клика или явный отказ TikTok: можно снова подготовить черновик.
                    with lock, SessionLocal() as db:
                        db.execute(update(Draft).where(Draft.id == draft_id).values(status=DraftStatus.DRAFT))
                        db.commit()
                    raise
                # Если сохранение результата упадёт, sending восстановится как review при запуске.
                with lock, SessionLocal() as db:
                    draft = db.get(Draft, draft_id)
                    draft.status, draft.sent_at = DraftStatus.SENT, datetime.now()
                    draft.video.status = VideoStatus.REPLIED
                    task = db.get(TaskRun, task_id)
                    sent += 1
                    task.found = sent
                    task.result = f"Опубликовано {sent} из {len(draft_ids)}"
                    db.commit()
                log_event("drafts", f"Автоотправка #{task_id}: черновик #{draft_id}, комментарий {comment_id}")
                if index + 1 < len(draft_ids):
                    # Остановка прерывает паузу, но не обрывает уже сделанный запрос публикации.
                    for _ in range(params["interval_seconds"]):
                        if _stopped(task_id):
                            raise StopRequested("Автоотправка остановлена")
                        await asyncio.sleep(1)
    except StopRequested:
        cancelled = True
    except asyncio.CancelledError:
        if not _stopped(task_id):
            raise
        cancelled = True
    finally:
        with lock, SessionLocal() as db:
            db.execute(update(Draft).where(
                Draft.id.in_(draft_ids), Draft.status == DraftStatus.PENDING_POST,
            ).values(status=DraftStatus.DRAFT))
            db.execute(update(Draft).where(
                Draft.id.in_(draft_ids), Draft.status == DraftStatus.SENDING,
            ).values(status=DraftStatus.REVIEW))
            db.commit()
    result = f"{'Остановлено' if cancelled else 'Завершено'}: опубликовано {sent} из {len(draft_ids)}"
    return result, sent, cancelled
