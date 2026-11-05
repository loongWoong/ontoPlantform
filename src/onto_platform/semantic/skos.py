"""SKOS export adapter (ontology vocabulary -> Turtle ConceptScheme).

Each published ``VocabularyTerm`` becomes a ``skos:Concept`` in one
``skos:ConceptScheme`` (docs/standards-plugin-architecture.md §2.5: the
business vocabulary is a SKOS artifact, separate from the OWL classes):

* scheme: ``a skos:ConceptScheme`` + ``skos:prefLabel`` = ontology name;
* concept: ``a skos:Concept`` + ``skos:prefLabel`` = term,
  ``skos:notation`` = concept id, one ``skos:altLabel`` per alias,
  ``skos:inScheme`` -> scheme, and ``skos:definition`` when a description
  is available.

The adapter reads only ``ctx.tbox`` and ``ctx.iri`` and stays zero-dependency.
"""

from __future__ import annotations

import re

from .formats import check_export_format, check_scope
from .iri import PREFIXES
from .spi import (
    ADAPTER_API_VERSION,
    AdapterError,
    AdapterManifest,
    Artifact,
    ExportRequest,
)
from .turtle import Literal, TurtleWriter

RDF = PREFIXES["rdf"]
SKOS = PREFIXES["skos"]

RDF_TYPE = RDF + "type"
SKOS_CONCEPT_SCHEME = SKOS + "ConceptScheme"
SKOS_CONCEPT = SKOS + "Concept"
SKOS_PREF_LABEL = SKOS + "prefLabel"
SKOS_ALT_LABEL = SKOS + "altLabel"
SKOS_NOTATION = SKOS + "notation"
SKOS_IN_SCHEME = SKOS + "inScheme"
SKOS_DEFINITION = SKOS + "definition"

#: CJK ideographs (U+4E00..U+9FFF): Chinese human labels get ``@zh``; ASCII
#: identifiers/notations stay plain ``xsd:string`` (deterministic decision).
_CJK = re.compile(r"[\u4e00-\u9fff]")


def _label(value: str) -> Literal:
    """A human label: ``@zh`` when it contains CJK, plain string otherwise."""
    return Literal(value, lang="zh") if _CJK.search(value) else Literal(value)


def _text(value: object) -> str:
    return "" if value is None else str(value)


def _as_version(value: object, fallback: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback


class SkosAdapter:
    """Ontology vocabulary -> SKOS ConceptScheme exporter (Turtle)."""

    manifest = AdapterManifest(
        name="skos",
        version="0.1.0",
        api_version=ADAPTER_API_VERSION,
        standards=("skos",),
        capabilities=("export",),
        formats_out=("text/turtle",),
        # D1: canonical plugin id stays "skos"; the architecture package name
        # is exposed as an alias so external integrations can use either.
        aliases=("onto-standards-skos",),
        description=(
            "SKOS export of the ontology vocabulary: one ConceptScheme with "
            "one Concept per term (prefLabel/notation/altLabel/definition) "
            "as Turtle."),
    )

    def export(self, ctx: object, request: ExportRequest | None = None) -> Artifact:
        request = request if request is not None else ExportRequest()
        check_export_format(request, self.manifest)
        check_scope(request, self.manifest)
        tbox = getattr(ctx, "tbox", None)
        iri = getattr(ctx, "iri", None)
        if tbox is None or iri is None:
            raise AdapterError(
                "skos export requires an AdapterContext with tbox and iri")

        version = _as_version(getattr(tbox, "version", None))
        fingerprint = _text(getattr(tbox, "schema_fingerprint", ""))
        statements, concepts = self._statements(tbox, iri)
        content = TurtleWriter(iri.prefix_map()).write(statements)
        slug = _text(getattr(iri, "ontology_name", "")) or "ontology"
        return Artifact(
            content=content,
            media_type="text/turtle",
            filename=f"{slug}-v{version}.skos.ttl",
            metadata={
                "adapter": self.manifest.name,
                "version": self.manifest.version,
                "schema_fingerprint": fingerprint,
                "concepts": concepts,
            },
        )

    # ------------------------------------------------------------------

    def _statements(
        self, tbox: object, iri: object
    ) -> tuple[list[tuple[str, str, object]], int]:
        scheme_iri = iri.concept_scheme_iri()
        statements: list[tuple[str, str, object]] = [
            (scheme_iri, RDF_TYPE, SKOS_CONCEPT_SCHEME),
            (scheme_iri, SKOS_PREF_LABEL,
             _label(_text(getattr(tbox, "name", "")))),
        ]

        vocabulary = getattr(tbox, "vocabulary", {}) or {}
        concepts = 0
        for term_key in sorted(vocabulary):
            term_spec = vocabulary[term_key]
            concept_iri = iri.vocabulary_iri(term_key)
            statements.append((concept_iri, RDF_TYPE, SKOS_CONCEPT))
            statements.append((
                concept_iri,
                SKOS_PREF_LABEL,
                _label(_text(getattr(term_spec, "term", "")) or term_key),
            ))
            notation = _text(getattr(term_spec, "concept", ""))
            if notation:
                statements.append((concept_iri, SKOS_NOTATION, Literal(notation)))
            # Sorted + de-duplicated so alias ordering never leaks into bytes.
            aliases = sorted({
                _text(alias) for alias in (getattr(term_spec, "aliases", []) or [])
                if _text(alias)
            })
            for alias in aliases:
                statements.append((concept_iri, SKOS_ALT_LABEL, _label(alias)))
            statements.append((concept_iri, SKOS_IN_SCHEME, scheme_iri))
            definition = _text(getattr(term_spec, "description", ""))
            if definition:
                statements.append(
                    (concept_iri, SKOS_DEFINITION, Literal(definition)))
            concepts += 1

        return statements, concepts


__all__ = ["SkosAdapter"]
