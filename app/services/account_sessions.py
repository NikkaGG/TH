"""Прокси и сессия принадлежат аккаунту, глобальный вход больше не используется."""
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from app.config import SESSION_DIR
from app.database import SessionLocal
from app.models import Account, AccountStatus
from app.services import settings


def state_path(account_id: int) -> Path:
    return SESSION_DIR / "accounts" / f"{int(account_id)}.json"


def ready(account: Account) -> bool:
    return bool(account.proxy_url and account.authenticated_at and state_path(account.id).exists())


@dataclass
class BrowserProfile:
    account_id: int
    username: str
    proxy_url: str
    state_path: Path
    use_system_chrome: bool
    browser_dir: Path


def profile(account_id: int | None = None) -> BrowserProfile:
    account_id = account_id or settings.load().get("active_account_id")
    with SessionLocal() as db:
        account = db.get(Account, account_id) if account_id else None
        if not account or account.status != AccountStatus.ACTIVE or not ready(account):
            raise ValueError("Подключите активный аккаунт: «Аккаунты» → «Добавить аккаунт»")
        # Reconnecting or changing the proxy/browser must not resurrect a stale login.
        revision = sha256(f"{account.authenticated_at.isoformat()}|{account.proxy_url}|{account.use_system_chrome}".encode()).hexdigest()[:16]
        browser_dir = SESSION_DIR / "browsers" / str(account.id) / revision
        return BrowserProfile(account.id, account.username, account.proxy_url, state_path(account.id), account.use_system_chrome, browser_dir)


def select_account(account: Account) -> None:
    data = settings.load()
    data["active_account_id"] = account.id
    data["connected_username"] = account.username
    settings.save(data)
