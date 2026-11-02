"""Ports for the Smart-UI plugin host.

The host itself is application machinery; these protocols are the contracts a
plugin implements.  They deliberately avoid importing the concrete
``Platform`` or engine classes so a plugin can be tested in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class PluginRef:
    """Stable identity of one installed plugin version."""

    id: str
    version: str
    kind: str


@dataclass(frozen=True)
class PluginDiagnostic:
    """One adjudication finding, attached to a plugin contribution."""

    code: str
    message: str
    severity: str = "warning"  # info | warning | error
    path: str = ""


@dataclass
class Candidate:
    """Output of a generator before final blueprint adjudication."""

    source: str
    proposal: dict[str, Any] | None = None
    blueprint: dict[str, Any] | None = None
    compiled: dict[str, Any] | None = None
    trace: dict[str, Any] = field(default_factory=dict)
    diagnostics: list[PluginDiagnostic] = field(default_factory=list)


class Generator(Protocol):
    """Turn a generation request into a candidate blueprint/proposal."""

    id: str
    version: str

    def generate(self, request: Any) -> Candidate: ...


class PromptPack(Protocol):
    """Contribute the prompt contract for a generation request."""

    id: str
    version: str

    def render(self, request: Any) -> tuple[str, str]: ...


class Adjudicator(Protocol):
    """Validate or repair a proposal before compilation."""

    id: str
    version: str
    order: int

    def adjudicate(self, proposal: dict[str, Any], context: Any) -> Any: ...


class ViewPlugin(Protocol):
    """Describe one presentation type and resolve its runtime payload."""

    id: str
    version: str
    perspective: str
    view_type: str
    component: str

    def options_schema(self) -> dict[str, Any]: ...
    def adjudicate_options(self, raw: Any, obj: Any, perspective: str,
                           pruned: list[dict[str, Any]]) -> dict[str, Any]: ...
    def resolve(self, context: Any, options: dict[str, Any]) -> dict[str, Any]: ...


class Renderer(Protocol):
    """Describe the frontend component that draws one view type.

    The component itself ships in the build-time bundle; this contract only
    makes the mapping explicit so the API and the execution lock can version it.
    """

    id: str
    version: str
    view_type: str
    component: str

    def descriptor(self) -> dict[str, Any]: ...


class Publisher(Protocol):
    """Turn a succeeded generation into a draft/published artifact."""

    id: str
    version: str

    def lock(self) -> list[PluginRef]: ...
