"""OWL/RDF structural export adapter (TBox -> Turtle, zero dependencies).

Scope follows docs/standards-plugin-architecture.md §2.5 -- the semantic red
line between OWL and SHACL:

* ``owl:Class`` for object types (+ ``rdfs:label``/``rdfs:comment``);
* ``owl:ObjectProperty`` for relations (+ ``rdfs:domain``/``rdfs:range`` and
  ``owl:inverseOf`` when the inverse relation is declared);
* ``owl:DatatypeProperty`` for properties (+ domain class and an ``xsd``
  range derived from the platform scalar type).

Deliberately **not** emitted: ``owl:hasKey`` (implies ``sameAs``),
``owl:cardinality``/``minCardinality``/``maxCardinality``,
``owl:FunctionalProperty``, ``owl:someValuesFrom``/``allValuesFrom``, and
anything from ``sh:``.  ``required``/``enum``/``min``/``max``/``unique`` are
platform runtime constraints and belong to the SHACL adapter (P1); dressing
them up as OWL axioms would produce a document that looks standard while
being semantically wrong.

The adapter reads only ``ctx.tbox`` (duck-typed: an ``Ontology`` or an
``OntologyView``) and ``ctx.iri``.  It never touches the repository, the
runtime kernel or the composition root.
"""

from __future__ import annotations

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
RDFS = PREFIXES["rdfs"]
OWL = PREFIXES["owl"]
XSD = PREFIXES["xsd"]

RDF_TYPE = RDF + "type"
RDFS_LABEL = RDFS + "label"
RDFS_COMMENT = RDFS + "comment"
RDFS_DOMAIN = RDFS + "domain"
RDFS_RANGE = RDFS + "range"
OWL_ONTOLOGY = OWL + "Ontology"
OWL_CLASS = OWL + "Class"
OWL_OBJECT_PROPERTY = OWL + "ObjectProperty"
OWL_DATATYPE_PROPERTY = OWL + "DatatypeProperty"
OWL_VERSION_INFO = OWL + "versionInfo"
OWL_VERSION_IRI = OWL + "versionIRI"
OWL_INVERSE_OF = OWL + "inverseOf"

#: Platform scalar type -> OWL datatype range.  ``object``/``array`` have no
#: stable RDF scalar, so they degrade to ``xsd:string`` (structural note only).
XSD_RANGES: dict[str, str] = {
    "string": XSD + "string",
    "integer": XSD + "integer",
    "number": XSD + "decimal",
    "boolean": XSD + "boolean",
    "datetime": XSD + "dateTime",
    "object": XSD + "string",
    "array": XSD + "string",
}

#: Accepted ``ExportRequest.format`` values ("" means the default format).
SUPPORTED_FORMATS: tuple[str, ...] = ("", "text/turtle", "turtle")

_DEFAULT_XSD_RANGE = XSD + "string"


def check_export_format(request: ExportRequest, manifest: AdapterManifest) -> str:
    """Validate the requested format, raising ``AdapterError`` otherwise."""
    format_name = str(getattr(request, "format", "") or "")
    if format_name not in SUPPORTED_FORMATS:
        raise AdapterError(
            f"{manifest.name} adapter does not support format {format_name!r}; "
            "supported formats: text/turtle, turtle")
    return format_name


def _as_version(value: object, fallback: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback


def _schema_version(tbox: object) -> int:
    return _as_version(getattr(tbox, "version", None))


def _version_info(version: int, fingerprint: str) -> str:
    text = f"v{version}"
    return f"{text} fingerprint:{fingerprint}" if fingerprint else text


def _xsd_range(type_kind: str) -> str:
    return XSD_RANGES.get(type_kind, _DEFAULT_XSD_RANGE)


def _text(value: object) -> str:
    return "" if value is None else str(value)


class OwlRdfAdapter:
    """TBox -> Turtle (OWL 2 structural subset) exporter."""

    manifest = AdapterManifest(
        name="owl-rdf",
        version="0.1.0",
        api_version=ADAPTER_API_VERSION,
        standards=("owl", "rdf", "turtle"),
        capabilities=("export",),
        formats_out=("text/turtle",),
        description=(
            "OWL 2 / RDF structural TBox export (classes, object properties "
            "with domain/range/inverse, datatype properties with xsd ranges) "
            "as Turtle. Runtime constraints stay in SHACL."),
    )

    def export(self, ctx: object, request: ExportRequest | None = None) -> Artifact:
        request = request if request is not None else ExportRequest()
        check_export_format(request, self.manifest)
        tbox = getattr(ctx, "tbox", None)
        iri = getattr(ctx, "iri", None)
        if tbox is None or iri is None:
            raise AdapterError(
                "owl-rdf export requires an AdapterContext with tbox and iri")

        # Fail before writing anything if two platform terms share a fragment.
        iri.assert_no_collision(tbox)

        version = _schema_version(tbox)
        fingerprint = _text(getattr(tbox, "schema_fingerprint", ""))
        statements, terms, skipped_inverse = self._statements(
            tbox, iri, version, fingerprint)
        content = TurtleWriter(iri.prefix_map()).write(statements)
        slug = _text(getattr(iri, "ontology_name", "")) or "ontology"
        return Artifact(
            content=content,
            media_type="text/turtle",
            filename=f"{slug}-v{version}.ttl",
            metadata={
                "adapter": self.manifest.name,
                "version": self.manifest.version,
                "schema_fingerprint": fingerprint,
                "terms": terms,
                "skipped_inverse": skipped_inverse,
            },
        )

    # ------------------------------------------------------------------

    def _statements(
        self, tbox: object, iri: object, version: int, fingerprint: str
    ) -> tuple[list[tuple[str, str, object]], int, list[str]]:
        statement_list: list[tuple[str, str, object]] = []
        objects = getattr(tbox, "objects", {}) or {}
        relations = getattr(tbox, "relations", {}) or {}
        declared_relations = set(relations)

        ontology_iri = iri.ontology_iri()
        statement_list.extend([
            (ontology_iri, RDF_TYPE, OWL_ONTOLOGY),
            (ontology_iri, RDFS_LABEL,
             Literal(_text(getattr(tbox, "name", "")))),
            (ontology_iri, OWL_VERSION_INFO,
             Literal(_version_info(version, fingerprint))),
            (ontology_iri, OWL_VERSION_IRI, iri.version_iri()),
        ])

        terms = 0
        for object_name in sorted(objects):
            object_spec = objects[object_name]
            class_iri = iri.class_iri(object_name)
            statement_list.extend([
                (class_iri, RDF_TYPE, OWL_CLASS),
                (class_iri, RDFS_LABEL, Literal(object_name)),
            ])
            description = _text(getattr(object_spec, "description", ""))
            if description:
                statement_list.append(
                    (class_iri, RDFS_COMMENT, Literal(description)))
            terms += 1

            properties = getattr(object_spec, "properties", {}) or {}
            for property_name in sorted(properties):
                property_spec = properties[property_name]
                property_iri = iri.property_iri(object_name, property_name)
                type_kind = _text(getattr(property_spec, "type", "")) or "string"
                statement_list.extend([
                    (property_iri, RDF_TYPE, OWL_DATATYPE_PROPERTY),
                    (property_iri, RDFS_DOMAIN, class_iri),
                    (property_iri, RDFS_RANGE, _xsd_range(type_kind)),
                ])
                property_description = _text(
                    getattr(property_spec, "description", ""))
                if property_description:
                    statement_list.append(
                        (property_iri, RDFS_COMMENT, Literal(property_description)))
                terms += 1

        skipped_inverse: list[str] = []
        for relation_name in sorted(relations):
            relation_spec = relations[relation_name]
            relation_iri = iri.relation_iri(relation_name)
            statement_list.append(
                (relation_iri, RDF_TYPE, OWL_OBJECT_PROPERTY))
            source = _text(getattr(relation_spec, "source", ""))
            target = _text(getattr(relation_spec, "target", ""))
            if source:
                statement_list.append(
                    (relation_iri, RDFS_DOMAIN, iri.class_iri(source)))
            if target:
                statement_list.append(
                    (relation_iri, RDFS_RANGE, iri.class_iri(target)))
            inverse = _text(getattr(relation_spec, "inverse", ""))
            if inverse:
                if inverse in declared_relations:
                    statement_list.append(
                        (relation_iri, OWL_INVERSE_OF,
                         iri.relation_iri(inverse)))
                else:
                    # Pointing at an undeclared property would be a dangling
                    # reference; skip it and report it in artifact metadata.
                    skipped_inverse.append(f"{relation_name}->{inverse}")
            terms += 1

        return statement_list, terms, skipped_inverse


__all__ = [
    "OwlRdfAdapter",
    "SUPPORTED_FORMATS",
    "XSD_RANGES",
    "check_export_format",
]
