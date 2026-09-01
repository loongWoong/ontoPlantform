"""WorkspaceGatewayProvider: one kernel runtime per workspace.

Workspaces are isolation containers (design.txt §4); each owns its own
kernel instance (schema + state + executor), created lazily on first
publish and cached for the process lifetime.
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
        self._gateways: dict[str, KernelRuntimeGateway] = {}

    def for_workspace(self, workspace_id: str, runtime_config: dict):
        if workspace_id not in self._gateways:
            store_kind = runtime_config.get("store") or self._default_store
            db_path = runtime_config.get("db_path")
            if store_kind == "sqlite" and not db_path:
                data_dir = Path(self._runtime_data_dir or ".onto/runtime")
                data_dir.mkdir(parents=True, exist_ok=True)
                db_path = str(data_dir / f"runtime_{workspace_id}.db")
            try:
                self._gateways[workspace_id] = KernelRuntimeGateway(
                    store_kind=store_kind, db_path=db_path
                )
            except ValidationError:
                raise
            except Exception as exc:
                raise ValidationError(f"cannot create runtime: {exc}") from exc
        return self._gateways[workspace_id]
