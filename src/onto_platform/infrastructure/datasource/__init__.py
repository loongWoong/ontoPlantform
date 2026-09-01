from .registry import connector_for, register
from .rest_ds import RestConnector
from .sqlite_ds import SqliteConnector
from .static_ds import StaticConnector

__all__ = ["RestConnector", "SqliteConnector", "StaticConnector", "connector_for", "register"]
