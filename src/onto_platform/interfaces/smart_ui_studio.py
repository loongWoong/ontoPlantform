"""Compatibility facade for Smart-UI Studio.

The implementation lives in :mod:`onto_platform.application.smartui.studio`.
The HTTP adapter and existing tests keep importing this module; new code
should import the application module directly.
"""

from __future__ import annotations

from typing import Any

from ..application.smartui import studio as _studio

GENERATION_KIND = _studio.GENERATION_KIND
DRAFT_KIND = _studio.DRAFT_KIND
accept_generation = _studio.accept_generation
application_ui_view = _studio.application_ui_view
cancel_generation = _studio.cancel_generation
delete_draft = _studio.delete_draft
delete_generation = _studio.delete_generation
generation_view = _studio.generation_view
get_generation = _studio.get_generation
list_drafts = _studio.list_drafts
list_generations = _studio.list_generations
publish_generation = _studio.publish_generation
refine_generation = _studio.refine_generation
start_generation = _studio.start_generation
studio_enabled = _studio.studio_enabled
update_draft = _studio.update_draft


def __getattr__(name: str) -> Any:
    return getattr(_studio, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_studio)))
