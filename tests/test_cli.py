"""CLI adapter smoke tests (drives main() directly)."""

from __future__ import annotations

import json

import pytest

from onto_platform.interfaces.cli import main
from tests.conftest import RECHARGE_SOURCE


@pytest.fixture()
def db(tmp_path):
    return str(tmp_path / "cli.db")


@pytest.fixture()
def cli(db, capsys):
    def run(*argv, expect_error=False):
        code = main(["--db", db, *argv])
        out, err = capsys.readouterr()
        assert code == (1 if expect_error else 0), err or out
        if expect_error:
            return None, err
        return json.loads(out), out

    return run


def test_workspace_and_ontology_flow(cli):
    ws, _ = cli("workspace", "create", "高速运营")
    wid = ws["id"]
    W = ["--workspace", wid]

    cli(*W, "ontology", "create", "traffic")
    cli(*W, "ontology", "add-object", "Vehicle",
        "--prop", "plate:string", "--prop", "balance:number", "--description", "车辆")
    cli(*W, "ontology", "add-object", "TollStation",
        "--prop", "name:string", "--prop", "status:string")
    cli(*W, "ontology", "add-relation", "passes", "Vehicle", "TollStation")
    cli(*W, "ontology", "add-term", "收费站", "TollStation")
    problems, _ = cli(*W, "ontology", "validate")
    assert problems["ok"] is True
    payload, _ = cli(*W, "ontology", "publish")
    assert {o["name"] for o in payload["runtime"]["objects"]} == {"Vehicle", "TollStation"}


def test_data_capability_state_flow(cli, tmp_path):
    ws, _ = cli("workspace", "create", "高速运营")
    wid = ws["id"]
    W = ["--workspace", wid]
    cli(*W, "ontology", "create", "traffic")
    cli(*W, "ontology", "add-object", "Vehicle",
        "--prop", "plate:string", "--prop", "balance:number")
    cli(*W, "ontology", "add-object", "TollStation",
        "--prop", "name:string", "--prop", "status:string")
    cli(*W, "ontology", "add-relation", "passes", "Vehicle", "TollStation")
    cli(*W, "ontology", "add-term", "车辆", "Vehicle")
    cli(*W, "ontology", "publish")

    rows = tmp_path / "vehicles.json"
    rows.write_text(json.dumps([
        {"vid": "V001", "plate": "鲁A", "balance": 10},
        {"vid": "V002", "plate": "鲁B", "balance": 20},
    ], ensure_ascii=False), encoding="utf-8")

    source, _ = cli(*W, "data", "add-source", "主数据", "--kind", "static",
                    "--asset", f"vehicles=@{rows}")
    projection, _ = cli(*W, "data", "add-projection", "车辆投影",
                        "--source", source["id"],
                        "--object-mapping", json.dumps({
                            "asset": "vehicles", "object_type": "Vehicle",
                            "id_field": "vid",
                            "fields": [
                                {"source_field": "plate", "property": "plate"},
                                {"source_field": "balance", "property": "balance"}]}))
    summary, _ = cli(*W, "data", "run-projection", projection["id"])
    assert summary["objects_upserted"] == 2

    source_file = tmp_path / "recharge.py"
    source_file.write_text(RECHARGE_SOURCE, encoding="utf-8")
    capability, _ = cli(*W, "capability", "create", "recharge", "--name", "车辆充值",
                        "--target", "Vehicle", "--input", "amount:number",
                        "--source", str(source_file))
    cli(*W, "capability", "publish", capability["id"])

    result, _ = cli(*W, "capability", "invoke", "Vehicle.recharge",
                    "--target", "Vehicle/V001", "--arg", "amount=90")
    assert result["output"]["balance"] == 100

    found, _ = cli(*W, "state", "find", "Vehicle", "--filter", "balance__gt=50")
    assert [o["id"] for o in found] == ["V001"]

    changes, _ = cli(*W, "state", "changes")
    assert any(c["source"] == "invoke" for c in changes)

    answer, _ = cli(*W, "ask", "哪些车辆余额大于50？")
    assert "V001" in answer["answer"]


def test_error_exit_code(cli):
    ws, _ = cli("workspace", "create", "ws")
    _, err = cli("--workspace", ws["id"], "ontology", "publish", expect_error=True)
    assert "error:" in err
