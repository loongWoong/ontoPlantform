"""Signed external plugin boundary for Smart-UI.

There is deliberately no remote plugin URL and no auto-discovery here.  An
operator installs a local package directory containing ``manifest.json`` and
``plugin.py``; the package is admitted only when:

* ``ONTO_SMARTUI_EXTERNAL_PLUGINS=1`` is set,
* ``ONTO_SMARTUI_PLUGIN_SECRET`` is configured,
* the HMAC-SHA256 signature over the canonical manifest + source matches,
* the manifest declares ``builtin: false`` and narrow permissions.

The code still runs in-process.  This is a trust boundary, not a process
sandbox; the documentation says so explicitly.  The deterministic compiler and
adjudication chain remain core-owned, so even a malicious generator can only
propose a blueprint that the platform validates.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...errors import ValidationError
from .contracts import PluginManifest


class ExternalPluginError(ValidationError):
    """An external package failed admission."""


def external_plugins_enabled() -> bool:
    return os.environ.get("ONTO_SMARTUI_EXTERNAL_PLUGINS", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _secret() -> str:
    return os.environ.get("ONTO_SMARTUI_PLUGIN_SECRET", "")


def _canonical_payload(manifest_raw: dict[str, Any], source: bytes) -> bytes:
    canonical = json.dumps(
        manifest_raw, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return canonical + b"\0" + source


def sign_package(manifest_raw: dict[str, Any], source: bytes,
                 secret: str) -> str:
    """Helper for operators/tests; the server never derives the secret itself."""
    if not secret:
        raise ExternalPluginError("a non-empty signing secret is required")
    return hmac.new(secret.encode("utf-8"),
                    _canonical_payload(manifest_raw, source),
                    hashlib.sha256).hexdigest()


def verify_package(manifest_raw: dict[str, Any], source: bytes,
                   signature: str, secret: str) -> None:
    if not secret:
        raise ExternalPluginError(
            "ONTO_SMARTUI_PLUGIN_SECRET is required to verify external plugins")
    expected = sign_package(manifest_raw, source, secret)
    if not hmac.compare_digest(expected, str(signature or "").strip().lower()):
        raise ExternalPluginError("external plugin signature verification failed")


@dataclass(frozen=True)
class ExternalPackage:
    manifest: PluginManifest
    manifest_raw: dict[str, Any]
    source: bytes
    path: Path


def read_package(package_dir: str | Path, signature: str) -> ExternalPackage:
    """Read and verify one local package; never executes it yet."""
    if not external_plugins_enabled():
        raise ExternalPluginError(
            "external plugins are disabled; set ONTO_SMARTUI_EXTERNAL_PLUGINS=1")
    root = Path(package_dir).expanduser().resolve()
    manifest_path = (root / "manifest.json").resolve()
    plugin_path = (root / "plugin.py").resolve()
    if root not in manifest_path.parents or root not in plugin_path.parents:
        raise ExternalPluginError("external package files must stay inside the package")
    if not manifest_path.is_file() or not plugin_path.is_file():
        raise ExternalPluginError(
            "external package must contain manifest.json and plugin.py")
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ExternalPluginError(f"invalid external manifest: {exc}") from exc
    if not isinstance(raw, dict):
        raise ExternalPluginError("external manifest must be a JSON object")
    source = plugin_path.read_bytes()
    verify_package(raw, source, signature, _secret())
    manifest = PluginManifest.from_dict(raw)
    if manifest.builtin:
        raise ExternalPluginError(
            "external packages must declare builtin=false")
    if not manifest.permissions:
        raise ExternalPluginError(
            "external packages must declare explicit permissions")
    return ExternalPackage(manifest=manifest, manifest_raw=raw,
                           source=source, path=root)


def install_external_plugin(host: Any, package_dir: str | Path,
                            signature: str) -> dict[str, Any]:
    """Verify a package, import its plugin.py, and register its factory result.

    The import is intentionally local-only and happens after signature
    verification.  A package can contribute any kind, but core adjudication,
    compile and publish still run outside the contribution.
    """
    package = read_package(package_dir, signature)
    module_name = f"onto_smartui_external_{package.manifest.id.replace('.', '_')}"
    spec = importlib.util.spec_from_file_location(
        module_name, package.path / "plugin.py")
    if spec is None or spec.loader is None:
        raise ExternalPluginError("could not load external plugin source")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise ExternalPluginError(f"external plugin import failed: {exc}") from exc
    factory = getattr(module, "create_plugin", None)
    if not callable(factory):
        raise ExternalPluginError(
            "external plugin.py must expose callable create_plugin()")
    try:
        implementation = factory()
    except Exception as exc:
        raise ExternalPluginError(f"external plugin factory failed: {exc}") from exc
    if implementation is None:
        raise ExternalPluginError("external plugin factory returned None")
    record = host.register(package.manifest, implementation, trusted=True)
    return {
        "id": record.manifest.id,
        "kind": record.manifest.kind,
        "version": record.manifest.version,
        "permissions": list(record.manifest.permissions),
        "path": str(package.path),
    }
