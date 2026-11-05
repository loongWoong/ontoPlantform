"""Plugin registry: builtin standard adapters + entry-point discovery.

The platform core is dependency-free, so standard-format support is provided
by adapters discovered at runtime:

1. builtins shipped with the core distribution (:data:`BUILTIN_ADAPTERS`);
2. third-party plugins registered under the ``onto_platform.standards``
   ``importlib.metadata`` entry-point group.

Discovery is fail-isolated: a broken plugin (import error, malformed
manifest, incompatible SPI version) must never prevent the platform from
starting.  Such items are recorded in :attr:`PluginRegistry.problems` and
reported by :meth:`PluginRegistry.manifests` with ``status="invalid"`` so an
operator can see what is wrong (``GET /standards/adapters``).
"""

from __future__ import annotations

import importlib
import importlib.metadata
from collections.abc import Iterable, Mapping

from .spi import (
    ADAPTER_API_VERSION,
    AdapterError,
    AdapterIncompatible,
    AdapterManifest,
    AdapterNotFound,
    _major,
    manifest_problems,
    requires_compatible,
)

#: importlib.metadata group scanned for third-party adapters
LIFECYCLE_GROUP = "onto_platform.standards"

#: same value, spelled for callers that think in terms of entry points
ENTRY_POINT_GROUP = LIFECYCLE_GROUP

#: adapters shipped with the core distribution, as ``module:Class`` paths
BUILTIN_ADAPTERS: tuple[str, ...] = (
    "onto_platform.semantic.owl:OwlRdfAdapter",
    "onto_platform.semantic.skos:SkosAdapter",
)

#: manifest capability -> adapter method that must be callable (G-02).  The
#: ``import`` capability maps to ``import_`` because ``import`` is a keyword.
CAPABILITY_METHODS: tuple[tuple[str, str], ...] = (
    ("export", "export"),
    ("validate", "validate"),
    ("query", "query"),
    ("import", "import_"),
)
_CAPABILITY_METHODS = dict(CAPABILITY_METHODS)

_STATUS_READY = "ready"
_STATUS_INVALID = "invalid"


def _empty_manifest(name: str) -> dict:
    """Placeholder manifest shown for a plugin that could not even load."""
    return {
        "name": name,
        "version": "",
        "api_version": "",
        "standards": [],
        "capabilities": [],
        "formats_out": [],
        "formats_in": [],
        "description": "",
        "permissions": {"network": False, "write": False},
        "formats": {"out": [], "in": []},
        "requires": {"ontoplatform": "", "extras": []},
        "config_schema": {},
        "aliases": [],
    }


def adapter_contract_problems(adapter) -> list[str]:
    """Problems where a declared capability has no callable implementation.

    A manifest is a promise: declaring ``validate`` while the object has no
    ``validate()`` makes the adapter lie about its capability surface (G-02).
    The registry treats it like any other contract violation - ``invalid`` on
    discovery, :class:`AdapterError` on manual registration - instead of
    exposing a plugin that raises ``AttributeError`` on first use.
    """
    manifest = getattr(adapter, "manifest", None)
    if not isinstance(manifest, AdapterManifest):
        return [f"adapter has no AdapterManifest (got {type(manifest).__name__})"]
    problems: list[str] = []
    for capability in manifest.capabilities:
        method_name = _CAPABILITY_METHODS.get(capability)
        if method_name is None:
            continue  # unknown capability is already a manifest problem
        if not callable(getattr(adapter, method_name, None)):
            problems.append(
                f"adapter declares capability {capability!r} but has no "
                f"callable {method_name}()")
    return problems


def _manifest_problems(manifest: AdapterManifest) -> list[str]:
    """Contract + compatibility problems of one discovered manifest."""
    problems: list[str] = []
    if _major(manifest.api_version) != _major(ADAPTER_API_VERSION):
        problems.append(
            f"manifest.api_version {manifest.api_version!r} is incompatible "
            f"with platform SPI {ADAPTER_API_VERSION!r}")
    # manifest_problems() repeats the major check; drop the duplicate so the
    # operator sees one actionable line per issue.
    for problem in manifest_problems(manifest):
        if "incompatible with platform SPI" in problem:
            continue
        problems.append(problem)
    return problems


def _entry_point_name(entry_point) -> str:
    name = getattr(entry_point, "name", None)
    return str(name) if name else repr(entry_point)


def _entry_point_loader(entry_point):
    loader = getattr(entry_point, "load", None)
    if not callable(loader):
        raise AdapterError(
            f"entry point {_entry_point_name(entry_point)!r} has no load()")
    return loader


class PluginRegistry:
    """Registry of standard adapters: builtin + installed entry points."""

    ENTRY_POINT_GROUP = ENTRY_POINT_GROUP
    LIFECYCLE_GROUP = LIFECYCLE_GROUP

    def __init__(self, entry_points=None) -> None:
        """Create an empty registry.

        ``entry_points`` may be injected for tests/hosts: either a mapping
        ``{name: loader}`` or an iterable of ``(name, loader)`` pairs (or
        entry-point-like objects with ``.name``/``.load``).  ``None`` means
        real ``importlib.metadata`` discovery at :meth:`discover` time.
        """
        self._entry_points = entry_points
        self._adapters: dict[str, object] = {}
        self._sources: dict[str, str] = {}
        #: alias -> canonical adapter name (architecture §4 package names)
        self._aliases: dict[str, str] = {}
        #: name -> (manifest dict, problems) for discovered-but-unusable items
        self._invalid: dict[str, tuple[dict, list[str]]] = {}
        #: name -> incompatibility reason (for AdapterIncompatible on get())
        self._incompatible: dict[str, str] = {}
        #: every discovery failure as ``(name, error message)``
        self.problems: list[tuple[str, str]] = []

    # ---- registration -------------------------------------------------

    def register(self, adapter, *, source: str = "manual") -> None:
        """Register one adapter under its manifest name.

        Raises :class:`AdapterError` for a malformed manifest, a capability
        without an implementation, a duplicate name, or a conflicting alias,
        and :class:`AdapterIncompatible` for an SPI or platform-version
        mismatch.
        """
        manifest = getattr(adapter, "manifest", None)
        if not isinstance(manifest, AdapterManifest):
            raise AdapterError(
                f"adapter from {source!r} has no AdapterManifest "
                f"(got {type(manifest).__name__})")
        if _major(manifest.api_version) != _major(ADAPTER_API_VERSION):
            raise AdapterIncompatible(
                f"adapter {manifest.name!r} declares api_version "
                f"{manifest.api_version!r}; platform SPI is "
                f"{ADAPTER_API_VERSION!r}")
        problems = manifest_problems(manifest)
        if problems:
            raise AdapterError(
                f"adapter {manifest.name!r} has an invalid manifest: "
                + "; ".join(problems))
        problems = adapter_contract_problems(adapter)
        if problems:
            raise AdapterError(
                f"adapter {manifest.name!r} does not implement its declared "
                f"capabilities: " + "; ".join(problems))
        compatible, reason = requires_compatible(manifest)
        if not compatible:
            raise AdapterIncompatible(reason)
        if manifest.name in self._adapters or manifest.name in self._invalid:
            raise AdapterError(
                f"adapter {manifest.name!r} is already registered")
        alias_problems = self._alias_problems(manifest)
        if alias_problems:
            raise AdapterError(
                f"adapter {manifest.name!r} has conflicting aliases: "
                + "; ".join(alias_problems))
        self._adapters[manifest.name] = adapter
        self._sources[manifest.name] = source
        self._record_aliases(manifest)


    # ---- discovery ----------------------------------------------------

    def discover(self) -> list[str]:
        """Load builtin adapters, then entry-point plugins.

        Each failure (load error, invalid manifest, incompatible SPI) is
        recorded in :attr:`problems`; nothing is raised, so one bad plugin
        cannot break platform startup.  Re-running discovery reloads plugins
        while keeping manually registered adapters.  Returns the names that
        loaded successfully in this run, in load order.
        """
        self._reset_discovered()
        loaded: list[str] = []
        for spec in BUILTIN_ADAPTERS:
            try:
                adapter = _load_object_path(spec)
            except Exception as exc:  # noqa: BLE001 - fail isolation is the point
                self._record_failure(spec, "builtin", exc)
                continue
            name = self._accept(adapter, fallback_name=spec, source="builtin")
            if name is not None:
                loaded.append(name)

        for entry_point in self._iter_entry_points():
            name = _entry_point_name(entry_point)
            try:
                adapter = _load_entry_point(entry_point)
            except Exception as exc:  # noqa: BLE001 - fail isolation is the point
                self._record_failure(name, "entry point", exc)
                continue
            loaded_name = self._accept(
                adapter, fallback_name=name, source=f"entry-point:{name}")
            if loaded_name is not None:
                loaded.append(loaded_name)
        return loaded

    def _iter_entry_points(self) -> Iterable:
        provided = self._entry_points
        if provided is None:
            yield from self._installed_entry_points()
            return
        if isinstance(provided, Mapping):
            items = list(provided.items())
        else:
            items = list(provided)
        for item in items:
            if isinstance(item, tuple) and len(item) == 2:
                name, loader = item
                yield _InjectedEntryPoint(str(name), loader)
            else:  # already entry-point-like (e.g. importlib.metadata.EntryPoint)
                yield item

    @staticmethod
    def _installed_entry_points() -> list:
        """Real entry points, tolerating the 3.10-3.12 API variants."""
        try:
            entry_points = importlib.metadata.entry_points(
                group=LIFECYCLE_GROUP)
        except TypeError:  # very old signature: no group= keyword
            entry_points = importlib.metadata.entry_points()
            select = getattr(entry_points, "select", None)
            if callable(select):
                entry_points = select(group=LIFECYCLE_GROUP)
            else:  # pragma: no cover - pre-3.10 dict-style API
                entry_points = entry_points.get(LIFECYCLE_GROUP, [])
        return list(entry_points)

    # ---- inspection ---------------------------------------------------

    def names(self) -> list[str]:
        """Canonical names of usable adapters, sorted (aliases excluded)."""
        return sorted(self._adapters)

    def get(self, name: str):
        """Return a loaded adapter by canonical name or by alias.

        Unknown name -> ``AdapterNotFound``; known but incompatible ->
        ``AdapterIncompatible``; known but otherwise unusable ->
        ``AdapterError``.
        """
        canonical = self._aliases.get(name, name)
        adapter = self._adapters.get(canonical)
        if adapter is not None:
            return adapter
        if canonical in self._incompatible:
            raise AdapterIncompatible(self._incompatible[canonical])
        if canonical in self._invalid:
            _, problems = self._invalid[canonical]
            raise AdapterError(
                f"adapter {canonical!r} is not usable: " + "; ".join(problems))
        raise AdapterNotFound(f"unknown standard adapter {name!r}")

    def manifests(self) -> list[dict]:
        """Manifest rows sorted by name, each with ``status`` + ``problems``.

        Unusable plugins (including a builtin that failed to import) are
        listed with ``status="invalid"`` so they can be surfaced with the
        actionable error instead of silently vanishing.
        """
        rows: list[dict] = []
        for name in sorted(self._adapters):
            row = self._adapters[name].manifest.to_dict()
            row["status"] = _STATUS_READY
            row["problems"] = []
            rows.append(row)
        for name in sorted(self._invalid):
            manifest, problems = self._invalid[name]
            row = {key: value for key, value in manifest.items()}
            row["status"] = _STATUS_INVALID
            row["problems"] = list(problems)
            rows.append(row)
        rows.sort(key=lambda row: row["name"])
        return rows

    # ---- internals ----------------------------------------------------

    def _reset_discovered(self) -> None:
        """Drop previously discovered entries; manual registrations survive."""
        manual = {
            name: adapter
            for name, adapter in self._adapters.items()
            if self._sources.get(name) == "manual"
        }
        self._adapters = manual
        self._sources = {
            name: source
            for name, source in self._sources.items()
            if source == "manual"
        }
        # keep only aliases that still point at a surviving adapter
        self._aliases = {
            alias: canonical
            for alias, canonical in self._aliases.items()
            if canonical in self._adapters
        }
        self._invalid = {}
        self._incompatible = {}
        self.problems = []

    def _alias_problems(self, manifest: AdapterManifest) -> list[str]:
        """Conflicts between the manifest's aliases and already known names."""
        name = manifest.name
        problems: list[str] = []
        if name in self._aliases:
            problems.append(
                f"adapter name {name!r} is already registered as an alias of "
                f"{self._aliases[name]!r}")
        for alias in manifest.aliases:
            if not isinstance(alias, str) or alias == name:
                continue  # already a manifest problem
            if alias in self._adapters or alias in self._invalid:
                problems.append(
                    f"alias {alias!r} collides with an existing adapter name")
            owner = self._aliases.get(alias)
            if owner is not None and owner != name:
                problems.append(
                    f"alias {alias!r} is already registered by {owner!r}")
        return problems

    def _record_aliases(self, manifest: AdapterManifest) -> None:
        for alias in manifest.aliases:
            if isinstance(alias, str) and alias and alias != manifest.name:
                self._aliases[alias] = manifest.name

    def _accept(self, adapter, *, fallback_name: str, source: str):
        """Validate a discovered adapter; return its name or None on failure."""
        manifest = getattr(adapter, "manifest", None)
        if not isinstance(manifest, AdapterManifest):
            self.problems.append((
                fallback_name,
                "adapter has no AdapterManifest "
                f"(got {type(manifest).__name__})"))
            self._remember_invalid(fallback_name, _empty_manifest(fallback_name),
                                   ["adapter has no AdapterManifest"])
            return None

        problems = _manifest_problems(manifest)
        name = manifest.name or fallback_name
        if problems:
            self.problems.append((name, "; ".join(problems)))
            self._remember_invalid(name, manifest.to_dict(), problems)
            if _major(manifest.api_version) != _major(ADAPTER_API_VERSION):
                self._incompatible[name] = (
                    f"adapter {name!r} declares api_version "
                    f"{manifest.api_version!r}; platform SPI is "
                    f"{ADAPTER_API_VERSION!r}")
            return None

        contract = adapter_contract_problems(adapter)
        if contract:
            self.problems.append((name, "; ".join(contract)))
            self._remember_invalid(name, manifest.to_dict(), contract)
            return None

        compatible, reason = requires_compatible(manifest)
        if not compatible:
            self.problems.append((name, reason))
            self._remember_invalid(name, manifest.to_dict(), [reason])
            self._incompatible[name] = reason
            return None

        alias_problems = self._alias_problems(manifest)
        if alias_problems:
            self.problems.append((name, "; ".join(alias_problems)))
            self._remember_invalid(name, manifest.to_dict(), alias_problems)
            return None

        if name in self._adapters:
            self.problems.append((
                name, f"duplicate adapter name from {source}"))
            return None
        self._adapters[name] = adapter
        self._sources[name] = source
        self._record_aliases(manifest)
        return name

    def _remember_invalid(self, name: str, manifest: dict,
                          problems: list[str]) -> None:
        if name in self._adapters or name in self._invalid:
            return
        self._invalid[name] = (manifest, list(problems))

    def _record_failure(self, name: str, source: str, exc: BaseException) -> None:
        message = f"{type(exc).__name__}: {exc}"
        self.problems.append((name, message))
        self._remember_invalid(
            name, _empty_manifest(name),
            [f"{source} plugin unavailable: {message}"])


class _InjectedEntryPoint:
    """Minimal entry-point stand-in for injected ``(name, loader)`` pairs."""

    __slots__ = ("name", "_loader")

    def __init__(self, name: str, loader) -> None:
        self.name = name
        self._loader = loader

    def load(self):
        return self._loader()


def _load_object_path(spec: str):
    """Import ``module:attribute`` and instantiate it if it is a class."""
    module_name, separator, attribute = spec.partition(":")
    if not separator or not module_name or not attribute:
        raise AdapterError(
            f"invalid adapter path {spec!r}; expected 'module:Class'")
    module = importlib.import_module(module_name)
    target = getattr(module, attribute)
    return _instantiate(target)


def _load_entry_point(entry_point):
    loader = _entry_point_loader(entry_point)
    return _instantiate(loader())


def _instantiate(target):
    return target() if isinstance(target, type) else target


# ----------------------------------------------------------------------
# Process-wide default registry (lazy discovery)
# ----------------------------------------------------------------------

_DEFAULT_REGISTRY: PluginRegistry | None = None


def default_registry() -> PluginRegistry:
    """Process-wide discovered plugin registry (lazy, idempotent)."""
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        registry = PluginRegistry()
        registry.discover()
        _DEFAULT_REGISTRY = registry
    return _DEFAULT_REGISTRY


def reset_default_registry() -> None:
    """Drop the process-wide registry (tests and hot-reload)."""
    global _DEFAULT_REGISTRY
    _DEFAULT_REGISTRY = None


__all__ = [
    "BUILTIN_ADAPTERS",
    "ENTRY_POINT_GROUP",
    "LIFECYCLE_GROUP",
    "PluginRegistry",
    "default_registry",
    "reset_default_registry",
]
