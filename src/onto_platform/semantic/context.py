"""Read-only context handed to standard-format adapter plugins.

Adapters never receive repository, gateway or kernel handles.  The platform
builds a frozen :class:`AdapterContext` from a published ``Ontology``
aggregate:

    Ontology --build_context--> AdapterContext(tbox=OntologyView, iri=...)

* :class:`OntologyView` exposes only definitions (object types, relations,
  vocabulary, profile) plus a compiled runtime schema; the source aggregate
  is snapshotted, so later mutations cannot leak into an in-flight export.
* :class:`SnapshotView` is the P0 placeholder for the ABox snapshot; ABox
  export/query are P1/P2 deliverables.
* :class:`AdapterContext` carries the deterministic :class:`IriPolicy`, the
  actor (for artifact metadata/audit) and the declared scope.

``OntologyView.compiled_schema()`` recompiles on every call instead of
caching a dict: a cached mutable schema could be modified by an adapter and
leak into a later export, which would break the byte-identical export
guarantee.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from ..domain.ontology.compiler import OntologyCompiler
from ..domain.ontology.model import (
    ObjectTypeSpec,
    Ontology,
    RelationSpec,
    VocabularyTerm,
)
from .iri import IriPolicy, iri_policy_for
from .spi import SCOPE_KINDS, AdapterError


def _readonly(mapping: Mapping | dict | None) -> Mapping:
    """Return a read-only shallow copy of a platform mapping.

    ``MappingProxyType`` blocks insertion/removal on the view itself while
    keeping the plain-dict comparison/iteration semantics adapters expect.
    """
    if not mapping:
        return MappingProxyType({})
    return MappingProxyType(dict(mapping))


@dataclass(frozen=True, slots=True)
class OntologyView:
    """Immutable TBox view of one ``Ontology`` version.

    Build it with :meth:`from_ontology`; the embedded (snapshotted) aggregate
    is what :meth:`compiled_schema` compiles.
    """

    id: str
    workspace_id: str
    name: str
    version: int
    schema_version: int
    schema_fingerprint: str
    profile: str
    objects: Mapping[str, ObjectTypeSpec] = field(default_factory=dict)
    relations: Mapping[str, RelationSpec] = field(default_factory=dict)
    vocabulary: Mapping[str, VocabularyTerm] = field(default_factory=dict)
    #: private snapshot of the source aggregate; excluded from repr/equality
    _ontology: Ontology | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "objects", _readonly(self.objects))
        object.__setattr__(self, "relations", _readonly(self.relations))
        object.__setattr__(self, "vocabulary", _readonly(self.vocabulary))

    @classmethod
    def from_ontology(cls, ontology: Ontology) -> "OntologyView":
        """Snapshot an ``Ontology`` aggregate into a read-only view.

        A deep copy is taken so neither the caller nor an adapter can mutate
        the exported definitions through a shared reference.
        """
        snapshot = copy.deepcopy(ontology)
        return cls(
            id=str(snapshot.id),
            workspace_id=str(snapshot.workspace_id),
            name=str(snapshot.name),
            version=int(snapshot.version),
            schema_version=int(snapshot.schema_version),
            schema_fingerprint=str(snapshot.schema_fingerprint),
            profile=str(snapshot.profile),
            objects=snapshot.objects,
            relations=snapshot.relations,
            vocabulary=snapshot.vocabulary,
            _ontology=snapshot,
        )

    def compiled_schema(self) -> dict:
        """Return a fresh ``OntologyCompiler`` payload (no capability functions)."""
        if self._ontology is None:
            raise AdapterError(
                "OntologyView has no embedded Ontology; build it with "
                "OntologyView.from_ontology()")
        return OntologyCompiler().compile(self._ontology, []).schema


@dataclass(frozen=True, slots=True)
class SnapshotView:
    """Read-only ABox snapshot placeholder (P0 exports TBox only)."""

    objects: tuple[dict, ...] = ()


@dataclass(frozen=True, slots=True)
class AdapterContext:
    """The only read surface an adapter plugin receives."""

    workspace_id: str
    tbox: OntologyView
    iri: IriPolicy
    actor: str
    scope: str = "tbox"
    abox: SnapshotView | None = None
    version: str = ""


def build_context(
    ontology: Ontology,
    actor: str,
    *,
    scope: str = "tbox",
    base: str | None = None,
) -> AdapterContext:
    """Build an :class:`AdapterContext` from a domain ``Ontology``.

    ``base`` overrides the public IRI host (the deterministic workspace /
    ontology / version suffix is preserved); ``scope`` must be one of
    :data:`onto_platform.semantic.spi.SCOPE_KINDS`.
    """
    if scope not in SCOPE_KINDS:
        raise AdapterError(
            f"unknown adapter scope {scope!r}; expected one of {list(SCOPE_KINDS)}")
    if base is None:
        policy = IriPolicy.for_ontology(ontology)
    else:
        # tolerate a configured host base with a trailing slash
        policy = iri_policy_for(ontology, base.rstrip("/"))
    return AdapterContext(
        workspace_id=str(ontology.workspace_id),
        tbox=OntologyView.from_ontology(ontology),
        iri=policy,
        actor=actor,
        scope=scope,
        version=str(ontology.version),
    )


__all__ = [
    "OntologyView",
    "SnapshotView",
    "AdapterContext",
    "build_context",
]
