"""examples/toll must stay loadable: the sample system is a test fixture too.

This re-generates ``data/toll.db`` from its fixed seed and runs the whole
bootstrap chain on an in-memory platform, so the sample cannot silently rot
when the ontology / data / projection contract changes.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

SAMPLE = Path(__file__).resolve().parents[1] / "examples" / "toll"
DB_FILE = SAMPLE / "data" / "toll.db"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def build_db():
    return _load("toll_build_db", SAMPLE / "data" / "build_db.py")


@pytest.fixture(scope="module")
def toll(build_db):
    return _load("toll_bootstrap", SAMPLE / "bootstrap.py")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_sample_files_exist(toll):
    assert (SAMPLE / "business.md").is_file()
    text = toll.load_business_text()
    assert len(text) > 300, "business.md 需要是一段完整的业务说明，供 LLM 解析"
    for doc in ("data-dictionary.md", "business-rules.md", "operations.md"):
        assert (SAMPLE / "docs" / doc).is_file()
    expected = json.loads((SAMPLE / "expected-ontology.json").read_text(encoding="utf-8"))
    assert len(expected["objects"]) == 4
    assert len(expected["relations"]) == 4
    assert len(expected["vocabulary"]) == 15
    names = {obj["name"] for obj in expected["objects"]}
    for relation in expected["relations"]:
        assert relation["source"] in names and relation["target"] in names


def test_db_build_is_reproducible(build_db, capsys):
    assert build_db.main() == 0
    first = _sha(DB_FILE)
    assert build_db.main() == 0
    assert _sha(DB_FILE) == first, "固定种子的样例库必须逐字节可复现"
    out = capsys.readouterr().out
    assert "84" in out


def test_bootstrap_runs_the_whole_chain_in_memory(toll, build_db):
    build_db.main()
    expected = toll.load_expected()
    report = toll.bootstrap(toll.MemoryBackend(), expected)

    assert report["object_types"] == 4
    assert report["relations"] == 4
    assert report["terms"] == 15
    assert report["upserts"] == 84
    assert report["links"] == 138
    assert report["counts"] == {
        "TollStation": 6, "Lane": 18, "Vehicle": 20, "TollTransaction": 40,
    }
    station = report["samples"]["TollStation"]
    assert station["id"] == "ST001"
    assert station["properties"]["status"] == "congested"


def test_projection_mappings_match_the_expected_ontology(toll):
    expected = toll.load_expected()
    names = {obj["name"] for obj in expected["objects"]}
    props = {obj["name"]: {p["name"] for p in obj["properties"]} for obj in expected["objects"]}
    relation_names = {rel["name"] for rel in expected["relations"]}

    for mapping in toll.OBJECT_MAPPINGS:
        assert mapping["object_type"] in names
        assert mapping["id_field"]
        for field in mapping["fields"]:
            assert field["property"] in props[mapping["object_type"]], field
    for mapping in toll.RELATION_MAPPINGS:
        assert mapping["relation"] in relation_names
    assets = {asset["name"] for asset in toll.ASSETS}
    for mapping in toll.OBJECT_MAPPINGS + toll.RELATION_MAPPINGS:
        assert mapping["asset"] in assets
