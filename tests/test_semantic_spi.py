"""SPI contract tests (task A: C0+C1 completion of the plugin manifest).

The manifest is a plugin's "identity card" (architecture §2.2).  These tests
pin the fields added by decision D3 - ``formats``/``requires``/
``config_schema``/``aliases`` - while proving every older manifest that only
knows the flat ``formats_out``/``formats_in`` keys stays valid and keeps its
keys in ``to_dict()`` (third-party plugins written before D3 must not break).

Also covered: the ``requires.ontoplatform`` grammar and its fail-open
compatibility check, and the ``AdapterInvocationError`` -> 502 mapping
(decision D7).
"""

from __future__ import annotations

from onto_platform import __version__ as package_version
from onto_platform.errors import PlatformError, classify
from onto_platform.semantic import spi as spi_module
from onto_platform.semantic.spi import (
    ADAPTER_API_VERSION,
    AdapterError,
    AdapterIncompatible,
    AdapterInvocationError,
    AdapterManifest,
    manifest_problems,
    platform_version,
    requires_compatible,
    requires_problems,
)


def _manifest(**overrides) -> AdapterManifest:
    data = {
        "name": "fake",
        "version": "1.0.0",
        "api_version": ADAPTER_API_VERSION,
        "standards": ("test",),
        "capabilities": ("export",),
        "formats_out": ("text/turtle",),
    }
    data.update(overrides)
    return AdapterManifest(**data)


# ----------------------------------------------------------------------
# backward compatibility: an old manifest is still a valid manifest
# ----------------------------------------------------------------------


def test_legacy_manifest_keeps_its_defaults_and_is_valid():
    manifest = _manifest()

    assert manifest.requires == {}
    assert manifest.config_schema == {}
    assert manifest.aliases == ()
    assert manifest_problems(manifest) == []


def test_legacy_positional_construction_still_works():
    # field order before D3: name, version, api_version, standards,
    # capabilities, formats_out, formats_in, description, permissions
    manifest = AdapterManifest(
        "fake", "0.1.0", ADAPTER_API_VERSION, ("rdf",), ("export",),
        ("text/turtle",))

    assert manifest_problems(manifest) == []
    assert manifest.requires == {}


def test_to_dict_keeps_flat_keys_for_existing_consumers():
    payload = _manifest().to_dict()

    for key in ("name", "version", "api_version", "standards", "capabilities",
                "formats_out", "formats_in", "description", "permissions"):
        assert key in payload
    assert payload["formats_out"] == ["text/turtle"]
    assert payload["formats_in"] == []
    assert payload["permissions"] == {"network": False, "write": False}


# ----------------------------------------------------------------------
# architecture §2.2 fields (decision D3)
# ----------------------------------------------------------------------


def test_to_dict_emits_the_grouped_architecture_fields():
    manifest = _manifest(
        formats_out=("text/turtle",),
        formats_in=("application/rdf+xml",),
        requires={"ontoplatform": ">=0.3", "extras": ["rdflib>=7"]},
        config_schema={
            "type": "object",
            "properties": {"base_iri": {"type": "string"}},
        },
        aliases=("onto-standards-fake", "fake-legacy"),
    )

    payload = manifest.to_dict()

    assert payload["formats"] == {
        "out": ["text/turtle"], "in": ["application/rdf+xml"]}
    assert payload["requires"] == {
        "ontoplatform": ">=0.3", "extras": ["rdflib>=7"]}
    assert payload["config_schema"]["properties"]["base_iri"]["type"] == "string"
    assert payload["aliases"] == ["onto-standards-fake", "fake-legacy"]


def test_to_dict_normalises_an_empty_requires_block():
    payload = _manifest(requires={}).to_dict()

    assert payload["requires"] == {"ontoplatform": "", "extras": []}
    assert payload["config_schema"] == {}
    assert payload["aliases"] == []


def test_manifest_problems_accepts_new_fields_when_well_formed():
    manifest = _manifest(
        requires={"ontoplatform": ">=0.1", "extras": ["rdflib>=7"]},
        config_schema={"type": "object"},
        aliases=("onto-standards-fake", "fake-legacy"),
    )

    assert manifest_problems(manifest) == []


# ----------------------------------------------------------------------
# requires: declaration grammar
# ----------------------------------------------------------------------


def test_requires_problems_accepts_empty_and_supported_constraints():
    assert requires_problems(_manifest(requires={})) == []
    assert requires_problems(_manifest(requires={
        "ontoplatform": "", "extras": []})) == []
    for constraint in (">=0.3", ">= 0.3.1", "==0.3", "<=1", ">0.2.1",
                       "<2.0.0", "0.3"):
        assert requires_problems(
            _manifest(requires={"ontoplatform": constraint})) == [], constraint


def test_requires_problems_rejects_unknown_keys():
    problems = requires_problems(_manifest(requires={"platform": ">=0.3"}))

    assert problems
    assert any("unsupported keys" in problem for problem in problems)


def test_requires_problems_rejects_unsupported_constraint_forms():
    for constraint in ("~=0.3", "^0.3", ">=0.3,<1.0", "0.3.*", "abc",
                       "1.2.3.4", ">= "):
        problems = requires_problems(
            _manifest(requires={"ontoplatform": constraint}))
        assert problems, constraint


def test_requires_problems_rejects_bad_extras():
    assert requires_problems(_manifest(requires={"extras": "rdflib"}))
    assert requires_problems(_manifest(requires={"extras": ["rdflib", 7]}))
    assert requires_problems(_manifest(requires={"extras": ["  "]}))


def test_manifest_problems_inherits_requires_problems():
    problems = manifest_problems(
        _manifest(requires={"ontoplatform": "~=1.0"}))

    assert any("ontoplatform" in problem for problem in problems)


# ----------------------------------------------------------------------
# config_schema / aliases validation
# ----------------------------------------------------------------------


def test_manifest_problems_rejects_a_non_dict_config_schema():
    problems = manifest_problems(_manifest(config_schema=[("base_iri", "x")]))

    assert any("config_schema" in problem for problem in problems)


def test_manifest_problems_rejects_malformed_aliases():
    for alias in ("BadName", "onto-standards-owl ", "", "onto standards"):
        problems = manifest_problems(_manifest(aliases=(alias,)))
        assert any("alias" in problem for problem in problems), alias

    assert any("repeat the adapter name" in problem
               for problem in manifest_problems(_manifest(aliases=("fake",))))
    assert any("duplicate" in problem
               for problem in manifest_problems(
                   _manifest(aliases=("dup-alias", "dup-alias"))))


def test_manifest_problems_rejects_a_string_instead_of_an_alias_sequence():
    problems = manifest_problems(_manifest(aliases="onto-standards-fake"))

    assert any("aliases" in problem for problem in problems)


# ----------------------------------------------------------------------
# requires.ontoplatform compatibility
# ----------------------------------------------------------------------


def test_platform_version_is_the_package_version():
    assert platform_version() == package_version


def test_requires_compatible_without_a_constraint():
    assert requires_compatible(_manifest()) == (True, "")
    assert requires_compatible(_manifest(requires={
        "ontoplatform": ""})) == (True, "")


def test_requires_compatible_accepts_satisfied_constraints():
    current = platform_version()
    for constraint in (f">={current}", f"=={current}", f"<={current}",
                       "<99.0.0", ">0.0.0"):
        compatible, reason = requires_compatible(
            _manifest(requires={"ontoplatform": constraint}))
        assert compatible is True, (constraint, reason)
        assert reason == ""


def test_requires_compatible_rejects_a_newer_requirement():
    compatible, reason = requires_compatible(
        _manifest(requires={"ontoplatform": ">=99.0"}))

    assert compatible is False
    assert "incompatible" in reason
    assert platform_version() in reason


def test_requires_compatible_comparisons_are_numeric_not_lexicographic():
    # "0.10" < "0.9" lexicographically but 0.10 > 0.9 numerically
    assert requires_compatible(_manifest(
        requires={"ontoplatform": f">={platform_version()}"}))[0] is True
    assert requires_compatible(_manifest(requires={
        "ontoplatform": ">=999.0.0"}))[0] is False


def test_requires_compatible_fails_open_for_an_unsupported_constraint():
    compatible, reason = requires_compatible(
        _manifest(requires={"ontoplatform": "~=1.0"}))

    assert compatible is True
    assert "cannot verify" in reason


def test_requires_compatible_fails_open_for_an_unverifiable_platform(monkeypatch):
    monkeypatch.setattr(spi_module, "platform_version", lambda: "0.3.0-dev")

    compatible, reason = requires_compatible(
        _manifest(requires={"ontoplatform": ">=0.1"}))

    assert compatible is True
    assert "cannot verify" in reason


# ----------------------------------------------------------------------
# error taxonomy (decision D7)
# ----------------------------------------------------------------------


def test_adapter_invocation_error_maps_to_502_plugin_failure():
    error = AdapterInvocationError("adapter crashed")

    assert isinstance(error, PlatformError)
    payload = classify(error)
    assert payload["code"] == "plugin_failure"
    assert payload["http_status"] == 502
    assert payload["retryable"] is False
    assert payload["type"] == "AdapterInvocationError"


def test_existing_adapter_error_taxonomy_is_unchanged():
    assert classify(AdapterError("bad request"))["http_status"] == 400
    assert classify(
        AdapterError("bad request"))["code"] == "validation"
    assert issubclass(AdapterIncompatible, AdapterError)
