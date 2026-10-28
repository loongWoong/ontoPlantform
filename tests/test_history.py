"""Phase 1.9: point-in-time read-back (console as-of) and change history."""

from __future__ import annotations

import pytest

from onto_platform.errors import ValidationError

from conftest import http_get, seed_workspace


def _head(platform, wid: str) -> int:
    entries = platform.ctx.gateway_for(wid).observe_changes(0)
    return int(entries[-1]["seq"]) if entries else 0


def test_as_of_reconstructs_an_earlier_property_value(platform):
    wid = seed_workspace(platform)
    gateway = platform.ctx.gateway_for(wid)
    assert gateway.get_object("Vehicle/V001")["properties"]["balance"] == 100
    before = _head(platform, wid)
    platform.capabilities.invoke(wid, "admin", "Vehicle.recharge",
                                 target="Vehicle/V001", args={"amount": 10})
    head = _head(platform, wid)
    assert head > before

    old = platform.history.object_as_of(wid, "admin", "Vehicle/V001", before)
    assert old["exists"] is True
    assert old["properties"]["balance"] == 100
    assert old["as_of_seq"] == before and old["head_seq"] == head
    assert old["changes_replayed"] >= 1

    new = platform.history.object_as_of(wid, "admin", "Vehicle/V001", head)
    assert new["properties"]["balance"] == 110
    assert new["changes_replayed"] == 0


def test_as_of_reports_non_existence_before_creation(platform):
    wid = seed_workspace(platform)
    before = _head(platform, wid)
    from onto_platform.ports.runtime import ObjectUpsert, SemanticDelta

    platform.ctx.gateway_for(wid).apply_delta(SemanticDelta(upserts=(
        ObjectUpsert(type="TollStation", id="ST900",
                     properties={"name": "new", "status": "free"}),)))
    old = platform.history.object_as_of(wid, "admin", "TollStation/ST900", before)
    assert old["exists"] is False and old["properties"] == {}
    now = platform.history.object_as_of(wid, "admin", "TollStation/ST900",
                                        _head(platform, wid))
    assert now["exists"] is True
    assert now["properties"]["name"] == "new"


def test_as_of_refuses_requests_outside_the_retained_window(platform):
    wid = seed_workspace(platform)
    platform.capabilities.invoke(wid, "admin", "Vehicle.recharge",
                                 target="Vehicle/V001", args={"amount": 1})
    gateway = platform.ctx.gateway_for(wid)
    gateway._changes = gateway._changes[-1:]  # simulate compaction
    oldest = gateway.oldest_change_seq()
    assert oldest > 1
    with pytest.raises(ValidationError) as excinfo:
        platform.history.object_as_of(wid, "admin", "Vehicle/V001", oldest - 1)
    assert "history window" in str(excinfo.value)


def test_history_lists_changes_touching_one_object(platform):
    wid = seed_workspace(platform)
    platform.capabilities.invoke(wid, "admin", "Vehicle.recharge",
                                 target="Vehicle/V001", args={"amount": 5})
    history = platform.history.changes_for(wid, "admin", "Vehicle/V001")
    assert history["ref"] == "Vehicle/V001"
    assert history["head_seq"] >= history["changes"][-1]["seq"]
    assert any(c["kind"] == "set_property"
               for entry in history["changes"] for c in entry["changes"])


def test_changes_cursor_resumes(platform):
    wid = seed_workspace(platform)
    gateway = platform.ctx.gateway_for(wid)
    first = gateway.observe_changes(0)
    assert first, "seeding must produce changes"
    cursor = first[-1]["seq"]
    platform.capabilities.invoke(wid, "admin", "Vehicle.recharge",
                                 target="Vehicle/V001", args={"amount": 3})
    tail = gateway.observe_changes(cursor)
    assert tail and all(e["seq"] > cursor for e in tail)
    assert [e["seq"] for e in tail] == sorted({e["seq"] for e in tail})
    # resuming from the new head yields nothing until the next write
    assert gateway.observe_changes(tail[-1]["seq"]) == []


def test_console_shows_asof(platform):
    """The shipped console bundle wires the as-of read-back (1.9 DoD)."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src" / "onto_platform" / \
        "interfaces" / "web" / "dist"
    scripts = list((root / "assets").glob("*.js"))
    assert scripts, "console bundle missing; run npm run build"
    bundle = "\n".join(p.read_text(encoding="utf-8", errors="replace")
                       for p in scripts)
    assert "state/as-of" in bundle and "state/history" in bundle
    assert "历史回读" in bundle


def test_as_of_over_http(platform, http_server):
    base, wid = http_server
    before = _head(platform, wid)
    platform.capabilities.invoke(wid, "admin", "Vehicle.recharge",
                                 target="Vehicle/V001", args={"amount": 7})
    status, body = http_get(
        base, f"/api/workspaces/{wid}/state/as-of?ref=Vehicle/V001&seq={before}")
    assert status == 200, body
    assert body["properties"]["balance"] == 100
    status, body = http_get(
        base, f"/api/workspaces/{wid}/state/history?ref=Vehicle/V001")
    assert status == 200 and body["changes"]


def test_object_as_of_timestamp_shows_effectivity_interval(platform, http_server,
                                                          monkeypatch):
    """`at` answers "what did the world look like at wall-clock t" (1.11)."""
    import onto_platform.infrastructure.runtime.kernel_gateway as kernel_gateway

    wid = seed_workspace(platform)
    monkeypatch.setattr(kernel_gateway, "_now",
                        lambda: "2030-01-01T00:00:00+00:00")
    platform.capabilities.invoke(wid, "admin", "Vehicle.recharge",
                                 target="Vehicle/V001", args={"amount": 9})
    gateway = platform.ctx.gateway_for(wid)
    earlier = "2029-12-31T23:59:59+00:00"
    at = "2030-01-01T00:00:00+00:00"

    old = platform.history.object_at_time(wid, "admin", "Vehicle/V001", earlier)
    assert old["exists"] is True and old["properties"]["balance"] == 100
    assert old["as_of"] == earlier
    new = platform.history.object_at_time(wid, "admin", "Vehicle/V001", at)
    assert new["properties"]["balance"] == 109
    assert new["as_of_seq"] == gateway.observe_changes(0)[-1]["seq"]

    # unknown object did not exist in the past either
    absent = platform.history.object_at_time(wid, "admin", "Vehicle/V999", earlier)
    assert absent["exists"] is False

    base, _ = http_server
    from urllib.parse import quote

    status, body = http_get(
        base, f"/api/workspaces/{wid}/state/as-of?ref=Vehicle/V001"
              f"&at={quote(earlier)}")
    assert status == 200, body
    assert body["properties"]["balance"] == 100
    with pytest.raises(ValidationError):
        platform.history.object_at_time(wid, "admin", "Vehicle/V001",
                                        "not-a-timestamp")

