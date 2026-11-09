"""P2-F: deterministic plugin SBOM and explicit in-process trust boundary."""

from __future__ import annotations

import json

from onto_platform.application.smartui.contracts import PluginManifest
from onto_platform.application.smartui.external import external_plugins_enabled
from onto_platform.application.smartui.host import PluginHost


class _Secretive:
    def __repr__(self) -> str:  # pragma: no cover - only asserted absent
        return "SECRET_IMPLEMENTATION_REPR"


def _manifest(**overrides) -> PluginManifest:
    data = {
        "id": "acme.inspector",
        "kind": "view",
        "version": "1.0.0",
        "builtin": True,
        "permissions": ("view.render",),
    }
    data.update(overrides)
    return PluginManifest(**data)


def test_sbom_is_deterministic_and_states_the_trust_boundary():
    host = PluginHost()
    host.register(_manifest(), _Secretive())

    first = host.sbom()
    second = host.sbom()

    assert first == second
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert first["bomFormat"] == "OntoPlatform-SBOM"
    assert first["trustBoundary"] == "in-process"
    assert first["processIsolation"] is False
    assert first["externalPluginsEnabled"] == external_plugins_enabled()
    assert len(first["components"]) == 1
    component = first["components"][0]
    assert component["name"] == "acme.inspector"
    assert component["kind"] == "view"
    assert component["permissions"] == ["view.render"]
    assert len(component["manifestDigest"]) == 64


def test_sbom_never_serialises_implementation_objects():
    host = PluginHost()
    host.register(_manifest(), _Secretive())

    payload = json.dumps(host.sbom(), ensure_ascii=False)

    assert "SECRET_IMPLEMENTATION_REPR" not in payload
    assert "implementation" not in payload
    assert "acme.inspector" in payload


def test_manifest_digest_tracks_manifest_content():
    first = PluginHost()
    first.register(_manifest(version="1.0.0"), _Secretive())
    second = PluginHost()
    second.register(_manifest(version="1.0.1"), _Secretive())

    assert (first.sbom()["components"][0]["manifestDigest"]
            != second.sbom()["components"][0]["manifestDigest"])


def test_default_host_sbom_covers_builtins():
    from onto_platform.application.smartui.builtin import build_default_host

    sbom = build_default_host().sbom()

    assert sbom["components"]
    kinds = {component["kind"] for component in sbom["components"]}
    assert {"view", "context", "generator"} & kinds
    assert all(component["builtin"] is True for component in sbom["components"])
