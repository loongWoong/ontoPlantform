"""Short-lived in-process decision cache.

The PDP is fast but list-heavy pages call it for every row.  This cache stores
only complete ``Decision`` objects (including scope and obligations), keyed by
the full request fingerprint: principal, tenant/workspace, action, resource,
policy version, role set and principal attributes.  Any security mutation
clears it, and entries have a hard TTL so a missed invalidation is bounded.

This is a single-process optimisation, not a distributed cache: a shared
Redis/decision service would need an explicit invalidation channel and is out
of scope for the embedded kernel.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DecisionCache:
    maxsize: int = 2048
    ttl_seconds: float = 15.0
    clock: Any = field(default_factory=lambda: time.monotonic)
    _items: OrderedDict = field(default_factory=OrderedDict)
    hits: int = 0
    misses: int = 0
    evictions: int = 0

    def get(self, key: tuple) -> Any | None:
        entry = self._items.get(key)
        if entry is None:
            self.misses += 1
            return None
        expires_at, decision = entry
        if float(self.clock()) >= expires_at:
            self._items.pop(key, None)
            self.misses += 1
            return None
        self._items.move_to_end(key)
        self.hits += 1
        return deepcopy(decision)

    def put(self, key: tuple, decision: Any) -> None:
        if self.maxsize <= 0:
            return
        self._items[key] = (float(self.clock()) + self.ttl_seconds,
                            deepcopy(decision))
        self._items.move_to_end(key)
        while len(self._items) > self.maxsize:
            self._items.popitem(last=False)
            self.evictions += 1

    def clear(self) -> None:
        self._items.clear()

    def stats(self) -> dict:
        total = self.hits + self.misses
        return {
            "size": len(self._items),
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
            "hit_rate": round(self.hits / total, 4) if total else 0.0,
            "ttl_seconds": self.ttl_seconds,
        }
