"""Standard-format adapter plugins (P0: RDF/OWL + SKOS export).

This package is part of the platform *core distribution* but has no
third-party runtime dependencies.  Adapters are discovered through
``PluginRegistry`` and only see a read-only ``AdapterContext``.
"""

from __future__ import annotations

from .context import AdapterContext, OntologyView, SnapshotView, build_context
from .iri import DEFAULT_BASE, PREFIXES, IriPolicy, iri_policy_for
from .registry import (
    BUILTIN_ADAPTERS,
    ENTRY_POINT_GROUP,
    LIFECYCLE_GROUP,
    PluginRegistry,
    default_registry,
    reset_default_registry,
)
from .spi import (
    ADAPTER_API_VERSION,
    CAPABILITY_KINDS,
    SCOPE_KINDS,
    AdapterError,
    AdapterIncompatible,
    AdapterManifest,
    AdapterNotFound,
    Artifact,
    ExportRequest,
    ImportResult,
    QueryResult,
    StandardAdapter,
    ValidationReport,
    manifest_problems,
)

__all__ = [
    "ADAPTER_API_VERSION",
    "CAPABILITY_KINDS",
    "SCOPE_KINDS",
    "AdapterError",
    "AdapterIncompatible",
    "AdapterManifest",
    "AdapterNotFound",
    "Artifact",
    "ExportRequest",
    "ImportResult",
    "QueryResult",
    "StandardAdapter",
    "ValidationReport",
    "manifest_problems",
    "DEFAULT_BASE",
    "PREFIXES",
    "IriPolicy",
    "iri_policy_for",
    "AdapterContext",
    "OntologyView",
    "SnapshotView",
    "build_context",
    "BUILTIN_ADAPTERS",
    "ENTRY_POINT_GROUP",
    "LIFECYCLE_GROUP",
    "PluginRegistry",
    "default_registry",
    "reset_default_registry",
]
