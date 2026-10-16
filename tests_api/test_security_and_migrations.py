import os
from pathlib import Path
import subprocess
import sys

from cryptography.fernet import Fernet
from sqlalchemy import create_engine, create_mock_engine, inspect, select

from tiktok_api.config import Settings, get_settings
from tiktok_api.db import Base
from tiktok_api.models import Account, Campaign, Mutex
from tiktok_api.providers.official import ProviderError
from tiktok_api.security import Vault


def test_key_rotation_and_fail_closed_configuration():
    import pytest

    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    encrypted = Vault(old).encrypt("token")
    combined = Vault(new + "," + old)
    assert combined.decrypt(encrypted) == "token"
    rotated = combined.encrypt(combined.decrypt(encrypted))
    assert Vault(new).decrypt(rotated) == "token"
    with pytest.raises(ValueError):
        Settings(admin_key="short")
    with pytest.raises(ValueError):
        Settings(redirect_uri="http://localhost/callback")


def test_revocation_failure_locally_blocks_account(
    client, headers, account, campaign_payload, isolated_db, monkeypatch
):
    from tiktok_api import main

    campaign_id = client.post(
        "/api/v1/campaigns", headers=headers, json={**campaign_payload, "interval_minutes": 15, "enabled": True}
    ).json()["id"]

    class Failing:
        def revoke(self, token):
            raise ProviderError("network_error", requires_action=True)

        def close(self):
            pass

    monkeypatch.setattr(main, "OfficialClient", Failing)
    assert client.delete("/api/v1/accounts/" + account.id, headers=headers).status_code == 409
    with isolated_db() as db:
        assert db.get(Account, account.id).status == "revocation_pending"
        assert db.get(Campaign, campaign_id).enabled is False
        assert db.get(Account, account.id).refresh_token

    class Success(Failing):
        def revoke(self, token):
            pass

    monkeypatch.setattr(main, "OfficialClient", Success)
    assert client.delete("/api/v1/accounts/" + account.id, headers=headers).json()["status"] == "disconnected"
    with isolated_db() as db:
        assert db.get(Account, account.id).access_token == ""
        assert db.get(Account, account.id).refresh_token == ""


def test_api_limit_and_security_headers(client, headers, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "api_requests_per_minute", 10)
    for _ in range(10):
        assert client.get("/api/v1/capabilities", headers=headers).status_code == 200
    limited = client.get("/api/v1/capabilities", headers=headers)
    assert limited.status_code == 429 and int(limited.headers["Retry-After"]) > 0
    public = client.get("/")
    assert "script-src 'self'" in public.headers["Content-Security-Policy"]
    assert public.headers["Referrer-Policy"] == "no-referrer"
    assert public.headers["X-Content-Type-Options"] == "nosniff"


def test_frozen_migration_upgrade_downgrade_upgrade(tmp_path):
    root = Path(__file__).resolve().parents[1]
    url = "sqlite:///" + str(tmp_path / "migration.db")
    environment = {**os.environ, "LH_DATABASE_URL": url}
    for command in [("upgrade", "head"), ("upgrade", "head"), ("downgrade", "base"), ("upgrade", "head")]:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *command],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
    engine = create_engine(url)
    assert "lh_accounts" in inspect(engine).get_table_names()
    with engine.connect() as connection:
        assert connection.scalar(select(Mutex.name)) == "accounts"
    engine.dispose()


def test_postgresql_schema_compiles():
    statements = []
    engine = create_mock_engine(
        "postgresql+psycopg://",
        lambda sql, *args, **kwargs: statements.append(str(sql.compile(dialect=engine.dialect))),
    )
    Base.metadata.create_all(engine)
    assert any("CREATE TABLE lh_accounts" in statement for statement in statements)
    assert any("UNIQUE (account_id, video_id)" in statement for statement in statements)
