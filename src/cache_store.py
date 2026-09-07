"""Short-lived JSON cache. Uses Redis when REDIS_URL is set, otherwise memory."""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Optional

from src import config

log = logging.getLogger(__name__)

_MEMORY_LOCK = threading.Lock()
_MEMORY: dict[str, tuple[float, str]] = {}


def _prefixed(key: str) -> str:
    return f"telegroup:{key}"


class CacheStore:
    """Process-local cache with an optional Redis backend for multi-process bots."""

    def __init__(self) -> None:
        self._redis = None
        url = getattr(config, "REDIS_URL", None)
        if not url:
            return
        try:
            import redis

            client = redis.Redis.from_url(url, decode_responses=True)
            client.ping()
            self._redis = client
            log.info("Account explorer cache is using Redis.")
        except Exception as e:
            log.warning(f"Redis is configured but unavailable; using in-memory cache: {e}")
            self._redis = None

    @property
    def backend(self) -> str:
        return "redis" if self._redis is not None else "memory"

    def get_json(self, key: str) -> Optional[Any]:
        full_key = _prefixed(key)
        raw = None
        if self._redis is not None:
            try:
                raw = self._redis.get(full_key)
            except Exception as e:
                log.warning(f"Redis get failed for {key}: {e}")
        if raw is None:
            raw = self._memory_get(full_key)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return None

    def set_json(self, key: str, value: Any, ttl_seconds: int) -> None:
        full_key = _prefixed(key)
        raw = json.dumps(value, ensure_ascii=False, default=str)
        if self._redis is not None:
            try:
                self._redis.setex(full_key, int(ttl_seconds), raw)
            except Exception as e:
                log.warning(f"Redis set failed for {key}: {e}")
        self._memory_set(full_key, raw, ttl_seconds)

    def delete(self, key: str) -> None:
        full_key = _prefixed(key)
        if self._redis is not None:
            try:
                self._redis.delete(full_key)
            except Exception as e:
                log.warning(f"Redis delete failed for {key}: {e}")
        with _MEMORY_LOCK:
            _MEMORY.pop(full_key, None)

    @staticmethod
    def _memory_get(full_key: str) -> Optional[str]:
        with _MEMORY_LOCK:
            item = _MEMORY.get(full_key)
            if not item:
                return None
            expires_at, raw = item
            if expires_at < time.time():
                _MEMORY.pop(full_key, None)
                return None
            return raw

    @staticmethod
    def _memory_set(full_key: str, raw: str, ttl_seconds: int) -> None:
        with _MEMORY_LOCK:
            _MEMORY[full_key] = (time.time() + max(1, int(ttl_seconds)), raw)


cache_store = CacheStore()
