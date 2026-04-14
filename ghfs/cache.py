"""
Thread-safe in-memory cache with per-entry TTL and optional LRU eviction.
"""

import time
import threading
import pickle
import os
import hashlib
import logging
from typing import Any, Optional
from collections import OrderedDict

logger = logging.getLogger(__name__)


class _Entry:
    __slots__ = ("value", "expires_at")

    def __init__(self, value: Any, ttl: float):
        self.value = value
        self.expires_at = time.monotonic() + ttl if ttl > 0 else float("inf")

    @property
    def is_alive(self) -> bool:
        return time.monotonic() < self.expires_at


class MemoryCache:
    """
    LRU in-memory cache.

    Args:
        max_size: Maximum number of entries (0 = unlimited).
        default_ttl: Default time-to-live in seconds.
    """

    def __init__(self, max_size: int = 4096, default_ttl: float = 300.0):
        self._max_size = max_size
        self._default_ttl = default_ttl
        self._store: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if not entry.is_alive:
                del self._store[key]
                return None
            # Move to end (most recently used)
            self._store.move_to_end(key)
            return entry.value

    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        ttl = ttl if ttl is not None else self._default_ttl
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
            self._store[key] = _Entry(value, ttl)
            # Evict oldest entry if over capacity
            if self._max_size and len(self._store) > self._max_size:
                self._store.popitem(last=False)

    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def invalidate_prefix(self, prefix: str) -> int:
        """Remove all entries whose key starts with `prefix`. Returns count removed."""
        with self._lock:
            keys = [k for k in self._store if k.startswith(prefix)]
            for k in keys:
                del self._store[k]
            return len(keys)

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)


class DiskCache:
    """
    Simple disk-backed cache using pickle files, with in-memory L1 cache in front.
    Falls back to memory-only mode if the directory is not writable.

    Args:
        cache_dir: Directory to store cache files.
        max_memory_entries: Max entries in the in-memory layer.
        default_ttl: Default TTL in seconds.
    """

    def __init__(
        self,
        cache_dir: str,
        max_memory_entries: int = 1024,
        default_ttl: float = 3600.0,
    ):
        self._cache_dir = cache_dir
        self._default_ttl = default_ttl
        self._memory = MemoryCache(max_size=max_memory_entries, default_ttl=default_ttl)
        self._disk_ok = False

        try:
            os.makedirs(cache_dir, exist_ok=True)
            # Verify writable
            test_path = os.path.join(cache_dir, ".write_test")
            with open(test_path, "w") as f:
                f.write("ok")
            os.remove(test_path)
            self._disk_ok = True
        except OSError as e:
            logger.warning("Disk cache unavailable (%s). Using memory-only cache.", e)

    def _key_to_path(self, key: str) -> str:
        h = hashlib.sha256(key.encode()).hexdigest()
        # Two-level directory structure to avoid too many files in one dir
        return os.path.join(self._cache_dir, h[:2], h[2:] + ".pkl")

    def get(self, key: str) -> Optional[Any]:
        # L1: memory
        value = self._memory.get(key)
        if value is not None:
            return value

        # L2: disk
        if not self._disk_ok:
            return None

        path = self._key_to_path(key)
        try:
            with open(path, "rb") as f:
                entry: _Entry = pickle.load(f)
            if not entry.is_alive:
                os.remove(path)
                return None
            # Promote to L1
            remaining_ttl = entry.expires_at - time.monotonic()
            self._memory.set(key, entry.value, ttl=remaining_ttl)
            return entry.value
        except (OSError, pickle.UnpicklingError, AttributeError):
            return None

    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        ttl = ttl if ttl is not None else self._default_ttl
        self._memory.set(key, value, ttl=ttl)

        if not self._disk_ok:
            return

        entry = _Entry(value, ttl)
        path = self._key_to_path(key)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "wb") as f:
                pickle.dump(entry, f, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(tmp, path)
        except (OSError, pickle.PicklingError) as e:
            logger.debug("Disk cache write failed for key %s: %s", key, e)

    def delete(self, key: str) -> None:
        self._memory.delete(key)
        if self._disk_ok:
            try:
                os.remove(self._key_to_path(key))
            except OSError:
                pass

    def clear(self) -> None:
        self._memory.clear()

    def invalidate_prefix(self, prefix: str) -> int:
        return self._memory.invalidate_prefix(prefix)


# Alias used throughout the codebase
Cache = MemoryCache
