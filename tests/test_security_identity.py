"""Enterprise identity tests: SCIM, OIDC, sessions and LDAP.

These pin the properties an authentication adapter must not lose:

* directory users are upserted idempotently and disabled instead of deleted;
* SCIM requires its bearer token and exposes the ListResponse contract;
* OIDC (HS256) fails closed for unconfigured/malformed/expired/wrong-audience
  tokens and accepts a valid one;
* session tokens are signed, tamper-evident, expire, and resolve to a user;
* a missing optional ``ldap3`` is a clear validation error, not an import
  crash, while the real bind+search path works against an injected module.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import types

import pytest

from onto_platform.application.security import context as auth_context
from onto_platform.application.security.identity import (
    SCIM_LIST_SCHEMA,
    SCIM_USER_SCHEMA,
    IdentityDirectory,
    IdentityService,
    LDAPAuthenticator,
    OIDCVerifier,
    SCIMService,
    SessionIssuer,
)
from onto_platform.errors import (
    AccessDeniedError,
    NotFoundError,
    ValidationError,
)
from tests.conftest import make_platform


@pytest.fixture(autouse=True)
def _isolated_security_context():
    auth_context.clear()
    yield
    auth_context.clear()


@pytest.fixture()
def id_platform():
    return make_platform()


@pytest.fixture()
def id_wid(id_platform):
    return id_platform.workspaces.create("身份中心", actor="admin")["id"]


@pytest.fixture()
def directory(id_platform, id_wid):
    return IdentityDirectory(id_platform.ctx, id_platform.ctx.security,
                             workspace_id=id_wid)


@pytest.fixture()
def scim(directory):
    service = SCIMService(directory, "scim-secret")
    service.authenticate("Bearer scim-secret")
    return service


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _jwt(secret: str, *, alg: str = "HS256", **claims) -> str:
    header = _b64(json.dumps({"alg": alg, "typ": "JWT"}).encode("utf-8"))
    payload = _b64(json.dumps(claims).encode("utf-8"))
    signature = _b64(hmac.new(secret.encode("utf-8"),
                              f"{header}.{payload}".encode("ascii"),
                              hashlib.sha256).digest())
    return f"{header}.{payload}.{signature}"


def _oidc(now: float, **overrides) -> OIDCVerifier:
    options = {
        "secret": "oidc-secret",
        "issuer": "https://idp.example.com",
        "audience": "onto-platform",
        "clock": lambda: now,
    }
    options.update(overrides)
    return OIDCVerifier(**options)


def _claims(now: float, **overrides) -> dict:
    claims = {
        "sub": "idp|alice",
        "preferred_username": "alice",
        "email": "alice@example.com",
        "name": "Alice",
        "iss": "https://idp.example.com",
        "aud": "onto-platform",
        "iat": int(now) - 10,
        "nbf": int(now) - 10,
        "exp": int(now) + 300,
    }
    claims.update(overrides)
    return claims


# ---------------------------------------------------------------------------
# directory + SCIM
# ---------------------------------------------------------------------------


def test_scim_requires_the_bearer_token(directory):
    service = SCIMService(directory, "scim-secret")
    with pytest.raises(AccessDeniedError, match="bearer token"):
        service.users()
    with pytest.raises(AccessDeniedError, match="invalid"):
        service.users(authorization="Bearer wrong")
    payload = {"userName": "alice"}
    with pytest.raises(AccessDeniedError):
        service.create_user(payload)
    # Passing the header per call is enough; no shared mutable state needed.
    response = service.create_user(payload, authorization="Bearer scim-secret")
    assert response["userName"] == "alice"


def test_scim_without_configured_token_fails_closed(directory):
    service = SCIMService(directory)
    with pytest.raises(AccessDeniedError, match="not configured"):
        service.users()
    with pytest.raises(AccessDeniedError, match="not configured"):
        service.authenticate("Bearer anything")
    # An explicit developer opt-in is the only way to open the endpoint.
    dev = SCIMService(directory, allow_anonymous=True)
    assert dev.users()["totalResults"] == 0


def test_scim_create_list_patch_and_disable_user(scim, directory):
    created = scim.create_user({
        "userName": "alice",
        "displayName": "Alice",
        "emails": [{"value": "alice@example.com", "primary": True}],
        "groups": ["ops"],
        "externalId": "idp|alice",
        "active": True,
    })
    assert created["schemas"] == [SCIM_USER_SCHEMA]
    assert created["userName"] == "alice"
    assert created["displayName"] == "Alice"
    assert created["emails"][0]["value"] == "alice@example.com"
    assert created["emails"][0]["primary"] is True
    assert created["active"] is True
    assert created["meta"]["resourceType"] == "User"

    listing = scim.users()
    assert listing["schemas"] == [SCIM_LIST_SCHEMA]
    assert listing["totalResults"] == 1
    assert [resource["id"] for resource in listing["Resources"]] == [
        created["id"]]

    patched = scim.patch_user(created["id"], {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        "Operations": [
            {"op": "replace", "path": "displayName", "value": "Alice Zhang"},
            {"op": "replace", "path": "emails", "value": ["a@example.com"]},
            {"op": "replace", "path": "groups", "value": ["ops", "finance"]},
        ],
    })
    assert patched["displayName"] == "Alice Zhang"
    assert [email["value"] for email in patched["emails"]] == ["a@example.com"]
    assert [group["value"] for group in patched["groups"]] == [
        "ops", "finance"]

    deactivated = scim.patch_user(created["id"], {"active": False})
    assert deactivated["active"] is False
    assert directory.get_user(created["id"])["active"] is False
    assert directory.list_users(active_only=True) == []
    assert len(directory.list_users()) == 1

    deleted = scim.delete_user(created["id"])
    assert deleted == {"id": created["id"], "active": False, "deleted": True,
                       "userName": "alice"}
    assert directory.get_user(created["id"])["active"] is False

    with pytest.raises(ValidationError):
        scim.patch_user(created["id"], {"nickName": "ali"})
    with pytest.raises(NotFoundError):
        scim.patch_user("missing", {"active": True})
    with pytest.raises(ValidationError):
        scim.create_user({"displayName": "no user name"})


def test_directory_upsert_is_idempotent_and_never_reactivates(directory):
    first = directory.upsert_user(
        "alice", display_name="Alice", emails=["alice@example.com"],
        external_id="idp|alice", provider="oidc")
    second = directory.upsert_user(
        "alice", display_name="Alice Zhang", external_id="idp|alice",
        provider="oidc")
    assert second["id"] == first["id"]
    assert len(directory.list_users()) == 1
    assert second["display_name"] == "Alice Zhang"
    assert directory.find_by_external_id("idp|alice")["id"] == first["id"]
    assert directory.find_by_user_name("ALICE")["id"] == first["id"]

    disabled = directory.delete_user(first["id"])
    assert disabled["active"] is False
    # A later login upsert (active=None) must not silently re-enable them.
    after_login = directory.upsert_user(
        "alice", external_id="idp|alice", provider="oidc")
    assert after_login["active"] is False

    with pytest.raises(ValidationError):
        directory.upsert_user("")
    with pytest.raises(NotFoundError):
        directory.get_user("missing")

    bob = directory.upsert_user("bob", external_id="idp|bob", provider="oidc")
    assert bob["id"]
    # Two accounts cannot claim the same IdP subject: the session subject is
    # looked up by external id, so ambiguity would be an authentication bug.
    with pytest.raises(ValidationError, match="already taken"):
        directory.upsert_user("alice", external_id="idp|bob",
                              provider="oidc")


def test_directory_writes_can_be_reauthorized(directory, id_platform, id_wid):
    id_platform.workspaces.add_member(id_wid, "bob", "viewer", actor="admin")
    with pytest.raises(AccessDeniedError):
        directory.upsert_user("eve", actor="bob")
    created = directory.upsert_user("eve", actor="admin")
    assert created["user_name"] == "eve"


class _GroupAPI:
    """Minimal ``SecurityService``-shaped group API for the SCIM mapping."""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    def list_groups(self, workspace_id):
        return list(self.rows.values())

    def create_group(self, workspace_id, actor, name, members):
        group_id = f"grp_{len(self.rows) + 1}"
        row = {"id": group_id, "name": name,
               "members": list(members), "created_at": ""}
        self.rows[group_id] = row
        return row

    def set_members(self, workspace_id, actor, group_id, members):
        self.rows[group_id]["members"] = list(members)
        return self.rows[group_id]


def test_scim_groups_map_to_an_injected_group_api(directory):
    api = _GroupAPI()
    service = SCIMService(directory, "scim-secret", group_api=api)
    service.authenticate("Bearer scim-secret")

    created = service.create_group("ops", ["alice"])
    assert created["displayName"] == "ops"
    assert [member["value"] for member in created["members"]] == ["alice"]
    listing = service.groups()
    assert listing["totalResults"] == 1
    assert listing["Resources"][0]["schemas"][0].endswith(":Group")

    patched = service.patch_group(created["id"], {
        "Operations": [{"op": "replace", "path": "members",
                        "value": ["alice", "bob"]}],
    })
    assert [member["value"] for member in patched["members"]] == [
        "alice", "bob"]
    with pytest.raises(ValidationError):
        service.patch_group(created["id"], {"active": False})

    plain = SCIMService(directory, "scim-secret")
    plain.authenticate("Bearer scim-secret")
    with pytest.raises(ValidationError, match="group_api"):
        plain.create_group("ops")


# ---------------------------------------------------------------------------
# OIDC
# ---------------------------------------------------------------------------


def test_oidc_verifies_a_valid_hs256_token():
    now = 1_800_000_000.0
    verifier = _oidc(now)
    claims = verifier.verify(_jwt("oidc-secret", **_claims(now)))
    assert claims["sub"] == "idp|alice"
    assert claims["aud"] == "onto-platform"
    # Audience lists are accepted too.
    listed = _claims(now, aud=["other", "onto-platform"])
    assert verifier.verify(_jwt("oidc-secret", **listed))["sub"] == "idp|alice"


def test_oidc_rejects_expired_not_yet_valid_and_bad_signature():
    now = 1_800_000_000.0
    verifier = _oidc(now)
    expired = _jwt("oidc-secret", **_claims(now, exp=int(now) - 120))
    with pytest.raises(AccessDeniedError, match="expired"):
        verifier.verify(expired)
    # Inside the 60s skew the token is still usable ...
    close = _jwt("oidc-secret", **_claims(now, exp=int(now) - 30))
    assert verifier.verify(close)["sub"] == "idp|alice"
    future = _jwt("oidc-secret", **_claims(now, nbf=int(now) + 600))
    with pytest.raises(AccessDeniedError, match="not valid yet"):
        verifier.verify(future)
    with pytest.raises(AccessDeniedError, match="signature"):
        verifier.verify(_jwt("another-secret", **_claims(now)))
    with pytest.raises(AccessDeniedError, match="malformed"):
        verifier.verify("not-a-jwt")


def test_oidc_rejects_wrong_issuer_audience_and_algorithm():
    now = 1_800_000_000.0
    verifier = _oidc(now)
    wrong_issuer = _jwt("oidc-secret", **_claims(
        now, iss="https://evil.example.com"))
    with pytest.raises(AccessDeniedError, match="issuer"):
        verifier.verify(wrong_issuer)
    wrong_audience = _jwt("oidc-secret", **_claims(now, aud="another-app"))
    with pytest.raises(AccessDeniedError, match="audience"):
        verifier.verify(wrong_audience)
    no_expiry = _jwt("oidc-secret", **{**_claims(now), "exp": None})
    with pytest.raises(AccessDeniedError):
        verifier.verify(no_expiry)
    for algorithm in ("none", "HS512"):
        token = _jwt("oidc-secret", alg=algorithm, **_claims(now))
        with pytest.raises(AccessDeniedError, match="algorithm"):
            verifier.verify(token)


def test_oidc_is_unconfigured_by_default(monkeypatch):
    for name in ("ONTO_OIDC_HS256_SECRET", "ONTO_OIDC_ISSUER",
                 "ONTO_OIDC_AUDIENCE", "ONTO_OIDC_CLOCK_SKEW"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(AccessDeniedError, match="not configured"):
        OIDCVerifier().verify("any.token.value")


def test_oidc_reads_environment_configuration(monkeypatch):
    now = 1_800_000_000.0
    monkeypatch.setenv("ONTO_OIDC_HS256_SECRET", "env-secret")
    monkeypatch.setenv("ONTO_OIDC_ISSUER", "https://idp.example.com")
    monkeypatch.setenv("ONTO_OIDC_AUDIENCE", "onto-platform")
    monkeypatch.setenv("ONTO_OIDC_CLOCK_SKEW", "0")
    verifier = OIDCVerifier(clock=lambda: now)
    claims = _claims(now)
    del claims["nbf"]
    token = _jwt("env-secret", **claims)
    assert verifier.verify(token)["sub"] == "idp|alice"
    expired = _jwt("env-secret", **_claims(now, exp=int(now) - 1))
    with pytest.raises(AccessDeniedError, match="expired"):
        verifier.verify(expired)
    with pytest.raises(ValidationError):
        OIDCVerifier(environ={"ONTO_OIDC_HS256_SECRET": "s",
                              "ONTO_OIDC_CLOCK_SKEW": "soon"})


# ---------------------------------------------------------------------------
# session tokens
# ---------------------------------------------------------------------------


def test_session_issue_verify_and_claims_roundtrip():
    now = 1_800_000_000.0
    issuer = SessionIssuer(secret="session-secret", clock=lambda: now,
                           ttl_seconds=600)
    token = issuer.issue("alice")
    assert token.count(".") == 2
    payload = issuer.verify(token)
    assert payload["sub"] == "alice"
    assert payload["exp"] == int(now) + 600
    assert payload["claims"] == {}

    with_claims = issuer.issue("alice", {"provider": "oidc", "user_id": "u1"})
    assert with_claims.count(".") == 3
    assert issuer.verify(with_claims)["claims"] == {
        "provider": "oidc", "user_id": "u1"}

    # An email subject contains dots; it must still verify as one subject.
    email_token = issuer.issue("alice@example.com")
    assert issuer.verify(email_token)["sub"] == "alice@example.com"
    with pytest.raises(ValidationError, match="JSON-serializable"):
        issuer.issue("alice", {"when": object()})


def test_session_tokens_are_tamper_evident_and_expire():
    now = 1_800_000_000.0
    issuer = SessionIssuer(secret="session-secret", clock=lambda: now)
    token = issuer.issue("alice")
    subject, expires, signature = token.rsplit(".", 2)
    forged_exp = f"{subject}.{int(expires) + 3600}.{signature}"
    with pytest.raises(AccessDeniedError, match="signature"):
        issuer.verify(forged_exp)
    last = "A" if signature[-1] != "A" else "B"
    forged_sig = f"{subject}.{expires}.{signature[:-1]}{last}"
    with pytest.raises(AccessDeniedError, match="signature"):
        issuer.verify(forged_sig)
    with pytest.raises(AccessDeniedError, match="malformed"):
        issuer.verify(f"{subject}.{expires}")

    expired = issuer.issue("alice", ttl_seconds=-5)
    with pytest.raises(AccessDeniedError, match="expired"):
        issuer.verify(expired)

    with pytest.raises(ValidationError):
        issuer.issue("")
    with pytest.raises(ValidationError):
        issuer.issue("alice", claims=["not", "a", "mapping"])


def test_session_issuer_requires_its_secret(monkeypatch):
    monkeypatch.delenv("ONTO_SESSION_SECRET", raising=False)
    issuer = SessionIssuer(environ={})
    with pytest.raises(ValidationError, match="ONTO_SESSION_SECRET"):
        issuer.issue("alice")
    with pytest.raises(AccessDeniedError, match="not configured"):
        issuer.verify("alice.1.sig")
    configured = SessionIssuer(environ={"ONTO_SESSION_SECRET": "s"})
    assert configured.verify(configured.issue("alice"))["sub"] == "alice"


# ---------------------------------------------------------------------------
# LDAP
# ---------------------------------------------------------------------------


class _FakeConnection:
    instances: list["_FakeConnection"] = []

    def __init__(self, server, *, user, password, auto_bind,
                 receive_timeout=None):
        self.server = server
        self.user = user
        self.password = password
        self.auto_bind = auto_bind
        self.receive_timeout = receive_timeout
        self.entries: list = []
        self.search_args = None
        self.unbound = False
        self.bind_result = True
        _FakeConnection.instances.append(self)

    def bind(self):
        return self.bind_result

    def search(self, base, filter_text, attributes=None):
        self.search_args = (base, filter_text, tuple(attributes or ()))
        self.entries = [{
            "mail": "alice@example.com",
            "displayName": "Alice",
            "memberOf": ["cn=ops,ou=groups,dc=example,dc=com"],
        }]

    def unbind(self):
        self.unbound = True


_FAKE_LDAP = types.SimpleNamespace(
    NONE="NONE",
    Server=lambda url, get_info=None: types.SimpleNamespace(url=url),
    Connection=_FakeConnection,
)


def test_ldap_missing_optional_package_is_a_clear_error():
    authenticator = LDAPAuthenticator(loader=lambda name: None)
    assert authenticator.available is False
    with pytest.raises(ValidationError,
                       match="LDAP support requires the optional ldap3 package"):
        authenticator.authenticate("alice", "secret")


def test_ldap_real_bind_and_search_path_with_injected_module():
    _FakeConnection.instances.clear()
    authenticator = LDAPAuthenticator(
        environ={
            "ONTO_LDAP_URL": "ldap://ldap.example.com",
            "ONTO_LDAP_BASE_DN": "dc=example,dc=com",
            "ONTO_LDAP_USER_TEMPLATE": "uid={username},ou=people,{base}",
        },
        loader=lambda name: _FAKE_LDAP,
    )
    assert authenticator.available is True
    profile = authenticator.authenticate("alice", "s3cret")
    assert profile["user_name"] == "alice"
    assert profile["display_name"] == "Alice"
    assert profile["emails"] == ["alice@example.com"]
    assert profile["groups"] == ["cn=ops,ou=groups,dc=example,dc=com"]
    assert profile["external_id"] == "uid=alice,ou=people,dc=example,dc=com"
    connection = _FakeConnection.instances[-1]
    assert connection.user == "uid=alice,ou=people,dc=example,dc=com"
    assert connection.password == "s3cret"
    assert connection.search_args[1] == "(uid=alice)"
    assert connection.unbound is True

    with pytest.raises(ValidationError, match="password"):
        authenticator.authenticate("alice", "")


def test_ldap_bind_failure_is_access_denied():
    authenticator = LDAPAuthenticator(
        environ={"ONTO_LDAP_URL": "ldap://x", "ONTO_LDAP_BASE_DN": "dc=x"},
        loader=lambda name: _FAKE_LDAP,
    )
    _FakeConnection.instances.clear()
    original_bind = _FakeConnection.bind
    _FakeConnection.bind = lambda self: False
    try:
        with pytest.raises(AccessDeniedError, match="bind"):
            authenticator.authenticate("alice", "wrong")
    finally:
        _FakeConnection.bind = original_bind


# ---------------------------------------------------------------------------
# login orchestration
# ---------------------------------------------------------------------------


class _StubLDAP:
    def __init__(self, profile: dict) -> None:
        self.profile = profile

    def authenticate(self, username, password):
        if password != "s3cret":
            raise AccessDeniedError("LDAP bind failed")
        return dict(self.profile)


def test_identity_service_oidc_login_and_session_resolution(
        directory, id_wid):
    now = time.time()
    oidc = _oidc(now, clock=lambda: now)
    sessions = SessionIssuer(secret="session-secret", clock=lambda: now)
    service = IdentityService(directory, oidc, sessions,
                              LDAPAuthenticator(loader=lambda name: None))

    login = service.oidc_login(_jwt("oidc-secret", **_claims(now)))
    assert login["user"]["user_name"] == "alice"
    assert login["user"]["provider"] == "https://idp.example.com"
    assert directory.find_by_external_id("idp|alice")["id"] == \
        login["user"]["id"]

    resolved = service.resolve_session(login["token"])
    assert resolved["user_name"] == "alice"
    assert resolved["id"] == login["user"]["id"]

    # Logging in again must not create a second identity.
    again = service.oidc_login(_jwt("oidc-secret", **_claims(now)))
    assert again["user"]["id"] == login["user"]["id"]
    assert len(directory.list_users()) == 1

    with pytest.raises(AccessDeniedError, match="known identity"):
        service.resolve_session(sessions.issue("nobody"))
    with pytest.raises(AccessDeniedError):
        service.resolve_session("broken.token.value")

    directory.delete_user(login["user"]["id"])
    with pytest.raises(AccessDeniedError, match="disabled"):
        service.resolve_session(login["token"])


def test_oidc_login_never_links_a_local_name_to_an_idp_subject(
        directory, id_wid):
    """A token claiming preferred_username=admin must not become local admin."""
    local = directory.upsert_user("admin", provider="local", actor="admin")
    now = time.time()
    service = IdentityService(
        directory, _oidc(now, clock=lambda: now),
        SessionIssuer(secret="session-secret", clock=lambda: now))
    claims = _claims(now, sub="idp|admin", preferred_username="admin",
                     email="admin@evil.example")
    login = service.oidc_login(_jwt("oidc-secret", **claims))

    assert login["user"]["id"] != local["id"]
    assert login["user"]["user_name"] != "admin"
    assert login["user"]["external_id"] == "idp|admin"
    # The local operator record is untouched: no IdP link, no new groups.
    untouched = directory.get_user(local["id"])
    assert untouched["external_id"] == ""
    assert untouched["provider"] == "local"
    assert service.resolve_session(login["token"])["id"] == \
        login["user"]["id"]


def test_identity_service_ldap_login(directory):
    now = time.time()
    profile = {
        "user_name": "bob",
        "display_name": "Bob",
        "emails": ["bob@example.com"],
        "groups": ["cn=ops,dc=example,dc=com"],
        "external_id": "uid=bob,ou=people,dc=example,dc=com",
    }
    service = IdentityService(
        directory, _oidc(now),
        SessionIssuer(secret="session-secret"),
        _StubLDAP(profile),
    )
    login = service.ldap_login("bob", "s3cret")
    assert login["user"]["user_name"] == "bob"
    assert login["user"]["provider"] == "ldap"
    assert service.resolve_session(login["token"])["id"] == login["user"]["id"]
    with pytest.raises(AccessDeniedError):
        service.ldap_login("bob", "wrong")
    with pytest.raises(ValidationError, match="ldap3"):
        IdentityService(directory, _oidc(now),
                        SessionIssuer(secret="s")).ldap_login("bob", "s3cret")
