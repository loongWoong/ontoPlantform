"""Plugin registry tests (T1, standards plugin P0).

Covers builtin registration, entry-point discovery (via injectable
``entry_points=``), fail isolation for broken plugins, manifest status
reporting and the process-wide default registry.
"""

from __future__ import annotations

import pytest

from onto_platform.semantic import registry as registry_module
from onto_platform.semantic.registry import (
    BUILTIN_ADAPTERS,
    ENTRY_POINT_GROUP,
    LIFECYCLE_GROUP,
    PluginRegistry,
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
