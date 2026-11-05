"""Deterministic IRI policy for exported standard documents.

The platform stores objects by workspace-local identifiers.  A standard
artifact (Turtle/OWL/SKOS) needs stable IRIs instead.  This module derives
them deterministically from ``(workspace, ontology name, version)`` so that:

* the same published version always exports byte-identical IRIs;
* a new version changes the namespace version segment, while term IRIs keep
  their local fragment (so diffs stay readable);
* collisions between class / relation / property / vocabulary fragments are
  detected before an adapter writes a document.

``profile`` and capability compilation are intentionally not part of the
IRI policy: IRIs are about identity, not about runtime constraints.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote

from ..errors import ValidationError
from .spi import AdapterError

#: Fixed public base; deployments may later parameterise this per tenant.
DEFAULT_BASE = "https://ontoplatform.local"

_SLUG_UNSAFE = re.compile(r"[^A-Za-z0-9_-]+")

#: W3C prefixes that standard documents may use without declaring them twice.
PREFIXES: dict[str, str] = {
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "skos": "http://www.w3.org/2004/02/skos/core#",
}


def _slug(value: str, what: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise AdapterError(f"{what} must not be empty to build a stable IRI")
    slug = _SLUG_UNSAFE.sub("_", text)
    if not slug or slug[0].isdigit() and what == "ontology name":
        slug = f"_{slug}" if slug else "unnamed"
    return slug


def _fragment(value: str) -> str:
    """Percent-encode a local fragment deterministically."""
    return quote(str(value), safe="-._~")


@dataclass(frozen=True, slots=True)
class IriPolicy:
    """Deterministic namespace + term IRIs for one ontology version."""

    workspace_id: str
    ontology_name: str
    version: int
    #: namespace ending in '#'; term IRIs are ``base + fragment``
    base: str

    @classmethod
    def for_ontology(cls, ontology) -> "IriPolicy":
        """Build the policy for a domain ``Ontology`` aggregate."""
        workspace = _slug(getattr(ontology, "workspace_id", ""), "workspace id")
        name = _slug(getattr(ontology, "name", ""), "ontology name")
        version = int(getattr(ontology, "version", 1) or 1)
        if version < 1:
            raise AdapterError(f"ontology version must be >= 1, got {version}")
        namespace = (
            f"{DEFAULT_BASE}/ws/{workspace}/onto/{name}/v{version}#")
        return cls(workspace_id=str(ontology.workspace_id), ontology_name=str(ontology.name),
                   version=version, base=namespace)

    # ---- document-level IRIs ------------------------------------------

    def ontology_iri(self) -> str:
        return self.base[:-1]

    def version_iri(self) -> str:
        return f"{self.ontology_iri()}/version/{self.version}"

    def prefix_map(self) -> dict[str, str]:
        prefixes = dict(PREFIXES)
        prefixes["onto"] = self.base
        return prefixes

    # ---- term-level IRIs ----------------------------------------------

    def class_iri(self, name: str) -> str:
        return self.base + _fragment(name)

    def relation_iri(self, name: str) -> str:
        return self.base + _fragment(name)

    def property_iri(self, owner: str, prop: str) -> str:
        return self.base + _fragment(f"{owner}.{prop}")

    def vocabulary_iri(self, term: str) -> str:
        return self.base + _fragment(f"term.{term}")

    def concept_scheme_iri(self) -> str:
        return self.base + "ConceptScheme"

    # ---- collision detection ------------------------------------------

    def _declared_terms_ordered(self, ontology) -> list[tuple[str, str]]:
        """Ordered (fragment, label) pairs for every platform term."""
        order: list[tuple[str, str]] = []
        for name in getattr(ontology, "objects", {}):
            order.append((_fragment(name), f"class {name}"))
            for prop in getattr(ontology.objects[name], "properties", {}):
                order.append((_fragment(f"{name}.{prop}"), f"property {name}.{prop}"))
        for name in getattr(ontology, "relations", {}):
            order.append((_fragment(name), f"relation {name}"))
        for term in getattr(ontology, "vocabulary", {}):
            order.append((_fragment(f"term.{term}"), f"vocabulary term {term}"))
        return order

    def declared_terms(self, ontology) -> dict[str, str]:
        """Map fragment -> label for every platform term (first declaration)."""
        fragments: dict[str, str] = {}
        for fragment, label in self._declared_terms_ordered(ontology):
            fragments.setdefault(fragment, label)
        return fragments

    def assert_no_collision(self, ontology) -> None:
        """Raise ``AdapterError`` when two platform terms share a fragment."""
        seen: dict[str, str] = {}
        for fragment, label in self._declared_terms_ordered(ontology):
            previous = seen.get(fragment)
            if previous is not None and previous != label:
                raise AdapterError(
                    f"IRI collision in namespace {self.base!r}: {previous!r} and "
                    f"{label!r} both map to fragment {fragment!r}")
            seen[fragment] = label


def iri_policy_for(ontology, base: str = DEFAULT_BASE) -> IriPolicy:
    """Convenience wrapper preserving a configurable public base."""
    policy = IriPolicy.for_ontology(ontology)
    if base and base != DEFAULT_BASE:
        # keep the deterministic suffix, replace the host part only
        suffix = policy.base[len(DEFAULT_BASE):]
        return IriPolicy(policy.workspace_id, policy.ontology_name,
                         policy.version, base + suffix)
    return policy


__all__ = [
    "DEFAULT_BASE", "PREFIXES", "IriPolicy", "iri_policy_for",
    "ValidationError",
]
