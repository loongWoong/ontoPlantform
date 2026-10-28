"""Point-in-time read-back (plan-v2 1.9: console as-of).

The runtime keeps an append-only change log with the previous value of every
property.  Within the retained window we can therefore reconstruct the state
of one object *as of* a change sequence by replaying the log backwards from
the current state:

    current props --(reverse changes with seq > N)--> props at seq N

``create_object``/``delete_object`` reconstruct existence; ``link``/``unlink``
are ignored for property state (relations are navigated live).  Requests older
than ``oldest_change_seq()`` must do a full resync instead: we refuse rather
than silently returning a wrong answer.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..errors import ValidationError
from .common import ApplicationContext


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        raise ValidationError(
            f"invalid RFC3339 timestamp {value!r}") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class StateHistory:
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def changes_for(self, workspace_id: str, actor: str, ref: str,
                    limit: int = 50) -> dict:
        self._ctx.require(actor, workspace_id, "viewer")
        gateway = self._ctx.gateway_for(workspace_id)
        entries = []
        for entry in gateway.observe_changes(0):
            touches = [c for c in entry.get("changes") or []
                       if ref in (c.get("object"), c.get("source"), c.get("target"))]
            if touches:
                entries.append({"seq": entry["seq"], "ts": entry.get("ts"),
                                "source": entry.get("source"),
                                "changes": touches})
        head = entries[-1]["seq"] if entries else 0
        return {"ref": ref, "changes": entries[-limit:] if limit else entries,
                "head_seq": head, "oldest_seq": gateway.oldest_change_seq()}

    def object_as_of(self, workspace_id: str, actor: str, ref: str,
                     seq: int) -> dict:
        """Reconstruct ``ref`` at ``seq`` (state after applying change seq)."""
        self._ctx.require(actor, workspace_id, "viewer")
        gateway = self._ctx.gateway_for(workspace_id)
        oldest = gateway.oldest_change_seq()
        if seq > 0 and oldest and seq < oldest:
            raise ValidationError(
                f"history window starts at seq {oldest}; re-request with "
                f"seq >= {oldest} or rebuild from a snapshot")
        result = self._replay(gateway, ref,
                              lambda entry: int(entry["seq"]) > seq)
        head = gateway.observe_changes(0)
        head_seq = int(head[-1]["seq"]) if head else 0
        return {
            "ref": ref,
            "as_of_seq": seq,
            "head_seq": head_seq,
            "oldest_seq": oldest,
            "exists": result["exists"],
            "properties": result["properties"],
            "changes_replayed": result["replayed"],
        }

    def object_at_time(self, workspace_id: str, actor: str, ref: str,
                       at: str) -> dict:
        """Point-in-time (valid time) read-back: state as of a wall-clock
        timestamp (plan-v2 1.11 temporal effectivity query).

        The change log's transaction timestamps are the effectivity boundary:
        a value written at ``t0`` is the state for every request ``t >= t0``
        until the next write.  Requests older than the retained window are
        refused instead of silently returning today's state.
        """
        self._ctx.require(actor, workspace_id, "viewer")
        cutoff = _parse_time(at)
        gateway = self._ctx.gateway_for(workspace_id)
        entries = gateway.observe_changes(0)
        if entries:
            oldest_ts = _parse_time(entries[0]["ts"])
            if cutoff < oldest_ts:
                raise ValidationError(
                    f"history window starts at {entries[0]['ts']}; re-request "
                    "with a later timestamp or rebuild from a snapshot")
        result = self._replay(
            gateway, ref, lambda entry: _parse_time(entry["ts"]) > cutoff)
        head = entries[-1]["seq"] if entries else 0
        return {
            "ref": ref,
            "as_of": at,
            "as_of_seq": result["seq"],
            "head_seq": head,
            "oldest_seq": gateway.oldest_change_seq(),
            "exists": result["exists"],
            "properties": result["properties"],
            "changes_replayed": result["replayed"],
        }

    def _replay(self, gateway, ref: str, after) -> dict:
        """Reverse-replay changes while ``after(entry)`` is true."""
        current = gateway.get_object(ref)
        props = dict((current or {}).get("properties") or {})
        exists = current is not None
        replayed = 0
        boundary_seq = 0
        entries = gateway.observe_changes(0)
        head_seq = int(entries[-1]["seq"]) if entries else 0
        for entry in reversed(entries):
            if not after(entry):
                boundary_seq = max(boundary_seq, int(entry["seq"]))
                break
            for change in reversed(entry.get("changes") or []):
                if change.get("object") != ref:
                    continue
                kind = change.get("kind")
                if kind == "set_property":
                    replayed += 1
                    prop = change["property"]
                    if change.get("old") is None:
                        props.pop(prop, None)
                    else:
                        props[prop] = change["old"]
                elif kind == "create_object":
                    exists = False
                    props = {}
                    replayed += 1
                elif kind == "delete_object":
                    exists = True
                    props = dict(change.get("properties") or {})
                    replayed += 1
        return {"exists": exists, "properties": props if exists else {},
                "replayed": replayed, "seq": boundary_seq or head_seq}
