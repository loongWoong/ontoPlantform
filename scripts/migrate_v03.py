#!/usr/bin/env python3
"""v0.2 -> v0.3 data migration.

The remediation adds fields to documents that already exist in a v0.2
database.  This script reads the ``documents`` table directly so it does not
start a platform, does not publish a release and does not touch the kernel
runtime.  It is dry-run by default: it prints exactly what would change and
writes nothing unless ``--apply`` is passed.

What it normalizes
------------------
* ``execution``: a process that died is not a success.  A ``running`` row with
  no live process is converted to ``unknown`` with ``in_flight=false`` and a
  migration note; it is never marked succeeded.  Missing ``in_flight`` on a
  terminal row becomes ``false``.
* ``deployment``: a current document without ``status`` is treated as
  ``active`` (v0.2 wrote the pointer before it had an explicit state).
* ``projection_run``: missing ``object_refs`` becomes an empty list, so the
  ownership-based reconciler treats older runs as owning nothing rather than
  reporting every object as foreign drift.
* ``event``: missing ``attempts`` becomes 0 and ``delivery`` is left unset
  until a dispatcher actually processes the event.

The migration is idempotent: running it twice changes nothing the second time.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _migrate(kind: str, data: dict, now: str) -> tuple[dict, list[str]]:
    changed: list[str] = []
    new = dict(data)
    if kind == "execution":
        status = new.get("status")
        if status == "running" or new.get("in_flight") is True:
            if status == "running":
                new["status"] = "unknown"
                changed.append("running->unknown (process restarted)")
            new["in_flight"] = False
            new.setdefault("recovered_at", now)
            changed.append("in_flight=false")
        elif "in_flight" not in new:
            new["in_flight"] = False
            changed.append("in_flight=false")
        if "attempt" not in new:
            new["attempt"] = 1
            changed.append("attempt=1")
        if "updated_at" not in new:
            new["updated_at"] = new.get("created_at") or now
            changed.append("updated_at")
    elif kind == "deployment":
        if not new.get("status"):
            new["status"] = "active"
            changed.append("status=active")
        if "kind" not in new:
            new["kind"] = "deployment"
            changed.append("kind=deployment")
    elif kind == "projection_run":
        if "object_refs" not in new:
            new["object_refs"] = []
            changed.append("object_refs=[] (ownership starts empty)")
        if "status" not in new:
            new["status"] = "succeeded"
            changed.append("status=succeeded")
    elif kind == "event":
        if "attempts" not in new:
            new["attempts"] = 0
            changed.append("attempts=0")
        if "next_attempt_at" not in new:
            new["next_attempt_at"] = None
            changed.append("next_attempt_at=null")
    elif kind == "release":
        if "fingerprint" not in new:
            new["fingerprint"] = ""
            changed.append("fingerprint='' (recomputed on deploy)")
    return new, changed


KINDS = ("execution", "deployment", "projection_run", "event", "release")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="platform sqlite database")
    parser.add_argument("--apply", action="store_true",
                        help="write changes; without it this is a dry run")
    args = parser.parse_args()
    path = Path(args.db)
    if not path.exists():
        print(f"database not found: {path}")
        return 2
    now = _now()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT kind, workspace_id, entity_id, data FROM documents "
            f"WHERE kind IN ({','.join('?' for _ in KINDS)})",
            KINDS,
        ).fetchall()
        pending: list[tuple[str, str | None, str, dict, list[str]]] = []
        for row in rows:
            try:
                data = json.loads(row["data"])
            except (TypeError, ValueError):
                print(f"skip malformed document {row['kind']}/{row['entity_id']}")
                continue
            if not isinstance(data, dict):
                continue
            new, changed = _migrate(row["kind"], data, now)
            if changed:
                pending.append((row["kind"], row["workspace_id"],
                                row["entity_id"], new, changed))
        if not pending:
            print(f"nothing to migrate ({len(rows)} documents inspected)")
            return 0
        for kind, wid, entity_id, new, changed in pending:
            print(f"[{'apply' if args.apply else 'dry-run'}] "
                  f"{kind}/{wid or '-'}/{entity_id}: {', '.join(changed)}")
        if not args.apply:
            print(f"\n{len(pending)} document(s) would change; "
                  "rerun with --apply to write")
            return 0
        with conn:
            for kind, wid, entity_id, new, changed in pending:
                conn.execute(
                    "UPDATE documents SET data = ?, version = version + 1 "
                    "WHERE kind = ? AND workspace_id IS ? AND entity_id = ?",
                    (json.dumps(new, ensure_ascii=False), kind, wid, entity_id),
                )
                conn.execute(
                    "INSERT INTO audit_log "
                    "(ts, actor, workspace_id, action, target, detail) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (now, "system.migration", wid, "data.migrated",
                     f"{kind}/{entity_id}",
                     json.dumps({"from": "0.2", "to": "0.3",
                                 "changes": changed}, ensure_ascii=False)),
                )
        print(f"\nmigrated {len(pending)} document(s) to 0.3")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
