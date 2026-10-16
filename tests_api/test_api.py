from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import func, select

from tiktok_api import accounts, jobs
from tiktok_api.db import utcnow
from tiktok_api.models import Account, Audit, Draft, Job, OAuthState, Video
from tiktok_api.security import digest, get_vault


def test_auth_roles_and_secret_redaction(client, headers, account):
    assert client.get("/health/live").status_code == 200
    assert client.get("/api/v1/accounts").status_code == 401
    response = client.get("/api/v1/accounts", headers=headers)
    assert response.status_code == 200
    assert "test-access-token" not in response.text
    assert "refresh_token" not in response.text
    viewer = {"Authorization": "Bearer " + "v" * 40}
    assert client.post("/api/v1/oauth/start", headers=viewer).status_code == 403
    assert client.get("/api/v1/audit", headers=viewer).status_code == 403
    assert client.get("/metrics", headers=viewer).status_code == 200
    assert client.get("/metrics").status_code == 401


def test_complete_import_filter_draft_workflow(client, headers, campaign_payload, isolated_db):
    created = client.post("/api/v1/campaigns", json=campaign_payload, headers=headers)
    assert created.status_code == 201
    campaign = created.json()
    data = {
        "videos": [
            {"url": "https://www.tiktok.com/@owner/video/111111111", "description": "Свежий #кофе на завтрак"},
            {"url": "https://www.tiktok.com/@owner/video/222222222", "description": "кофе реклама"},
            {
                "url": "https://www.tiktok.com/@owner/video/333333333",
                "description": "кофейня: слово кофе не добавлено?",
            },
        ]
    }
    assert client.post("/api/v1/videos/import", json=data, headers=headers).status_code == 201
    run_headers = {**headers, "Idempotency-Key": "a-stable-request-key"}
    run = client.post(f"/api/v1/campaigns/{campaign['id']}/run", headers=run_headers)
    assert run.status_code == 202
    job_id = run.json()["id"]
    assert client.post(f"/api/v1/campaigns/{campaign['id']}/run", headers=run_headers).json()["id"] == job_id
    jobs.dispatch(jobs.process)
    status = client.get(f"/api/v1/jobs/{job_id}", headers=headers).json()
    assert status["status"] == "succeeded"
    assert status["result"] == {"found": 3, "matched": 2, "drafts_created": 2, "comments_published": 0}
    drafts = client.get("/api/v1/drafts", headers=headers).json()
    draft_id = drafts[0]["id"]
    assert client.post(f"/api/v1/drafts/{draft_id}/publish", headers=headers).status_code == 409
    confirmation = client.post(
        f"/api/v1/drafts/{draft_id}/manual-confirm",
        headers=headers,
        json={"confirmed": True, "comment_id": "123456789"},
    )
    assert confirmation.json()["verified_by_tiktok"] is False
    assert confirmation.json()["status"] == "manual_confirmed"
    assert client.patch(f"/api/v1/drafts/{draft_id}", headers=headers, json={"text": "edited"}).status_code == 409
    assert client.get(f"/api/v1/campaigns/{campaign['id']}/candidates", headers=headers).status_code == 200
    # A later job also cannot create a second draft for this account/video.
    client.post(
        f"/api/v1/campaigns/{campaign['id']}/run", headers={**headers, "Idempotency-Key": "another-request-key"}
    )
    jobs.dispatch(jobs.process)
    with isolated_db() as db:
        assert db.scalar(select(func.count(Draft.id))) == 2
        assert db.scalar(select(func.count(Job.id))) == 2


def test_validation_and_ssrf_urls(client, headers, campaign_payload):
    for url in [
        "http://127.0.0.1/video/123456",
        "https://www.tiktok.com.evil.test/@x/video/123456",
        "https://user:pass@www.tiktok.com/@x/video/123456",
        "https://www.tiktok.com:8443/@x/video/123456",
        "https://vm.tiktok.com/test",
        "https://www.tiktok.com/@x/video/123456/extra",
    ]:
        assert client.post("/api/v1/videos/import", headers=headers, json={"videos": [{"url": url}]}).status_code == 422
    assert (
        client.post("/api/v1/campaigns", headers=headers, json={**campaign_payload, "keywords": [" "]}).status_code
        == 422
    )
    assert (
        client.post("/api/v1/campaigns", headers=headers, json={**campaign_payload, "enabled": True}).status_code == 422
    )
    assert (
        client.post(
            "/api/v1/campaigns", headers=headers, json={**campaign_payload, "source": "experimental"}
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/v1/research/videos/query",
            headers=headers,
            json={"keyword": "coffee", "start_date": "2026-01-01", "end_date": "2026-01-02"},
        ).status_code
        == 409
    )
    assert client.post("/api/v1/videos/import", headers=headers, content=b"x" * (1024 * 1024 + 1)).status_code == 413


def test_import_cannot_forge_authoritative_metadata(client, headers, isolated_db, account):
    with isolated_db() as db:
        db.add(
            Video(
                external_id="111111111",
                url="https://www.tiktok.com/@owner/video/111111111",
                description="official",
                source="display",
                metadata_verified=True,
                owner_account_id=account.id,
            )
        )
        db.commit()
    client.post(
        "/api/v1/videos/import",
        headers=headers,
        json={"videos": [{"url": "https://www.tiktok.com/@attacker/video/111111111", "description": "forged"}]},
    )
    result = client.get("/api/v1/videos", headers=headers).json()[0]
    assert result["description"] == "official"
    assert result["owner_account_id"] == account.id


def test_oauth_cookie_binding_single_use_and_identity(client, headers, isolated_db, monkeypatch):
    from tiktok_api import main

    start = client.post("/api/v1/oauth/start", headers=headers)
    assert start.status_code == 200
    assert "HttpOnly" in start.headers["set-cookie"] and "Secure" in start.headers["set-cookie"]
    state = parse_qs(urlsplit(start.json()["authorization_url"]).query)["state"][0]
    with isolated_db() as db:
        record = db.get(OAuthState, digest(state))
        assert record and record.used is False
        assert state not in record.state_hash

    class Client:
        def token(self, **kwargs):
            return {
                "open_id": "new-open-id",
                "access_token": "secret-access",
                "refresh_token": "secret-refresh",
                "scope": "user.info.basic,video.list",
                "expires_in": 86400,
                "refresh_expires_in": 31536000,
            }

        def profile(self, token):
            return {"open_id": "new-open-id", "display_name": "Owner"}

        def close(self):
            pass

    monkeypatch.setattr(main, "OfficialClient", Client)
    callback = client.get("/api/v1/oauth/callback", params={"state": state, "code": "code"}, follow_redirects=False)
    assert callback.status_code == 303
    assert client.get("/api/v1/oauth/callback", params={"state": state, "code": "code"}).status_code == 400
    with isolated_db() as db:
        account = db.scalar(select(Account).where(Account.open_id == "new-open-id"))
        assert account.access_token != "secret-access"
        assert get_vault().decrypt(account.access_token) == "secret-access"
        assert "secret-access" not in str(list(db.scalars(select(Audit.details))))


def test_oauth_denial_wrong_cookie_and_expiration(client, headers, isolated_db):
    with isolated_db() as db:
        db.add(
            OAuthState(
                state_hash=digest("expired"), binding_hash=digest("cookie"), expires_at=utcnow() - timedelta(seconds=1)
            )
        )
        db.commit()
    client.cookies.set("lh_oauth_binding", "cookie", path="/api/v1/oauth/callback")
    assert client.get("/api/v1/oauth/callback", params={"state": "expired", "code": "x"}).status_code == 400
    start = client.post("/api/v1/oauth/start", headers=headers)
    state = parse_qs(urlsplit(start.json()["authorization_url"]).query)["state"][0]
    client.cookies.clear()
    assert client.get("/api/v1/oauth/callback", params={"state": state, "code": "x"}).status_code == 400


def test_cancel_queued_job_and_broker_failure(client, headers, campaign_payload, isolated_db):
    campaign_id = client.post("/api/v1/campaigns", json=campaign_payload, headers=headers).json()["id"]
    job = client.post(
        f"/api/v1/campaigns/{campaign_id}/run", headers={**headers, "Idempotency-Key": "cancel-request"}
    ).json()

    def fail(_id):
        raise ConnectionError("broker secret must not escape")

    jobs.dispatch(fail)
    with isolated_db() as db:
        stored = db.get(Job, job["id"])
        assert stored.status == "queued" and stored.error_code == "broker_unavailable"
    assert client.post(f"/api/v1/jobs/{job['id']}/cancel", headers=headers).json()["status"] == "cancelled"
    called = []
    jobs.dispatch(called.append)
    assert called == []


def test_expired_state_atomic_consumption(isolated_db):
    with isolated_db() as db:
        db.add(
            OAuthState(
                state_hash=digest("state"), binding_hash=digest("binding"), expires_at=utcnow() + timedelta(minutes=5)
            )
        )
        db.commit()
        assert accounts.consume_state(db, "state", "wrong") is False
        assert accounts.consume_state(db, "state", "binding") is True
        assert accounts.consume_state(db, "state", "binding") is False


def test_blank_hashtag_and_unknown_automation_fields_rejected(client, headers, campaign_payload):
    assert (
        client.post("/api/v1/campaigns", headers=headers, json={**campaign_payload, "keywords": ["###"]}).status_code
        == 422
    )
    assert (
        client.post("/api/v1/campaigns", headers=headers, json={**campaign_payload, "auto_publish": True}).status_code
        == 422
    )
