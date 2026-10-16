from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx
import redis

from ..config import get_settings
from ..limits import allowance

BASE = "https://open.tiktokapis.com"


class ProviderError(Exception):
    def __init__(self, code, *, retryable=False, retry_after=60, requires_action=False):
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.retry_after = retry_after
        self.requires_action = requires_action


def retry_delay(value):
    try:
        return max(1, int(value))
    except (ValueError, TypeError):
        try:
            parsed = parsedate_to_datetime(value)
            return max(1, int((parsed - datetime.now(timezone.utc)).total_seconds()) + 1)
        except (ValueError, TypeError, OverflowError):
            return 60


class OfficialClient:
    def __init__(self, client=None):
        self.settings = get_settings()
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(25, connect=10), follow_redirects=False, trust_env=False
        )

    def close(self):
        self.client.close()

    def request(self, method, path, *, access_token=None, oauth=False, empty_ok=False, **kwargs):
        try:
            allowed, wait = allowance(
                "provider:" + self.settings.client_key + ":" + path, self.settings.provider_requests_per_minute
            )
        except redis.RedisError:
            raise ProviderError("limiter_unavailable", retryable=True) from None
        if not allowed:
            raise ProviderError("local_rate_limit", retryable=True, retry_after=wait)
        headers = {"Authorization": "Bearer " + access_token} if access_token else {}
        try:
            response = self.client.request(method, BASE + path, headers=headers, **kwargs)
        except httpx.HTTPError:
            raise ProviderError("network_error", retryable=not oauth, requires_action=oauth) from None
        if response.status_code == 429:
            raise ProviderError(
                "rate_limit_exceeded", retryable=True, retry_after=retry_delay(response.headers.get("Retry-After"))
            )
        if response.status_code >= 500:
            raise ProviderError("provider_unavailable", retryable=not oauth, requires_action=oauth)
        if response.status_code in (401, 403):
            raise ProviderError("authorization_required", requires_action=True)
        if response.status_code != 200:
            raise ProviderError("provider_http_error")
        if empty_ok and not response.content:
            return {}
        try:
            body = response.json()
        except ValueError:
            raise ProviderError("invalid_response", retryable=not oauth, requires_action=oauth) from None
        if not isinstance(body, dict):
            raise ProviderError("invalid_response")
        error = body.get("error")
        if oauth:
            if error is not None and not isinstance(error, (str, dict)):
                raise ProviderError("invalid_response", requires_action=True)
            code = error if isinstance(error, str) else (error or {}).get("code")
        else:
            if not isinstance(error, dict) or "code" not in error:
                raise ProviderError("missing_success_evidence", retryable=True)
            code = error["code"]
        if code and code != "ok":
            if code == "rate_limit_exceeded":
                raise ProviderError(
                    "rate_limit_exceeded", retryable=True, retry_after=retry_delay(response.headers.get("Retry-After"))
                )
            if code in ("access_token_invalid", "scope_not_authorized", "invalid_grant", "invalid_token"):
                raise ProviderError("authorization_required", requires_action=True)
            raise ProviderError("provider_rejected")
        if not oauth and not isinstance(body.get("data"), dict):
            raise ProviderError("invalid_response", retryable=True)
        return body if oauth else body["data"]

    def token(self, **data):
        return self.request(
            "POST",
            "/v2/oauth/token/",
            oauth=True,
            data={
                "client_key": self.settings.client_key,
                "client_secret": self.settings.client_secret.get_secret_value(),
                **data,
            },
        )

    def profile(self, token):
        data = self.request("GET", "/v2/user/info/", access_token=token, params={"fields": "open_id,display_name"})
        if not isinstance(data.get("user"), dict) or not data["user"].get("open_id"):
            raise ProviderError("invalid_profile")
        return data["user"]

    def videos(self, token, limit=100):
        videos, cursor = [], 0
        while len(videos) < limit:
            data = self.request(
                "POST",
                "/v2/video/list/",
                access_token=token,
                params={"fields": "id,title,video_description,share_url,create_time"},
                json={"max_count": min(20, limit - len(videos)), "cursor": cursor},
            )
            if not isinstance(data, dict) or not isinstance(data.get("videos"), list):
                raise ProviderError("invalid_video_page", retryable=True)
            videos.extend(data["videos"][: limit - len(videos)])
            if not data.get("has_more"):
                break
            next_cursor = data.get("cursor")
            if not isinstance(next_cursor, int) or next_cursor == cursor:
                raise ProviderError("pagination_stalled")
            cursor = next_cursor
        return videos

    def revoke(self, token):
        self.request(
            "POST",
            "/v2/oauth/revoke/",
            oauth=True,
            empty_ok=True,
            data={
                "client_key": self.settings.client_key,
                "client_secret": self.settings.client_secret.get_secret_value(),
                "token": token,
            },
        )

    def research(self, query):
        settings = self.settings
        if not settings.research_enabled:
            raise ProviderError("research_not_enabled")
        token = self.request(
            "POST",
            "/v2/oauth/token/",
            oauth=True,
            data={
                "client_key": settings.research_client_key,
                "client_secret": settings.research_client_secret.get_secret_value(),
                "grant_type": "client_credentials",
            },
        )
        if not token.get("access_token"):
            raise ProviderError("invalid_token_response")
        payload = {
            "query": {"and": [{"operation": "EQ", "field_name": "keyword", "field_values": [query.keyword]}]},
            "start_date": query.start_date.strftime("%Y%m%d"),
            "end_date": query.end_date.strftime("%Y%m%d"),
            "cursor": query.cursor,
            "max_count": query.max_count,
        }
        if query.search_id:
            payload["search_id"] = query.search_id
        return self.request(
            "POST",
            "/v2/research/video/query/",
            access_token=token["access_token"],
            params={"fields": "id,username,video_description,create_time,hashtag_names"},
            json=payload,
        )
