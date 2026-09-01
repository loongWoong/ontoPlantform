from .compiler import OntologyCompiler
from .model import (
    TYPE_KINDS,
    Ontology,
    ObjectTypeSpec,
    PropertySpec,
    RelationSpec,
    VocabularyTerm,
)
from .validation import validate_ontology

__all__ = [
    "TYPE_KINDS",
    "Ontology",
    "OntologyCompiler",
    "ObjectTypeSpec",
    "PropertySpec",
    "RelationSpec",
    "VocabularyTerm",
    "validate_ontology",
]
