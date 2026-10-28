"""Mutation gate (plan-v2 §5.3): test the tests, not just the code.

Stdlib-only, no mutmut dependency.  Each entry below is a *known semantic
mutation* of one line in ``src/onto_platform`` with the smallest test set that
must catch it.  For every mutation the script:

1. copies ``src/`` into a throwaway directory,
2. applies the mutation there (the real source is never touched),
3. runs the mapped tests with ``PYTHONPATH`` pointing at the copy,
4. records KILLED (tests failed) or SURVIVED (tests passed).

Score = killed / total.  Exit code 0 only when the score meets ``--threshold``
(default 0.6 for Phase 0/1 files).  A report is written to
``docs/mutation-report.json``.

Usage:
    python scripts/mutation_check.py
    python scripts/mutation_check.py --list
    python scripts/mutation_check.py --threshold 0.75 --only validation
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SRC_PKG = SRC / "onto_platform"

#: (name, source file under src/onto_platform, find, replace, tests)
MUTATIONS: list[tuple[str, str, str, str, list[str]]] = [
    (
        "value-category-identity-rejected",
        "domain/ontology/validation.py",
        '        if otype.category == "value" and otype.identity:\n            problems.append(',
        '        if False:\n            problems.append(',
        ["tests/test_modeling_lint.py"],
    ),
    (
        "dangling-relation-target-rejected",
        "domain/ontology/validation.py",
        "        if rel.target not in ontology.objects:",
        "        if False:",
        ["tests/test_plan_acceptance.py"],
    ),
    (
        "unit-mismatch-rejected",
        "application/data_usecases.py",
        'if field_map.unit and declared and field_map.unit != declared:',
        "if False:",
        ["tests/test_modeling_lint.py"],
    ),
    (
        "fingerprint-mismatch-detected",
        "application/common.py",
        "if actual == expected:",
        "if True:",
        ["tests/test_revision_contract.py"],
    ),
    (
        "history-window-refused",
        "application/history.py",
        "if seq > 0 and oldest and seq < oldest:",
        "if False:",
        ["tests/test_history.py"],
    ),
    (
        "illegal-lifecycle-transition-rejected",
        "domain/governance/model.py",
        "if current not in allowed_from:",
        "if False:",
        ["tests/test_governance.py"],
    ),
    (
        "objective-band-respected",
        "application/objective_usecases.py",
        "ok = current >= target - band",
        "ok = current >= target + band",
        ["tests/test_objective.py"],
    ),
    (
        "objective-breach-opens-todo",
        "application/objective_usecases.py",
        "        if ok:\n            return None",
        "        if False:\n            return None",
        ["tests/test_objective.py"],
    ),
    (
        "idempotent-replay-returns-before-execute",
        "application/capability_usecases.py",
        'if prior is not None and prior.get("status") != "unknown":',
        "if False:",
        ["tests/test_idempotency.py"],
    ),
    (
        "projection-watermark-skips-unchanged",
        "application/data_usecases.py",
        'if not force and last is not None and last.get("watermark") == watermark \\',
        "if False and last is not None and last.get(\"watermark\") == watermark \\",
        ["tests/test_contract_verifier.py"],
    ),
    (
        "as-of-does-not-undo-future-changes",
        "application/history.py",
        'lambda entry: int(entry["seq"]) > seq',
        "lambda entry: True",
        ["tests/test_history.py"],
    ),
    (
        "viewer-cannot-write",
        "domain/governance/model.py",
        "if _ROLE_RANK.get(m.role, -1) >= rank_needed:",
        "if False:",
        ["tests/test_governance.py"],
    ),
]


def _run_mutation(name: str, relative: str, find: str, replace: str,
                  tests: list[str], workdir: Path) -> dict:
    source = SRC_PKG / relative
    text = source.read_text(encoding="utf-8")
    occurrences = text.count(find)
    if occurrences != 1:
        return {"name": name, "killed": False, "status": "invalid",
                "detail": f"find string occurs {occurrences} times in {relative}",
                "tests": tests, "seconds": 0.0}
    target = workdir / "src" / "onto_platform" / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text.replace(find, replace), encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(workdir / "src") + os.pathsep + str(SRC)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    started = time.monotonic()
    process = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-x", "--no-header",
         "-p", "no:cacheprovider", *tests],
        cwd=str(ROOT), env=env, capture_output=True, text=True)
    seconds = round(time.monotonic() - started, 2)
    killed = process.returncode != 0
    detail = "" if killed else (process.stdout or "")[-400:]
    return {"name": name, "killed": killed,
            "status": "KILLED" if killed else "SURVIVED",
            "detail": detail, "tests": tests, "seconds": seconds}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threshold", type=float, default=0.6)
    parser.add_argument("--only", default="", help="substring filter on names")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--report", default="docs/mutation-report.json")
    args = parser.parse_args()
    selected = [m for m in MUTATIONS if args.only in m[0]]
    if args.list:
        for mutation in selected:
            print(f"{mutation[0]:45s} {mutation[1]:40s} {mutation[4]}")
        return 0
    results: list[dict] = []
    for name, relative, find, replace, tests in selected:
        with tempfile.TemporaryDirectory(prefix="onto-mut-") as tmp:
            workdir = Path(tmp)
            # seed the sandbox with a full copy so relative imports work
            shutil.copytree(SRC, workdir / "src",
                            ignore=shutil.ignore_patterns("__pycache__"))
            result = _run_mutation(name, relative, find, replace, tests, workdir)
        results.append(result)
        print(f"[{result['status']:8s}] {name} ({result['seconds']}s)")
        if result["detail"]:
            print("          " + result["detail"].replace("\n", "\n          "))
    killed = sum(1 for r in results if r["killed"])
    total = len(results)
    score = killed / total if total else 0.0
    report = {"score": round(score, 4), "killed": killed, "total": total,
              "threshold": args.threshold, "results": results}
    report_path = ROOT / args.report
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(f"\nmutation score: {killed}/{total} = {score:.0%} "
          f"(threshold {args.threshold:.0%}) -> {report_path}")
    return 0 if score >= args.threshold else 1


if __name__ == "__main__":
    raise SystemExit(main())
