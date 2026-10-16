"""Пути и настройки приложения. Всё переопределяется переменными окружения TTLH_*."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("TTLH_DATA_DIR", BASE_DIR / "data"))
DB_PATH = DATA_DIR / "app.db"
LOG_DIR = DATA_DIR / "logs"
SESSION_DIR = DATA_DIR / "session"

# Одна сессия браузера для парсера поиска (вход выполняется вручную, см. шаг 3).
SEARCH_STATE_PATH = SESSION_DIR / "search_state.json"

STATIC_DIR = Path(__file__).resolve().parent / "static"

for _d in (DATA_DIR, LOG_DIR, SESSION_DIR):
    _d.mkdir(parents=True, exist_ok=True)
