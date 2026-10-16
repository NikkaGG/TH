from datetime import timedelta
import json

import httpx
import pytest
from sqlalchemy import select

from tiktok_api import accounts, jobs
from tiktok_api.db import utcnow
from tiktok_api.matching import match
from tiktok_api.models import Account, Campaign, Draft, Job
from tiktok_api.providers.official import OfficialClient, ProviderError, retry_delay
from tiktok_api.security import get_vault


@pytest.fixture
def mock_http():
    def factory(handler):
        return OfficialClient(httpx.Client(transport=httpx.MockTransport(handler)))

    return factory


def test_official_display_pagination_and_contract(mock_http):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.host == "open.tiktokapis.com"
        assert request.headers["Authorization"] == "Bearer access"
        body = json.loads(request.content)
        page = len(calls)
        assert body["max_count"] <= 20
        return httpx.Response(
            200,
            json={
                "data": {"videos": [{"id": str(page)}], "cursor": 1000, "has_more": page == 1},
                "error": {"code": "ok"},
            },
        )

    client = mock_http(handler)
    assert len(client.videos("access", 2)) == 2
    assert json.loads(calls[1].content)["cursor"] == 1000


@pytest.mark.parametrize(
    "response,code,retry",
    [
        (httpx.Response(200, content=b""), "invalid_response", True),
        (httpx.Response(200, json={"data": {"videos": []}}), "missing_success_evidence", True),
        (httpx.Response(429, headers={"Retry-After": "123"}), "rate_limit_exceeded", True),
        (httpx.Response(503), "provider_unavailable", True),
        (httpx.Response(401), "authorization_required", False),
        (
            httpx.Response(200, json={"error": {"code": "scope_not_authorized", "message": "SECRET"}}),
            "authorization_required",
            False,
        ),
    ],
)
def test_provider_errors_are_explicit_and_sanitized(mock_http, response, code, retry):
    client = mock_http(lambda request: response)
    with pytest.raises(ProviderError) as caught:
        client.videos("token", 1)
    assert caught.value.code == code and caught.value.retryable is retry
    assert "SECRET" not in str(caught.value)
    if code == "rate_limit_exceeded":
        assert caught.value.retry_after == 123


def test_oauth_form_encoding_and_no_unsafe_retry(mock_http):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["content-type"].startswith("application/x-www-form-urlencoded")
        assert b"grant_type=authorization_code" in request.content
        return httpx.Response(503)

    with pytest.raises(ProviderError) as caught:
        mock_http(handler).token(grant_type="authorization_code", code="a&b")
    assert len(calls) == 1
    assert not caught.value.retryable and caught.value.requires_action


def test_refresh_rotation_persisted_and_refresh_failure_blocks_account(isolated_db, account):
    class RefreshClient:
        def token(self, **kwargs):
            assert kwargs["refresh_token"] == "test-refresh-token"
            return {
                "open_id": account.open_id,
                "access_token": "new-access",
                "refresh_token": "rotated-refresh",
                "scope": "user.info.basic,video.list",
                "expires_in": 86400,
                "refresh_expires_in": 31536000,
            }

    with isolated_db() as db:
        stored = db.get(Account, account.id)
        stored.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
        assert accounts.access_token(db, account.id, RefreshClient()) == "new-access"
        db.commit()
    with isolated_db() as db:
        stored = db.get(Account, account.id)
        assert get_vault().decrypt(stored.refresh_token) == "rotated-refresh"
        stored.expires_at = utcnow() - timedelta(seconds=1)
        stored.refresh_expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
        with pytest.raises(ProviderError):
            accounts.access_token(db, account.id, RefreshClient())
        assert db.get(Account, account.id).status == "reauthorization_required"


def test_job_retry_budget_and_schedule(isolated_db, account):
    with isolated_db() as db:
        campaign = Campaign(
            name="scheduled",
            account_id=account.id,
            source="display",
            keywords=["coffee"],
            comment_text="Thanks",
            enabled=True,
            interval_minutes=15,
            next_run_at=utcnow(),
        )
        db.add(campaign)
        db.commit()
    jobs.schedule_due()
    jobs.schedule_due()
    with isolated_db() as db:
        pending = list(db.scalars(select(Job)))
        assert len(pending) == 1
        job_id = pending[0].id

    class Client:
        def videos(self, *args):
            raise ProviderError("rate_limit_exceeded", retryable=True, retry_after=900)

        def close(self):
            pass

    for attempt in range(1, 4):
        jobs.dispatch(lambda identity: jobs.process(identity, Client))
        with isolated_db() as db:
            job = db.get(Job, job_id)
            assert job.attempts == attempt
            if attempt < 3:
                assert job.status == "retry_wait"
                assert job.available_at >= utcnow() + timedelta(seconds=890)
                job.available_at = utcnow() - timedelta(seconds=1)
                db.commit()
            else:
                assert job.status == "failed"
    with isolated_db() as db:
        assert list(db.scalars(select(Draft))) == []


def test_worker_recovery_and_duplicate_delivery(isolated_db, account):
    with isolated_db() as db:
        campaign = Campaign(
            name="recover", account_id=account.id, source="import", keywords=["coffee"], comment_text="Thanks"
        )
        db.add(campaign)
        db.flush()
        job = jobs.enqueue(db, campaign.id, "recovery-key")
        job.status, job.lease_until, job.attempts = "running", utcnow() - timedelta(seconds=1), 1
        db.commit()
        job_id = job.id
    jobs.recover_leases()

    class Client:
        def close(self):
            pass

    jobs.dispatch(lambda identity: jobs.process(identity, Client))
    jobs.process(job_id, Client)
    with isolated_db() as db:
        assert db.get(Job, job_id).status == "succeeded"
        assert db.get(Job, job_id).attempts == 2


def test_word_matching_exclusions_unicode_and_phrases():
    assert match("#КОФЕ и ＣＯＦＦＥＥ", ["кофе", "coffee"], [])[0] == 100
    assert match("кофейня", ["кофе"], [])[0] == 0
    assert match("кофе реклама", ["кофе"], ["реклама"])[0] == 0
    assert match("Cold   brew", ["cold brew"], [])[0] == 100
    assert retry_delay("900") == 900
    assert retry_delay("invalid") == 60
    assert match("anything", ["#"], [])[0] == 0


def test_experimental_empty_response_is_sanitized(monkeypatch):
    from tiktok_api.config import get_settings
    from tiktok_api.providers import experimental

    monkeypatch.setattr(get_settings(), "experimental_search_enabled", True)
    EmptyResponseException = type("EmptyResponseException", (Exception,), {})

    async def fail(term, limit):
        raise EmptyResponseException("SECRET COOKIE BODY")

    monkeypatch.setattr(experimental, "_search", fail)
    with pytest.raises(ProviderError) as caught:
        experimental.search("coffee", 3)
    assert caught.value.code == "experimental_empty_response"
    assert "SECRET" not in str(caught.value)


def test_success_envelope_with_invalid_data_is_rejected(mock_http):
    client = mock_http(lambda request: httpx.Response(200, json={"error": {"code": "ok"}, "data": []}))
    with pytest.raises(ProviderError, match="invalid_response"):
        client.videos("token", 1)


def test_account_capacity_and_identity_mismatch(isolated_db, account):
    from tiktok_api.config import get_settings

    settings = get_settings().model_copy(update={"max_accounts": 1})

    class Client:
        def token(self, **kwargs):
            return {
                "open_id": "other-id",
                "access_token": "a",
                "refresh_token": "r",
                "scope": "user.info.basic",
                "expires_in": 86400,
                "refresh_expires_in": 31536000,
            }

        def profile(self, token):
            return {"open_id": "other-id"}

    with isolated_db() as db:
        with pytest.raises(ProviderError, match="account_capacity_reached"):
            accounts.connect(db, "code", Client(), settings)
        db.rollback()

    class WrongClient(Client):
        def profile(self, token):
            return {"open_id": "wrong-id"}

    with isolated_db() as db:
        with pytest.raises(ProviderError, match="identity_mismatch"):
            accounts.connect(db, "code", WrongClient(), settings)
