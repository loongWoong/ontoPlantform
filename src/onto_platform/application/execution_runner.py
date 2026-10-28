"""Bounded execution runner for capability invocations (P1-1 / P0-2).

A timeout at the API boundary must not kill or duplicate the execution.  The
portal therefore runs every invocation on a daemon worker and waits for its
completion only up to ``timeout_seconds``.  When the wait expires the caller
gets ``status="unknown"`` while the worker keeps running and finalizes the
execution ledger when it actually finishes.

    request thread ──wait(timeout)──> unknown response
          │
          └── daemon worker ──> lease -> gateway.invoke -> ledger.finalize

Threads are bounded by a semaphore so a runaway workspace cannot exhaust the
process.  Truly killing a runaway capability still needs process isolation;
that limitation is documented in the product docs.
"""

from __future__ import annotations

import threading
from typing import Callable

from ..errors import RateLimitError

DEFAULT_MAX_WORKERS = 8


class ExecutionRunner:
    def __init__(self, max_workers: int = DEFAULT_MAX_WORKERS) -> None:
        self._slots = threading.BoundedSemaphore(max(1, int(max_workers)))
        self._threads: set[threading.Thread] = set()
        self._lock = threading.Lock()

    def run(self, fn: Callable[[], object]) -> threading.Thread:
        """Start ``fn`` on a daemon thread; raises RateLimitError when full."""
        if not self._slots.acquire(blocking=False):
            raise RateLimitError(
                "too many capability invocations in flight; retry later")
        thread = threading.Thread(target=self._guarded, args=(fn,),
                                  name="onto-execution", daemon=True)
        with self._lock:
            self._threads.add(thread)
        thread.start()
        return thread

    def _guarded(self, fn: Callable[[], object]) -> None:
        try:
            fn()
        except BaseException:  # pragma: no cover - defensive
            # the worker itself records failures; nothing may escape a daemon
            # thread and print a traceback after the request already returned
            pass
        finally:
            self._slots.release()
            current = threading.current_thread()
            with self._lock:
                self._threads.discard(current)
