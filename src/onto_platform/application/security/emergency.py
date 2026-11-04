"""Break-glass emergency access and TOTP multi-factor authentication.

Both are deliberately small and standard-library only:

* TOTP follows RFC 6238 (SHA-1, 30-second period, 6 digits) with one step of
  clock drift, replay protection and one-time recovery codes;
* break-glass is a two-person, time-boxed, audited grant.  The requester must
  already be an owner (or platform admin) and, when enrolled, pass TOTP; a
  *different* owner must approve it.  Approval creates a short-lived role that
  ``SecurityService.roles_for`` sees, and expiry is enforced at every check.

Nothing here is a bearer credential: the TOTP secret is stored as a control
plane row and only valid codes produce a short-lived role.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from dataclasses import dataclass
from typing import Any

from ...errors import AccessDeniedError, NotFoundError, ValidationError

MFA_KIND = "mfa_enrollment"
BREAK_GLASS_KIND = "break_glass"
TOTP_PERIOD = 30
TOTP_DIGITS = 6
TOTP_DRIFT_STEPS = 1
MAX_BREAK_GLASS_MINUTES = 60


def _b32encode(raw: bytes) -> str:
    return base64.b32encode(raw).decode("ascii").rstrip("=")


def _b32decode(text: str) -> bytes:
    raw = str(text or "").strip().upper()
    return base64.b32decode(raw + "=" * (-len(raw) % 8))


def totp(secret: str, *, step: int | None = None,
         digits: int = TOTP_DIGITS) -> str:
    counter = int(time.time() // TOTP_PERIOD) if step is None else int(step)
    digest = hmac.new(_b32decode(secret),
                      struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10 ** digits)).zfill(digits)


def verify_totp(secret: str, code: str, *, at: float | None = None,
                last_step: int | None = None) -> int | None:
    """Return the matched time step, or ``None``; reject replayed steps."""
    presented = str(code or "").strip().replace(" ", "")
    if not presented.isdigit() or len(presented) != TOTP_DIGITS:
        return None
    now = time.time() if at is None else float(at)
    current = int(now // TOTP_PERIOD)
    for offset in range(-TOTP_DRIFT_STEPS, TOTP_DRIFT_STEPS + 1):
        step = current + offset
        if last_step is not None and step <= int(last_step):
            continue
        if hmac.compare_digest(totp(secret, step=step), presented):
            return step
    return None


@dataclass
class MFAService:
    repo: Any
    clock: Any = None

    def __post_init__(self) -> None:
        self.clock = self.clock or (lambda: time.time())
        self._store = getattr(self.repo, "raw", self.repo)

    def enrollment(self, user: str) -> dict | None:
        return self._store.get(MFA_KIND, None, str(user))

    def enrolled(self, user: str) -> bool:
        row = self.enrollment(user)
        return bool(row and row.get("active") and row.get("secret"))

    def enroll(self, user: str, *, issuer: str = "OntoPlatform") -> dict:
        user = str(user or "").strip()
        if not user:
            raise ValidationError("MFA user must not be empty")
        secret = _b32encode(secrets.token_bytes(20))
        recovery = [secrets.token_hex(4) for _ in range(8)]
        now = float(self.clock())
        row = {
            "id": user,
            "user": user,
            "secret": secret,
            "active": True,
            "last_step": -1,
            "recovery_hashes": [hashlib.sha256(code.encode()).hexdigest()
                                for code in recovery],
            "created_at": now,
            "updated_at": now,
        }
        self._store.put(MFA_KIND, None, user, row)
        label = f"{issuer}:{user}".replace(" ", "%20")
        return {
            "user": user,
            "secret": secret,
            "otpauth_uri": (
                f"otpauth://totp/{label}?secret={secret}"
                f"&issuer={issuer.replace(' ', '%20')}&digits={TOTP_DIGITS}"
                f"&period={TOTP_PERIOD}"),
            "recovery_codes": recovery,
        }

    def verify(self, user: str, code: str) -> bool:
        row = self.enrollment(user)
        if not row or not row.get("active"):
            raise AccessDeniedError("MFA is not enrolled for this user")
        presented = str(code or "").strip()
        key = presented.lower().replace("-", "").replace(" ", "")
        hashes = list(row.get("recovery_hashes") or [])
        digest = hashlib.sha256(key.encode()).hexdigest()
        for index, stored in enumerate(hashes):
            if hmac.compare_digest(str(stored), digest):
                row["recovery_hashes"] = hashes[:index] + hashes[index + 1:]
                row["updated_at"] = float(self.clock())
                self._store.put(MFA_KIND, None, row["user"], row)
                return True
        step = verify_totp(str(row["secret"]), presented,
                           at=float(self.clock()),
                           last_step=int(row.get("last_step", -1)))
        if step is None:
            return False
        row["last_step"] = step
        row["updated_at"] = float(self.clock())
        self._store.put(MFA_KIND, None, row["user"], row)
        return True

    def disable(self, user: str) -> dict:
        row = self.enrollment(user)
        if row is None:
            raise NotFoundError(f"MFA is not enrolled for {user!r}")
        row["active"] = False
        row["updated_at"] = float(self.clock())
        self._store.put(MFA_KIND, None, str(user), row)
        return {"user": user, "active": False}


@dataclass
class BreakGlassService:
    ctx: Any
    security: Any
    mfa: MFAService

    @property
    def _store(self):
        return getattr(self.ctx.repo, "raw", self.ctx.repo)

    def _require_owner(self, actor: str, workspace_id: str) -> None:
        if actor in getattr(self.ctx, "platform_admins", frozenset()):
            return
        # ``require`` resolves tenant membership too, so a tenant owner may
        # use break-glass for any workspace in that tenant.
        self.ctx.require(actor, workspace_id, "owner")

    def request(self, workspace_id: str, actor: str, reason: str, *,
                ticket: str = "", minutes: int = 30,
                role: str = "owner", mfa_code: str = "") -> dict:
        self._require_owner(actor, workspace_id)
        reason = str(reason or "").strip()
        if len(reason) < 10:
            raise ValidationError(
                "break-glass reason must be at least 10 characters")
        if role not in ("viewer", "editor", "owner"):
            raise ValidationError("break-glass role must be viewer/editor/owner")
        try:
            duration = max(1, min(int(minutes), MAX_BREAK_GLASS_MINUTES))
        except (TypeError, ValueError) as exc:
            raise ValidationError("break-glass minutes must be an integer") from exc
        if self.mfa.enrolled(actor):
            if not mfa_code or not self.mfa.verify(actor, mfa_code):
                raise AccessDeniedError(
                    "a valid MFA code is required for break-glass")
        now = time.time()
        row = {
            "id": self.ctx.id_factory(),
            "workspace_id": workspace_id,
            "requester": actor,
            "approver": "",
            "reason": reason,
            "ticket": str(ticket or ""),
            "role": role,
            "minutes": duration,
            "status": "pending",
            "requested_at": now,
            "approved_at": None,
            "expires_at": None,
            "revoked_at": None,
        }
        self._store.put(BREAK_GLASS_KIND, workspace_id, row["id"], row)
        self.ctx.audit_event(actor, workspace_id, "security.breakglass_requested",
                             row["id"], {"reason": reason, "ticket": ticket,
                                         "minutes": duration})
        return row

    def approve(self, workspace_id: str, request_id: str, actor: str, *,
                mfa_code: str = "") -> dict:
        self._require_owner(actor, workspace_id)
        row = self._load(workspace_id, request_id)
        if row["status"] != "pending":
            raise ValidationError("break-glass request is not pending")
        if row["requester"] == actor:
            raise ValidationError(
                "separation of duties: the requester cannot approve")
        if self.mfa.enrolled(actor):
            if not mfa_code or not self.mfa.verify(actor, mfa_code):
                raise AccessDeniedError(
                    "a valid MFA code is required to approve break-glass")
        now = time.time()
        row["status"] = "active"
        row["approver"] = actor
        row["approved_at"] = now
        row["expires_at"] = now + int(row["minutes"]) * 60
        self._store.put(BREAK_GLASS_KIND, workspace_id, row["id"], row)
        self.ctx.audit_event(actor, workspace_id, "security.breakglass_activated",
                             row["id"], {"requester": row["requester"],
                                         "role": row["role"],
                                         "expires_at": row["expires_at"]})
        return row

    def revoke(self, workspace_id: str, request_id: str, actor: str) -> dict:
        self._require_owner(actor, workspace_id)
        row = self._load(workspace_id, request_id)
        if row["status"] not in ("pending", "active"):
            raise ValidationError("break-glass request is already closed")
        row["status"] = "revoked"
        row["revoked_at"] = time.time()
        self._store.put(BREAK_GLASS_KIND, workspace_id, row["id"], row)
        self.ctx.audit_event(actor, workspace_id, "security.breakglass_revoked",
                             row["id"], {"status": "revoked"})
        return row

    def _load(self, workspace_id: str, request_id: str) -> dict:
        row = self._store.get(BREAK_GLASS_KIND, workspace_id, request_id)
        if row is None:
            raise NotFoundError(
                f"break-glass request not found: {request_id!r}")
        return row

    def active_roles(self, workspace_id: str, actor: str) -> list[dict]:
        now = time.time()
        rows: list[dict] = []
        for row in self._store.list(BREAK_GLASS_KIND, workspace_id):
            if row.get("requester") != actor or row.get("status") != "active":
                continue
            expiry = row.get("expires_at")
            if expiry is None or float(expiry) <= now:
                continue
            rows.append(row)
        return rows

    def list(self, workspace_id: str, actor: str) -> list[dict]:
        self._require_owner(actor, workspace_id)
        return self._store.list(BREAK_GLASS_KIND, workspace_id)
