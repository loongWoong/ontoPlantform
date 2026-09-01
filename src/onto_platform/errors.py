"""Platform error model.

Design-time and governance failures raise; runtime failures never do
(``RuntimeGateway.invoke`` mirrors the kernel contract and reports errors
in the result).  Interfaces translate these into HTTP status codes /
CLI exit codes.
"""

from __future__ import annotations


class PlatformError(Exception):
    """Base class of every OntoPlatform error."""


class ValidationError(PlatformError):
    """A definition violates platform or ontology semantics."""


class CompilerError(PlatformError):
    """Platform model -> Kernel schema compilation failed."""


class NotFoundError(PlatformError):
    """A referenced platform resource does not exist."""


class LifecycleError(PlatformError):
    """An illegal lifecycle transition was attempted."""


class AccessDeniedError(PlatformError):
    """The actor lacks the required role in the workspace."""


class RuntimeUnavailableError(PlatformError):
    """The runtime kernel is not published / not installed."""


class ConnectorError(PlatformError):
    """A data source connector failed."""
