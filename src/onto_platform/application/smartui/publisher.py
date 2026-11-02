"""Publisher contribution for Smart-UI.

A publisher turns an accepted draft into the immutable artifact attached to an
application.  The core still owns authorization, draft state and attachment;
the plugin only owns the artifact shape.  That keeps a publisher from granting
itself extra state transitions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...errors import ValidationError


@dataclass(frozen=True)
class PublishRequest:
    """Everything the publisher may read; it cannot mutate the draft."""

    platform: Any
    workspace_id: str
    actor: str
    draft: dict[str, Any]
    source_row: dict[str, Any]
    published_at: str


class DefaultPublisher:
    """The built-in artifact publisher."""

    id = "onto.smartui.publisher.default"
    version = "1.1.0"

    def build_artifact(self, request: PublishRequest) -> dict[str, Any]:
        draft = request.draft
        source_trace = request.source_row.get("trace") or {}
        artifact = {
            "generation_id": request.source_row.get("id"),
            "draft_id": draft.get("id"),
            "version": draft.get("version"),
            "program_hash": draft.get("program_hash"),
            "published_at": request.published_at,
            "published_by": request.actor,
            "name": draft.get("name"),
            # The artifact is the stable published contract.  Keeping the
            # adjudicated blueprint (not just a generation id) lets the
            # application render after the generation record is gone, and lets
            # an engineer see exactly what was published.  The program is kept
            # for reproducibility; the live view is still recompiled from the
            # blueprint so data stays fresh and permissions are re-checked.
            "blueprint": draft.get("blueprint"),
            "program": draft.get("program"),
            "question": draft.get("question"),
            "roles": list(draft.get("roles") or []),
            "ontology_version": source_trace.get("ontologyVersion"),
            "engine_version": source_trace.get("engineVersion"),
            "plugin_lock": (draft.get("plugin_lock")
                            or source_trace.get("pluginLock") or []),
            "pack": draft.get("pack") or source_trace.get("workspacePack"),
        }
        if not artifact["blueprint"]:
            raise ValidationError("published draft has no blueprint")
        return artifact
