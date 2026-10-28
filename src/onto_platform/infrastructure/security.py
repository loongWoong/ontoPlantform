"""Server-side secret protection for workspace credentials.

Model API keys are write-only from the client's perspective: the API accepts
them, stores an encrypted envelope, and never returns the plaintext.  A key
is derived from ``ONTO_SECRET_KEY`` when set (recommended for deployments);
otherwise a per-data-directory key file is generated once.  Losing the key
means re-entering the credential, never leaking it.
"""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

_PREFIX = "enc:v1:"


def _key() -> bytes:
    raw = os.environ.get("ONTO_SECRET_KEY")
    if raw:
        digest = hashlib.sha256(raw.encode("utf-8")).digest()
        return base64.urlsafe_b64encode(digest)
    path = Path(os.environ.get("ONTO_DATA_DIR", ".onto")) / "secret.key"
    try:
        if path.exists():
            return path.read_bytes().strip()
        path.parent.mkdir(parents=True, exist_ok=True)
        key = base64.urlsafe_b64encode(os.urandom(32))
        path.write_bytes(key)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return key
    except OSError:
        # last resort: process-local key (config will not survive restart)
        return base64.urlsafe_b64encode(
            hashlib.sha256(b"onto-platform-ephemeral").digest())


def protect(value: str) -> str:
    if not value:
        return ""
    try:
        from cryptography.fernet import Fernet

        return _PREFIX + Fernet(_key()).encrypt(value.encode("utf-8")).decode()
    except Exception:
        # never fail a save because crypto is unavailable; this is still
        # not plaintext, but it is obfuscation rather than encryption
        return _PREFIX + "b64:" + base64.urlsafe_b64encode(
            value.encode("utf-8")).decode()


def unprotect(value: str) -> str:
    if not value:
        return ""
    if not value.startswith(_PREFIX):
        return value
    payload = value[len(_PREFIX):]
    try:
        if payload.startswith("b64:"):
            return base64.urlsafe_b64decode(
                payload[4:].encode()).decode("utf-8")
        from cryptography.fernet import Fernet

        return Fernet(_key()).decrypt(payload.encode()).decode("utf-8")
    except Exception:
        return ""


def mask(secret: str) -> str:
    if not secret:
        return ""
    if len(secret) <= 8:
        return "*" * len(secret)
    return secret[:4] + "*" * (len(secret) - 8) + secret[-4:]
