import asyncio
import contextlib
import io
import logging

from ..config import get_settings
from .official import ProviderError


async def _search(term, limit):
    # No browser-cookie imports, CAPTCHA solvers, account rotation or write requests.
    try:
        from TikTokApi import TikTokApi
    except ImportError:
        raise ProviderError("experimental_dependency_missing", requires_action=True) from None
    token = get_settings().experimental_ms_token.get_secret_value()
    async with TikTokApi(logging_level=logging.CRITICAL) as api:
        await api.create_sessions(
            ms_tokens=[token] if token else None, num_sessions=1, sleep_after=3, browser="chromium", headless=True
        )
        videos, cursor, seen = [], 0, set()
        while len(videos) < limit:
            response = await api.make_request(
                url="https://www.tiktok.com/api/search/item/full/",
                params={
                    "keyword": term,
                    "count": min(10, limit - len(videos)),
                    "cursor": cursor,
                    "source": "search_video",
                },
            )
            if not isinstance(response, dict) or response.get("status_code", 0) != 0:
                raise ProviderError("experimental_challenge_or_rejection", requires_action=True)
            if not isinstance(response.get("item_list"), list):
                raise ProviderError("experimental_schema_changed", requires_action=True)
            for video in response["item_list"]:
                vid = str(video.get("id", ""))
                if vid and vid not in seen:
                    seen.add(vid)
                    videos.append(
                        {
                            "id": vid,
                            "video_description": video.get("desc", ""),
                            "author": video.get("author", {}).get("uniqueId", ""),
                        }
                    )
                    if len(videos) >= limit:
                        break
            if not response.get("has_more"):
                return videos
            next_cursor = response.get("cursor")
            if next_cursor == cursor or next_cursor is None or not response["item_list"]:
                raise ProviderError("experimental_pagination_stalled", requires_action=True)
            cursor = next_cursor
        return videos


def search(term, limit, timeout=120):
    if not get_settings().experimental_search_enabled:
        raise ProviderError("experimental_search_disabled", requires_action=True)
    try:
        # Upstream sometimes prints raw responses containing session material.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return asyncio.run(asyncio.wait_for(_search(term, limit), timeout=timeout))
    except ProviderError:
        raise
    except Exception as exc:
        codes = {
            "EmptyResponseException": "experimental_empty_response",
            "CaptchaException": "experimental_captcha",
            "InvalidJSONException": "experimental_invalid_json",
            "InvalidResponseException": "experimental_invalid_response",
            "TimeoutError": "experimental_timeout",
            "TargetClosedError": "experimental_browser_closed",
        }
        # Class names are classified locally; upstream messages/body/headers never escape.
        raise ProviderError(codes.get(type(exc).__name__, "experimental_search_failed"), requires_action=True) from None
