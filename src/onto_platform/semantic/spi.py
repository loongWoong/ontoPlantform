"""Standard adapter SPI: the plugin contract for standard-format adapters.

The platform core must stay dependency-free (``pyproject``: ``dependencies
= []``).  Standard-format support (RDF/OWL/SKOS/SHACL/SPARQL) therefore
lives in adapters that implement this small protocol and are discovered via
``PluginRegistry``.  An adapter only ever sees a read-only
``AdapterContext``; it never touches the repository, the runtime kernel or
the bootstrap composition root.

Semantic red lines (see docs/standards-plugin-architecture.md §2.5):
platform runtime constraints are **not** OWL axioms.  ``required`` /
``enum`` / ``min`` / ``max`` / ``unique`` belong to SHACL (P1), while OWL
exports only structural statements (classes, object/datatype properties,
``rdfs:domain``/``rdfs:range``, ``owl:inverseOf``).  In particular an
adapter must not emit ``owl:hasKey`` for platform identity (that would
assert same-individual semantics) or OWL cardinality restrictions for
platform cardinality facets.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..errors import NotFoundError, ValidationError

#: SPI version of this contract; adapters declare a compatible major.
ADAPTER_API_VERSION = "1.0"

#: capabilities an adapter may declare in its manifest.
CAPABILITY_KINDS: tuple[str, ...] = ("export", "validate", "query", "import")

#: export scopes understood by P0 adapters.
SCOPE_KINDS: tuple[str, ...] = ("tbox", "abox", "both")

_ADAPTER_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_VERSION = re.compile(r"^\d+(?:\.\d+){0,2}(?:[-+][0-9A-Za-z.-]+)?$")


class AdapterError(ValidationError):
    """An adapter request is invalid (maps to HTTP 400)."""


class AdapterIncompatible(AdapterError):
    """The adapter's ``api_version`` cannot work with this platform."""


class AdapterNotFound(NotFoundError):
    """The requested adapter (or format) is not registered."""


def _major(version: str) -> str:
    return str(version or "").split(".")[0].strip()


@dataclass(frozen=True, slots=True)
class AdapterManifest:
    """Identity + capability declaration of one standard adapter."""

    name: str
    version: str
    api_version: str
    standards: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    formats_out: tuple[str, ...] = ()
    formats_in: tuple[str, ...] = ()
    description: str = ""
    #: v1 policy: adapters are read-only, no network, no writes.
    permissions: dict[str, bool] = field(
        default_factory=lambda: {"network": False, "write": False})

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "api_version": self.api_version,
            "standards": list(self.standards),
            "capabilities": list(self.capabilities),
            "formats_out": list(self.formats_out),
            "formats_in": list(self.formats_in),
            "description": self.description,
            "permissions": dict(self.permissions),
        }


def manifest_problems(manifest: AdapterManifest) -> list[str]:
    """Return a list of manifest contract violations (empty = valid)."""
    if not isinstance(manifest, AdapterManifest):
        return [f"manifest must be AdapterManifest, got {type(manifest).__name__}"]
    problems: list[str] = []
    if not _ADAPTER_NAME.match(manifest.name or ""):
        problems.append(
            f"manifest.name {manifest.name!r} must match {_ADAPTER_NAME.pattern}")
    if not _VERSION.match(manifest.version or ""):
        problems.append(f"manifest.version {manifest.version!r} is not a version")
    if _major(manifest.api_version) != _major(ADAPTER_API_VERSION):
        problems.append(
            f"manifest.api_version {manifest.api_version!r} is incompatible "
            f"with platform SPI {ADAPTER_API_VERSION!r}")
    if not manifest.capabilities:
        problems.append("manifest.capabilities must not be empty")
    for cap in manifest.capabilities:
        if cap not in CAPABILITY_KINDS:
            problems.append(f"unknown capability {cap!r}; expected {CAPABILITY_KINDS}")
    for standard in manifest.standards:
        if not isinstance(standard, str) or not standard.strip():
            problems.append("manifest.standards entries must be non-empty strings")
            break
    if "export" in manifest.capabilities and not manifest.formats_out:
        problems.append("an export adapter must declare formats_out")
    if "export" not in manifest.capabilities and manifest.formats_out:
        problems.append("formats_out declared without the export capability")
    if "import" in manifest.capabilities and not manifest.formats_in:
        problems.append("an import adapter must declare formats_in")
    if not manifest.permissions.get("write", False) is False:
        problems.append("v1 adapters must not request write permission")
    if manifest.permissions.get("network", False):
        problems.append("v1 adapters must not request network permission")
    return problems


@dataclass(frozen=True, slots=True)
class ExportRequest:
    """One export call: requested media type and scope."""

    format: str = ""
    scope: str = "tbox"
    include_abox: bool = False


@dataclass(frozen=True, slots=True)
class Artifact:
    """A standard-format document produced by an adapter."""

    content: bytes
    media_type: str
    filename: str = ""
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.content, (bytes, bytearray)):
            raise AdapterError("Artifact.content must be bytes")
        if not self.media_type:
            raise AdapterError("Artifact.media_type must not be empty")
        object.__setattr__(self, "content", bytes(self.content))


@dataclass(frozen=True, slots=True)
class ValidationReport:
    ok: bool
    violations: tuple[dict, ...] = ()


@dataclass(frozen=True, slots=True)
class QueryResult:
    rows: tuple[dict, ...] = ()
    variables: tuple[str, ...] = ()
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class ImportResult:
    patch: dict = field(default_factory=dict)
    conflicts: tuple[dict, ...] = ()


@runtime_checkable
class StandardAdapter(Protocol):
    """The only surface the platform knows about a standard adapter.

    Only ``export`` is required by the P0 SPI; ``validate``, ``query`` and
    ``import_`` are optional methods advertised through
    ``manifest.capabilities`` and dispatched by the registry/service layer.
    """

    manifest: AdapterManifest

    def export(self, ctx: Any, request: ExportRequest) -> Artifact: ...
