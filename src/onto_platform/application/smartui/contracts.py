"""Plugin manifest contract for Smart UI.

A manifest is deliberately small: identity, compatibility version, the
contribution kind and the perspectives a view can render.  Implementations
stay in Python objects registered with the host; manifests are what the
artifact lock and the API expose.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ...errors import ValidationError
from .version import compatible, parse_api_version, satisfies

#: Default API version written by built-in contributions.  It is a *minimum*
#: version: a manifest declaring v1 runs on a v1.x host.  Use
#: ``api_versions`` for an explicit range.
PLUGIN_API_VERSION = "smartui/v1"
#: The API version this host implements.
HOST_API_VERSION = "smartui/v1.2"

PLUGIN_KINDS = frozenset({
    "context",
    "generator",
    "prompt",
    "adjudicator",
    "view",
    "renderer",
    "publisher",
    "ui",
})

#: Permissions a plugin may request.  The host only lets a contribution run
#: when the requested permission is declared by its manifest.
PERMISSION_CONTEXT_READ = "context.read"
PERMISSION_PROPOSAL_GENERATE = "proposal.generate"
PERMISSION_PROMPT_RENDER = "prompt.render"
PERMISSION_PROPOSAL_ADJUDICATE = "proposal.adjudicate"
PERMISSION_VIEW_RENDER = "view.render"
PERMISSION_RENDER_COMPONENT = "renderer.component"
PERMISSION_ARTIFACT_PUBLISH = "artifact.publish"
PERMISSION_UI_SLOT = "ui.slot"
PERMISSION_PACK_BIND = "pack.bind"

PLUGIN_PERMISSIONS = frozenset({
    PERMISSION_CONTEXT_READ,
    PERMISSION_PROPOSAL_GENERATE,
    PERMISSION_PROMPT_RENDER,
    PERMISSION_PROPOSAL_ADJUDICATE,
    PERMISSION_VIEW_RENDER,
    PERMISSION_RENDER_COMPONENT,
    PERMISSION_ARTIFACT_PUBLISH,
    PERMISSION_UI_SLOT,
    PERMISSION_PACK_BIND,
})

_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)+$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")


@dataclass(frozen=True)
class PluginManifest:
    """Static metadata every plugin must declare."""

    id: str
    kind: str
    version: str = "1.0.0"
    api_version: str = PLUGIN_API_VERSION
    summary: str = ""
    perspectives: tuple[str, ...] = ()
    view_type: str = ""
    component: str = ""
    order: int = 100
    builtin: bool = True
    permissions: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    #: Optional API constraints.  When empty, ``api_version`` is treated as a
    #: minimum v1.x version.  Example: (">=smartui/v1 <smartui/v2",).
    api_versions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _ID_RE.match(self.id):
            raise ValidationError(f"invalid plugin id: {self.id!r}")
        if self.kind not in PLUGIN_KINDS:
            raise ValidationError(
                f"invalid plugin kind {self.kind!r}; expected one of {sorted(PLUGIN_KINDS)}")
        if self.kind == "renderer" and (not self.view_type or not self.component):
            raise ValidationError(
                f"renderer plugin {self.id} must declare view_type and component")
        if not _VERSION_RE.match(self.version):
            raise ValidationError(f"invalid plugin version: {self.version!r}")
        try:
            parse_api_version(self.api_version)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        for constraint in self.api_versions:
            try:
                satisfies(HOST_API_VERSION, constraint)
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
        unknown = set(self.permissions) - PLUGIN_PERMISSIONS
        if unknown:
            raise ValidationError(
                f"plugin {self.id} requests unknown permissions: {sorted(unknown)}")
        if not self.api_versions and not compatible(
                self.api_version, HOST_API_VERSION):
            raise ValidationError(
                f"plugin {self.id} targets {self.api_version!r}, host speaks "
                f"{HOST_API_VERSION!r}")

    def compatible_with(self, host_version: str = HOST_API_VERSION) -> bool:
        if self.api_versions:
            return any(satisfies(host_version, item)
                       for item in self.api_versions)
        return compatible(self.api_version, host_version)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "version": self.version,
            "apiVersion": self.api_version,
            "apiVersions": list(self.api_versions),
            "summary": self.summary,
            "perspectives": list(self.perspectives),
            "viewType": self.view_type,
            "component": self.component,
            "order": self.order,
            "builtin": self.builtin,
            "permissions": list(self.permissions),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PluginManifest":
        if not isinstance(data, dict):
            raise ValidationError("plugin manifest must be a JSON object")
        return cls(
            id=str(data.get("id") or ""),
            kind=str(data.get("kind") or ""),
            version=str(data.get("version") or "1.0.0"),
            api_version=str(data.get("apiVersion") or data.get("api_version")
                           or PLUGIN_API_VERSION),
            summary=str(data.get("summary") or ""),
            perspectives=tuple(str(item) for item in data.get("perspectives") or ()),
            view_type=str(data.get("viewType") or data.get("view_type") or ""),
            component=str(data.get("component") or ""),
            order=int(data.get("order") or 100),
            builtin=bool(data.get("builtin", True)),
            permissions=tuple(str(item) for item in data.get("permissions") or ()),
            metadata=dict(data.get("metadata") or {}),
            api_versions=tuple(
                str(item) for item in data.get("apiVersions")
                or data.get("api_versions") or ()),
        )


@dataclass(frozen=True)
class PluginLockEntry:
    """One pinned plugin version in a generated artifact."""

    id: str
    version: str
    kind: str
    api_version: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"id": self.id, "version": self.version, "kind": self.kind,
                "apiVersion": self.api_version}
