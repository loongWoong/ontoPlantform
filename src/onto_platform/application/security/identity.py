"""Enterprise identity: user directory, SCIM 2.0, OIDC, sessions and LDAP.

Zero third-party runtime dependencies.  Every optional integration is either
standard library (``hmac``/``base64``/``json`` for OIDC and sessions) or
lazily imported (``ldap3`` only when it is installed *and* an LDAP bind is
attempted), so importing this module never fails on a minimal deployment.

Security properties:

* the directory stores users as control-plane rows (kind ``identity_user``);
  deactivation is a flag, never a hard delete, so audit/role references keep
  resolving to a disabled identity instead of an unknown one;
* OIDC is HS256 only and fails closed when unconfigured, malformed, badly
  signed, expired or addressed to another issuer/audience;
* session tokens are HMAC-SHA256 signed over ``sub.exp`` (plus an optional
  claims segment) and are compared in constant time;
* SCIM mutations require the configured bearer token; a missing/incorrect
  token is an ``AccessDeniedError``, never an anonymous write.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import importlib
import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Sequence

from ...errors import AccessDeniedError, NotFoundError, ValidationError

#: Repository kind for directory users (control-plane: tenant-pinned, never
#: filtered by a business row scope).
IDENTITY_KIND = "identity_user"

SCIM_USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
SCIM_GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
SCIM_LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
SCIM_PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"

#: Session token lifetime: 12 hours unless the issuer is told otherwise.
DEFAULT_SESSION_TTL_SECONDS = 12 * 3600
#: OIDC tolerance for clock drift between the platform and the IdP.
DEFAULT_OIDC_CLOCK_SKEW = 60


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _uuid_id(prefix: str = "usr") -> str:
    from uuid import uuid4

    return f"{prefix}_{uuid4().hex[:12]}"


def _call_id_factory(factory: Callable[..., str] | None, prefix: str) -> str:
    if factory is None:
        return _uuid_id(prefix)
    try:
        value = factory(prefix)
    except TypeError:
        value = factory()
    text = str(value or "").strip()
    return text or _uuid_id(prefix)


def _text(value: Any, label: str, *, required: bool = True) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise ValidationError(f"{label} must not be empty")
    return text


# ---------------------------------------------------------------------------
# small attribute normalizers (shared by the directory and SCIM)
# ---------------------------------------------------------------------------


def normalize_emails(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, (str, bytes)):
        values = [values]
    try:
        items = list(values)
    except TypeError as exc:
        raise ValidationError("identity emails must be a list") from exc
    result: list[str] = []
    for item in items:
        if isinstance(item, Mapping):
            item = item.get("value") or item.get("address") or ""
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def normalize_groups(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, (str, bytes)):
        values = [values]
    try:
        items = list(values)
    except TypeError as exc:
        raise ValidationError("identity groups must be a list") from exc
    result: list[str] = []
    for item in items:
        if isinstance(item, Mapping):
            item = item.get("value") or item.get("display") or ""
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def as_bool(value: Any, label: str = "value") -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes", "on"):
            return True
        if lowered in ("false", "0", "no", "off", ""):
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    raise ValidationError(f"{label} must be a boolean")


# ---------------------------------------------------------------------------
# user directory
# ---------------------------------------------------------------------------


class IdentityDirectory:
    """Platform-local user directory (kind ``identity_user``).

    ``ctx`` is an ``ApplicationContext`` (or a bare ``Repository``).  When
    ``workspace_id`` is omitted the directory is platform-global; pass a
    workspace id to keep a directory per tenant.  Writes are audited through
    the platform context and optionally re-authorized when ``actor`` is given.
    """

    KIND = IDENTITY_KIND

    def __init__(self, ctx: Any, security: Any = None, *,
                 workspace_id: str | None = None) -> None:
        repo = getattr(ctx, "repo", None)
        if repo is None and hasattr(ctx, "put") and hasattr(ctx, "list"):
            repo = ctx
            ctx = None
        if repo is None:
            raise ValidationError("identity directory requires a repository")
        self._ctx = ctx
        self.repo = repo
        self._security = security if security is not None \
            else getattr(ctx, "security", None)
        if workspace_id is None or str(workspace_id).strip() == "":
            self._workspace_id = None
        else:
            self._workspace_id = str(workspace_id).strip()
        clock = getattr(ctx, "clock", None)
        self._clock = clock if callable(clock) else _now
        self._id_factory = getattr(ctx, "id_factory", None)

    # ---- reads --------------------------------------------------------

    def list_users(self, *, active_only: bool = False) -> list[dict]:
        rows = self.repo.list(IDENTITY_KIND, self._workspace_id)
        users = [self._record(row) for row in rows]
        if active_only:
            users = [user for user in users if user["active"]]
        return sorted(users, key=lambda user: (user["user_name"].casefold(),
                                               user["id"]))

    def get_user(self, user_id: str) -> dict:
        uid = _text(user_id, "identity user id")
        row = self.repo.get(IDENTITY_KIND, self._workspace_id, uid)
        if row is None:
            raise NotFoundError(f"identity user not found: {user_id!r}")
        return self._record(row)

    def find_by_user_name(self, user_name: str) -> dict | None:
        target = str(user_name or "").strip().casefold()
        if not target:
            return None
        for user in self.list_users():
            if user["user_name"].casefold() == target:
                return user
        return None

    def find_by_external_id(self, external_id: str,
                            provider: str | None = None) -> dict | None:
        target = str(external_id or "").strip()
        if not target:
            return None
        matches = [
            user for user in self.list_users()
            if user["external_id"] == target
            and (provider is None or user["provider"] == provider)
        ]
        if len(matches) > 1:
            # Two identities claiming the same IdP subject is an ambiguity an
            # authentication path must never resolve arbitrarily.
            raise ValidationError(
                f"multiple identities share external id {target!r}")
        return matches[0] if matches else None

    # ---- writes -------------------------------------------------------

    def upsert_user(self, user_name: str = "", *, user_id: str = "",
                    display_name: str | None = None,
                    emails: Sequence[Any] | None = None,
                    groups: Sequence[Any] | None = None,
                    active: bool | None = None,
                    external_id: str | None = None,
                    provider: str | None = None,
                    actor: str = "") -> dict:
        """Create or update a directory user.

        The lookup order is explicit id, then ``external_id`` (the IdP
        subject), then ``user_name``.  ``None`` means "keep the stored value";
        a new user defaults to active.  A conflicting ``user_name`` (or an
        ambiguous external id) raises instead of silently merging identities.
        """
        self._authorize(actor)
        now = str(self._clock())
        target_id = str(user_id or "").strip()
        existing: dict | None = None
        if target_id:
            try:
                existing = self.get_user(target_id)
            except NotFoundError:
                existing = None

        requested_name = str(user_name or "").strip()
        requested_external = (None if external_id is None
                              else str(external_id).strip())
        if existing is None and requested_name:
            existing = self.find_by_user_name(requested_name)
        if existing is None and requested_external:
            existing = self.find_by_external_id(
                requested_external,
                provider=str(provider).strip() if provider else None)

        if existing is None and not requested_name:
            raise ValidationError("identity user_name must not be empty")

        record = dict(existing) if existing is not None else {
            "id": target_id or _call_id_factory(self._id_factory, "usr"),
            "user_name": "",
            "display_name": "",
            "emails": [],
            "groups": [],
            "active": True,
            "external_id": "",
            "provider": "local",
            "created_at": now,
            "updated_at": now,
        }
        resolved_name = requested_name or record["user_name"]
        if not resolved_name:
            raise ValidationError("identity user_name must not be empty")
        conflict = self.find_by_user_name(resolved_name)
        if conflict is not None and conflict["id"] != record["id"]:
            raise ValidationError(
                f"identity user_name {resolved_name!r} is already taken")
        if requested_external:
            clash = self.find_by_external_id(requested_external)
            if clash is not None and clash["id"] != record["id"]:
                raise ValidationError(
                    f"identity external id {requested_external!r} is already "
                    "taken")
        record["user_name"] = resolved_name
        if display_name is not None:
            record["display_name"] = str(display_name or "").strip()
        if emails is not None:
            record["emails"] = normalize_emails(emails)
        if groups is not None:
            record["groups"] = normalize_groups(groups)
        if active is not None:
            record["active"] = bool(active)
        if requested_external is not None:
            record["external_id"] = requested_external
        if provider is not None:
            record["provider"] = str(provider or "").strip() or "local"
        record["updated_at"] = now
        self.repo.put(IDENTITY_KIND, self._workspace_id, record["id"], record)
        return self._record(record)

    def delete_user(self, user_id: str, *, actor: str = "") -> dict:
        """Deactivate a user (soft delete); safe to call twice."""
        self._authorize(actor)
        record = self.get_user(user_id)
        if not record["active"]:
            return record
        record["active"] = False
        record["updated_at"] = str(self._clock())
        self.repo.put(IDENTITY_KIND, self._workspace_id, record["id"], record)
        return record

    # ---- internals ----------------------------------------------------

    def _record(self, row: Mapping[str, Any]) -> dict:
        return {
            "id": str(row.get("id") or ""),
            "user_name": str(row.get("user_name") or ""),
            "display_name": str(row.get("display_name") or ""),
            "emails": normalize_emails(row.get("emails")),
            "groups": normalize_groups(row.get("groups")),
            "active": bool(row.get("active", True)),
            "external_id": str(row.get("external_id") or ""),
            "provider": str(row.get("provider") or "local"),
            "created_at": str(row.get("created_at") or ""),
            "updated_at": str(row.get("updated_at") or ""),
        }

    def _authorize(self, actor: str) -> None:
        if not actor or self._security is None or not self._workspace_id:
            return
        check = getattr(self._security, "check_action", None)
        if callable(check):
            check(actor, self._workspace_id, "security.manage")


# ---------------------------------------------------------------------------
# SCIM 2.0 (subset)
# ---------------------------------------------------------------------------


class SCIMService:
    """SCIM 2.0 Users (complete) and Groups (mapped to a ``group_api``).

    ``bearer_token`` is the token the SCIM endpoint expects.  An adapter must
    either pass the incoming ``Authorization`` header to every call or call
    :meth:`authenticate` once per request.  A missing token fails closed; a
    deployment that intentionally exposes SCIM without auth (only plausible on
    a loopback developer machine) must opt in with ``allow_anonymous=True``.
    """

    def __init__(self, directory: IdentityDirectory, bearer_token: str = "", *,
                 group_api: Any = None,
                 workspace_id: str | None = None,
                 actor: str = "scim",
                 allow_anonymous: bool = False) -> None:
        self.directory = directory
        self._bearer_token = str(bearer_token or "")
        self._group_api = group_api
        self._workspace_id = (workspace_id
                              if workspace_id is not None
                              else getattr(directory, "_workspace_id", None))
        self._actor = actor
        self._allow_anonymous = bool(allow_anonymous)
        self.authorization: str | None = None

    # ---- authentication ----------------------------------------------

    def authenticate(self, authorization: str) -> bool:
        """Validate an ``Authorization`` header (or a bare token)."""
        if not self._bearer_token:
            if self._allow_anonymous:
                return True
            raise AccessDeniedError(
                "SCIM bearer token is not configured; set "
                "ONTO_SCIM_BEARER_TOKEN or explicitly allow anonymous SCIM")
        value = str(authorization or "").strip()
        token = value[7:].strip() if value.lower().startswith("bearer ") \
            else value
        if not token or not hmac.compare_digest(
                token.encode("utf-8"), self._bearer_token.encode("utf-8")):
            raise AccessDeniedError("invalid or missing SCIM bearer token")
        self.authorization = token
        return True

    def _guard(self, authorization: str | None) -> None:
        if not self._bearer_token:
            if not self._allow_anonymous:
                raise AccessDeniedError(
                    "SCIM bearer token is not configured; set "
                    "ONTO_SCIM_BEARER_TOKEN or explicitly allow anonymous "
                    "SCIM")
            return
        presented = authorization if authorization is not None \
            else self.authorization
        if presented is None:
            raise AccessDeniedError("SCIM bearer token is required")
        self.authenticate(presented)

    # ---- users --------------------------------------------------------

    def users(self, *, filter: str | None = None, start_index: int = 1,
              count: int | None = None,
              authorization: str | None = None) -> dict:
        self._guard(authorization)
        resources = [self._user_resource(user)
                     for user in self.directory.list_users()]
        resources = _filter_scim_resources(resources, filter)
        return self._list_response(resources, start_index=start_index,
                                   count=count)

    def user(self, user_id: str, *, authorization: str | None = None) -> dict:
        self._guard(authorization)
        return self._user_resource(self.directory.get_user(user_id))

    def create_user(self, payload: Mapping[str, Any], *,
                    authorization: str | None = None) -> dict:
        self._guard(authorization)
        fields = self._user_fields(payload)
        if not fields.get("user_name"):
            raise ValidationError("SCIM user requires userName")
        fields.setdefault("provider", "scim")
        return self._user_resource(self.directory.upsert_user(**fields))

    def patch_user(self, user_id: str, payload: Mapping[str, Any], *,
                   authorization: str | None = None) -> dict:
        self._guard(authorization)
        self.directory.get_user(user_id)  # NotFoundError -> 404 at the edge
        changes = self._patch_changes(payload)
        if not changes:
            return self._user_resource(self.directory.get_user(user_id))
        return self._user_resource(
            self.directory.upsert_user(user_id=str(user_id), **changes))

    def delete_user(self, user_id: str, *,
                    authorization: str | None = None) -> dict:
        self._guard(authorization)
        user = self.directory.delete_user(user_id)
        return {"id": user["id"], "active": False, "deleted": True,
                "userName": user["user_name"]}

    # ---- groups (RFC 7644 core subset: CRUD, filter, pagination) ------

    def groups(self, *, filter: str | None = None, start_index: int = 1,
               count: int | None = None,
               authorization: str | None = None) -> dict:
        self._guard(authorization)
        resources = [self._group_resource(row) for row in self._group_rows()]
        resources = _filter_scim_resources(resources, filter)
        return self._list_response(resources, start_index=start_index,
                                   count=count)

    def group(self, group_id: str, *,
              authorization: str | None = None) -> dict:
        self._guard(authorization)
        return self._group_resource(self._find_group(group_id))

    def create_group(self, name: Any, members: Iterable[str] = (), *,
                     authorization: str | None = None,
                     external_id: str = "") -> dict:
        """Create a group from a SCIM payload or ``(name, members)``."""
        self._guard(authorization)
        creator = getattr(self._group_api, "create_group", None)
        if not callable(creator) or not self._workspace_id:
            raise ValidationError(
                "SCIM group management requires a group_api")
        if isinstance(name, Mapping):
            fields = self._group_fields(name)
            display_name = fields.get("displayName") or fields.get("name")
            if not display_name:
                raise ValidationError("SCIM group requires displayName")
            members = fields.get("members") or []
            external_id = fields.get("externalId") or ""
        else:
            display_name = _text(name, "SCIM group displayName")
        row = self._create_group_via_api(
            str(display_name), [str(member) for member in members],
            str(external_id or ""))
        return self._group_resource(row)

    def _create_group_via_api(self, name: str, members: list[str],
                              external_id: str) -> dict:
        creator = getattr(self._group_api, "create_group", None)
        payload = {"name": name, "members": members,
                   "external_id": external_id}
        try:
            return creator(self._workspace_id, self._actor, payload)
        except TypeError:
            # Adapter written for the pre-payload service signature.
            return creator(self._workspace_id, self._actor, name, members)

    def replace_group(self, group_id: str, payload: Mapping[str, Any], *,
                      authorization: str | None = None) -> dict:
        self._guard(authorization)
        self._find_group(group_id)
        api = self._group_api
        updater = getattr(api, "update_group", None)
        if not callable(updater) or not self._workspace_id:
            raise ValidationError(
                "SCIM group management requires a group_api")
        fields = self._group_fields(payload)
        display_name = fields.get("displayName") or fields.get("name")
        if not display_name:
            raise ValidationError("SCIM group requires displayName")
        members = [str(member) for member in fields.get("members") or []]
        row = updater(self._workspace_id, self._actor, group_id, {
            "name": display_name,
            "members": members,
            "external_id": fields.get("externalId") or "",
        })
        return self._group_resource(row)

    def delete_group(self, group_id: str, *,
                     authorization: str | None = None) -> dict:
        self._guard(authorization)
        self._find_group(group_id)
        deleter = getattr(self._group_api, "delete_group", None)
        if not callable(deleter) or not self._workspace_id:
            raise ValidationError(
                "SCIM group management requires a group_api")
        deleter(self._workspace_id, self._actor, group_id)
        return {"id": group_id, "deleted": True}

    def patch_group(self, group_id: str, payload: Mapping[str, Any], *,
                    authorization: str | None = None) -> dict:
        self._guard(authorization)
        changes = self._group_patch_changes(payload)
        current = self._find_group(group_id)
        api = self._group_api
        if api is None or not self._workspace_id:
            raise ValidationError(
                "SCIM group management requires a group_api")
        if "members" in changes:
            # RFC 7644 ``add`` is additive; ``replace``/``remove`` overwrite.
            added: list[str] = []
            for operation in (payload.get("Operations")
                              or payload.get("operations") or []):
                if not isinstance(operation, Mapping):
                    continue
                if str(operation.get("op") or "").lower() != "add":
                    continue
                path = str(operation.get("path") or "").strip().lower()
                if path not in ("members", ""):
                    continue
                added.extend(str(member)
                             for member in normalize_groups(
                                 operation.get("value") or []))
            if added:
                existing = [str(member)
                            for member in (current.get("members") or [])]
                changes["members"] = list(dict.fromkeys(existing + added))
            setter = (getattr(api, "set_group_members", None)
                      or getattr(api, "set_members", None))
            if callable(setter):
                setter(self._workspace_id, self._actor, group_id,
                       changes["members"])
            else:
                raise ValidationError(
                    "the configured group_api cannot update group members")
        if "displayname" in changes:
            updater = getattr(api, "update_group", None)
            if not callable(updater):
                raise ValidationError(
                    "the configured group_api cannot rename groups")
            updater(self._workspace_id, self._actor, group_id,
                    {"name": str(changes["displayname"] or "")})
        return self._group_resource(self._find_group(group_id))

    def _group_patch_changes(self, payload: Mapping[str, Any]) -> dict:
        operations = self._operations(payload)
        changes: dict[str, Any] = {}
        for key, value in operations.items():
            if key in ("displayname", "name.formatted"):
                changes["displayname"] = "" if value is None else str(value)
            elif key == "members":
                changes["members"] = normalize_groups(value)
            elif key in ("schemas", "id", "meta", "externalid"):
                continue
            else:
                raise ValidationError(
                    f"unsupported SCIM group attribute {key!r}")
        return changes

    # ---- SCIM mapping -------------------------------------------------

    def _list_response(self, resources: list[dict], *, start_index: int = 1,
                       count: int | None = None) -> dict:
        total = len(resources)
        start = max(1, int(start_index or 1))
        page = resources[start - 1:]
        if count is not None:
            page = page[:max(0, int(count))]
        return {
            "schemas": [SCIM_LIST_SCHEMA],
            "totalResults": total,
            "Resources": page,
            "itemsPerPage": len(page),
            "startIndex": start,
        }

    def _user_resource(self, user: Mapping[str, Any]) -> dict:
        emails = list(user.get("emails") or ())
        return {
            "schemas": [SCIM_USER_SCHEMA],
            "id": str(user.get("id") or ""),
            "externalId": str(user.get("external_id") or ""),
            "userName": str(user.get("user_name") or ""),
            "name": {"formatted": str(user.get("display_name") or "")},
            "displayName": str(user.get("display_name") or ""),
            "emails": [
                {"value": email, "type": "work", "primary": index == 0}
                for index, email in enumerate(emails)
            ],
            "groups": [
                {"value": group, "display": group}
                for group in user.get("groups") or ()
            ],
            "active": bool(user.get("active", True)),
            "meta": {
                "resourceType": "User",
                "created": str(user.get("created_at") or ""),
                "lastModified": str(user.get("updated_at") or ""),
                "location": f"/scim/v2/Users/{user.get('id') or ''}",
            },
        }

    def _group_resource(self, row: Mapping[str, Any]) -> dict:
        members = row.get("members") or ()
        return {
            "schemas": [SCIM_GROUP_SCHEMA],
            "id": str(row.get("id") or ""),
            "externalId": str(row.get("external_id")
                              or row.get("externalId") or ""),
            "displayName": str(row.get("name") or row.get("displayName") or ""),
            "members": [
                {"value": str(member), "display": str(member)}
                for member in members
            ],
            "meta": {
                "resourceType": "Group",
                "created": str(row.get("created_at") or ""),
                "lastModified": str(row.get("updated_at")
                                    or row.get("created_at") or ""),
                "location": f"/scim/v2/Groups/{row.get('id') or ''}",
            },
        }

    def _group_fields(self, payload: Mapping[str, Any]) -> dict:
        if not isinstance(payload, Mapping):
            raise ValidationError("SCIM group payload must be an object")
        fields: dict[str, Any] = {}
        for key in ("displayName", "display_name", "name"):
            if payload.get(key) is not None:
                fields["displayName"] = _text(payload[key],
                                              "SCIM group displayName")
                break
        if payload.get("externalId") is not None:
            fields["externalId"] = str(payload["externalId"])
        members = payload.get("members")
        if members is not None:
            if not isinstance(members, list):
                raise ValidationError("SCIM group members must be a list")
            values: list[str] = []
            for item in members:
                if isinstance(item, Mapping):
                    value = item.get("value")
                else:
                    value = item
                if value is not None and str(value).strip():
                    values.append(str(value).strip())
            fields["members"] = normalize_groups(values)
        return fields

    def _user_fields(self, payload: Mapping[str, Any]) -> dict:
        """Translate a SCIM user payload into directory kwargs."""
        if not isinstance(payload, Mapping):
            raise ValidationError("SCIM user payload must be an object")
        fields: dict[str, Any] = {}
        user_name = payload.get("userName", payload.get("user_name"))
        if user_name is not None:
            fields["user_name"] = _text(user_name, "SCIM userName")
        display = payload.get("displayName", payload.get("display_name"))
        if display is None and isinstance(payload.get("name"), Mapping):
            display = payload["name"].get("formatted")
        if display is not None:
            fields["display_name"] = str(display)
        emails = payload.get("emails")
        if emails is None and payload.get("email") is not None:
            emails = [payload["email"]]
        if emails is not None:
            fields["emails"] = normalize_emails(emails)
        if payload.get("groups") is not None:
            fields["groups"] = normalize_groups(payload["groups"])
        if payload.get("active") is not None:
            fields["active"] = as_bool(payload["active"], "SCIM active")
        external = payload.get("externalId", payload.get("external_id"))
        if external is not None:
            fields["external_id"] = str(external)
        if payload.get("provider") is not None:
            fields["provider"] = str(payload["provider"])
        return fields

    def _patch_changes(self, payload: Mapping[str, Any]) -> dict:
        operations = self._operations(payload)
        changes: dict[str, Any] = {}
        for key, value in operations.items():
            if key in ("active",):
                changes["active"] = as_bool(value, "SCIM active")
            elif key in ("displayname", "name.formatted"):
                changes["display_name"] = "" if value is None else str(value)
            elif key == "name":
                if isinstance(value, Mapping) and value.get("formatted") is not None:
                    changes["display_name"] = str(value["formatted"])
                elif value is None:
                    changes["display_name"] = ""
                else:
                    raise ValidationError(
                        "SCIM name patch requires a name.formatted value")
            elif key in ("emails",):
                changes["emails"] = normalize_emails(value)
            elif key in ("groups",):
                changes["groups"] = normalize_groups(value)
            elif key in ("username",):
                if value is None:
                    raise ValidationError("SCIM userName cannot be removed")
                changes["user_name"] = _text(value, "SCIM userName")
            elif key in ("externalid",):
                changes["external_id"] = "" if value is None else str(value)
            elif key in ("provider",):
                changes["provider"] = "" if value is None else str(value)
            elif key in ("schemas", "id", "meta"):
                continue
            else:
                raise ValidationError(f"unsupported SCIM attribute {key!r}")
        return changes

    def _operations(self, payload: Mapping[str, Any]) -> dict:
        """Flatten a direct attribute map or a SCIM PatchOp into one mapping.

        Attribute names are case-folded; ``None`` marks a remove.
        """
        if not isinstance(payload, Mapping):
            raise ValidationError("SCIM patch payload must be an object")
        operations = payload.get("Operations")
        if operations is None:
            operations = payload.get("operations")
        if operations is None:
            return {
                str(key).casefold(): value
                for key, value in payload.items()
                if str(key).casefold() != "schemas"
            }
        if not isinstance(operations, list):
            raise ValidationError("SCIM patch Operations must be a list")
        flattened: dict[str, Any] = {}
        for operation in operations:
            if not isinstance(operation, Mapping):
                raise ValidationError(
                    "each SCIM patch operation must be an object")
            action = str(operation.get("op") or "replace").strip().lower()
            if action not in ("add", "replace", "remove"):
                raise ValidationError(f"unsupported SCIM patch op {action!r}")
            path = operation.get("path")
            value = operation.get("value")
            if not path:
                if action == "remove" and not isinstance(value, Mapping):
                    continue
                if not isinstance(value, Mapping):
                    raise ValidationError(
                        "SCIM patch without path requires a value object")
                for key, item in value.items():
                    flattened[str(key).casefold()] = (
                        None if action == "remove" else item)
                continue
            key = str(path).strip().casefold()
            if key.startswith("name.") or key == "name":
                pass
            flattened[key] = None if action == "remove" else value
        return flattened

    # ---- groups helpers ----------------------------------------------

    def _group_rows(self) -> list[dict]:
        lister = getattr(self._group_api, "list_groups", None)
        if not callable(lister) or not self._workspace_id:
            return []
        return list(lister(self._workspace_id) or [])
    def _find_group(self, group_id: str) -> dict:
        target = str(group_id or "").strip()
        for row in self._group_rows():
            if str(row.get("id") or "") == target:
                return row
        raise NotFoundError(f"SCIM group not found: {group_id!r}")


#: Supported SCIM filter operators.  RFC 7644 allows a large grammar; the
#: core operators below cover the provisioning flows Entra/Okta emit.
_SCIM_FILTER_RE = re.compile(
    r'^\s*([A-Za-z][A-Za-z0-9_.]*)\s+'
    r'(eq|ne|co|sw|pr)\s*(?:"([^"]*)"|([^\s]+))?\s*$', re.IGNORECASE)


def _filter_scim_resources(resources: list[dict],
                           expression: str | None) -> list[dict]:
    """Apply the common ``attr eq "value"``/``and``/``or`` SCIM filters.

    Only allow-listed operators and top-level attributes are parsed; anything
    unrecognized raises ``ValidationError`` (400) instead of silently returning
    the unfiltered page, which would leak other directory entries.
    """
    text = str(expression or "").strip()
    if not text:
        return resources
    parts = re.split(r"\s+(and|or)\s+", text, flags=re.IGNORECASE)
    terms: list[tuple[str, str, str]] = []
    joiner = "and"
    index = 0
    while index < len(parts):
        if index:
            joiner = parts[index].lower()
            index += 1
        match = _SCIM_FILTER_RE.match(parts[index])
        if not match:
            raise ValidationError(f"unsupported SCIM filter {text!r}")
        field, op, quoted, bare = match.groups()
        terms.append((field.casefold(), op.casefold(),
                      quoted if quoted is not None else (bare or "")))
        index += 1

    def matches(resource: Mapping[str, Any]) -> bool:
        results: list[bool] = []
        for field, op, expected in terms:
            actual = resource
            for component in field.split("."):
                if component == "members":
                    actual = [item.get("value")
                              for item in (actual.get("members") or [])]
                    break
                if isinstance(actual, Mapping):
                    if component in actual:
                        actual = actual.get(component)
                    else:
                        # Attribute names are case-insensitive in SCIM.
                        folded = {str(key).casefold(): value
                                  for key, value in actual.items()}
                        actual = folded.get(component.casefold())
                else:
                    actual = None
                    break
            if isinstance(actual, list):
                values = [str(item) for item in actual]
            elif actual is None:
                values = []
            else:
                values = [str(actual)]
            if op == "pr":
                results.append(bool(values))
            elif op == "eq":
                results.append(expected in values)
            elif op == "ne":
                results.append(expected not in values)
            elif op == "co":
                results.append(any(expected in value for value in values))
            elif op == "sw":
                results.append(any(value.startswith(expected)
                                   for value in values))
            else:  # pragma: no cover - regex limits operators
                results.append(False)
        if not results:
            return True
        if joiner == "or":
            return any(results)
        return all(results)

    return [resource for resource in resources if matches(resource)]


# ---------------------------------------------------------------------------
# OIDC (HS256 / RS256, discovery + JWKS rotation)
# ---------------------------------------------------------------------------


def _b64url_decode(segment: Any, label: str = "token") -> bytes:
    text = str(segment or "")
    try:
        return base64.b64decode(
            (text + "=" * (-len(text) % 4)).encode("ascii"),
            altchars=b"-_", validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise AccessDeniedError(f"{label} is not valid base64url") from exc


def _json_segment(segment: Any, label: str) -> dict:
    try:
        value = json.loads(_b64url_decode(segment, label).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AccessDeniedError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise AccessDeniedError(f"{label} must be a JSON object")
    return value


def _b64url_uint(segment: Any) -> int:
    raw = _b64url_decode(segment, "JWK parameter")
    return int.from_bytes(raw, "big")


def _rsa_sha256_verify(jwk: Mapping[str, Any], signature: bytes,
                       message: bytes) -> bool:
    """RSASSA-PKCS1-v1_5/SHA-256 verification from ``n``/``e``."""
    if str(jwk.get("kty") or "") != "RSA":
        raise AccessDeniedError("OIDC signing key is not RSA")
    if str(jwk.get("alg") or "RS256") != "RS256":
        raise AccessDeniedError("OIDC signing key does not allow RS256")
    modulus = _b64url_uint(jwk.get("n"))
    exponent = _b64url_uint(jwk.get("e"))
    size = (modulus.bit_length() + 7) // 8
    if len(signature) != size:
        return False
    value = int.from_bytes(signature, "big")
    if value >= modulus:
        return False
    encoded = pow(value, exponent, modulus).to_bytes(size, "big")
    digest = hashlib.sha256(message).digest()
    # DER prefix for DigestInfo(SHA-256) followed by the 32-byte digest.
    digest_info = bytes.fromhex(
        "3031300d060960864801650304020105000420") + digest
    padding = size - len(digest_info) - 3
    if padding < 8:
        return False
    expected = b"\x00\x01" + b"\xff" * padding + b"\x00" + digest_info
    return hmac.compare_digest(encoded, expected)


class JWKSCache:
    """TTL cache for a JWKS document with forced refresh on an unknown kid."""

    def __init__(self, *, fetcher: Callable[[str], dict],
                 ttl_seconds: int = 300,
                 clock: Callable[[], float] | None = None) -> None:
        self._fetcher = fetcher
        self._ttl = max(1, int(ttl_seconds))
        self._clock = clock or time.time
        self._entries: dict[str, tuple[float, dict]] = {}

    def _load(self, uri: str) -> dict:
        document = self._fetcher(uri)
        if not isinstance(document, dict) or not isinstance(
                document.get("keys"), list):
            raise AccessDeniedError("OIDC JWKS document is malformed")
        self._entries[uri] = (float(self._clock()), document)
        return document

    def document(self, uri: str, *, force: bool = False) -> dict:
        cached = self._entries.get(uri)
        if force or cached is None or \
                float(self._clock()) - cached[0] >= self._ttl:
            return self._load(uri)
        return cached[1]

    def key(self, uri: str, kid: str | None = None, *,
            force: bool = False) -> dict:
        document = self.document(uri, force=force)
        if kid:
            for candidate in document["keys"]:
                if str(candidate.get("kid") or "") == kid:
                    return dict(candidate)
            # Key rotation: one bounded refetch before failing closed.
            if not force:
                return self.key(uri, kid, force=True)
            raise AccessDeniedError(f"OIDC signing key {kid!r} was not found")
        keys = [key for key in document["keys"]
                if str(key.get("kty") or "") == "RSA"]
        if not keys:
            raise AccessDeniedError("OIDC JWKS has no RSA signing key")
        return dict(keys[0])


def _fetch_json_url(url: str) -> dict:
    """Fetch a JSON document with a bounded size and timeout (stdlib only)."""
    import urllib.request

    request = urllib.request.Request(
        str(url), headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            body = response.read(1024 * 1024 + 1)
    except Exception as exc:
        raise AccessDeniedError(
            f"OIDC discovery request failed: {exc}") from exc
    if len(body) > 1024 * 1024:
        raise AccessDeniedError("OIDC discovery response is too large")
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AccessDeniedError("OIDC discovery response is not JSON") from exc
    if not isinstance(value, dict):
        raise AccessDeniedError("OIDC discovery response must be an object")
    return value


class OIDCVerifier:
    """Verify OIDC ID tokens: HS256 shared secret or RS256 via discovery/JWKS.

    RS256 mode discovers ``issuer + /.well-known/openid-configuration`` (or an
    explicit ``ONTO_OIDC_DISCOVERY_URL``), fetches the ``jwks_uri``, caches keys
    for ``jwks_ttl_seconds`` and force-refreshes once on an unknown ``kid`` so a
    rotated signing key is picked up without a restart.  No third-party
    crypto dependency is used: PKCS#1 v1.5 RSA verification is implemented with
    modular exponentiation and constant-time comparison.
    """

    def __init__(self, *, secret: str | None = None,
                 issuer: str | None = None,
                 audience: str | None = None,
                 clock_skew: int | None = None,
                 environ: Mapping[str, str] | None = None,
                 clock: Callable[[], float] | None = None,
                 discovery_url: str | None = None,
                 jwks_uri: str | None = None,
                 fetcher: Callable[[str], dict] | None = None,
                 jwks_ttl_seconds: int = 300) -> None:
        env = os.environ if environ is None else environ
        self.secret = (str(secret) if secret is not None
                       else str(env.get("ONTO_OIDC_HS256_SECRET") or ""))
        self.issuer = (str(issuer) if issuer is not None
                       else str(env.get("ONTO_OIDC_ISSUER") or ""))
        self.audience = (str(audience) if audience is not None
                         else str(env.get("ONTO_OIDC_AUDIENCE") or ""))
        raw_skew = (clock_skew if clock_skew is not None
                    else env.get("ONTO_OIDC_CLOCK_SKEW",
                                 DEFAULT_OIDC_CLOCK_SKEW))
        try:
            self.clock_skew = max(0, int(raw_skew))
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                "ONTO_OIDC_CLOCK_SKEW must be an integer number of seconds"
            ) from exc
        self._clock = clock or time.time
        self.discovery_url = (str(discovery_url) if discovery_url is not None
                              else str(env.get("ONTO_OIDC_DISCOVERY_URL") or ""))
        self._configured_jwks_uri = (str(jwks_uri) if jwks_uri is not None
                                     else str(env.get("ONTO_OIDC_JWKS_URI")
                                              or ""))
        self._discovery: dict | None = None
        self._jwks_cache = JWKSCache(
            fetcher=fetcher or _fetch_json_url,
            ttl_seconds=max(1, int(jwks_ttl_seconds)),
            clock=self._clock)

    # ---- discovery ----------------------------------------------------

    def discovery_document(self) -> dict:
        if self._discovery is not None:
            return self._discovery
        url = self.discovery_url
        if not url and self.issuer:
            url = self.issuer.rstrip("/") + "/.well-known/openid-configuration"
        if not url:
            raise AccessDeniedError(
                "OIDC discovery is not configured (ONTO_OIDC_DISCOVERY_URL)")
        document = _fetch_json_url(url)
        discovered_issuer = str(document.get("issuer") or "")
        if self.issuer and discovered_issuer and discovered_issuer != self.issuer:
            raise AccessDeniedError(
                "OIDC discovery issuer does not match ONTO_OIDC_ISSUER")
        if not document.get("jwks_uri"):
            raise AccessDeniedError("OIDC discovery has no jwks_uri")
        self._discovery = document
        return document

    def jwks_uri(self) -> str:
        if self._configured_jwks_uri:
            return self._configured_jwks_uri
        return str(self.discovery_document()["jwks_uri"])

    def key_for(self, kid: str | None, *, force: bool = False) -> dict:
        uri = self.jwks_uri()
        return self._jwks_cache.key(uri, kid=kid, force=force)

    def discover(self) -> dict:
        """Public snapshot for the console/ops endpoint."""
        try:
            document = self.discovery_document()
        except Exception as exc:
            return {"configured": False, "error": str(exc)}
        return {
            "configured": True,
            "issuer": document.get("issuer"),
            "jwks_uri": document.get("jwks_uri"),
            "authorization_endpoint": document.get("authorization_endpoint"),
            "token_endpoint": document.get("token_endpoint"),
        }

    # ---- token verification ------------------------------------------

    def verify(self, id_token: str) -> dict:
        """Return the validated claims or raise ``AccessDeniedError``."""
        if not self.secret and not (self.discovery_url
                                    or self._configured_jwks_uri
                                    or self.issuer):
            raise AccessDeniedError(
                "OIDC verification is not configured "
                "(ONTO_OIDC_HS256_SECRET or ONTO_OIDC_DISCOVERY_URL)")
        parts = str(id_token or "").strip().split(".")
        if len(parts) != 3:
            raise AccessDeniedError("malformed id token")
        header = _json_segment(parts[0], "id token header")
        algorithm = str(header.get("alg") or "")
        signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
        signature = _b64url_decode(parts[2], "id token signature")
        if algorithm == "HS256":
            if not self.secret:
                raise AccessDeniedError(
                    "OIDC verification is not configured "
                    "(ONTO_OIDC_HS256_SECRET)")
            expected = hmac.new(self.secret.encode("utf-8"), signing_input,
                                hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected):
                raise AccessDeniedError("id token signature is invalid")
        elif algorithm == "RS256":
            kid = str(header.get("kid") or "") or None
            jwk = self.key_for(kid)
            try:
                verified = _rsa_sha256_verify(jwk, signature, signing_input)
            except (TypeError, ValueError, KeyError) as exc:
                raise AccessDeniedError(
                    "id token signing key is invalid") from exc
            if not verified:
                # A rotated key may use the same kid; one forced refresh is
                # both bounded and sufficient (no request amplification).
                jwk = self.key_for(kid, force=True)
                if not _rsa_sha256_verify(jwk, signature, signing_input):
                    raise AccessDeniedError("id token signature is invalid")
        else:
            raise AccessDeniedError(
                f"unsupported id token algorithm {algorithm!r}")
        claims = _json_segment(parts[1], "id token payload")
        now = float(self._clock())
        expires = claims.get("exp")
        if expires is None:
            raise AccessDeniedError("id token has no expiry")
        try:
            expires_at = float(expires)
        except (TypeError, ValueError) as exc:
            raise AccessDeniedError("id token expiry is not numeric") from exc
        if now > expires_at + self.clock_skew:
            raise AccessDeniedError("id token has expired")
        not_before = claims.get("nbf")
        if not_before is not None:
            try:
                not_before_at = float(not_before)
            except (TypeError, ValueError) as exc:
                raise AccessDeniedError(
                    "id token nbf is not numeric") from exc
            if now + self.clock_skew < not_before_at:
                raise AccessDeniedError("id token is not valid yet")
        if self.issuer and str(claims.get("iss") or "") != self.issuer:
            raise AccessDeniedError("id token issuer does not match")
        if self.audience and not _audience_matches(claims.get("aud"),
                                                   self.audience):
            raise AccessDeniedError("id token audience does not match")
        return claims


def _audience_matches(claim: Any, audience: str) -> bool:
    if isinstance(claim, (list, tuple)):
        return audience in [str(item) for item in claim]
    return str(claim or "") == audience


# ---------------------------------------------------------------------------
# session tokens
# ---------------------------------------------------------------------------


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


class SessionIssuer:
    """HMAC-SHA256 session tokens in ``sub.exp.sig`` form.

    ``sub`` is the authenticated subject (a user name or IdP subject) and
    ``exp`` is a Unix timestamp.  When claims are supplied the signed material
    is extended with one base64url JSON segment: ``sub.exp.claims.sig``.  A
    canonical ``issue(sub)`` therefore still produces the documented
    three-part token, and :meth:`verify` accepts both shapes by splitting from
    the right (subjects may legitimately contain ``.``, e.g. an email address).
    """

    def __init__(self, *, secret: str | None = None,
                 environ: Mapping[str, str] | None = None,
                 clock: Callable[[], float] | None = None,
                 ttl_seconds: int | None = None) -> None:
        env = os.environ if environ is None else environ
        self.secret = (str(secret) if secret is not None
                       else str(env.get("ONTO_SESSION_SECRET") or ""))
        self._clock = clock or time.time
        try:
            self.ttl_seconds = int(
                ttl_seconds if ttl_seconds is not None
                else env.get("ONTO_SESSION_TTL_SECONDS")
                or DEFAULT_SESSION_TTL_SECONDS)
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                "ONTO_SESSION_TTL_SECONDS must be an integer") from exc

    def issue(self, sub: str, claims: Mapping[str, Any] | None = None, *,
              ttl_seconds: int | None = None) -> str:
        subject = _text(sub, "session subject")
        if not self.secret:
            raise ValidationError("ONTO_SESSION_SECRET is not configured")
        if claims is not None and not isinstance(claims, Mapping):
            raise ValidationError("session claims must be an object")
        ttl = int(self.ttl_seconds if ttl_seconds is None else ttl_seconds)
        expires_at = int(float(self._clock())) + ttl
        signing = f"{subject}.{expires_at}"
        claims_segment = ""
        if claims:
            try:
                encoded_claims = json.dumps(
                    dict(claims), sort_keys=True, separators=(",", ":"),
                    ensure_ascii=False).encode("utf-8")
            except (TypeError, ValueError) as exc:
                raise ValidationError(
                    "session claims must be JSON-serializable") from exc
            claims_segment = _b64url_encode(encoded_claims)
            signing = f"{signing}.{claims_segment}"
        signature = _b64url_encode(hmac.new(
            self.secret.encode("utf-8"), signing.encode("utf-8"),
            hashlib.sha256).digest())
        if claims_segment:
            return f"{subject}.{expires_at}.{claims_segment}.{signature}"
        return f"{subject}.{expires_at}.{signature}"

    def verify(self, token: str) -> dict:
        if not self.secret:
            raise AccessDeniedError(
                "session verification is not configured (ONTO_SESSION_SECRET)")
        raw = str(token or "").strip()
        if raw.count(".") < 2:
            raise AccessDeniedError("malformed session token")
        # Canonical form first (sub.exp.sig), then the claims-extended form.
        candidates: list[tuple[str, str, str | None, str]] = []
        head, expires, signature = raw.rsplit(".", 2)
        candidates.append((head, expires, None, signature))
        if raw.count(".") >= 3:
            head2, expires2, claims2, signature2 = raw.rsplit(".", 3)
            candidates.append((head2, expires2, claims2, signature2))
        for subject, expires_at, claims_segment, presented in candidates:
            # The middle field of a candidate must be the numeric expiry; this
            # is what distinguishes ``sub.exp.sig`` from
            # ``sub.exp.claims.sig`` when the subject itself contains dots.
            try:
                expires = int(expires_at)
            except (TypeError, ValueError):
                continue
            signing = f"{subject}.{expires_at}"
            if claims_segment is not None:
                signing = f"{signing}.{claims_segment}"
            expected = _b64url_encode(hmac.new(
                self.secret.encode("utf-8"), signing.encode("utf-8"),
                hashlib.sha256).digest())
            if not hmac.compare_digest(presented, expected):
                continue
            if float(self._clock()) > expires:
                raise AccessDeniedError("session token has expired")
            claims: dict = {}
            if claims_segment:
                try:
                    decoded = json.loads(
                        _b64url_decode(claims_segment,
                                       "session claims").decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise AccessDeniedError(
                        "session claims are not valid JSON") from exc
                if not isinstance(decoded, dict):
                    raise AccessDeniedError(
                        "session claims must be a JSON object")
                claims = decoded
            return {"sub": subject, "exp": expires, "claims": claims}
        raise AccessDeniedError("session token signature is invalid")


# ---------------------------------------------------------------------------
# LDAP (optional ldap3)
# ---------------------------------------------------------------------------


def _import_ldap3() -> Any:
    try:
        return importlib.import_module("ldap3")
    except Exception:  # pragma: no cover - depends on the deployment
        return None


def _escape_filter(value: str) -> str:
    escaped: list[str] = []
    for character in str(value):
        if character in "\\*()\x00":
            escaped.append("\\" + format(ord(character), "02x"))
        else:
            escaped.append(character)
    return "".join(escaped)


def _entry_values(entry: Any, name: str) -> list[str]:
    raw = None
    if isinstance(entry, Mapping):
        raw = entry.get(name)
        if raw is None:
            attributes = entry.get("attributes") or {}
            if isinstance(attributes, Mapping):
                raw = attributes.get(name)
    else:
        raw = getattr(entry, name, None)
    values = getattr(raw, "values", None)
    if values is None:
        values = getattr(raw, "value", None)
    if values is None:
        values = raw
    if values is None:
        return []
    if isinstance(values, (list, tuple, set)):
        return [str(item) for item in values if str(item)]
    return [str(values)] if str(values) else []


class LDAPAuthenticator:
    """Optional LDAP bind + attribute lookup.

    Constructing this object never imports/fails on a missing ``ldap3``; the
    dependency is only required when :meth:`authenticate` actually runs.
    Configuration is read from ``ONTO_LDAP_URL``, ``ONTO_LDAP_BASE_DN`` and
    ``ONTO_LDAP_USER_TEMPLATE`` (default ``uid={username},{base}``).
    """

    def __init__(self, *, environ: Mapping[str, str] | None = None,
                 loader: Callable[[str], Any] | None = None,
                 timeout: float = 5.0) -> None:
        env = os.environ if environ is None else environ
        self.url = str(env.get("ONTO_LDAP_URL") or "")
        self.base_dn = str(env.get("ONTO_LDAP_BASE_DN") or "")
        self.user_template = str(
            env.get("ONTO_LDAP_USER_TEMPLATE") or "uid={username},{base}")
        self.timeout = float(timeout)
        self._loader = loader
        self._ldap = loader("ldap3") if loader is not None else _import_ldap3()

    @property
    def available(self) -> bool:
        return self._ldap is not None

    def authenticate(self, username: str, password: str) -> dict:
        if self._ldap is None:
            raise ValidationError(
                "LDAP support requires the optional ldap3 package")
        account = _text(username, "LDAP username")
        secret = str(password or "")
        if not secret:
            raise ValidationError("LDAP password must not be empty")
        if not self.url or not self.base_dn:
            raise ValidationError(
                "LDAP is not configured (ONTO_LDAP_URL / ONTO_LDAP_BASE_DN)")
        try:
            user_dn = self.user_template.format(username=account,
                                                base=self.base_dn)
        except (KeyError, IndexError) as exc:
            raise ValidationError(
                "ONTO_LDAP_USER_TEMPLATE must contain {username} and {base}"
            ) from exc
        ldap = self._ldap
        try:
            server = ldap.Server(self.url,
                                 get_info=getattr(ldap, "NONE", "NONE"))
            connection = ldap.Connection(
                server, user=user_dn, password=secret,
                auto_bind=False, receive_timeout=self.timeout)
        except Exception as exc:  # pragma: no cover - depends on ldap3
            raise AccessDeniedError("LDAP connection failed") from exc
        try:
            if not connection.bind():
                raise AccessDeniedError("LDAP bind failed")
            filter_text = f"({self._filter_attribute()}={_escape_filter(account)})"
            connection.search(self.base_dn, filter_text,
                              attributes=["mail", "displayName", "memberOf"])
            entries = list(getattr(connection, "entries", []) or [])
        except AccessDeniedError:
            raise
        except Exception as exc:
            raise AccessDeniedError("LDAP search failed") from exc
        finally:
            try:
                connection.unbind()
            except Exception:  # pragma: no cover - best-effort cleanup
                pass
        entry = entries[0] if entries else {}
        display = (_entry_values(entry, "displayName")
                   or _entry_values(entry, "displayname") or [account])[0]
        emails = _entry_values(entry, "mail")
        groups = _entry_values(entry, "memberOf")
        return {
            "user_name": account,
            "display_name": display,
            "emails": emails,
            "groups": groups,
            "external_id": user_dn,
            "provider": "ldap",
            "dn": user_dn,
        }

    def _filter_attribute(self) -> str:
        head = self.user_template.split(",", 1)[0]
        if "=" in head:
            attribute = head.split("=", 1)[0].strip()
            if attribute and "{" not in attribute and "}" not in attribute:
                return attribute
        return "uid"


# ---------------------------------------------------------------------------
# login orchestration
# ---------------------------------------------------------------------------


class IdentityService:
    """Resolve sessions and turn IdP/LDAP credentials into platform sessions."""

    def __init__(self, directory: IdentityDirectory, oidc: OIDCVerifier,
                 sessions: SessionIssuer,
                 ldap: LDAPAuthenticator | None = None) -> None:
        self.directory = directory
        self.oidc = oidc
        self.sessions = sessions
        self.ldap = ldap

    # ---- session ------------------------------------------------------

    def resolve_session(self, token: str) -> dict:
        payload = self.sessions.verify(token)
        subject = str(payload.get("sub") or "")
        user = (self.directory.find_by_user_name(subject)
                or self.directory.find_by_external_id(subject))
        if user is None:
            raise AccessDeniedError(
                "the session subject is not a known identity")
        if not user.get("active", True):
            raise AccessDeniedError("this identity is disabled")
        return user

    # ---- OIDC ---------------------------------------------------------

    def oidc_login(self, id_token: str) -> dict:
        claims = self.oidc.verify(id_token)
        subject = str(claims.get("sub") or "").strip()
        if not subject:
            raise AccessDeniedError("id token has no subject")
        user_name = _text(
            claims.get("preferred_username") or claims.get("email")
            or claims.get("upn") or subject, "id token user name")
        display = str(claims.get("name")
                      or claims.get("display_name") or user_name)
        email = claims.get("email")
        provider = str(claims.get("iss") or "oidc")
        # Link only through the IdP subject.  Matching an existing local
        # user by preferred_username would let any valid token for "admin"
        # inherit the local admin's roles (account takeover); when the name
        # is taken, mint a distinct directory identity instead.
        existing = self.directory.find_by_external_id(
            subject, provider=provider)
        if existing is not None:
            user = self.directory.upsert_user(
                existing["user_name"],
                user_id=existing["id"],
                display_name=display,
                emails=[email] if email else [],
                groups=claims.get("groups") or [],
                external_id=subject,
                provider=provider,
            )
        else:
            candidate = user_name
            if self.directory.find_by_user_name(candidate) is not None:
                digest = hashlib.sha256(
                    f"{provider}:{subject}".encode("utf-8")).hexdigest()[:8]
                candidate = f"{user_name}-{digest}"
                while self.directory.find_by_user_name(candidate) is not None:
                    candidate += "x"
            user = self.directory.upsert_user(
                candidate,
                display_name=display,
                emails=[email] if email else [],
                groups=claims.get("groups") or [],
                external_id=subject,
                provider=provider,
            )
        if not user.get("active", True):
            raise AccessDeniedError("this identity is disabled")
        token = self.sessions.issue(subject, {
            "provider": "oidc",
            "iss": str(claims.get("iss") or ""),
            "user_id": user["id"],
        })
        return {"user": user, "token": token, "claims": claims}

    # ---- LDAP ---------------------------------------------------------

    def ldap_login(self, username: str, password: str) -> dict:
        if self.ldap is None:
            raise ValidationError(
                "LDAP support requires the optional ldap3 package")
        profile = self.ldap.authenticate(username, password)
        user = self.directory.upsert_user(
            profile["user_name"],
            display_name=profile.get("display_name") or profile["user_name"],
            emails=profile.get("emails") or [],
            groups=profile.get("groups") or [],
            external_id=profile.get("external_id") or profile["user_name"],
            provider="ldap",
        )
        if not user.get("active", True):
            raise AccessDeniedError("this identity is disabled")
        token = self.sessions.issue(profile["user_name"], {
            "provider": "ldap",
            "user_id": user["id"],
        })
        return {"user": user, "token": token, "profile": profile}


__all__ = [
    "DEFAULT_OIDC_CLOCK_SKEW",
    "DEFAULT_SESSION_TTL_SECONDS",
    "IDENTITY_KIND",
    "SCIM_GROUP_SCHEMA",
    "SCIM_LIST_SCHEMA",
    "SCIM_PATCH_SCHEMA",
    "SCIM_USER_SCHEMA",
    "IdentityDirectory",
    "IdentityService",
    "LDAPAuthenticator",
    "OIDCVerifier",
    "SCIMService",
    "SessionIssuer",
    "as_bool",
    "normalize_emails",
    "normalize_groups",
]
