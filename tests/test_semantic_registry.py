"""Plugin registry tests (T1/A, standards plugin P0).

Covers builtin registration, entry-point discovery (via injectable
``entry_points=``), fail isolation for broken plugins, manifest status
reporting, the process-wide default registry, and the C1 additions: alias
resolution (``onto-standards-*`` names, decision D1), capability <-> method
consistency (G-02) and ``requires.ontoplatform`` compatibility (D3).
"""

from __future__ import annotations

import pytest

from onto_platform.semantic import registry as registry_module
from onto_platform.semantic.registry import (
    BUILTIN_ADAPTERS,
    ENTRY_POINT_GROUP,
    LIFECYCLE_GROUP,
    PluginRegistry,
    adapter_contract_problems,
    default_registry,
    reset_default_registry,
)
from onto_platform.semantic.spi import (
    ADAPTER_API_VERSION,
    AdapterError,
    AdapterIncompatible,
    AdapterManifest,
    AdapterNotFound,
    Artifact,
)


#: captured before the autouse fixture swaps in an empty builtin tuple
_REAL_BUILTINS = BUILTIN_ADAPTERS


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


class FakeAdapter:
    """Minimal StandardAdapter stand-in (only the manifest matters here)."""

    def __init__(self, manifest: AdapterManifest | None = None) -> None:
        self.manifest = manifest or _manifest()

    def export(self, ctx, request) -> Artifact:  # pragma: no cover - not called
        return Artifact(content=b"", media_type="text/turtle")


@pytest.fixture(autouse=True)
def _no_builtins(monkeypatch):
    """Keep discovery deterministic: tests opt into builtins explicitly."""
    monkeypatch.setattr(registry_module, "BUILTIN_ADAPTERS", ())


def _registry(entry_points=None) -> PluginRegistry:
    return PluginRegistry(entry_points=entry_points)


def _explode():
    raise RuntimeError("plugin import failed")


# ----------------------------------------------------------------------
# constants
# ----------------------------------------------------------------------


def test_discovery_contract_constants():
    assert LIFECYCLE_GROUP == "onto_platform.standards"
    assert ENTRY_POINT_GROUP == LIFECYCLE_GROUP
    assert PluginRegistry.ENTRY_POINT_GROUP == LIFECYCLE_GROUP
    assert BUILTIN_ADAPTERS == (
        "onto_platform.semantic.owl:OwlRdfAdapter",
        "onto_platform.semantic.skos:SkosAdapter",
    )


# ----------------------------------------------------------------------
# manual registration
# ----------------------------------------------------------------------


def test_register_names_sorted_and_get():
    registry = _registry([])
    zeta = FakeAdapter(_manifest(name="zeta"))
    alpha = FakeAdapter(_manifest(name="alpha"))

    registry.register(zeta)
    registry.register(alpha)

    assert registry.names() == ["alpha", "zeta"]
    assert registry.get("alpha") is alpha
    assert registry.get("zeta") is zeta
    assert [row["name"] for row in registry.manifests()] == ["alpha", "zeta"]


def test_duplicate_registration_is_rejected():
    registry = _registry([])
    registry.register(FakeAdapter(_manifest(name="owl-rdf")))

    with pytest.raises(AdapterError) as excinfo:
        registry.register(FakeAdapter(_manifest(name="owl-rdf")))
    assert "already registered" in str(excinfo.value)


def test_invalid_manifest_is_rejected():
    registry = _registry([])
    broken = FakeAdapter(_manifest(name="bad-adapter", capabilities=()))

    with pytest.raises(AdapterError) as excinfo:
        registry.register(broken)
    assert "capabilities" in str(excinfo.value)
    assert registry.names() == []


def test_adapter_without_a_manifest_is_rejected():
    registry = _registry([])

    class NoManifest:
        manifest = {"name": "nope"}

    with pytest.raises(AdapterError):
        registry.register(NoManifest())


def test_incompatible_api_version_is_rejected():
    registry = _registry([])
    legacy = FakeAdapter(_manifest(name="legacy", api_version="2.0"))

    with pytest.raises(AdapterIncompatible) as excinfo:
        registry.register(legacy)
    assert excinfo.value.__class__ is AdapterIncompatible
    assert "api_version" in str(excinfo.value)


def test_compatible_major_version_is_accepted():
    registry = _registry([])
    registry.register(FakeAdapter(_manifest(name="patch-level", api_version="1.9")))

    assert registry.names() == ["patch-level"]


def test_unknown_adapter_raises_not_found():
    registry = _registry([])

    with pytest.raises(AdapterNotFound):
        registry.get("ghost")


# ----------------------------------------------------------------------
# discovery: injected entry points
# ----------------------------------------------------------------------


def test_discover_loads_injected_entry_points():
    class GammaAdapter(FakeAdapter):
        def __init__(self) -> None:
            super().__init__(_manifest(name="gamma"))

    registry = _registry([
        ("alpha", lambda: FakeAdapter(_manifest(name="alpha"))),
        # loaders may also return the adapter class itself
        ("gamma", lambda: GammaAdapter),
    ])

    loaded = registry.discover()

    assert loaded == ["alpha", "gamma"]
    assert registry.names() == ["alpha", "gamma"]
    assert registry.problems == []


def test_discover_accepts_mapping_entry_points():
    registry = _registry({"beta": lambda: FakeAdapter(_manifest(name="beta"))})

    assert registry.discover() == ["beta"]
    assert registry.get("beta").manifest.name == "beta"


def test_discover_isolates_plugin_failures():
    registry = _registry([
        ("alpha", lambda: FakeAdapter(_manifest(name="alpha"))),
        ("broken", _explode),
    ])

    loaded = registry.discover()

    assert loaded == ["alpha"]
    assert registry.names() == ["alpha"]
    assert len(registry.problems) == 1
    problem_name, problem_message = registry.problems[0]
    assert problem_name == "broken"
    assert "RuntimeError" in problem_message

    rows = {row["name"]: row for row in registry.manifests()}
    assert rows["alpha"]["status"] == "ready"
    assert rows["alpha"]["problems"] == []
    assert rows["broken"]["status"] == "invalid"
    assert rows["broken"]["problems"]


def test_discover_reports_an_invalid_manifest_instead_of_raising():
    registry = _registry([
        ("bad", lambda: FakeAdapter(_manifest(name="bad", version="not-a-version"))),
    ])

    assert registry.discover() == []
    assert "bad" not in registry.names()

    row = registry.manifests()[0]
    assert row["name"] == "bad"
    assert row["status"] == "invalid"
    assert any("version" in problem for problem in row["problems"])
    with pytest.raises(AdapterError):
        registry.get("bad")


def test_discover_marks_an_incompatible_plugin_and_get_raises_incompatible():
    registry = _registry([
        ("future", lambda: FakeAdapter(_manifest(name="future", api_version="2.0"))),
    ])

    assert registry.discover() == []
    row = registry.manifests()[0]
    assert row["status"] == "invalid"
    assert any("incompatible" in problem for problem in row["problems"])

    with pytest.raises(AdapterIncompatible):
        registry.get("future")


def test_discover_records_duplicate_names_without_raising():
    def duplicate():
        return FakeAdapter(_manifest(name="same"))

    registry = _registry([("first", duplicate), ("second", duplicate)])

    loaded = registry.discover()

    assert loaded == ["same"]
    assert registry.names() == ["same"]
    assert any("duplicate" in message for _, message in registry.problems)


def test_missing_builtin_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(
        registry_module, "BUILTIN_ADAPTERS",
        ("onto_platform.semantic._missing_plugin:MissingAdapter",))
    registry = _registry([])

    assert registry.discover() == []
    assert registry.problems
    name, message = registry.problems[0]
    assert name == "onto_platform.semantic._missing_plugin:MissingAdapter"
    assert "ModuleNotFoundError" in message

    rows = registry.manifests()
    assert len(rows) == 1
    assert rows[0]["status"] == "invalid"
    assert rows[0]["problems"]


def test_discover_reload_keeps_manual_and_picks_up_new_plugins():
    registry = _registry([("alpha", lambda: FakeAdapter(_manifest(name="alpha")))])
    registry.register(FakeAdapter(_manifest(name="manual")))

    assert registry.discover() == ["alpha"]
    assert registry.names() == ["alpha", "manual"]

    registry._entry_points.append(
        ("beta", lambda: FakeAdapter(_manifest(name="beta"))))
    assert registry.discover() == ["alpha", "beta"]
    assert registry.names() == ["alpha", "beta", "manual"]


def test_discover_never_raises_when_real_builtins_are_missing_or_present(
        monkeypatch):
    # restore the real builtin list the autouse fixture swapped out
    monkeypatch.setattr(registry_module, "BUILTIN_ADAPTERS", _REAL_BUILTINS)
    # no injection -> real discovery: before/after T2 lands this must not raise
    registry = PluginRegistry(entry_points=[])

    loaded = registry.discover()

    assert isinstance(loaded, list)
    assert set(loaded) <= {"owl-rdf", "skos"}
    for name in loaded:
        assert registry.get(name).manifest.name == name


# ----------------------------------------------------------------------
# manifests contract
# ----------------------------------------------------------------------


def test_manifest_rows_contain_manifest_fields_and_status():
    registry = _registry([
        ("zulu", lambda: FakeAdapter(_manifest(name="zulu"))),
        ("alpha", lambda: FakeAdapter(_manifest(name="alpha"))),
    ])
    registry.discover()

    rows = registry.manifests()

    assert [row["name"] for row in rows] == ["alpha", "zulu"]
    expected_keys = set(_manifest().to_dict()) | {"status", "problems"}
    for row in rows:
        assert set(row) == expected_keys
        assert row["status"] == "ready"
        assert row["problems"] == []
        assert row["api_version"] == ADAPTER_API_VERSION


def test_unloadable_plugin_rows_share_the_ready_row_shape(monkeypatch):
    monkeypatch.setattr(
        registry_module, "BUILTIN_ADAPTERS",
        ("onto_platform.semantic._missing_plugin:MissingAdapter",))
    registry = _registry([])

    registry.discover()

    rows = registry.manifests()
    assert rows[0]["status"] == "invalid"
    # API consumers read every row with the same schema, even when the
    # plugin could not be imported at all.
    assert {
        "name", "version", "api_version", "standards", "capabilities",
        "formats_out", "formats_in", "description", "permissions",
        "formats", "requires", "config_schema", "aliases",
    } <= set(rows[0])


# ----------------------------------------------------------------------
# process-wide default registry
# ----------------------------------------------------------------------


def test_default_registry_is_lazy_and_resettable():
    reset_default_registry()
    try:
        first = default_registry()
        assert first is default_registry()
        reset_default_registry()
        assert default_registry() is not first
    finally:
        reset_default_registry()


# ----------------------------------------------------------------------
# aliases (architecture §4 package names, decision D1)
# ----------------------------------------------------------------------


def test_aliases_resolve_in_get_but_never_in_names():
    registry = _registry([
        ("plug", lambda: FakeAdapter(_manifest(
            name="plug", aliases=("onto-standards-plug", "plug-legacy")))),
    ])

    assert registry.discover() == ["plug"]

    adapter = registry.get("onto-standards-plug")
    assert adapter is registry.get("plug")
    assert registry.get("plug-legacy") is adapter
    assert registry.names() == ["plug"]
    assert registry.manifests()[0]["aliases"] == [
        "onto-standards-plug", "plug-legacy"]


def test_alias_conflicting_with_a_canonical_name_is_rejected():
    registry = _registry([])
    registry.register(FakeAdapter(_manifest(name="owl-rdf")))

    conflicting = FakeAdapter(_manifest(name="fake", aliases=("owl-rdf",)))
    with pytest.raises(AdapterError) as excinfo:
        registry.register(conflicting)
    assert "alias" in str(excinfo.value)
    assert registry.names() == ["owl-rdf"]


def test_duplicate_alias_across_adapters_is_rejected():
    registry = _registry([])
    registry.register(FakeAdapter(_manifest(name="one", aliases=("shared",))))

    with pytest.raises(AdapterError) as excinfo:
        registry.register(FakeAdapter(_manifest(name="two", aliases=("shared",))))
    assert "shared" in str(excinfo.value)


def test_canonical_name_that_is_already_an_alias_is_rejected():
    registry = _registry([])
    registry.register(FakeAdapter(_manifest(name="one", aliases=("beta",))))

    with pytest.raises(AdapterError) as excinfo:
        registry.register(FakeAdapter(_manifest(name="beta")))
    assert "alias" in str(excinfo.value)


def test_alias_conflicts_are_isolated_during_discovery():
    registry = _registry([
        ("one", lambda: FakeAdapter(_manifest(name="one", aliases=("shared",)))),
        ("two", lambda: FakeAdapter(_manifest(name="two", aliases=("shared",)))),
    ])

    assert registry.discover() == ["one"]
    assert registry.names() == ["one"]
    assert registry.get("shared").manifest.name == "one"

    rows = {row["name"]: row for row in registry.manifests()}
    assert rows["two"]["status"] == "invalid"
    assert any("alias" in problem for problem in rows["two"]["problems"])


def test_manual_aliases_survive_rediscovery_while_stale_ones_are_dropped():
    registry = _registry([
        ("plug", lambda: FakeAdapter(_manifest(
            name="plug", aliases=("plug-alias",)))),
    ])
    registry.register(FakeAdapter(_manifest(
        name="manual", aliases=("manual-alias",))))

    assert registry.discover() == ["plug"]
    assert registry.get("plug-alias").manifest.name == "plug"

    registry._entry_points.clear()
    assert registry.discover() == []
    assert registry.get("manual-alias").manifest.name == "manual"
    assert registry.names() == ["manual"]
    with pytest.raises(AdapterNotFound):
        registry.get("plug-alias")


# ----------------------------------------------------------------------
# capability <-> method consistency (G-02)
# ----------------------------------------------------------------------


class _ManifestOnlyAdapter:
    """Declares capabilities it does not implement."""

    def __init__(self, manifest: AdapterManifest) -> None:
        self.manifest = manifest

    def export(self, ctx, request):  # pragma: no cover - not called
        return Artifact(content=b"", media_type="text/turtle")


def test_capability_without_implementation_is_rejected_on_register():
    registry = _registry([])
    lying = _ManifestOnlyAdapter(
        _manifest(name="lying", capabilities=("validate",), formats_out=()))

    with pytest.raises(AdapterError) as excinfo:
        registry.register(lying)
    message = str(excinfo.value)
    assert "capabilities" in message
    assert "validate" in message
    assert registry.names() == []


def test_contract_problems_helper_reports_each_missing_method():
    problems = adapter_contract_problems(FakeAdapter(_manifest(
        name="partial", capabilities=("export", "validate", "query"),
        formats_out=())))
    assert len(problems) == 2
    assert all("no callable" in problem for problem in problems)


def test_import_capability_requires_the_import_underscore_method():
    class Importer(FakeAdapter):
        def import_(self, ctx, payload):  # pragma: no cover - protocol shape
            raise NotImplementedError

    registry = _registry([])
    registry.register(Importer(_manifest(
        name="importer", capabilities=("export", "import"),
        formats_in=("text/turtle",))))

    assert registry.names() == ["importer"]


def test_contract_violations_are_isolated_during_discovery():
    registry = _registry([
        ("good", lambda: FakeAdapter(_manifest(name="good"))),
        ("liar", lambda: _ManifestOnlyAdapter(
            _manifest(name="liar", capabilities=("validate",), formats_out=()))),
    ])

    assert registry.discover() == ["good"]

    rows = {row["name"]: row for row in registry.manifests()}
    assert rows["good"]["status"] == "ready"
    assert rows["liar"]["status"] == "invalid"
    assert any("validate" in problem for problem in rows["liar"]["problems"])


# ----------------------------------------------------------------------
# requires.ontoplatform compatibility (G-13 / decision D3)
# ----------------------------------------------------------------------


def test_requires_incompatible_plugin_is_rejected_on_register():
    registry = _registry([])
    future = FakeAdapter(_manifest(
        name="future", requires={"ontoplatform": ">=99.0"}))

    with pytest.raises(AdapterIncompatible) as excinfo:
        registry.register(future)
    message = str(excinfo.value)
    assert "incompatible" in message
    assert registry.names() == []


def test_requires_compatible_plugin_registers_and_reports_its_requires():
    registry = _registry([])
    registry.register(FakeAdapter(_manifest(
        name="modern",
        requires={"ontoplatform": ">=0.1", "extras": ["rdflib>=7"]})))

    assert registry.names() == ["modern"]
    row = registry.manifests()[0]
    assert row["status"] == "ready"
    assert row["requires"] == {
        "ontoplatform": ">=0.1", "extras": ["rdflib>=7"]}


def test_requires_incompatibility_is_isolated_and_get_raises_incompatible():
    registry = _registry([
        ("future", lambda: FakeAdapter(_manifest(
            name="future", requires={"ontoplatform": ">=99.0"}))),
    ])

    assert registry.discover() == []

    rows = {row["name"]: row for row in registry.manifests()}
    assert rows["future"]["status"] == "invalid"
    assert any("incompatible" in problem
               for problem in rows["future"]["problems"])

    with pytest.raises(AdapterIncompatible):
        registry.get("future")


def test_malformed_requires_block_is_an_invalid_manifest():
    registry = _registry([
        ("bad", lambda: FakeAdapter(_manifest(
            name="bad", requires={"ontoplatform": "~=1.0"}))),
    ])

    assert registry.discover() == []

    rows = registry.manifests()
    assert rows[0]["status"] == "invalid"
    assert any("ontoplatform" in problem for problem in rows[0]["problems"])
