from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import TaskRun
from app.schemas import TaskOut
from app.services import settings as app_settings
from app.services import tasks
from app.services.logs import log_event

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsOut(BaseModel):
    search_proxy: str | None
    proxy_source: Literal["ui", "env"] | None
    use_system_chrome: bool
    last_proxy_test: TaskOut | None


class BrowserIn(BaseModel):
    use_system_chrome: bool


class ProxyIn(BaseModel):
    proxy: str = Field(default="", max_length=500)  # пусто = без прокси
    scheme: Literal["http", "https", "socks5"] = "http"


@router.get("", response_model=SettingsOut)
def get_settings(db: Session = Depends(get_db)):
    last = db.scalars(select(TaskRun).where(TaskRun.kind == "test_proxy").order_by(TaskRun.id.desc()).limit(1)).first()
    return SettingsOut(
        search_proxy=app_settings.mask_proxy(app_settings.search_proxy()),
        proxy_source=app_settings.proxy_source(),
        use_system_chrome=app_settings.use_system_chrome(),
        last_proxy_test=last,
    )


@router.put("/proxy", response_model=SettingsOut)
def set_proxy(data: ProxyIn, db: Session = Depends(get_db)):
    value = None
    if data.proxy.strip():
        try:
            value = app_settings.normalize_proxy(data.proxy, data.scheme)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
    app_settings.set_search_proxy(value)
    log_event("settings", f"Прокси: {app_settings.mask_proxy(value) or 'отключён'}", db=db)
    return get_settings(db)


@router.put("/browser", response_model=SettingsOut)
def set_browser(data: BrowserIn, db: Session = Depends(get_db)):
    app_settings.set_use_system_chrome(data.use_system_chrome)
    log_event("settings", "Браузер: " + ("Google Chrome" if data.use_system_chrome else "встроенный Chromium"), db=db)
    return get_settings(db)


@router.post("/proxy/test", response_model=TaskOut, status_code=status.HTTP_202_ACCEPTED)
def test_proxy():
    return tasks.submit("test_proxy")
