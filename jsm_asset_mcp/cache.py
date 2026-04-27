"""Generic TTL (time-to-live) in-memory cache.

Domain-agnostic — knows nothing about Jira, schemas, or MCP.  Easy to swap
for Redis, disk, or a bounded LRU in the future.
"""

from __future__ import annotations

import time
from typing import Any


class TTLCache:
    """Simple key→value store where entries expire after *ttl* seconds."""

    def __init__(self, ttl: int = 600) -> None:
        self._ttl = ttl
        self._store: dict[str, dict[str, Any]] = {}

    def get(self, key: str) -> Any | None:
        """Return the cached value for *key*, or ``None`` if missing/expired."""
        entry = self._store.get(key)
        if entry is None:
            return None
        if (time.time() - entry["ts"]) >= self._ttl:
            del self._store[key]
            return None
        return entry["data"]

    def set(self, key: str, value: Any) -> None:
        """Store *value* under *key* with the current timestamp."""
        self._store[key] = {"data": value, "ts": time.time()}

    def clear(self) -> None:
        """Drop all cached entries."""
        self._store.clear()
