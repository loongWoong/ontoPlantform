"""In-process plugin host for Smart UI.

The host deliberately stays boring: registration, enable/disable, deterministic
lookup, permission checks and an execution lock.  There is no remote code
loading here; built-in adapters are registered by :mod:`builtin`, and
operator-installed code goes through the explicit external trust boundary.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable

from ...errors import NotFoundError, ValidationError
from .contracts import (
    HOST_API_VERSION,
    PluginLockEntry,
    PluginManifest,
)
from .descriptors import EngineDescriptorRegistry
from .external import external_plugins_enabled
from .packs import WorkspacePack, WorkspacePackCatalog
from .renderers import RendererRegistry


@dataclass
class PluginRecord:
    """Manifest plus the optional runtime implementation object."""

    manifest: PluginManifest
    implementation: Any = None
    enabled: bool = True


class PluginHost:
    """Registry and lifecycle for Smart-UI contribution points."""

    def __init__(self, packs: WorkspacePackCatalog | None = None) -> None:
        self._records: dict[str, PluginRecord] = {}
        self.packs = packs or WorkspacePackCatalog()
        self.host_api_version = HOST_API_VERSION
        self.engine_descriptors = EngineDescriptorRegistry()
        self.renderers = RendererRegistry()

    # -- registration -----------------------------------------------------

    def register(self, manifest: PluginManifest,
                 implementation: Any = None,
                 *, enabled: bool = True,
                 trusted: bool | None = None) -> PluginRecord:
        if manifest.id in self._records:
            raise ValidationError(f"plugin already registered: {manifest.id}")
        if not manifest.compatible_with(self.host_api_version):
            raise ValidationError(
                f"plugin {manifest.id} targets {manifest.api_version!r}, host "
                f"speaks {self.host_api_version!r}")
        # Built-ins are trusted by construction.  Non-builtin code only enters
        # through the signed external package loader, which passes trusted=True
        # after signature verification; a plain ``register`` cannot smuggle a
        # third-party implementation into the in-process host.
        trusted = manifest.builtin if trusted is None else bool(trusted)
        if not manifest.builtin and not trusted:
            raise ValidationError(
                f"external plugin {manifest.id} must be installed through the "
                "signed package boundary")
        record = PluginRecord(manifest=manifest, implementation=implementation,
                              enabled=enabled)
        self._records[manifest.id] = record
        if enabled:
            try:
                self.engine_descriptors.register(manifest)
                self.renderers.register(manifest, implementation)
            except Exception:
                self._records.pop(manifest.id, None)
                self.engine_descriptors.unregister(manifest.id)
                self.renderers.unregister(manifest.id)
                raise
        return record

    def unregister(self, plugin_id: str) -> None:
        self._records.pop(plugin_id, None)
        self.engine_descriptors.unregister(plugin_id)
        self.renderers.unregister(plugin_id)

    # -- lookup -----------------------------------------------------------

    def get(self, plugin_id: str) -> PluginRecord:
        record = self._records.get(plugin_id)
        if record is None:
            raise NotFoundError(f"plugin not found: {plugin_id}")
        return record

    def authorize(self, plugin_id: str, permission: str | None = None) -> PluginRecord:
        """Return a runnable record or raise a validation error.

        ``permission`` is enforced against the manifest declaration; a plugin
        cannot silently start using a capability it did not ask for.  The
        built-in/external trust decision is separate and deliberately explicit.
        """
        record = self.get(plugin_id)
        if not record.enabled:
            raise ValidationError(f"plugin is disabled: {plugin_id}")
        if record.implementation is None:
            raise ValidationError(f"plugin has no implementation: {plugin_id}")
        if permission is not None and permission not in record.manifest.permissions:
            raise ValidationError(
                f"plugin {plugin_id} did not declare permission {permission!r}")
        return record

    def implementation(self, plugin_id: str,
                       *, permission: str | None = None) -> Any:
        return self.authorize(plugin_id, permission).implementation

    def find(self, kind: str | None = None,
             *, perspective: str | None = None,
             permission: str | None = None,
             enabled_only: bool = True) -> list[PluginRecord]:
        records = [
            record for record in self._records.values()
            if (kind is None or record.manifest.kind == kind)
            and (not enabled_only or record.enabled)
            and (permission is None
                 or permission in record.manifest.permissions)
            and (perspective is None
                 or perspective in record.manifest.perspectives)
        ]
        records.sort(key=lambda record: (record.manifest.kind,
                                         record.manifest.order,
                                         record.manifest.id))
        return records

    def has(self, plugin_id: str) -> bool:
        return plugin_id in self._records

    # -- lifecycle --------------------------------------------------------

    def set_enabled(self, plugin_id: str, enabled: bool) -> PluginRecord:
        record = self.get(plugin_id)
        was_enabled = record.enabled
        record.enabled = bool(enabled)
        if record.enabled:
            try:
                self.engine_descriptors.register(record.manifest)
                self.renderers.register(record.manifest, record.implementation)
            except Exception:
                record.enabled = was_enabled
                raise
        else:
            self.engine_descriptors.unregister(plugin_id)
            self.renderers.unregister(plugin_id)
        return record

    def enable(self, plugin_id: str) -> PluginRecord:
        return self.set_enabled(plugin_id, True)

    def disable(self, plugin_id: str) -> PluginRecord:
        return self.set_enabled(plugin_id, False)

    # -- artifact / API views --------------------------------------------

    def lock(self) -> list[PluginLockEntry]:
        """Registry lock: every enabled manifest, implemented or not."""
        entries = [
            PluginLockEntry(id=record.manifest.id,
                            version=record.manifest.version,
                            kind=record.manifest.kind,
                            api_version=record.manifest.api_version)
            for record in self._records.values() if record.enabled
        ]
        entries.sort(key=lambda entry: (entry.kind, entry.id))
        return entries

    def execution_lock(self) -> list[PluginLockEntry]:
        """Execution lock: only enabled contributions with an implementation."""
        entries = [
            PluginLockEntry(id=record.manifest.id,
                            version=record.manifest.version,
                            kind=record.manifest.kind,
                            api_version=record.manifest.api_version)
            for record in self._records.values()
            if record.enabled and record.implementation is not None
        ]
        entries.sort(key=lambda entry: (entry.kind, entry.id))
        return entries

    def lock_dicts(self) -> list[dict[str, str]]:
        return [entry.as_dict() for entry in self.lock()]

    def execution_lock_dicts(self) -> list[dict[str, str]]:
        return [entry.as_dict() for entry in self.execution_lock()]

    def summary(self) -> list[dict[str, Any]]:
        records = sorted(self._records.values(),
                         key=lambda record: (record.manifest.kind,
                                             record.manifest.order,
                                             record.manifest.id))
        return [
            {
                **record.manifest.as_dict(),
                "enabled": record.enabled,
                "implemented": record.implementation is not None,
            }
            for record in records
        ]

    def ui_manifest(self) -> dict[str, Any]:
        manifest: dict[str, Any] | None = None
        for record in self.find("ui"):
            implementation = record.implementation
            if implementation is not None and hasattr(implementation, "manifest"):
                manifest = implementation.manifest()
                break
        if manifest is None:
            manifest = {"slots": [], "renderers": [], "remoteScripts": False}
        # Renderer contributions are authoritative; the UI provider's list is
        # only a fallback for a host built without renderer plugins.
        renderers = self.renderers.as_dicts()
        if renderers:
            manifest = {**manifest, "renderers": renderers}
        return manifest

    def sbom(self) -> dict[str, Any]:
        """Deterministic software bill of materials for the plugin registry.

        The report is an *inventory*, not a sandbox: it states explicitly that
        contributions run in-process and exposes each manifest digest so an
        operator can detect drift.  Runtime implementation objects are never
        serialized (their ``repr`` could leak paths or secrets).
        """
        components: list[dict[str, Any]] = []
        for record in sorted(
                self._records.values(),
                key=lambda item: (item.manifest.kind, item.manifest.id)):
            manifest = record.manifest
            canonical = json.dumps(
                manifest.as_dict(), ensure_ascii=False, sort_keys=True,
                separators=(",", ":"))
            components.append({
                "type": "library",
                "name": manifest.id,
                "version": manifest.version,
                "kind": manifest.kind,
                "apiVersion": manifest.api_version,
                "builtin": manifest.builtin,
                "enabled": bool(record.enabled),
                "implemented": record.implementation is not None,
                "permissions": list(manifest.permissions),
                "manifestDigest": hashlib.sha256(
                    canonical.encode("utf-8")).hexdigest(),
            })
        return {
            "bomFormat": "OntoPlatform-SBOM",
            "specVersion": "1.0",
            "hostApiVersion": self.host_api_version,
            "trustBoundary": "in-process",
            "processIsolation": False,
            "externalPluginsEnabled": external_plugins_enabled(),
            "components": components,
            "dependencies": [],
        }

    def renderer_dicts(self) -> list[dict[str, Any]]:
        return self.renderers.as_dicts()

    def renderer(self, view_type: str) -> dict[str, Any] | None:
        descriptor = self.renderers.get(view_type)
        return descriptor.as_dict() if descriptor is not None else None

    def engine_descriptor_dicts(self) -> list[dict[str, Any]]:
        return self.engine_descriptors.as_dicts()

    # -- declarative workspace packs -------------------------------------

    def register_pack(self, pack: WorkspacePack) -> WorkspacePack:
        return self.packs.register(pack)

    def pack(self, pack_id: str) -> WorkspacePack:
        return self.packs.get(pack_id)

    def pack_summaries(self) -> list[dict[str, Any]]:
        return self.packs.summaries()

    def register_many(self, items: Iterable[tuple[PluginManifest, Any]]) -> None:
        for manifest, implementation in items:
            self.register(manifest, implementation)
