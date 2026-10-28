"""0.1 failure injection: killing a writer mid-transaction must not corrupt.

The child process seeds one committed sentinel row, opens a SQLite write
transaction, writes rows, reports READY and then hangs.  The parent kills it
(TerminateProcess on Windows = SIGKILL semantics).  Re-opening the database
must show: the sentinel persisted, the uncommitted rows rolled back, and the
database still usable for new writes.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from onto_platform.infrastructure.persistence.sqlite_repo import SqliteRepository

CHILD = '''
import sys
import time

from onto_platform.infrastructure.persistence.sqlite_repo import SqliteRepository

db = sys.argv[1]
repo = SqliteRepository(db)
repo.put("sentinel", "w", "s", {"ok": True})
with repo.transaction():
    for i in range(20):
        repo.put("kill9", "w", f"k{i}", {"i": i})
    print("READY", flush=True)
    time.sleep(30)
print("COMMITTED", flush=True)
'''


def test_sigkill_mid_transaction_leaves_no_partial_commit(tmp_path):
    root = Path(__file__).resolve().parents[1]
    db = tmp_path / "kill9.db"
    script = tmp_path / "writer.py"
    script.write_text(CHILD, encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root / "src") + os.pathsep + env.get("PYTHONPATH", "")
    process = subprocess.Popen(
        [sys.executable, str(script), str(db)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    try:
        line = process.stdout.readline()
        assert line.strip() == "READY", line
        process.kill()  # equivalent to kill -9: no cleanup handlers run
        assert process.wait(timeout=30) != 0
    finally:
        if process.poll() is None:
            process.kill()

    repo = SqliteRepository(str(db))
    assert repo.get("sentinel", "w", "s") == {"ok": True}
    assert repo.list("kill9", "w") == [], "killed transaction left partial rows"
    # the store is still writable afterwards
    repo.put("sentinel", "w", "after", {"ok": True})
    assert repo.get("sentinel", "w", "after") == {"ok": True}
