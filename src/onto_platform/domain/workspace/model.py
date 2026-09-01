"""Workspace: the platform-level isolation and organization container.

Workspace 负责：资源组织 / 权限隔离 / 生命周期 / 发布范围 (design.txt §4)。
v0.1 deliberately has no Solution/Project hierarchy below it.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class Workspace:
    id: str
    name: str
    description: str = ""
    created_at: str = ""
    # runtime configuration for this workspace's kernel instance:
    # {"store": "memory"|"sqlite", "db_path": str|None}
    runtime_config: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at,
            "runtime_config": dict(self.runtime_config),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Workspace":
        return cls(
            id=data["id"],
            name=data["name"],
            description=data.get("description", ""),
            created_at=data.get("created_at", ""),
            runtime_config=dict(data.get("runtime_config", {})),
        )
