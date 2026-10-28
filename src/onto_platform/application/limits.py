"""Runtime limits for the multi-workspace platform (plan-v2 2.4).

Small, dependency-free building blocks:

* ``RateLimiter`` - per-workspace sliding window (invocations per minute)
* ``WorkspaceQuota`` - workspace count ceiling for one deployment

They live in the application layer because they are policy, not mechanism;
the values come from ``PlatformConfig.extra`` / environment variables.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from contextlib import contextmanager

from ..errors import RateLimitError, ValidationError


def rate_limit_from_env() -> int:
    raw = os.environ.get("ONTO_RATE_LIMIT_PER_MINUTE", "")
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


class RateLimiter:
    """Sliding-window limiter; ``per_minute=0`` disables it."""

    def __init__(self, per_minute: int = 0,
                 clock=time.monotonic, window_seconds: float = 60.0) -> None:
        self.per_minute = max(0, int(per_minute))
        self._clock = clock
        self._window = float(window_seconds)
        self._events: dict[str, deque] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        if not self.per_minute:
            return
        now = self._clock()
        with self._lock:
            bucket = self._events.setdefault(str(key), deque())
            cutoff = now - self._window
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self.per_minute:
                retry_in = max(0.0, self._window - (now - bucket[0]))
                raise RateLimitError(
                    f"rate limit for {key!r} exceeded: {self.per_minute} per "
                    f"{int(self._window)}s; retry in {retry_in:.1f}s")
            bucket.append(now)

    def snapshot(self) -> dict:
        with self._lock:
            now = self._clock()
            return {key: sum(1 for ts in bucket if ts > now - self._window)
                    for key, bucket in self._events.items()}


def check_workspace_quota(limit: int, existing: int) -> None:
    if limit and existing >= limit:
        raise ValidationError(
            f"workspace quota reached ({existing}/{limit}); delete a workspace "
            "or raise the deployment quota before creating another")


class LeaseManager:
    """Per-key execution leases (plan-v2 2.4).

    Capabilities run read-modify-write logic outside the kernel lock, so two
    concurrent invocations on the same target can lose an update.  A lease
    serializes invocations per key (``workspace:target``); it is reentrant so
    nested capability/workflow invocations on the same target do not
    deadlock.  Waiting longer than ``timeout`` (seconds; ``None`` = forever)
    raises a retryable ``ConcurrencyConflictError``.
    """

    def __init__(self, timeout: float | None = 10.0) -> None:
        self.timeout = timeout
        self._locks: dict[str, threading.RLock] = {}
        self._lock = threading.Lock()

    def _lock_for(self, key: str) -> threading.RLock:
        with self._lock:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.RLock()
                self._locks[key] = lock
            return lock

    @contextmanager
    def hold(self, key: str):
        from ..errors import ConcurrencyConflictError

        lock = self._lock_for(key)
        acquired = lock.acquire(timeout=self.timeout) if self.timeout is not None \
            else lock.acquire()
        if not acquired:
            raise ConcurrencyConflictError(
                f"another execution holds the lease for {key!r}; retry later")
        try:
            yield
        finally:
            lock.release()

    def active_keys(self) -> list[str]:
        with self._lock:
            return list(self._locks)
