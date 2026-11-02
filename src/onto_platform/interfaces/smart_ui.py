"""Compatibility facade for the Smart-UI bridge.

The implementation lives in :mod:`onto_platform.application.smartui.bridge`.
HTTP routes and older tests keep importing this module; deleting it would
force every caller to know the internal package layout.  New code should
import the application module directly.
"""

from __future__ import annotations

from typing import Any

from ..application.smartui import bridge as _bridge

# Import a few names explicitly so ``from .smart_ui import name`` remains
# discoverable to static tooling; ``__getattr__`` covers the rest (including
# the private helpers that characterization tests exercise).
ENGINE_VERSION = _bridge.ENGINE_VERSION
ROLE_IDS = _bridge.ROLE_IDS
ROLE_OPTIONS = _bridge.ROLE_OPTIONS
compile_smart_ui = _bridge.compile_smart_ui
compile_smart_ui_blueprint = _bridge.compile_smart_ui_blueprint
invoke_smart_action = _bridge.invoke_smart_action
smart_ui_engine_available = _bridge.smart_ui_engine_available
smart_ui_focus_options = _bridge.smart_ui_focus_options
smart_ui_meta = _bridge.smart_ui_meta
smart_ui_snapshot = _bridge.smart_ui_snapshot


def __getattr__(name: str) -> Any:
    return getattr(_bridge, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_bridge)))
