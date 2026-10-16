"""A single public, read-only compatibility probe; does not use connected accounts."""

import importlib.metadata
import json
import os
from pathlib import Path
import time

os.environ["LH_EXPERIMENTAL_SEARCH_ENABLED"] = "true"
from tiktok_api.config import get_settings  # noqa: E402
from tiktok_api.db import utcnow  # noqa: E402
from tiktok_api.providers.experimental import search  # noqa: E402
from tiktok_api.providers.official import ProviderError  # noqa: E402

get_settings.cache_clear()
started = time.monotonic()
result = {
    "checked_at_utc": utcnow().isoformat() + "Z",
    "query": "coffee",
    "count_requested": 3,
    "tiktokapi": importlib.metadata.version("TikTokApi"),
    "playwright": importlib.metadata.version("playwright"),
    "account_cookies_used": False,
    "write_actions": 0,
}
try:
    videos = search("coffee", 3, timeout=45)
    result.update(
        status="data_returned" if videos else "empty_result",
        returned=len(videos),
        ids=[video["id"] for video in videos],
    )
except ProviderError as exc:
    result.update(status="failed", error_code=exc.code)
result["elapsed_seconds"] = round(time.monotonic() - started, 2)
target = Path(__file__).resolve().parents[1] / "docs/evidence/live-search.json"
target.write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result))
