import json
import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.models import AccountStatus, DraftStatus, VideoStatus

_USERNAME_RE = re.compile(r"^[a-z0-9._]{2,24}$")


# ---------- Аккаунты ----------

class AccountBase(BaseModel):
    display_name: str | None = Field(default=None, max_length=128)
    status: AccountStatus = AccountStatus.ACTIVE
    daily_reply_limit: int = Field(default=20, ge=0, le=500)
    notes: str | None = None


class AccountCreate(AccountBase):
    username: str

    @field_validator("username")
    @classmethod
    def normalize_username(cls, v: str) -> str:
        v = v.strip().lstrip("@").lower()
        if not _USERNAME_RE.match(v):
            raise ValueError("username: 2–24 символа, латиница, цифры, '.' и '_'")
        return v


class AccountUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=128)
    status: AccountStatus | None = None
    daily_reply_limit: int | None = Field(default=None, ge=0, le=500)
    notes: str | None = None


class AccountOut(AccountBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    created_at: datetime
    updated_at: datetime
    replies_today: int = 0
    proxy_label: str | None = None
    session_ready: bool = False
    authenticated_at: datetime | None = None
    selected: bool = False


class AccountConnectIn(BaseModel):
    proxy: str = Field(min_length=1, max_length=500)
    scheme: Literal["http", "https", "socks5"] = "http"
    use_system_chrome: bool = False
    sessionid: SecretStr | None = None


# ---------- Ролики ----------

class VideoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    video_id: str
    url: str
    author: str | None
    description: str | None
    views: int | None
    query: str
    status: VideoStatus
    found_at: datetime


class VideoPage(BaseModel):
    total: int
    items: list[VideoOut]


class VideoUpdate(BaseModel):
    status: VideoStatus


class VideoBulkUpdate(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=1000)
    status: VideoStatus


# ---------- Шаблоны (spintax) ----------

class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    body: str = Field(min_length=1, max_length=2000)


class TemplateOut(TemplateIn):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime


class SpinPreviewIn(BaseModel):
    body: str = Field(min_length=1, max_length=2000)
    variables: dict[str, str] = Field(default_factory=dict)
    count: int = Field(default=5, ge=1, le=20)


class SpinPreviewOut(BaseModel):
    variants: list[str]
    total_combinations: int


# ---------- Черновики ответов ----------

class DraftCreate(BaseModel):
    video_id: int
    template_id: int | None = None
    text: str | None = Field(default=None, max_length=2000)
    account_id: int | None = None


class DraftGenerate(BaseModel):
    template_id: int
    video_ids: list[int] | None = None  # None: все ролики со статусом «relevant» без черновиков
    account_id: int | None = None


class DraftUpdate(BaseModel):
    text: str | None = Field(default=None, min_length=1, max_length=2000)
    account_id: int | None = None


class DraftOut(BaseModel):
    id: int
    video_id: int
    video_url: str
    video_author: str | None
    video_description: str | None
    account_id: int | None
    account_username: str | None
    text: str
    status: DraftStatus
    created_at: datetime
    sent_at: datetime | None


# ---------- Задачи и сессия парсера ----------

class SearchTaskIn(BaseModel):
    account_id: int | None = Field(default=None, gt=0)
    queries: list[str] = Field(min_length=1, max_length=20)
    limit_per_query: int = Field(default=30, ge=1, le=200)
    visible: bool = False  # открыть окно браузера, чтобы капчу можно было пройти вручную
    template_id: int | None = None  # сразу создать черновики по этому шаблону

    @field_validator("queries")
    @classmethod
    def clean_queries(cls, v: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(q.strip() for q in v if q.strip()))
        if not cleaned:
            raise ValueError("Нужен хотя бы один запрос")
        return cleaned


class AutoCommentIn(BaseModel):
    account_id: int = Field(gt=0)
    draft_ids: list[int] | None = Field(default=None, min_length=1, max_length=200)
    limit: int = Field(default=5, ge=1, le=200)
    interval_seconds: int = Field(default=60, ge=30, le=3600)
    visible: bool = True


class DraftResolveIn(BaseModel):
    published: bool


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    params: dict[str, Any]
    status: str
    found: int
    result: str | None
    error: str | None
    created_at: datetime
    finished_at: datetime | None

    @field_validator("params", mode="before")
    @classmethod
    def parse_params(cls, v: Any) -> Any:
        return json.loads(v or "{}") if isinstance(v, str) else v


class SessionInfo(BaseModel):
    state_saved: bool
    username: str | None = None  # подключённый аккаунт (по последней успешной проверке)
    saved_at: datetime | None
    proxy_configured: bool
    last_check: TaskOut | None


# ---------- Статистика и логи ----------

class StatsOut(BaseModel):
    accounts_total: int
    accounts_by_status: dict[str, int]
    videos_total: int
    videos_by_status: dict[str, int]
    drafts_pending: int
    replies_sent_total: int
    replies_sent_today: int
    tasks_running: int
    last_task: dict | None


class LogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ts: datetime
    level: str
    source: str
    message: str
