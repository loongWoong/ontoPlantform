"""WorkspaceGatewayProvider: one kernel runtime per workspace and ring.

Workspaces are isolation containers (design.txt §4); each owns its own
kernel instance (schema + state + executor), created lazily on first
publish and cached for the process lifetime.  Deployment rings are isolated
too: the canary has its own kernel, so staging a release can never mutate the
serving runtime's schema (P0-5).
"""

from __future__ import annotations

from pathlib import Path

from ...errors import ValidationError
from .kernel_gateway import KernelRuntimeGateway


class WorkspaceGatewayProvider:
    def __init__(self, default_store: str = "memory",
                 runtime_data_dir: str | None = None) -> None:
        self._default_store = default_store
        self._runtime_data_dir = runtime_data_dir
        self._gateways: dict[tuple[str, str], KernelRuntimeGateway] = {}

    def for_workspace(self, workspace_id: str, runtime_config: dict,
                      ring: str = "current"):
        cache_key = (workspace_id, ring)
        if cache_key not in self._gateways:
            store_kind = runtime_config.get("store") or self._default_store
            db_path = runtime_config.get("db_path")
            change_log_path = None
            if store_kind == "sqlite" and not db_path:
                data_dir = Path(self._runtime_data_dir or ".onto/runtime")
                data_dir.mkdir(parents=True, exist_ok=True)
                suffix = "" if ring == "current" else f"_{ring}"
                db_path = str(data_dir / f"runtime_{workspace_id}{suffix}.db")
                change_log_path = str(
                    data_dir / f"changes_{workspace_id}{suffix}.jsonl")
            elif store_kind == "sqlite" and ring == "canary":
                # an explicit db_path is the serving file; the canary gets
                # its own copy, never the same file (P0-5)
                base = Path(db_path)
                db_path = str(base.with_name(base.stem + "_canary.db"))
                change_log_path = str(
                    base.with_name(base.stem + "_canary_changes.jsonl"))
            if ring == "canary" and store_kind == "sqlite":
                self._clone_current_sqlite(workspace_id, db_path)
            try:
                self._gateways[cache_key] = KernelRuntimeGateway(
                    store_kind=store_kind, db_path=db_path,
                    change_log_path=change_log_path,
                )
            except ValidationError:
                raise
            except Exception as exc:
                raise ValidationError(f"cannot create runtime: {exc}") from exc
        return self._gateways[cache_key]

    def state_mode(self, workspace_id: str, ring: str = "current") -> str:
        """How much state the ring starts with (schema_only vs cloned)."""
        gateway = self._gateways.get((workspace_id, ring))
        if gateway is None:
            return "schema_only"
        store = getattr(gateway, "_store_kind", None)
        return "cloned" if store == "sqlite" else "schema_only"

    def _clone_current_sqlite(self, workspace_id: str,
                              canary_path: str) -> None:
        """Copy the serving SQLite state into the canary before it opens.

        The canary is an isolated verification ring: it may start from a
        snapshot of production state, but writes to it never reach the
        serving database.  Missing current state is not an error (a fresh
        canary simply starts schema-only).
        """
        import sqlite3

        current = self._gateways.get((workspace_id, "current"))
        source = getattr(current, "_db_path", None) if current else None
        if not source or not Path(source).exists():
            return
        target = Path(canary_path)
        if target.exists():
            return
        try:
            src = sqlite3.connect(source)
            dst = sqlite3.connect(str(target))
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()
        except Exception:
            # cloning is best-effort; the caller sees state_mode=schema_only
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
