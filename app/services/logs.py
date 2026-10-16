"""Единая точка логирования: пишет в файл (data/logs/app.log) и в таблицу logs для дашборда."""
import logging
from logging.handlers import RotatingFileHandler

from sqlalchemy.orm import Session

from app.config import LOG_DIR
from app.database import SessionLocal
from app.models import LogEntry

logger = logging.getLogger("ttlh")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    _fh = RotatingFileHandler(LOG_DIR / "app.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(_fh)
    logger.addHandler(logging.StreamHandler())


def log_event(source: str, message: str, level: str = "INFO", db: Session | None = None) -> None:
    logger.log(getattr(logging, level, logging.INFO), "[%s] %s", source, message)
    own = db is None
    db = db or SessionLocal()
    try:
        db.add(LogEntry(level=level, source=source, message=message))
        db.commit()
    except Exception:  # лог не должен ронять основную операцию
        db.rollback()
        logger.exception("failed to write log entry")
    finally:
        if own:
            db.close()
