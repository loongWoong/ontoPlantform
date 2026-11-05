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

import importlib.metadata
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..errors import (  # noqa: F401 - AdapterInvocationError is re-exported
    AdapterInvocationError,
    NotFoundError,
    ValidationError,
)

#: SPI version of this contract; adapters declare a compatible major.
ADAPTER_API_VERSION = "1.0"

#: capabilities an adapter may declare in its manifest.
CAPABILITY_KINDS: tuple[str, ...] = ("export", "validate", "query", "import")

#: export scopes understood by P0 adapters.
SCOPE_KINDS: tuple[str, ...] = ("tbox", "abox", "both")

#: keys allowed in ``manifest.requires`` (architecture §2.2)
REQUIRES_KEYS: tuple[str, ...] = ("ontoplatform", "extras")

_ADAPTER_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_VERSION = re.compile(r"^\d+(?:\.\d+){0,2}(?:[-+][0-9A-Za-z.-]+)?$")
#: grammar of ``requires.ontoplatform``: optional operator + ``x.y[.z]``
_ONTOLOGY_SPEC = re.compile(r"^(>=|<=|==|>|<)?\s*\d+(?:\.\d+){0,2}$")

_LOGGER = logging.getLogger(__name__)


class AdapterError(ValidationError):
    """An adapter request is invalid (maps to HTTP 400)."""


class AdapterIncompatible(AdapterError):
    """The adapter's ``api_version`` cannot work with this platform."""


class AdapterNotFound(NotFoundError):
    """The requested adapter (or format) is not registered."""


def _major(version: str) -> str:
    return str(version or "").split(".")[0].strip()


def platform_version() -> str:
    """The running platform version used for ``requires.ontoplatform``.

    Read lazily from the package (``onto_platform.__version__``) so this
    module stays importable on its own; falls back to installed metadata and
    finally to ``"0.0.0"`` when neither is available.
    """
    try:
        from .. import __version__ as version
    except Exception:  # pragma: no cover - the package is the normal case
        version = ""
    if version:
        return str(version)
    try:  # pragma: no cover - only when imported outside the distribution
        return str(importlib.metadata.version("onto-platform"))
    except Exception:  # pragma: no cover
        return "0.0.0"


def _version_tuple(text: str) -> tuple[int, int, int] | None:
    """Parse ``x[.y[.z]]`` into a comparable tuple (``None`` if unparseable)."""
    match = re.match(r"^\s*(\d+)(?:\.(\d+))?(?:\.(\d+))?\s*$",
                     str(text or ""))
    if match is None:
        return None
    return tuple(int(part or 0) for part in match.groups())  # type: ignore[return-value]


def _parse_version_spec(spec: str) -> tuple[str, tuple[int, int, int]] | None:
    """Split ``">=0.3.1"`` into ``(">=", (0, 3, 1))``; ``None`` if unsupported."""
    match = re.match(r"^(>=|<=|==|>|<)?\s*(\d+)(?:\.(\d+))?(?:\.(\d+))?$",
                     str(spec or "").strip())
    if match is None:
        return None
    operator = match.group(1) or "=="  # a bare version means an exact match
    version = tuple(int(part or 0) for part in match.group(2, 3, 4))
    return operator, version  # type: ignore[return-value]


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
    #: platform/runtime requirements (architecture §2.2); only
    #: ``{"ontoplatform": ">=0.3", "extras": ["rdflib>=7"]}`` is understood.
    requires: dict = field(default_factory=dict)
    #: declaration of adapter configuration; opaque to the P0 platform.
    config_schema: dict = field(default_factory=dict)
    #: compatibility names, e.g. the architecture's package name
    #: ``onto-standards-owl`` for the canonical id ``owl-rdf`` (decision D1).
    aliases: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        requires = self.requires if isinstance(self.requires, dict) else {}
        extras = requires.get("extras") or ()
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
            # architecture §2.2 (decision D3): grouped views of the same data;
            # the flat keys above stay for existing consumers.
            "formats": {
                "out": list(self.formats_out),
                "in": list(self.formats_in),
            },
            "requires": {
                "ontoplatform": str(requires.get("ontoplatform", "") or ""),
                "extras": [str(extra) for extra in extras],
            },
            "config_schema": dict(self.config_schema)
            if isinstance(self.config_schema, dict) else {},
            "aliases": [str(alias) for alias in self.aliases]
            if isinstance(self.aliases, (tuple, list)) else [],
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
    if manifest.permissions.get("write", False) is not False:
        problems.append("v1 adapters must not request write permission")
    if manifest.permissions.get("network", False):
        problems.append("v1 adapters must not request network permission")
    problems.extend(requires_problems(manifest))
    if not isinstance(manifest.config_schema, dict):
        problems.append(
            "manifest.config_schema must be a dict, got "
            f"{type(manifest.config_schema).__name__}")
    aliases = manifest.aliases
    if isinstance(aliases, str) or not isinstance(aliases, (tuple, list)):
        problems.append("manifest.aliases must be a sequence of adapter names")
    else:
        seen: set[str] = set()
        for alias in aliases:
            if not isinstance(alias, str) or not _ADAPTER_NAME.match(alias):
                problems.append(
                    f"manifest.alias {alias!r} must match "
                    f"{_ADAPTER_NAME.pattern}")
                continue
            if alias == manifest.name:
                problems.append(
                    "manifest.aliases must not repeat the adapter name")
            if alias in seen:
                problems.append(f"duplicate manifest alias {alias!r}")
            seen.add(alias)
    return problems


def requires_problems(manifest: AdapterManifest) -> list[str]:
    """Structural problems of the ``requires`` block (not compatibility).

    Compatibility with *this* platform is :func:`requires_compatible`; this
    function only judges whether the declaration itself is well formed, so a
    plugin author sees a manifest error independent of the platform they run
    on (and can be listed on a bigger platform's registry).
    """
    requires = getattr(manifest, "requires", None)
    if requires is None:
        return ["manifest.requires must be a dict"]
    if not isinstance(requires, dict):
        return [
            "manifest.requires must be a dict, got "
            f"{type(requires).__name__}"]
    problems: list[str] = []
    unknown = sorted(str(key) for key in set(requires) - set(REQUIRES_KEYS))
    if unknown:
        problems.append(
            f"manifest.requires has unsupported keys {unknown}; expected "
            f"{list(REQUIRES_KEYS)}")
    constraint = requires.get("ontoplatform", "")
    if not isinstance(constraint, str):
        problems.append("manifest.requires.ontoplatform must be a string")
    elif constraint.strip() and not _ONTOLOGY_SPEC.match(constraint.strip()):
        problems.append(
            f"manifest.requires.ontoplatform {constraint!r} is not supported; "
            "expected e.g. '>=0.3' (operators: >=, <=, ==, >, <)")
    extras = requires.get("extras", [])
    if isinstance(extras, str) or not isinstance(extras, (list, tuple)):
        problems.append("manifest.requires.extras must be a list of strings")
    else:
        for extra in extras:
            if not isinstance(extra, str) or not extra.strip():
                problems.append(
                    "manifest.requires.extras entries must be non-empty "
                    "strings")
    return problems


def requires_compatible(manifest: AdapterManifest) -> tuple[bool, str]:
    """Whether this platform satisfies ``manifest.requires.ontoplatform``.

    Returns ``(compatible, reason)`` with an empty ``reason`` when there is
    nothing to report.  A constraint this implementation cannot parse, or a
    platform version it cannot compare (e.g. a ``0.3.0-dev`` build), is
    treated as **compatible** and described in ``reason`` (also logged as a
    warning): a plugin must never be blocked by the platform's own inability
    to evaluate the check (fail-open, decision D3).
    """
    requires = getattr(manifest, "requires", None) or {}
    if not isinstance(requires, dict):
        return True, ""
    constraint = str(requires.get("ontoplatform", "") or "").strip()
    if not constraint:
        return True, ""
    parsed = _parse_version_spec(constraint)
    if parsed is None:
        reason = (
            f"cannot verify requires.ontoplatform {constraint!r}: unsupported "
            "version constraint; assuming compatible")
        _LOGGER.warning(reason)
        return True, reason
    operator, wanted = parsed
    current = _version_tuple(platform_version())
    if current is None:
        reason = (
            f"cannot verify requires.ontoplatform {constraint!r} against "
            f"platform version {platform_version()!r}; assuming compatible")
        _LOGGER.warning(reason)
        return True, reason
    satisfied = {
        "==": current == wanted,
        ">=": current >= wanted,
        "<=": current <= wanted,
        ">": current > wanted,
        "<": current < wanted,
    }[operator]
    if satisfied:
        return True, ""
    return False, (
        f"requires ontoplatform {constraint!r}, which is incompatible with "
        f"this platform ({platform_version()})")



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
