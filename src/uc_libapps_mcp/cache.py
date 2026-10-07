"""Thread-safe in-memory TTL cache with single-flight loading per key."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Hashable
from typing import Any


class TTLCache:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._data: dict[Hashable, tuple[float, Any]] = {}
        self._key_locks: dict[Hashable, threading.Lock] = {}
        self._lock = threading.Lock()

    def _lookup(self, key: Hashable) -> tuple[bool, Any]:
        entry = self._data.get(key)
        if entry is not None and entry[0] > self._clock():
            return True, entry[1]
        return False, None

    def get_or_load(self, key: Hashable, ttl: float, loader: Callable[[], Any]) -> Any:
        with self._lock:
            hit, value = self._lookup(key)
            if hit:
                return value
            key_lock = self._key_locks.setdefault(key, threading.Lock())
        with key_lock:
            with self._lock:
                hit, value = self._lookup(key)
            if hit:
                return value
            value = loader()
            with self._lock:
                now = self._clock()
                for stale in [k for k, (exp, _) in self._data.items() if exp <= now]:
                    del self._data[stale]
                self._data[key] = (now + ttl, value)
            return value

    def invalidate(self, key: Hashable) -> None:
        with self._lock:
            self._data.pop(key, None)
