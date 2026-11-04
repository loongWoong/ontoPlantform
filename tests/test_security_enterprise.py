"""Enterprise hardening regressions: tenancy, durable queue, SCIM, OIDC,
break-glass/MFA, decision cache + SQL pushdown, approval delegation/risk.
"""

from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from onto_platform.application.security import (
    OIDCVerifier,
    default_policy_document,
)
from onto_platform.application.security.emergency import totp, verify_totp
from onto_platform.application.security.policy import policy_validation_report
from onto_platform.bootstrap import PlatformConfig, build_platform
from onto_platform.errors import AccessDeniedError, ValidationError
from tests.conftest import make_platform, seed_workspace


def _owner(platform, wid: str, actor: str) -> None:
    platform.workspaces.add_member(wid, actor, "owner", actor="admin")


# ---------------------------------------------------------------------------
# Tenant aggregation
# ---------------------------------------------------------------------------


def test_tenant_groups_workspaces_and_tenant_roles_apply():
    platform = make_platform()
    tenant = platform.tenants.create("admin", "Acme")
    first = platform.workspaces.create("one", actor="admin",
                                        tenant_id=tenant["id"])
    second = platform.workspaces.create("two", actor="admin",
                                         tenant_id=tenant["id"])
    platform.tenants.add_member(tenant["id"], "admin", "bob", "viewer")

    # tenant viewer can read every workspace in the tenant, but not another
    platform.ctx.require("bob", first["id"], "viewer")
    platform.ctx.require("bob", second["id"], "viewer")

    summary = platform.tenants.summary(tenant["id"], "admin")
    assert {item["name"] for item in summary["workspaces"]} == {"one", "two"}
    assert summary["totals"]["workspace"] == 2

    outsider = platform.workspaces.create("other", actor="admin")
    with pytest.raises(AccessDeniedError):
        platform.tenants.summary(tenant["id"], "mallory")
    with pytest.raises(AccessDeniedError):
        platform.ctx.require("bob", outsider["id"], "viewer")


def test_tenant_routes_create_workspace_and_aggregate(http_server):
    from tests.conftest import http_get, http_post

    base, _wid = http_server
    status, tenant = http_post(base, "/api/tenants",
                               {"name": "Acme", "user": "admin"})
    assert status == 201
    status, workspace = http_post(base, "/api/workspaces", {
        "name": "tenant-ws", "user": "admin", "tenant_id": tenant["id"]})
    assert status == 200
    status, summary = http_get(
        base, f"/api/tenants/{tenant['id']}?user=admin")
    assert status == 200
    assert [item["name"] for item in summary["workspaces"]] == ["tenant-ws"]
    status, member = http_post(
        base, f"/api/tenants/{tenant['id']}/members",
        {"member": "bob", "role": "viewer", "user": "admin"})
    assert status == 201 and member["user"] == "bob"
    status, members = http_get(
        base, f"/api/tenants/{tenant['id']}/members?user=admin")
    assert status == 200
    assert [item["user"] for item in members] == ["admin", "bob"]
    status, listed = http_get(base, "/api/tenants?user=admin")
    assert status == 200
    assert any(item["id"] == tenant["id"] for item in listed)


def test_scim_groups_over_http(monkeypatch):
    from http.server import ThreadingHTTPServer
    from onto_platform.interfaces.api import build_handler
    from tests.conftest import http_call, http_delete, http_get, http_post

    monkeypatch.setenv("ONTO_SCIM_ALLOW_ANONYMOUS", "1")
    platform = make_platform()
    wid = seed_workspace(platform)
    platform.scim._workspace_id = wid
    platform.scim._actor = "admin"
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(platform))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        status, created = http_post(base, "/scim/v2/Groups", {
            "displayName": "http-ops", "members": [{"value": "alice"}]})
        assert status == 201
        assert created["displayName"] == "http-ops"
        status, listed = http_get(
            base, '/scim/v2/Groups?filter=displayName%20eq%20%22http-ops%22')
        assert status == 200
        assert listed["totalResults"] == 1
        status, _headers, patched = http_call(
            "PATCH", base, f"/scim/v2/Groups/{created['id']}",
            {"Operations": [{"op": "replace", "path": "members",
                             "value": [{"value": "bob"}]}]})
        assert status == 200
        assert [member["value"] for member in patched["members"]] == ["bob"]
        status, deleted = http_delete(base,
                                      f"/scim/v2/Groups/{created['id']}")
        assert status == 200 and deleted["deleted"] is True
    finally:
        httpd.shutdown()


# ---------------------------------------------------------------------------
# Durable async queue
# ---------------------------------------------------------------------------


def test_async_queue_survives_restart_and_runs_once(sqlite_platform, tmp_path):
    wid = seed_workspace(sqlite_platform)
    sqlite_platform.start_scheduler(poll_seconds=60)
    task = sqlite_platform.async_tasks.enqueue(
        wid, "admin", "Vehicle.recharge", target="Vehicle/V001",
        args={"amount": 1}, delay_seconds=0)
    row = sqlite_platform.ctx.repo.raw.get("async_task", wid, task["id"])
    assert row["status"] == "pending"
    sqlite_platform.stop_scheduler()
    sqlite_platform.ctx.repo.close()

    # A new process reads the same SQLite file and rebuilds the queue.
    restarted = build_platform(PlatformConfig(
        store="sqlite", db_path=str(tmp_path / "platform.db")))
    restarted.start_scheduler(poll_seconds=60)
    try:
        pending = restarted.async_tasks.list_tasks(wid)
        assert [item["id"] for item in pending] == [task["id"]]
        assert pending[0]["status"] == "pending"
        results = restarted.async_tasks.run_now(task["id"])
        assert results and results[0]["ok"] is True
        done = restarted.ctx.repo.raw.get("async_task", wid, task["id"])
        assert done["status"] == "done"
    finally:
        restarted.stop_scheduler()
        restarted.ctx.repo.close()


# ---------------------------------------------------------------------------
# SCIM Groups
# ---------------------------------------------------------------------------


def test_scim_groups_crud_filter_patch_and_delete(platform):
    wid = seed_workspace(platform)
    platform.scim._bearer_token = "scim-secret"
    platform.scim._workspace_id = wid
    platform.scim._actor = "admin"
    platform.scim.authenticate("Bearer scim-secret")

    created = platform.scim.create_group({
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
        "displayName": "ops",
        "externalId": "idp-ops",
        "members": [{"value": "alice"}, {"value": "bob"}],
    })
    assert created["displayName"] == "ops"
    assert created["externalId"] == "idp-ops"
    assert [m["value"] for m in created["members"]] == ["alice", "bob"]

    filtered = platform.scim.groups(filter='displayName eq "ops"')
    assert filtered["totalResults"] == 1
    assert platform.scim.groups(filter='displayName eq "nope"')[
        "totalResults"] == 0

    patched = platform.scim.patch_group(created["id"], {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        "Operations": [{"op": "add", "path": "members",
                        "value": [{"value": "carol"}]}],
    })
    assert {m["value"] for m in patched["members"]} == {
        "alice", "bob", "carol"}

    replaced = platform.scim.replace_group(created["id"], {
        "displayName": "operators",
        "members": [{"value": "dave"}],
    })
    assert replaced["displayName"] == "operators"
    assert [m["value"] for m in replaced["members"]] == ["dave"]

    assert platform.scim.delete_group(created["id"])["deleted"] is True
    with pytest.raises(Exception):
        platform.scim.group(created["id"])


# ---------------------------------------------------------------------------
# OIDC discovery + JWKS rotation
# ---------------------------------------------------------------------------

_TEST_N = (
    "uhDNzc26NB4kDW0cKGjW5QfNGdVQaL09tjO8KKfUkK2pKSRNy5csJJqdWxBrlp-"
    "RkzyUIyva-pwGZuv84JWl9xt2-OWi85YTlf5zZVrxXOtYV05giPd5WhiAlhIMfbHv-"
    "zWw4RrPjEU9c20FcjvAE1WJJn42RmfC1T1Pzr8-fY3buQmcO9RFL4sTZslxAQph51x"
    "Bovz2pFfHnSLevtCB6-lIFGN8gsM9boQPAxbvU-N51IGYjI0bdovbkLc3dkIeiHIHn"
    "Miv9bgS1IvV2oI0CpMZh3wlGUP5lcwP38qYlw-MPpSV4liqympJXPPD9M24uuwNoII"
    "Ae-T00IohWHGqyQ"
)
_TEST_E = "AQAB"
_TEST_D = (
    "XzhoSlFlYHSoWKKlDV0R1KXYZccP3noyUcNKILg7BUuKTX7BvVYBBj6GFuthU_UXZ_"
    "VRjmpMJ4KfmB5fDIQpKjvI9_l1GhmMrm9ImO8gpD_gttVqe83CMcRmUeass8rTGIlSm"
    "vDAlHV-zEpXxi0j0AEvLh7bIc7KG5zD8fdXQKLvOVM6ohL5Trz_MNv1ATtUk_Lic3Pl"
    "cnLgMbUOVmwMzkvuPW68tJ-rO6iwN6CgAxAIQVOIGmXwRDXMO4CFfsh4TlaiQUw6eWP"
    "HlhGFM_81fXwHKLgn2CgOabqEfo-M56VOCM1oz8t-sNhe2OLC4k2Qu3LdALvkocupeCi"
    "O0VRRTQ"
)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_int(value: str) -> int:
    return int.from_bytes(
        base64.urlsafe_b64decode(value + "=" * (-len(value) % 8)), "big")


def _rs256(claims: dict, *, kid: str = "k1") -> str:
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT", "kid": kid},
                                separators=(",", ":")).encode())
    payload = _b64url(json.dumps(claims, separators=(",", ":")).encode())
    signing = f"{header}.{payload}".encode()
    digest_info = bytes.fromhex(
        "3031300d060960864801650304020105000420") + hashlib.sha256(
            signing).digest()
    size = (_b64url_int(_TEST_N).bit_length() + 7) // 8
    padding = size - len(digest_info) - 3
    encoded = b"\x00\x01" + b"\xff" * padding + b"\x00" + digest_info
    signature = pow(int.from_bytes(encoded, "big"), _b64url_int(_TEST_D),
                    _b64url_int(_TEST_N)).to_bytes(size, "big")
    return f"{header}.{payload}.{_b64url(signature)}"


def test_oidc_discovery_jwks_and_key_rotation():
    state = {"kid": "k1", "fetches": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/.well-known/openid-configuration":
                body = {"issuer": f"http://127.0.0.1:{self.server.server_port}",
                        "jwks_uri": f"http://127.0.0.1:"
                                    f"{self.server.server_port}/jwks"}
                return self._json(body)
            if self.path == "/jwks":
                state["fetches"] += 1
                return self._json({"keys": [{
                    "kty": "RSA", "kid": state["kid"],
                    "n": _TEST_N, "e": _TEST_E, "alg": "RS256",
                }]})
            self.send_error(404)

        def _json(self, body):
            raw = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        now = time.time()
        verifier = OIDCVerifier(
            issuer=base, audience="onto-platform", clock=lambda: now,
            discovery_url=base + "/.well-known/openid-configuration")
        claims = {"iss": base, "aud": "onto-platform", "sub": "idp|alice",
                  "exp": int(now) + 300}
        assert verifier.verify(_rs256(claims))["sub"] == "idp|alice"

        # Unknown kid triggers one forced JWKS refresh (rotation path).
        state["kid"] = "k2"
        assert verifier.verify(_rs256(claims, kid="k2"))["sub"] == "idp|alice"
        assert state["fetches"] >= 2
        assert verifier.discover()["issuer"] == base
    finally:
        server.shutdown()


# ---------------------------------------------------------------------------
# MFA + break-glass
# ---------------------------------------------------------------------------


def test_totp_replay_protection_and_recovery_codes(platform):
    enrollment = platform.mfa.enroll("admin")
    code = totp(enrollment["secret"])
    assert platform.mfa.verify("admin", code) is True
    # The same time step cannot be replayed.
    assert platform.mfa.verify("admin", code) is False
    recovery = enrollment["recovery_codes"][0]
    assert platform.mfa.verify("admin", recovery) is True
    assert platform.mfa.verify("admin", recovery) is False


def test_break_glass_requires_second_owner_and_is_time_boxed(platform):
    wid = seed_workspace(platform)
    _owner(platform, wid, "approver")
    # A platform admin without a workspace role can use break-glass.
    platform.ctx.platform_admins = frozenset({"ops"})
    platform.ctx.security.platform_admins = frozenset({"ops"})

    request = platform.break_glass.request(
        wid, "ops", "生产事故需要紧急恢复访问权限", ticket="INC-1", minutes=15)
    assert request["status"] == "pending"
    with pytest.raises(ValidationError, match="separation of duties"):
        platform.break_glass.approve(wid, request["id"], "ops")

    approved = platform.break_glass.approve(wid, request["id"], "approver")
    assert approved["status"] == "active"
    roles = platform.ctx.security.roles_for("ops", wid)
    assert "owner" in roles
    platform.break_glass.revoke(wid, request["id"], "approver")
    assert "owner" not in platform.ctx.security.roles_for("ops", wid)


def test_mfa_is_required_for_break_glass_when_enrolled(platform):
    wid = seed_workspace(platform)
    _owner(platform, wid, "approver")
    enrollment = platform.mfa.enroll("admin")
    with pytest.raises(AccessDeniedError, match="MFA"):
        platform.break_glass.request(
            wid, "admin", "生产事故需要紧急恢复访问权限", ticket="INC-2")
    code = totp(enrollment["secret"])
    request = platform.break_glass.request(
        wid, "admin", "生产事故需要紧急恢复访问权限", ticket="INC-2",
        mfa_code=code)
    assert platform.break_glass.approve(
        wid, request["id"], "approver")["status"] == "active"


# ---------------------------------------------------------------------------
# Decision cache + SQL pushdown
# ---------------------------------------------------------------------------


def test_decision_cache_hits_and_is_invalidated_by_security_write(platform):
    wid = seed_workspace(platform)
    platform.ctx.security.check_action("admin", wid, "workspace.read")
    platform.ctx.security.check_action("admin", wid, "workspace.read")
    assert platform.ctx.security.cache_stats()["hits"] >= 1
    platform.security.create_policy(wid, "admin",
                                    document=default_policy_document())
    assert platform.ctx.security.cache_stats()["size"] == 0


def test_sql_pushdown_avoids_loading_every_row(sqlite_platform):
    wid = seed_workspace(sqlite_platform)
    raw = sqlite_platform.ctx.repo.raw
    for index, status in enumerate(("open", "closed", "open")):
        raw.put("vehicle_pass", wid, f"pass-{index}", {
            "tenant_id": wid, "workspace_id": wid,
            "status": status, "plate": f"A{index}",
        })

    original_list = raw.list

    def _guarded(kind, workspace_id=None, *args, **kwargs):
        if kind == "vehicle_pass":
            raise AssertionError(
                "full list() must not be used for a pushed query")
        return original_list(kind, workspace_id, *args, **kwargs)

    raw.list = _guarded
    rows = sqlite_platform.security.query_objects(
        wid, "admin", "vehicle_pass",
        filter={"field": "status", "op": "eq", "value": "open"})
    assert {row["plate"] for row in rows} == {"A0", "A2"}


# ---------------------------------------------------------------------------
# Approval delegation + risk tiers
# ---------------------------------------------------------------------------


def test_approval_delegation_counts_for_the_principal(platform):
    wid = seed_workspace(platform)
    for actor in ("approver-1", "approver-2", "delegate"):
        _owner(platform, wid, actor)
    document = default_policy_document()
    document["approval"] = {
        "requiredFor": ["policy.publish"],
        "separationOfDuties": True,
        "requiredApprovals": 2,
        "delegates": [{"from": "approver-1", "to": "delegate",
                       "expiresAt": "2099-01-01T00:00:00+00:00"}],
    }
    created = platform.security.create_policy(wid, "admin",
                                              document=document)
    platform.security.validate_policy(wid, "admin", created["id"])
    first = platform.security.approve_policy(wid, "delegate", created["id"])
    assert first["approval_progress"]["approvers"] == ["approver-1"]
    assert first["approvals"][0]["delegated"] is True
    second = platform.security.approve_policy(wid, "approver-2",
                                              created["id"])
    assert second["status"] == "approved"


def test_risk_tier_raises_required_approvals_and_expired_delegate_is_ignored(
        platform):
    wid = seed_workspace(platform)
    for actor in ("approver-1", "approver-2", "approver-3", "delegate"):
        _owner(platform, wid, actor)
    document = default_policy_document()
    document["approval"] = {
        "requiredFor": ["policy.publish"],
        "separationOfDuties": True,
        "useRiskTiers": True,
        "riskTier": "high",
        "delegates": [{"from": "approver-1", "to": "delegate",
                       "expiresAt": "2000-01-01T00:00:00+00:00"}],
    }
    assert policy_validation_report(document)["valid"] is True
    created = platform.security.create_policy(wid, "admin",
                                              document=document)
    platform.security.validate_policy(wid, "admin", created["id"])
    assert created["approval_progress"]["required"] == 3
    assert created["approval_progress"]["riskTier"] == "high"

    first = platform.security.approve_policy(wid, "delegate", created["id"])
    # The expired delegation is not honoured, so the delegate's own identity
    # is the accountable approver.
    assert first["approval_progress"]["approvers"] == ["delegate"]
    assert first["approval_progress"]["satisfied"] is False
    with pytest.raises(ValidationError):
        platform.security.publish_policy(wid, "admin", created["id"])
