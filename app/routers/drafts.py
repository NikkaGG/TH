"""Очередь черновиков: подготовка текста ответа и учёт отправленного.

Жизненный цикл: draft → sent (ответ опубликован и отмечен) или skipped.
"""
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import Account, AccountStatus, Draft, DraftStatus, ReplyTemplate, Video, VideoStatus
from app.schemas import DraftCreate, DraftGenerate, DraftOut, DraftResolveIn, DraftUpdate
from app.services import auto_comments, spintax
from app.services.logs import log_event

router = APIRouter(prefix="/api/drafts", tags=["drafts"])


def _out(d: Draft) -> DraftOut:
    return DraftOut(
        id=d.id,
        video_id=d.video_id,
        video_url=d.video.url,
        video_author=d.video.author,
        video_description=d.video.description,
        account_id=d.account_id,
        account_username=d.account.username if d.account else None,
        text=d.text,
        status=d.status,
        created_at=d.created_at,
        sent_at=d.sent_at,
    )


def _get_or_404(db: Session, draft_id: int) -> Draft:
    d = db.get(Draft, draft_id, options=[joinedload(Draft.video), joinedload(Draft.account)])
    if not d:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Черновик не найден")
    return d


def _check_account(db: Session, account_id: int | None) -> None:
    if account_id is not None and not db.get(Account, account_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Аккаунт не найден")


@router.get("", response_model=list[DraftOut])
def list_drafts(
    status_filter: Literal["draft", "sent", "skipped", "posting", "pending_post", "sending", "review"] | None = Query("draft", alias="status"),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    q = select(Draft).options(joinedload(Draft.video), joinedload(Draft.account))
    if status_filter == "posting":
        q = q.where(Draft.status.in_(auto_comments.BUSY))
    elif status_filter:
        q = q.where(Draft.status == status_filter)
    order = Draft.sent_at.desc() if status_filter == DraftStatus.SENT else Draft.created_at
    return [_out(d) for d in db.scalars(q.order_by(order).limit(limit)).all()]


@router.post("", response_model=DraftOut, status_code=status.HTTP_201_CREATED)
def create_draft(data: DraftCreate, db: Session = Depends(get_db)):
    video = db.get(Video, data.video_id)
    if not video:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ролик не найден")
    _check_account(db, data.account_id)
    if data.text:
        text = data.text
    elif data.template_id:
        tpl = db.get(ReplyTemplate, data.template_id)
        if not tpl:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Шаблон не найден")
        text = spintax.spin(tpl.body, spintax.video_variables(video))
    else:
        raise HTTPException(422, "Укажите text или template_id")
    d = Draft(video_id=video.id, account_id=data.account_id, text=text)
    db.add(d)
    db.commit()
    return _out(_get_or_404(db, d.id))


@router.post("/generate")
def generate_drafts(data: DraftGenerate, db: Session = Depends(get_db)):
    tpl = db.get(ReplyTemplate, data.template_id)
    if not tpl:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Шаблон не найден")
    _check_account(db, data.account_id)

    has_draft = select(Draft.video_id).where(Draft.status != DraftStatus.SKIPPED)
    q = select(Video).where(Video.id.not_in(has_draft))
    q = q.where(Video.id.in_(data.video_ids)) if data.video_ids else q.where(Video.status == VideoStatus.RELEVANT)
    videos = db.scalars(q).all()

    db.add_all(
        Draft(video_id=v.id, account_id=data.account_id, text=spintax.spin(tpl.body, spintax.video_variables(v)))
        for v in videos
    )
    db.commit()
    log_event("drafts", f"Создано {len(videos)} черновиков по шаблону «{tpl.name}»", db=db)
    return {"created": len(videos)}


@router.patch("/{draft_id}", response_model=DraftOut)
@auto_comments.serialized
def update_draft(data: DraftUpdate, draft_id: int, db: Session = Depends(get_db)):
    d = _get_or_404(db, draft_id)
    if d.status != DraftStatus.DRAFT:
        raise HTTPException(409, "Редактировать можно только свободный черновик")
    changes = data.model_dump(exclude_unset=True)
    if "account_id" in changes:
        _check_account(db, changes["account_id"])
    for field, value in changes.items():
        setattr(d, field, value)
    db.commit()
    return _out(_get_or_404(db, d.id))


@router.post("/{draft_id}/sent", response_model=DraftOut)
@auto_comments.serialized
def mark_sent(draft_id: int, db: Session = Depends(get_db)):
    d = _get_or_404(db, draft_id)
    if d.status != DraftStatus.DRAFT:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Черновик уже в статусе «{d.status}»")
    if not d.account:
        raise HTTPException(status.HTTP_409_CONFLICT, "Выберите аккаунт, с которого отправлен ответ")
    if d.account.status != AccountStatus.ACTIVE:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Аккаунт @{d.account.username} в статусе «{d.account.status}»")

    sent_today = auto_comments.reply_usage(db, d.account_id)
    if sent_today >= d.account.daily_reply_limit:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Дневной лимит @{d.account.username} исчерпан ({sent_today}/{d.account.daily_reply_limit})",
        )

    d.status, d.sent_at = DraftStatus.SENT, datetime.now()
    d.video.status = VideoStatus.REPLIED
    db.commit()
    log_event("drafts", f"@{d.account.username} ответил под {d.video.url}", db=db)
    return _out(d)


@router.post("/{draft_id}/skip", response_model=DraftOut)
@auto_comments.serialized
def mark_skipped(draft_id: int, db: Session = Depends(get_db)):
    d = _get_or_404(db, draft_id)
    if d.status != DraftStatus.DRAFT:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Черновик уже в статусе «{d.status}»")
    d.status = DraftStatus.SKIPPED
    db.commit()
    return _out(d)


@router.post("/{draft_id}/resolve", response_model=DraftOut)
@auto_comments.serialized
def resolve_publication(data: DraftResolveIn, draft_id: int, db: Session = Depends(get_db)):
    d = _get_or_404(db, draft_id)
    if d.status != DraftStatus.REVIEW:
        raise HTTPException(409, "Этот черновик не ожидает проверки публикации")
    d.status = DraftStatus.SENT if data.published else DraftStatus.DRAFT
    d.sent_at = datetime.now() if data.published else None
    if data.published:
        d.video.status = VideoStatus.REPLIED
    db.commit()
    log_event("drafts", f"Ручная проверка черновика #{d.id}: "
              + ("публикация подтверждена" if data.published else "комментария нет, возвращён в очередь"), db=db)
    return _out(d)


@router.delete("/{draft_id}", status_code=status.HTTP_204_NO_CONTENT)
@auto_comments.serialized
def delete_draft(draft_id: int, db: Session = Depends(get_db)):
    d = _get_or_404(db, draft_id)
    if d.status in (*auto_comments.BUSY, DraftStatus.REVIEW):
        raise HTTPException(409, "Сначала остановите автоотправку и проверьте результат публикации")
    db.delete(d)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
