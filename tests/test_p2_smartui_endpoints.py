"""P2-F HTTP surface: generation scorecard and plugin SBOM."""

from __future__ import annotations

from tests.conftest import http_get


def test_smartui_eval_endpoint_returns_a_scorecard(http_server):
    base, wid = http_server

    status, body = http_get(base, f"/api/workspaces/{wid}/smart-ui/eval")

    assert status == 200, body
    assert body["workspace_id"] == wid
    assert body["current"]["count"] == 0


def test_smartui_sbom_endpoint_states_trust_boundary(http_server):
    base, wid = http_server

    status, body = http_get(base, f"/api/workspaces/{wid}/smart-ui/sbom")

    assert status == 200, body
    assert body["bomFormat"] == "OntoPlatform-SBOM"
    assert body["trustBoundary"] == "in-process"
    assert body["components"]
