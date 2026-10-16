from collections.abc import Iterator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import DB_PATH

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")  # API и воркер пишут параллельно
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def migrate_accounts() -> None:
    """Аддитивное обновление старой локальной БД, без удаления существующих записей."""
    columns = {c["name"] for c in inspect(engine).get_columns("accounts")}
    changes = {"proxy_url": "TEXT", "use_system_chrome": "BOOLEAN NOT NULL DEFAULT 0", "authenticated_at": "DATETIME"}
    with engine.begin() as conn:
        for name, sql_type in changes.items():
            if name not in columns:
                conn.execute(text(f"ALTER TABLE accounts ADD COLUMN {name} {sql_type}"))


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
