import threading
import time

import redis

from .config import get_settings

_lock = threading.Lock()
_buckets = {}
_script = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
local ttl = redis.call('TTL', KEYS[1])
return {count, ttl}
"""


def allowance(bucket, limit, seconds=60):
    settings = get_settings()
    if settings.queue_mode == "celery":
        client = redis.Redis.from_url(settings.redis_url, socket_timeout=2, socket_connect_timeout=2)
        count, ttl = client.eval(_script, 1, "lh:limit:" + bucket, seconds)
        return count <= limit, max(1, ttl)
    with _lock:
        now = time.monotonic()
        count, until = _buckets.get(bucket, (0, now + seconds))
        if now >= until:
            count, until = 0, now + seconds
        _buckets[bucket] = count + 1, until
        return count < limit, max(1, int(until - now) + 1)
