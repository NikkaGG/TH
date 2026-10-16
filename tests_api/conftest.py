import os
from datetime import timedelta

from cryptography.fernet import Fernet
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

# No user secrets, production data or real TikTok requests are used in tests.
os.environ.update(
    LH_ADMIN_KEY="a" * 40,
    LH_OPERATOR_KEY="o" * 40,
    LH_VIEWER_KEY="v" * 40,
    LH_ENCRYPTION_KEYS=Fernet.generate_key().decode(),
    LH_QUEUE_MODE="local",
    LH_DATABASE_URL="sqlite://",
    LH_CLIENT_KEY="test-client",
    LH_CLIENT_SECRET="test-client-secret",
    LH_REDIRECT_URI="https://testserver/api/v1/oauth/callback",
    LH_EXPERIMENTAL_SEARCH_ENABLED="false",
    LH_RESEARCH_ENABLED="false",
)

from tiktok_api import db, jobs, limits  # noqa: E402
from tiktok_api.config import get_settings  # noqa: E402
from tiktok_api.main import app  # noqa: E402
from tiktok_api.models import Account, Mutex  # noqa: E402
from tiktok_api.security import get_vault  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    get_settings.cache_clear()
    engine = db.make_engine("sqlite:///" + str(tmp_path / "test.db"))
    db.Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db, "SessionLocal", factory)
    monkeypatch.setattr(jobs, "SessionLocal", factory)
    limits._buckets.clear()
    with factory() as session:
        session.add(Mutex(name="accounts"))
        session.commit()
    # Network is forbidden unless a test explicitly injects a MockTransport.
    import httpx

    def no_network(*args, **kwargs):
        raise AssertionError("Real network call attempted in isolated tests")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", no_network)
    yield factory
    engine.dispose()
    get_settings.cache_clear()


@pytest.fixture
def client():
    with TestClient(app, base_url="https://testserver") as client:
        yield client


@pytest.fixture
def headers():
    return {"Authorization": "Bearer " + "a" * 40}


@pytest.fixture
def account(isolated_db):
    vault = get_vault()
    with isolated_db() as session:
        account = Account(
            open_id="test-open-id",
            display_name="Test owner",
            scopes=["user.info.basic", "video.list"],
            access_token=vault.encrypt("test-access-token"),
            refresh_token=vault.encrypt("test-refresh-token"),
            expires_at=db.utcnow() + timedelta(hours=24),
            refresh_expires_at=db.utcnow() + timedelta(days=365),
        )
        session.add(account)
        session.commit()
        return account


@pytest.fixture
def campaign_payload(account):
    return {
        "name": "Coffee",
        "account_id": account.id,
        "source": "import",
        "keywords": ["кофе", "coffee"],
        "exclude_keywords": ["реклама"],
        "comment_text": "Спасибо за обзор!",
    }
