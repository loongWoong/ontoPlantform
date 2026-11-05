"""TurtleWriter: prefixes, QNames, escaping, xsd literals and determinism.

Covers the T2 acceptance criteria for the shared serializer
(docs/standards-plugin-implementation-plan-v1.md §4 T2).
"""

from __future__ import annotations

import random

import pytest

from onto_platform.domain.ontology.model import Ontology
from onto_platform.semantic.iri import IriPolicy
from onto_platform.semantic.turtle import Literal, TurtleWriter, write_turtle

RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
OWL = "http://www.w3.org/2002/07/owl#"
XSD = "http://www.w3.org/2001/XMLSchema#"


def build_ontology(version: int = 2) -> Ontology:
    return Ontology.from_dict({
        "id": "onto-traffic",
        "workspace_id": "ws-1",
        "name": "traffic",
        "version": version,
        "schema_fingerprint": "abc123",
        "objects": {
            "Vehicle": {"name": "Vehicle", "description": "车辆"},
            "TollStation": {"name": "TollStation", "description": "收费站"},
        },
    })


def policy() -> IriPolicy:
    return IriPolicy.for_ontology(build_ontology())


def writer() -> TurtleWriter:
    return TurtleWriter(policy().prefix_map())


def single_line(object_value: object) -> str:
    """Serialize one triple and return its (non-empty) body line."""
    iri = policy()
    text = writer().serialize(
        [(iri.class_iri("Vehicle"), RDFS + "label", object_value)])
    body = [line for line in text.splitlines()
            if line and not line.startswith("@prefix")]
    assert len(body) == 1
    return body[0]


# ---------------------------------------------------------------------------
# prefixes / document shape
# ---------------------------------------------------------------------------


def test_all_policy_prefixes_are_declared_sorted_with_trailing_newline():
    prefixes = policy().prefix_map()
    text = TurtleWriter(prefixes).serialize([])

    assert text == "".join(
        f"@prefix {name}: <{namespace}> .\n"
        for name, namespace in sorted(prefixes.items()))
    assert text.endswith("\n")
    assert [line.split(":")[0].removeprefix("@prefix ")
            for line in text.splitlines()] == [
        "onto", "owl", "rdf", "rdfs", "skos", "xsd"]
    assert "@prefix owl: <http://www.w3.org/2002/07/owl#> ." in text
    assert "@prefix onto: <https://ontoplatform.local/ws/ws-1/onto/traffic/v2#> ." in text


def test_empty_statements_output_only_the_prefix_block():
    prefixes = policy().prefix_map()
    text = writer().serialize(())
    assert text.count("@prefix") == len(prefixes)
    assert " .\n\n" not in text  # no empty body separator
    assert not any(line.endswith(" .") and not line.startswith("@prefix")
                   for line in text.splitlines())


def test_prefix_table_is_copied_not_aliased():
    source = {"ex": "http://example.org/"}
    w = TurtleWriter(source)
    source["ex"] = "http://mutated.example/"
    assert w.prefixes["ex"] == "http://example.org/"


def test_invalid_prefix_namespaces_are_rejected():
    with pytest.raises(ValueError):
        TurtleWriter({"1bad": "http://example.org/"})
    with pytest.raises(ValueError):
        TurtleWriter({"ex": ""})


# ---------------------------------------------------------------------------
# QNames vs full IRIs
# ---------------------------------------------------------------------------


def test_qname_when_prefix_matches_and_full_iri_otherwise():
    iri = policy()
    text = writer().serialize([
        (iri.class_iri("Vehicle"), RDF + "type", OWL + "Class"),
        ("http://example.org/Thing", RDF + "type", OWL + "Class"),
    ])

    assert "onto:Vehicle rdf:type owl:Class ." in text
    assert "<http://example.org/Thing> rdf:type owl:Class ." in text


def test_longest_matching_prefix_wins():
    prefixes = policy().prefix_map()
    prefixes["wide"] = "https://ontoplatform.local/ws/ws-1/onto/"
    w = TurtleWriter(prefixes)
    text = w.serialize([
        ("https://ontoplatform.local/ws/ws-1/onto/traffic/v2#Vehicle",
         RDF + "type", OWL + "Class")])
    assert "onto:Vehicle rdf:type owl:Class ." in text


def test_percent_encoded_local_fragment_stays_a_qname():
    iri = policy()
    text = writer().serialize([
        (iri.vocabulary_iri("收费站"), RDF + "type", OWL + "Class")])
    assert ("onto:term.%E6%94%B6%E8%B4%B9%E7%AB%99 rdf:type owl:Class ."
            in text)


# ---------------------------------------------------------------------------
# literals: escaping, xsd types, lang tags
# ---------------------------------------------------------------------------


def test_string_escaping_quotes_backslash_newline_tab_carriage_return():
    value = 'he said "hi"\\path\nnext\ttab\rcr'
    line = single_line(Literal(value))
    assert line == (
        'onto:Vehicle rdfs:label '
        '"he said \\"hi\\"\\\\path\\nnext\\ttab\\rcr"^^xsd:string .')


def test_remaining_control_characters_use_unicode_escape():
    line = single_line(Literal("a\x01b"))
    assert line == 'onto:Vehicle rdfs:label "a\\u0001b"^^xsd:string .'


def test_integer_decimal_boolean_are_bare_literals():
    assert single_line(Literal("42", XSD + "integer")) == (
        "onto:Vehicle rdfs:label 42 .")
    assert single_line(Literal("42.5", XSD + "decimal")) == (
        "onto:Vehicle rdfs:label 42.5 .")
    assert single_line(Literal("true", XSD + "boolean")) == (
        "onto:Vehicle rdfs:label true .")
    assert single_line(Literal("false", XSD + "boolean")) == (
        "onto:Vehicle rdfs:label false .")


def test_datetime_and_string_keep_explicit_datatype():
    assert single_line(Literal("2026-11-05T10:00:00", XSD + "dateTime")) == (
        'onto:Vehicle rdfs:label "2026-11-05T10:00:00"^^xsd:dateTime .')
    assert single_line(Literal("plain")) == (
        'onto:Vehicle rdfs:label "plain"^^xsd:string .')
    assert single_line(("plain", None)) == (
        'onto:Vehicle rdfs:label "plain"^^xsd:string .')


def test_non_numeric_value_under_numeric_datatype_is_quoted():
    assert single_line(Literal("not a number", XSD + "decimal")) == (
        'onto:Vehicle rdfs:label "not a number"^^xsd:decimal .')


def test_tuple_shorthand_and_language_literals():
    iri = policy()
    text = writer().serialize([
        (iri.class_iri("Vehicle"), RDFS + "label", ("hello", XSD + "string")),
        (iri.class_iri("Vehicle"), RDFS + "comment", ("你好", "@zh")),
        (iri.class_iri("Vehicle"), RDFS + "seeAlso", Literal("Hello", lang="en")),
    ])
    assert '"hello"^^xsd:string' in text
    assert '"你好"@zh' in text
    assert '"Hello"@en' in text


def test_write_returns_utf8_bytes():
    data = write_turtle(
        [(policy().class_iri("Vehicle"), RDFS + "label", Literal("车辆"))],
        policy().prefix_map(),
    )
    assert isinstance(data, bytes)
    assert "车辆" in data.decode("utf-8")


# ---------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------


def test_output_is_independent_of_statement_order():
    iri = policy()
    statements = [
        (iri.class_iri("Vehicle"), RDFS + "label", Literal("Vehicle")),
        (iri.class_iri("Vehicle"), RDF + "type", OWL + "Class"),
        (iri.class_iri("TollStation"), RDFS + "label", Literal("TollStation")),
        (iri.class_iri("TollStation"), RDF + "type", OWL + "Class"),
        (iri.class_iri("Vehicle"), RDFS + "comment", Literal("车辆")),
    ]
    baseline = writer().write(statements)

    shuffled = list(statements)
    random.Random(20261105).shuffle(shuffled)
    assert writer().write(shuffled) == baseline
    assert writer().write(reversed(statements)) == baseline
    assert writer().write(statements + statements) == baseline  # de-duplicated


def test_statements_sort_by_subject_predicate_object():
    iri = policy()
    text = writer().serialize([
        (iri.class_iri("TollStation"), RDFS + "label", Literal("TollStation")),
        (iri.class_iri("Vehicle"), RDFS + "label", Literal("b")),
        (iri.class_iri("Vehicle"), RDFS + "label", "http://example.org/object"),
        (iri.class_iri("Vehicle"), RDFS + "label", Literal("a")),
    ])
    body = [line for line in text.splitlines()
            if line and not line.startswith("@prefix")]
    assert body == [
        "onto:TollStation rdfs:label \"TollStation\"^^xsd:string .",
        'onto:Vehicle rdfs:label "a"^^xsd:string .',
        'onto:Vehicle rdfs:label "b"^^xsd:string .',
        "onto:Vehicle rdfs:label <http://example.org/object> .",
    ]


# ---------------------------------------------------------------------------
# defensive input handling
# ---------------------------------------------------------------------------


def test_malformed_statements_are_rejected():
    iri = policy()
    with pytest.raises(TypeError):
        writer().serialize([(iri.class_iri("Vehicle"), RDFS + "label")])
    with pytest.raises(TypeError):
        writer().serialize([(None, RDFS + "label", "http://example.org/x")])
    with pytest.raises(TypeError):
        writer().serialize([(iri.class_iri("Vehicle"), RDFS + "label", 42)])
