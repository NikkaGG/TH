import re
import unicodedata
from datetime import date
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CampaignIn(InputModel):
    name: str = Field(min_length=1, max_length=128)
    account_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    source: Literal["display", "import", "experimental"] = "display"
    keywords: list[str] = Field(min_length=1, max_length=20)
    exclude_keywords: list[str] = Field(default_factory=list, max_length=20)
    comment_text: str = Field(min_length=1, max_length=150)
    min_score: int = Field(1, ge=1, le=100)
    max_results: int = Field(100, ge=1, le=200)
    interval_minutes: int | None = Field(None, ge=15, le=10080)
    enabled: bool = False

    @field_validator("name", "comment_text")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Must not be blank")
        return value.strip()

    @field_validator("keywords", "exclude_keywords")
    @classmethod
    def terms(cls, values):
        normalized = list(dict.fromkeys(unicodedata.normalize("NFKC", s).strip().casefold() for s in values))
        if any(not s or len(s) > 100 or not any(c.isalnum() for c in s) for s in normalized):
            raise ValueError("Keywords must contain 1..100 characters")
        return normalized

    @model_validator(mode="after")
    def schedule(self):
        if self.enabled and self.interval_minutes is None:
            raise ValueError("Enabled schedules need interval_minutes")
        return self


class VideoIn(InputModel):
    url: str = Field(max_length=512)
    description: str = Field(default="", max_length=10000)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value):
        p = urlsplit(value)
        if (
            p.scheme != "https"
            or p.hostname not in ("www.tiktok.com", "tiktok.com")
            or p.netloc not in ("www.tiktok.com", "tiktok.com")
            or not re.fullmatch(r"/@[\w.\-]{1,64}/video/\d{5,32}", p.path, re.ASCII)
        ):
            raise ValueError("Use a full HTTPS TikTok video URL, not a shortened link")
        return "https://www.tiktok.com" + p.path


class VideoBatch(InputModel):
    videos: list[VideoIn] = Field(min_length=1, max_length=200)


class ManualConfirmation(InputModel):
    confirmed: Literal[True]
    comment_id: str | None = Field(None, pattern=r"^\d{5,32}$")


class DraftEdit(InputModel):
    text: str = Field(min_length=1, max_length=150)

    @field_validator("text")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Text must not be blank")
        return value.strip()


class ResearchQuery(InputModel):
    keyword: str = Field(min_length=1, max_length=100)
    start_date: date
    end_date: date
    cursor: int = Field(0, ge=0)
    search_id: str | None = Field(None, max_length=256)
    max_count: int = Field(100, ge=1, le=100)

    @model_validator(mode="after")
    def dates(self):
        if not 0 <= (self.end_date - self.start_date).days <= 29:
            raise ValueError("Research query must cover 1..30 calendar days")
        return self
