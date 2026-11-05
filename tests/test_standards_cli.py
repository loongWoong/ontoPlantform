"""CLI wiring for standard-format adapter plugins (T3).

``onto standards list`` prints the discovered adapters, ``onto standards
export`` writes the artifact either to ``--out`` or to stdout.  The tests
drive ``cli.main`` directly (like tests/test_cli.py) with the seeded platform
injected through the composition-root seam, so no on-disk database is needed
and the assertions stay about argument handling / output, not about SQLite.
"""

from __future__ import annotations

import json

import pytest

from onto_platform.application.standards_usecases import StandardsUseCases
from onto_platform.interfaces import cli
from onto_platform.interfaces.cli import main
from onto_platform.semantic.registry import PluginRegistry
from onto_platform.semantic.spi import ADAPTER_API_VERSION, AdapterManifest


class FailingAdapter:
    """A registered plugin whose ``export`` always raises (decision D7)."""

    manifest = AdapterManifest(
        name="failing-adapter",
        version="9.9.9",
        api_version=ADAPTER_API_VERSION,
        capabilities=("export",),
        formats_out=("text/turtle",),
        description="test double that always fails",
    )

    def export(self, ctx, request):
        raise RuntimeError("plugin exploded")


def use_private_registry(platform, *adapters) -> PluginRegistry:
    registry = PluginRegistry()
    for adapter in adapters:
        registry.register(adapter)
    platform.standards = StandardsUseCases(platform.ctx, registry=registry)
    return registry


@pytest.fixture()
def standards_cli(platform, workspace_id, monkeypatch):
    """Seeded platform behind ``cli.main`` (composition root seam)."""
    monkeypatch.setattr(cli, "build_platform", lambda config=None: platform)
    return platform, workspace_id


def test_list_adapters(standards_cli, capsys):
    _, wid = standards_cli
    code = main(["standards", "list", "--workspace", wid])

    out, err = capsys.readouterr()
    assert code == 0, err
    rows = json.loads(out)
    assert {"owl-rdf", "skos"} <= {row["name"] for row in rows}
    assert all(row["status"] == "ready" for row in rows)


def test_export_writes_file(standards_cli, tmp_path, capsys):
    _, wid = standards_cli
    target = tmp_path / "onto" / "traffic.ttl"

    code = main(["standards", "export", "--workspace", wid,
                 "--adapter", "owl-rdf", "--format", "text/turtle",
                 "--out", str(target)])

    out, err = capsys.readouterr()
    assert code == 0, err
    assert target.is_file()
    assert target.stat().st_size > 0
    text = target.read_text(encoding="utf-8")
    assert "owl:Class" in text
    assert "rdf:type" in text

    summary = json.loads(out)
    assert summary["adapter"] == "owl-rdf"
    assert summary["format"] == "text/turtle"
    assert summary["path"] == str(target)
    assert summary["bytes"] == target.stat().st_size
    # provenance travels with the summary; the body itself is never embedded
    metadata = summary["metadata"]
    assert metadata["actor"] == "admin"
    assert metadata["ontology_version"] == 1
    assert metadata["adapter_version"] == "0.1.0"
    assert metadata["schema_fingerprint"]
    assert metadata["workspace_id"]
    assert "content" not in summary


def test_export_default_format_and_global_workspace_flag(standards_cli,
                                                         tmp_path):
    _, wid = standards_cli
    target = tmp_path / "default.ttl"

    # --workspace before the command is the documented global form; --format
    # omitted must default to text/turtle.
    code = main(["--workspace", wid, "standards", "export",
                 "--adapter", "skos", "--out", str(target)])

    assert code == 0
    text = target.read_text(encoding="utf-8")
    assert "skos:ConceptScheme" in text
    assert "skos:Concept" in text


def test_export_to_stdout(standards_cli, capfd):
    _, wid = standards_cli
    code = main(["standards", "export", "--workspace", wid,
                 "--adapter", "owl-rdf"])

    out, err = capfd.readouterr()
    assert code == 0, err
    assert "owl:Class" in out
    assert "rdf:type" in out


def test_unknown_adapter_exits_non_zero(standards_cli, capsys):
    _, wid = standards_cli
    code = main(["standards", "export", "--workspace", wid,
                 "--adapter", "does-not-exist"])

    out, err = capsys.readouterr()
    assert code == 1
    assert "error:" in err
    assert "does-not-exist" in err


def test_missing_adapter_is_a_usage_error(standards_cli):
    _, wid = standards_cli
    with pytest.raises(SystemExit) as excinfo:
        main(["standards", "export", "--workspace", wid])
    assert excinfo.value.code not in (0, None)


def test_standards_requires_workspace(standards_cli):
    with pytest.raises(SystemExit) as excinfo:
        main(["standards", "list"])
    assert excinfo.value.code not in (0, None)


def test_export_accepts_architecture_alias(standards_cli, tmp_path, capsys):
    _, wid = standards_cli
    target = tmp_path / "alias.ttl"

    code = main(["standards", "export", "--workspace", wid,
                 "--adapter", "onto-standards-owl", "--out", str(target)])

    out, err = capsys.readouterr()
    assert code == 0, err
    summary = json.loads(out)
    assert summary["adapter"] == "onto-standards-owl"
    assert "owl:Class" in target.read_text(encoding="utf-8")


def test_adapter_failure_exits_one(standards_cli, capsys):
    platform, wid = standards_cli
    use_private_registry(platform, FailingAdapter())

    code = main(["standards", "export", "--workspace", wid,
                 "--adapter", "failing-adapter"])

    out, err = capsys.readouterr()
    assert code == 1
    assert "error:" in err
    assert "plugin exploded" in err
    assert "owl:Class" not in out


def test_standards_absent_exits_one(standards_cli, monkeypatch, capsys):
    platform, wid = standards_cli
    monkeypatch.setattr(platform, "standards", None)

    code = main(["standards", "list", "--workspace", wid])

    out, err = capsys.readouterr()
    assert code == 1
    assert out == ""
    assert "not available" in err
