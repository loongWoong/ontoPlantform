"""Small, dependency-free Smart-UI API version compatibility helpers.

The manifest keeps ``api_version`` as the single declared version string.  A
plugin may additionally declare ``api_versions`` as a set of constraints for
ranges.  The host compares those constraints with its own API version; this is
intentionally not a general semver implementation.
"""

from __future__ import annotations

import re

_API_RE = re.compile(r"^smartui/v(\d+)(?:\.(\d+))?(?:\.(\d+))?$")
_COMPARATOR_RE = re.compile(
    r"^(>=|<=|>|<|=)?\s*(smartui/v\d+(?:\.\d+){0,2})$")


def parse_api_version(value: str) -> tuple[int, int, int]:
    match = _API_RE.match(str(value or "").strip())
    if match is None:
        raise ValueError(f"invalid Smart-UI API version: {value!r}")
    major = int(match.group(1))
    minor = int(match.group(2) or 0)
    patch = int(match.group(3) or 0)
    return major, minor, patch


def format_api_version(version: tuple[int, int, int]) -> str:
    major, minor, patch = version
    suffix = f".{minor}" if minor else ""
    if patch:
        suffix += f".{patch}"
    return f"smartui/v{major}{suffix}"


def _satisfies_one(host: tuple[int, int, int], token: str) -> bool:
    match = _COMPARATOR_RE.match(token.strip())
    if match is None:
        raise ValueError(f"invalid API version constraint: {token!r}")
    operator = match.group(1) or "="
    wanted = parse_api_version(match.group(2))
    if operator == "=":
        return host == wanted
    if operator == ">=":
        return host >= wanted
    if operator == "<=":
        return host <= wanted
    if operator == ">":
        return host > wanted
    return host < wanted


def satisfies(host_version: str, constraint: str) -> bool:
    """Return True when the host satisfies a space-separated constraint set.

    Examples: ``smartui/v1`` (exact), ``>=smartui/v1 <smartui/v2``.
    ``*`` matches any host version.
    """
    text = str(constraint or "").strip()
    if text in {"", "*", "smartui/*"}:
        return True
    host = parse_api_version(host_version)
    return all(_satisfies_one(host, token) for token in text.split())


def compatible(plugin_version: str, host_version: str) -> bool:
    """Default compatibility for manifests that do not declare a range.

    A plugin targeting ``smartui/v1`` runs on a host ``smartui/v1.2``; a
    plugin targeting ``smartui/v1.2`` does not run on ``smartui/v1``.
    """
    plugin = parse_api_version(plugin_version)
    host = parse_api_version(host_version)
    return plugin[0] == host[0] and host[1:] >= plugin[1:]
