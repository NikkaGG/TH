from datetime import datetime
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, utcnow


def new_id():
    return uuid.uuid4().hex


class Account(Base):
    __tablename__ = "lh_accounts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    open_id: Mapped[str] = mapped_column(String(256), unique=True)
    display_name: Mapped[str] = mapped_column(String(256), default="")
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    access_token: Mapped[str] = mapped_column(Text)
    refresh_token: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    refresh_expires_at: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(32), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class OAuthState(Base):
    __tablename__ = "lh_oauth_states"
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    binding_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class Mutex(Base):
    __tablename__ = "lh_mutexes"
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=0)


class Campaign(Base):
    __tablename__ = "lh_campaigns"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(128))
    account_id: Mapped[str] = mapped_column(ForeignKey("lh_accounts.id"), index=True)
    source: Mapped[str] = mapped_column(String(32), default="display")
    keywords: Mapped[list] = mapped_column(JSON)
    exclude_keywords: Mapped[list] = mapped_column(JSON, default=list)
    comment_text: Mapped[str] = mapped_column(Text)
    min_score: Mapped[int] = mapped_column(Integer, default=1)
    max_results: Mapped[int] = mapped_column(Integer, default=100)
    interval_minutes: Mapped[int | None] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Video(Base):
    __tablename__ = "lh_videos"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    external_id: Mapped[str] = mapped_column(String(32), unique=True)
    url: Mapped[str] = mapped_column(String(512))
    author: Mapped[str] = mapped_column(String(128), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(32))
    owner_account_id: Mapped[str | None] = mapped_column(ForeignKey("lh_accounts.id"))
    metadata_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Candidate(Base):
    __tablename__ = "lh_candidates"
    __table_args__ = (UniqueConstraint("campaign_id", "video_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("lh_campaigns.id"), index=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("lh_videos.id"))
    score: Mapped[int] = mapped_column(Integer)
    matched_terms: Mapped[list] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text)


class Draft(Base):
    __tablename__ = "lh_drafts"
    __table_args__ = (UniqueConstraint("account_id", "video_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("lh_campaigns.id"), index=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("lh_accounts.id"), index=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("lh_videos.id"))
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    comment_id: Mapped[str | None] = mapped_column(String(32))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Job(Base):
    __tablename__ = "lh_jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("lh_campaigns.id"), index=True)
    idempotency_hash: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    error_code: Mapped[str | None] = mapped_column(String(80))
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class Audit(Base):
    __tablename__ = "lh_audit"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[str] = mapped_column(String(64))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
