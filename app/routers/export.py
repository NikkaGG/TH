"""Выгрузка в CSV для Excel и Google Sheets.

Формат: UTF-8 с BOM и разделитель «;». Так файл корректно открывается
двойным кликом в русском Excel, а Google Sheets определяет разделитель сам
(Файл → Импорт → Загрузить).
"""
import csv
import io
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import Draft, DraftStatus, Video, VideoStatus

router = APIRouter(prefix="/api/export", tags=["export"])

VIDEO_LABEL = {"new": "новый", "relevant": "подходит", "irrelevant": "не подходит", "replied": "отвечено"}
DRAFT_LABEL = {"draft": "в очереди", "sent": "отправлен", "skipped": "пропущен",
               "pending_post": "ждёт автоотправки", "sending": "публикуется", "review": "проверить публикацию"}


def _cell(v) -> str:
    """Текст из TikTok не должен выполниться в Excel как формула (CSV injection)."""
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M")
    s = str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def _csv(name: str, header: list[str], rows: list[list]) -> Response:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(header)
    w.writerows([_cell(v) for v in row] for row in rows)
    filename = f"{name}_{datetime.now():%Y-%m-%d_%H-%M}.csv"
    return Response(
        ("﻿" + buf.getvalue()).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/leads.csv")
def export_leads(
    status_filter: VideoStatus | None = Query(None, alias="status"),
    query: str | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
):
    """Ролики с теми же фильтрами, что на вкладке «Ролики», и с последним ответом по каждому."""
    stmt = select(Video).order_by(Video.found_at.desc(), Video.id.desc())
    if status_filter:
        stmt = stmt.where(Video.status == status_filter)
    if query:
        stmt = stmt.where(Video.query == query)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Video.author.ilike(like), Video.description.ilike(like)))
    videos = db.scalars(stmt).all()

    # Для каждого ролика берём последний черновик, причём непропущенный важнее пропущенного.
    best: dict[int, Draft] = {}
    if videos:
        drafts = db.scalars(
            select(Draft).options(joinedload(Draft.account))
            .where(Draft.video_id.in_([v.id for v in videos]))
            .order_by(Draft.created_at)
        ).all()
        for d in drafts:
            cur = best.get(d.video_id)
            if cur is None or d.status != DraftStatus.SKIPPED or cur.status == DraftStatus.SKIPPED:
                best[d.video_id] = d

    rows = []
    for v in videos:
        d = best.get(v.id)
        rows.append([
            v.author, v.url, v.description, v.views, v.query, VIDEO_LABEL.get(v.status, v.status), v.found_at,
            d.text if d else None,
            DRAFT_LABEL.get(d.status, d.status) if d else None,
            d.account.username if d and d.account else None,
            d.sent_at if d else None,
        ])
    return _csv(
        "leads",
        ["Автор", "Ссылка", "Описание", "Просмотры", "Запрос", "Статус ролика", "Найден",
         "Ответ", "Статус ответа", "Аккаунт", "Отправлен"],
        rows,
    )


@router.get("/replies.csv")
def export_replies(
    date_from: date | None = Query(None, description="YYYY-MM-DD, включительно"),
    date_to: date | None = Query(None, description="YYYY-MM-DD, включительно"),
    db: Session = Depends(get_db),
):
    """Отправленные ответы за период: для отчёта о проделанной работе."""
    stmt = (
        select(Draft).options(joinedload(Draft.video), joinedload(Draft.account))
        .where(Draft.status == DraftStatus.SENT)
        .order_by(Draft.sent_at.desc())
    )
    if date_from:
        stmt = stmt.where(Draft.sent_at >= datetime.combine(date_from, time.min))
    if date_to:
        stmt = stmt.where(Draft.sent_at < datetime.combine(date_to + timedelta(days=1), time.min))
    drafts = db.scalars(stmt).all()
    rows = [
        [d.sent_at, d.account.username if d.account else None, d.video.author, d.video.url, d.text, d.video.query]
        for d in drafts
    ]
    return _csv("replies", ["Отправлен", "Аккаунт", "Автор ролика", "Ссылка", "Текст ответа", "Запрос"], rows)
