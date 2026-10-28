"""Meta-contract: the registry has no empty or dangling entries (plan-v2 DoD).

This test does not execute the contract tests again; it proves that every
K1-K10 / C1-C10 contract is registered with at least one concrete test and
that all referenced test functions really exist in the source tree.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from contracts.registry import CONTRACTS

ROOT = Path(__file__).resolve().parents[1]


def _test_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            names.add(node.name)
    return names


def _check_node_ids(node_ids: list[str], root: Path) -> list[str]:
    """Return problems for node ids that do not name an existing test."""
    problems: list[str] = []
    for node_id in node_ids:
        file_part, _, function = node_id.partition("::")
        path = root / file_part
        if not path.exists():
            problems.append(f"missing file: {node_id}")
            continue
        if not function:
            problems.append(f"missing test function: {node_id}")
            continue
        if function not in _test_names(path):
            problems.append(f"unknown test function: {node_id}")
    return problems


def _kernel_root() -> Path | None:
    candidate = Path(os.environ.get("ONTORK_PATH", ROOT.parent / "ontoRK"))
    return candidate if candidate.is_dir() else None


def test_registry_ids_are_exactly_k1_k10_and_c1_c10():
    ids = [entry["id"] for entry in CONTRACTS]
    assert ids == [f"K{i}" for i in range(1, 11)] + [f"C{i}" for i in range(1, 11)]
    assert len(set(ids)) == len(ids)


def test_every_contract_has_a_concrete_test():
    empty = [entry["id"] for entry in CONTRACTS
             if not entry["platform"] and not entry["kernel"]]
    assert empty == [], f"contracts without any covering test: {empty}"
    for entry in CONTRACTS:
        assert entry["name"].strip(), entry["id"]
        for kind in ("platform", "kernel"):
            for node_id in entry[kind]:
                assert "::test_" in node_id, (entry["id"], node_id)


def test_platform_node_ids_exist_in_source():
    problems: list[str] = []
    for entry in CONTRACTS:
        problems.extend(f"{entry['id']}: {p}"
                        for p in _check_node_ids(entry["platform"], ROOT))
    assert problems == [], "\n".join(problems)


def test_kernel_node_ids_exist_when_checkout_is_present():
    kernel = _kernel_root()
    if kernel is None:
        pytest.skip("ontoRK checkout not present; kernel-side coverage checked in its CI")
    problems: list[str] = []
    for entry in CONTRACTS:
        problems.extend(f"{entry['id']}: {p}"
                        for p in _check_node_ids(entry["kernel"], kernel))
    assert problems == [], "\n".join(problems)


def test_no_duplicate_platform_entries_within_contract():
    for entry in CONTRACTS:
        assert len(set(entry["platform"])) == len(entry["platform"]), entry["id"]
