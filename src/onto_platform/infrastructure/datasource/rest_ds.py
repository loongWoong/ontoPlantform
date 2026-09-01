"""RestConnector: an HTTP JSON API as a DataSource (stdlib urllib)."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

_LIST_KEYS = ("items", "data", "rows", "results", "records")


class RestConnector:
    def __init__(self, config: dict) -> None:
        self._base_url = (config.get("base_url") or "").rstrip("/")
        if not self._base_url:
            raise ValueError("rest datasource config requires 'base_url'")
        self._headers = dict(config.get("headers", {}))
        self._timeout = float(config.get("timeout", 30))

    def fetch(self, asset: dict) -> list[dict]:
        path = asset.get("path", "")
        params = asset.get("params", {})
        url = self._base_url + path
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers=self._headers)
        with urllib.request.urlopen(request, timeout=self._timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if isinstance(payload, list):
            return [dict(row) for row in payload]
        if isinstance(payload, dict):
            for key in _LIST_KEYS:
                if isinstance(payload.get(key), list):
                    return [dict(row) for row in payload[key]]
            return [payload]
        raise ValueError(f"rest asset returned unsupported payload: {type(payload).__name__}")

    def test(self) -> bool:
        with urllib.request.urlopen(self._base_url, timeout=self._timeout) as response:
            return response.status < 400
