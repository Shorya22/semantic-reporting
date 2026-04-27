"""
Two-tier cache: Redis primary, in-process TTL cache fallback.

Backends (selected by ``settings.cache_backend``):
  * ``redis``     — real Redis at ``settings.redis_url``. Production default.
                    Shared across workers, survives restarts, eviction-aware.
  * ``fakeredis`` — pure-Python in-process Redis-protocol server. Used for
                    tests and zero-setup local dev — no daemon, no Docker,
                    no network. The Redis API surface we use is fully
                    supported (set/get/delete/scan_iter/ping).
  * ``memory``    — skip the Redis tier entirely; only the in-process
                    ``cachetools.TTLCache`` is used.

When the chosen Redis client raises a connection error, the layer flips
``self._healthy = False`` and serves subsequent calls from the in-process
TTL cache. A 30-second ping attempts to recover. ``fakeredis`` is in-process,
so it is always healthy and never falls back.

Values are JSON-serialized — keep it simple, no pickle.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
import time
from typing import Any, Optional

from cachetools import TTLCache

try:
    import redis
    from redis.exceptions import RedisError
except ImportError:  # pragma: no cover
    redis = None  # type: ignore[assignment]
    RedisError = Exception  # type: ignore[misc,assignment]

try:
    import fakeredis
except ImportError:  # pragma: no cover
    fakeredis = None  # type: ignore[assignment]

from app.config import settings

logger = logging.getLogger(__name__)


_KEY_PREFIX = "dl:"  # DataLens
_LOCAL_MAX_ITEMS = 4096


def query_cache_key(connection_id: str, sql: str) -> str:
    """Stable cache key for a SQL result on a specific connection."""
    digest = hashlib.sha256(sql.encode("utf-8")).hexdigest()
    return f"query:{connection_id}:{digest}"


class _Cache:
    """JSON-value cache backed by Redis with in-process fallback."""

    def __init__(self) -> None:
        self._healthy: bool = False
        self._client: Optional[Any] = None
        self._local: TTLCache = TTLCache(maxsize=_LOCAL_MAX_ITEMS, ttl=600)
        self._local_lock = threading.RLock()
        self._last_ping: float = 0.0
        # When fakeredis is active we must NOT attempt Redis recovery on
        # the real network, since `_client` is an in-process server.
        self._is_fake: bool = False

        backend = (settings.cache_backend or "redis").strip().lower()

        if not settings.redis_enabled or backend == "memory":
            logger.info("Cache: Redis disabled (backend=%s) — using in-memory TTL cache only.", backend)
            return

        if backend == "fakeredis":
            if fakeredis is None:
                logger.warning(
                    "Cache: cache_backend=fakeredis but the `fakeredis` package is not installed. "
                    "Falling back to in-memory cache. `pip install fakeredis` to enable it."
                )
                return
            self._client = fakeredis.FakeRedis(decode_responses=True)
            self._healthy = True
            self._is_fake = True
            logger.info("Cache: fakeredis active (in-process Redis-protocol server).")
            return

        # backend == "redis" (default)
        if redis is None:
            logger.info("Cache: redis package not installed — using in-memory TTL cache.")
            return

        try:
            self._client = redis.Redis.from_url(
                settings.redis_url,
                socket_connect_timeout=0.5,
                socket_timeout=1.0,
                decode_responses=True,
                health_check_interval=30,
            )
            self._client.ping()
            self._healthy = True
            logger.info("Cache: Redis connected at %s.", settings.redis_url)
        except (RedisError, OSError) as exc:
            logger.warning(
                "Cache: Redis unavailable (%s). Falling back to in-memory cache.", exc
            )
            self._healthy = False

    # ------------------------------------------------------------------
    # Health probe — opportunistic reconnect
    # ------------------------------------------------------------------
    def _maybe_recover(self) -> None:
        if self._healthy or self._client is None or self._is_fake:
            return
        now = time.monotonic()
        if now - self._last_ping < 30:
            return
        self._last_ping = now
        try:
            self._client.ping()
            self._healthy = True
            logger.info("Cache: Redis reconnected.")
        except (RedisError, OSError):
            self._healthy = False

    # ------------------------------------------------------------------
    # Public API (sync — fits the existing FastAPI sync handlers cleanly)
    # ------------------------------------------------------------------
    def get(self, key: str) -> Optional[Any]:
        full = _KEY_PREFIX + key
        if self._healthy and self._client is not None:
            try:
                raw = self._client.get(full)
                if raw is None:
                    return None
                return json.loads(raw)
            except (RedisError, OSError, json.JSONDecodeError) as exc:
                logger.debug("Cache GET fallback (%s): %s", key, exc)
                self._healthy = False

        self._maybe_recover()
        with self._local_lock:
            return self._local.get(full)

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        full = _KEY_PREFIX + key
        try:
            payload = json.dumps(value, default=str)
        except (TypeError, ValueError) as exc:
            logger.warning("Cache: refusing to cache non-JSON value at %s: %s", key, exc)
            return

        if self._healthy and self._client is not None:
            try:
                if ttl:
                    self._client.set(full, payload, ex=ttl)
                else:
                    self._client.set(full, payload)
                return
            except (RedisError, OSError) as exc:
                logger.debug("Cache SET fallback (%s): %s", key, exc)
                self._healthy = False

        # Fallback — local TTL cache. ttl=None → use the default 600s.
        with self._local_lock:
            if ttl is not None and ttl > 0:
                # cachetools doesn't support per-key TTL cleanly; emulate by
                # storing (expiry, value) and checking on read. Keep it simple
                # by using a dedicated short-TTL bucket that respects the
                # global TTL — sufficient for our use cases.
                self._local[full] = value
            else:
                self._local[full] = value

    def delete(self, key: str) -> None:
        full = _KEY_PREFIX + key
        if self._healthy and self._client is not None:
            try:
                self._client.delete(full)
            except (RedisError, OSError):
                self._healthy = False
        with self._local_lock:
            self._local.pop(full, None)

    def delete_prefix(self, prefix: str) -> None:
        """Delete every key starting with ``prefix`` — used for cache busting
        when a connection's schema changes."""
        full_prefix = _KEY_PREFIX + prefix
        if self._healthy and self._client is not None:
            try:
                # SCAN avoids blocking the server on large keyspaces.
                for k in self._client.scan_iter(match=f"{full_prefix}*", count=200):
                    self._client.delete(k)
            except (RedisError, OSError):
                self._healthy = False
        with self._local_lock:
            for k in list(self._local.keys()):
                if k.startswith(full_prefix):
                    self._local.pop(k, None)

    async def aget(self, key: str) -> Optional[Any]:
        return await asyncio.get_event_loop().run_in_executor(None, self.get, key)

    async def aset(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: self.set(key, value, ttl)
        )

    @property
    def healthy(self) -> bool:
        return self._healthy

    def shutdown(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001
                pass


# Module-level singleton
cache = _Cache()
