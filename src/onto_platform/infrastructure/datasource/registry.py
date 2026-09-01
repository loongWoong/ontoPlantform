"""DataSourceConnector registry: kind -> connector implementation."""

from __future__ import annotations

from ...errors import ValidationError
from ...ports.datasource import DataSourceConnector
from .rest_ds import RestConnector
from .sqlite_ds import SqliteConnector
from .static_ds import StaticConnector

_CONNECTORS: dict[str, type] = {}


def register(kind: str, connector_cls: type) -> None:
    _CONNECTORS[kind] = connector_cls


def connector_for(kind: str, config: dict) -> DataSourceConnector:
    cls = _CONNECTORS.get(kind)
    if cls is None:
        raise ValidationError(
            f"unknown datasource kind {kind!r}; available: {sorted(_CONNECTORS)}"
        )
    return cls(dict(config))  # type: ignore[return-value]


register("static", StaticConnector)
register("sqlite", SqliteConnector)
register("rest", RestConnector)
