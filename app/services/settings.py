"""Настройки, которые меняются из интерфейса. Хранятся в data/settings.json.

Переменная окружения TTLH_SEARCH_PROXY используется, только если прокси
в интерфейсе не задан.
"""
import json
import os
from uuid import uuid4
from urllib.parse import quote, urlparse

from app.config import DATA_DIR

SETTINGS_PATH = DATA_DIR / "settings.json"
_SCHEMES = ("http", "https", "socks5")


def load() -> dict:
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def save(data: dict) -> None:
    temporary = SETTINGS_PATH.with_name(f"settings-{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(SETTINGS_PATH)
    finally:
        temporary.unlink(missing_ok=True)


def search_proxy() -> str | None:
    return load().get("search_proxy") or os.getenv("TTLH_SEARCH_PROXY") or None


def proxy_source() -> str | None:
    if load().get("search_proxy"):
        return "ui"
    return "env" if os.getenv("TTLH_SEARCH_PROXY") else None


def normalize_proxy(raw: str, scheme: str = "http") -> str:
    """Приводит прокси к виду scheme://user:pass@host:port.

    Понимает форматы, в которых прокси обычно выдают продавцы:
      host:port
      host:port:login:password
      login:password@host:port
      http://login:password@host:port, socks5://host:port
    """
    raw = raw.strip()
    if scheme not in _SCHEMES:
        raise ValueError(f"Тип прокси: {', '.join(_SCHEMES)}")
    if "://" not in raw:
        parts = raw.split(":")
        # host:port:login:password проверяем первым: в пароле может быть «@».
        if len(parts) == 4 and parts[1].isdigit():
            host, port, user, password = parts
            raw = f"{scheme}://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}"
        elif "@" in raw:
            raw = f"{scheme}://{raw}"
        elif len(parts) == 2:
            raw = f"{scheme}://{parts[0]}:{parts[1]}"
        else:
            raise ValueError("Не понял формат. Примеры: 1.2.3.4:8080 или 1.2.3.4:8080:логин:пароль")

    p = urlparse(raw)
    if p.scheme not in _SCHEMES:
        raise ValueError(f"Тип прокси должен быть одним из: {', '.join(_SCHEMES)}")
    try:
        port = p.port
    except ValueError:
        port = None
    if not p.hostname or not port:
        raise ValueError("Нужны адрес и порт, например 1.2.3.4:8080")
    if p.scheme == "socks5" and p.username:
        raise ValueError(
            "Chromium не поддерживает SOCKS5 с логином и паролем. "
            "Возьмите у провайдера HTTP-вариант этого же прокси или SOCKS5 с доступом по IP"
        )
    return raw


def set_search_proxy(value: str | None) -> None:
    data = load()
    if value:
        data["search_proxy"] = value
    else:
        data.pop("search_proxy", None)
    save(data)


def connected_username() -> str | None:
    return load().get("connected_username")


def set_connected_username(username: str | None) -> None:
    data = load()
    if username:
        data["connected_username"] = username.lower()
    else:
        data.pop("connected_username", None)
    save(data)


def use_system_chrome() -> bool:
    """Запускать установленный Google Chrome вместо встроенного Chromium.
    Помогает, когда антивирус или фаервол блокирует встроенный браузер."""
    return bool(load().get("use_system_chrome"))


def set_use_system_chrome(value: bool) -> None:
    data = load()
    data["use_system_chrome"] = bool(value)
    save(data)


def mask_proxy(url: str | None) -> str | None:
    """Прокси без пароля, для логов."""
    if not url:
        return None
    p = urlparse(url)
    auth = f"{p.username}:***@" if p.username else ""
    return f"{p.scheme}://{auth}{p.hostname}:{p.port}"
