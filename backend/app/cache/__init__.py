"""Cache subsystem — Redis with transparent in-memory fallback."""

from app.cache.cache import cache, query_cache_key

__all__ = ["cache", "query_cache_key"]
