"""Ontology semantic validation (design-time, pre-publish).

Returns human-readable problems; an empty list means publishable.
Reference integrity is already enforced while editing the aggregate;
this check adds cross-cutting rules.
"""

from __future__ import annotations

from .model import TYPE_KINDS, Ontology

_IDENTIFIER_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


def validate_ontology(ontology: Ontology) -> list[str]:
    problems: list[str] = []

    if not ontology.objects:
        problems.append("ontology defines no object types")

    for otype in ontology.objects.values():
        if not otype.name or set(otype.name) - _IDENTIFIER_OK or otype.name[0].isdigit():
            problems.append(f"object type name {otype.name!r} is not an identifier")
        if not otype.properties:
            problems.append(f"object type {otype.name!r} has no properties")
        for prop in otype.properties.values():
            if prop.type not in TYPE_KINDS:
                problems.append(
                    f"{otype.name}.{prop.name}: unknown property type {prop.type!r}"
                )

    for rel in ontology.relations.values():
        if rel.source not in ontology.objects:
            problems.append(
                f"relation {rel.name!r}: source {rel.source!r} is not defined"
            )
        if rel.target not in ontology.objects:
            problems.append(
                f"relation {rel.name!r}: target {rel.target!r} is not defined"
            )

    for term in ontology.vocabulary.values():
        if term.kind == "object" and term.concept not in ontology.objects:
            problems.append(
                f"vocabulary {term.term!r}: concept {term.concept!r} is not defined"
            )

    return problems
