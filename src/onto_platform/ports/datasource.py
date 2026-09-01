"""DataSourceConnector Port: how reality becomes rows.

Domain only knows "a source yields rows for an asset"; MySQL / ES /
Kafka / files / APIs are infrastructure implementations
(design.txt §12).
"""

from __future__ import annotations

from typing import Protocol


class DataSourceConnector(Protocol):

    def fetch(self, asset: dict) -> list[dict]:
        """Return the rows of one asset as plain dicts."""
        ...

    def test(self) -> bool:
        """Cheap connectivity check used by ConnectDataSource."""
        ...
