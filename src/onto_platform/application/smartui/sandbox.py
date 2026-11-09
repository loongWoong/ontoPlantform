"""P2.3: out-of-process execution sandbox for Smart-UI external plugins.

The signed-package boundary in :mod:`external` is a *trust* boundary: after an
operator installs and signs a package, its ``create_plugin`` currently runs
in-process.  This module adds an optional **L2 execution boundary** for one
*run* of an external package: the plugin source is copied to a temporary
directory and executed by a fresh ``sys.executable -I`` child process through a
fixed wrapper, with a wall-clock timeout and (on POSIX) kernel resource limits.

What this boundary honestly is
------------------------------
* **process**: the plugin runs in a separate OS process with no reference to
  platform objects, the ``ApplicationContext``, credentials or module state;
* **filesystem**: only ``temporary-directory`` - the source file lives in a
  private temp dir and the child's CWD is that dir, but there is **no chroot**
  or filesystem jail: the child can still read the host filesystem as its user;
* **environment**: an explicit allowlist is built from scratch (never
  ``os.environ`` copied wholesale), so ``ONTO_*`` platform secrets and API keys
  never reach the child;
* **network**: ``not-enforced`` - there is no network namespace/firewall; a
  plugin can still open sockets.  Network isolation is only real under an outer
  container/VM boundary;
* **resource limits**: on POSIX we set ``RLIMIT_CPU``/``RLIMIT_AS``/
  ``RLIMIT_FSIZE``/``RLIMIT_NOFILE`` in the child; on Windows there is no
  ``resource`` module, so ``enforced=False`` and only the wall-clock timeout and
  process-tree kill apply.

This module is intentionally independent of the plugin host: it does not
register results as plugins and does not change ``host.py``.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Any

from ...errors import ValidationError
from .external import read_package

#: Version of the JSON request/result contract spoken with the child wrapper.
SANDBOX_API_VERSION = "1.0"
DEFAULT_TIMEOUT_SECONDS = 10
MAX_TIMEOUT_SECONDS = 60
DEFAULT_MEMORY_MB = 256
MAX_MEMORY_MB = 1024
MAX_SOURCE_BYTES = 262144
MAX_OUTPUT_BYTES = 65536

#: Fixed prefix the wrapper uses for its single-line JSON result on stdout.
RESULT_PREFIX = "SANDBOX_RESULT:"
#: Bytes of stdout/stderr kept as diagnostic excerpts in the result.
EXCERPT_BYTES = 2048

#: Minimal environment forwarded to the child.  Deliberately excludes every
#: ``ONTO_*`` variable, ``*_API_KEY`` and anything credential-shaped.
BASE_ENV_KEYS = (
    "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATH", "PATHEXT",
    "TEMP", "TMP", "LANG", "LC_ALL", "PYTHONUTF8", "PYTHONIOENCODING",
)
#: An explicit env may add keys, but never credential-shaped ones.
SENSITIVE_ENV_MARKERS = ("TOKEN", "SECRET", "PASSWORD", "APIKEY", "API_KEY",
                         "CREDENTIAL", "PRIVATE")


@dataclass(frozen=True)
class SandboxResult:
    """Outcome of one sandboxed plugin execution."""

    ok: bool
    result: Any
    error: str
    exit_code: int
    timed_out: bool
    duration_ms: int
    stdout_excerpt: str
    stderr_excerpt: str
    isolation: dict
    resource_limits: dict

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "result": self.result,
            "error": self.error,
            "exitCode": self.exit_code,
            "timedOut": self.timed_out,
            "durationMs": self.duration_ms,
            "stdoutExcerpt": self.stdout_excerpt,
            "stderrExcerpt": self.stderr_excerpt,
            "isolation": dict(self.isolation),
            "resourceLimits": dict(self.resource_limits),
        }


# ----------------------------------------------------------------------
# fixed child wrapper (never interpolates the plugin source)
# ----------------------------------------------------------------------


def _wrapper_source(max_output_bytes: int,
                    result_prefix: str = RESULT_PREFIX) -> str:
    """The fixed wrapper executed as ``python -I -c`` in the child.

    The wrapper:
    * reads one JSON request from stdin (source path, entrypoint, payload),
    * redirects fd 1 to stderr so plugin output (including ``print``) can never
      be mistaken for the result line,
    * ``exec``s the plugin source into its own globals and calls the entrypoint,
    * writes a single ``SANDBOX_RESULT:`` JSON line to a saved copy of fd 1.
    """
    return f'''
import json, os, sys, traceback

_RESULT_FD = os.dup(1)
_RESULT_PREFIX = {result_prefix!r}
try:
    os.dup2(2, 1)
except OSError:
    pass


def _emit(payload):
    try:
        data = json.dumps(payload, ensure_ascii=False, default=repr).encode("utf-8")
    except Exception as exc:  # pragma: no cover - payload is built here
        data = json.dumps({{"ok": False, "error": "unserialisable sandbox result: "
                                                  + str(exc)}}).encode("utf-8")
    try:
        os.write(_RESULT_FD, _RESULT_PREFIX.encode("utf-8") + data + b"\\n")
    except OSError:
        pass


def main():
    request_raw = sys.stdin.buffer.read()
    sys.stdin = open(os.devnull, "r")
    try:
        request = json.loads(request_raw.decode("utf-8"))
        source_path = request["source"]
        entrypoint = request["entrypoint"]
        payload = request.get("payload")
    except Exception as exc:
        _emit({{"ok": False, "error": "invalid sandbox request: " + str(exc)}})
        return
    # Every plugin-level write, including print(), goes to stderr.
    sys.stdout = sys.stderr
    try:
        with open(source_path, "rb") as handle:
            source = handle.read()
        namespace = {{"__name__": "onto_sandbox_plugin",
                     "__file__": source_path}}
        exec(compile(source, source_path, "exec"), namespace)
        function = namespace.get(entrypoint)
        if not callable(function):
            _emit({{"ok": False, "error": "entrypoint " + repr(entrypoint)
                                              + " is not a callable defined by the plugin"}})
            return
        result = function(payload)
        probe = json.dumps(result, ensure_ascii=False, default=repr)
        if len(probe.encode("utf-8")) > {max_output_bytes}:
            _emit({{"ok": False,
                    "error": "result exceeds the sandbox output limit"}})
            return
        _emit({{"ok": True, "result": result}})
    except SystemExit as exc:
        code = exc.code
        if code in (None, 0):
            _emit({{"ok": True, "result": None}})
        else:
            _emit({{"ok": False, "error": "SystemExit: " + str(code)}})
    except BaseException as exc:
        try:
            traceback.print_exc()
        except Exception:
            pass
        _emit({{"ok": False, "error": type(exc).__name__ + ": " + str(exc)}})


main()
'''


# ----------------------------------------------------------------------
# validation / environment
# ----------------------------------------------------------------------


def _source_bytes(source: bytes | str) -> bytes:
    if isinstance(source, str):
        return source.encode("utf-8")
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    raise ValidationError("plugin source must be str or bytes")


def _validate_entrypoint(entrypoint: object) -> str:
    if not isinstance(entrypoint, str) or not entrypoint.strip():
        raise ValidationError("entrypoint must be a non-empty string")
    return entrypoint


def _coerce_timeout(timeout_seconds: object) -> float:
    if isinstance(timeout_seconds, bool) or not isinstance(
            timeout_seconds, (int, float)):
        raise ValidationError("timeout_seconds must be a number")
    value = float(timeout_seconds)
    if value <= 0:
        raise ValidationError("timeout_seconds must be greater than zero")
    # A caller may not disable the timeout by asking for a huge one.
    return min(value, float(MAX_TIMEOUT_SECONDS))


def _coerce_memory(memory_limit_mb: object) -> int:
    if isinstance(memory_limit_mb, bool) or not isinstance(
            memory_limit_mb, (int, float)):
        raise ValidationError("memory_limit_mb must be a number")
    value = int(memory_limit_mb)
    if value <= 0:
        raise ValidationError("memory_limit_mb must be greater than zero")
    return min(value, MAX_MEMORY_MB)


def _child_environment(extra: dict | None) -> dict[str, str]:
    """Build the child env from scratch; never inherit the parent wholesale."""
    child = {key: os.environ[key] for key in BASE_ENV_KEYS if key in os.environ}
    if extra is not None:
        if not isinstance(extra, dict):
            raise ValidationError("env must be a dict of strings")
        for key, value in extra.items():
            if not isinstance(key, str) or not key.strip() or not isinstance(
                    value, str):
                raise ValidationError("env keys and values must be strings")
            upper = key.upper()
            if any(marker in upper for marker in SENSITIVE_ENV_MARKERS):
                raise ValidationError(
                    f"environment key {key!r} looks credential-shaped and is "
                    "refused by the plugin sandbox")
            child[key] = value
    child.setdefault("PYTHONIOENCODING", "utf-8")
    return child


def _isolation() -> dict[str, Any]:
    """Truthful, static description of this boundary (no wishful thinking)."""
    return {
        "process": True,
        "filesystem": "temporary-directory",
        "environment": "allowlist",
        "network": "not-enforced",
    }


def _posix_limits_available() -> bool:
    try:  # pragma: no cover - trivial import probe
        import resource  # noqa: F401
    except ImportError:  # pragma: no cover - Windows
        return False
    return True


def _resource_limits(timeout_seconds: float, memory_limit_mb: int) -> dict:
    return {
        "cpuSeconds": max(1, int(timeout_seconds) + 1),
        "addressSpaceMb": int(memory_limit_mb),
        "enforced": _posix_limits_available(),
        "platform": sys.platform,
    }


def _posix_limiter(timeout_seconds: float, memory_limit_mb: int):
    """preexec_fn applying kernel limits on POSIX; no-op if unavailable."""
    cpu = max(1, int(timeout_seconds) + 1)
    address = int(memory_limit_mb) * 1024 * 1024

    def _limit() -> None:  # pragma: no cover - runs in the forked child
        try:
            import resource

            resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))
            resource.setrlimit(resource.RLIMIT_AS, (address, address))
            resource.setrlimit(resource.RLIMIT_FSIZE,
                               (MAX_OUTPUT_BYTES, MAX_OUTPUT_BYTES))
            resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
        except Exception:
            pass

    return _limit


# ----------------------------------------------------------------------
# process plumbing
# ----------------------------------------------------------------------


def _kill_tree(process: "subprocess.Popen[bytes]") -> None:
    """Kill the child and everything it spawned (never raise)."""
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True, timeout=10)
        except Exception:
            pass
    else:  # pragma: no cover - POSIX only
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except Exception:
            pass
    try:
        process.kill()
    except Exception:
        pass


def _read_capped(pipe, sink: list[bytes], cap: int) -> None:
    """Read a pipe, keeping at most ``cap`` bytes, draining the rest."""
    size = 0
    try:
        while True:
            chunk = pipe.read(65536)
            if not chunk:
                break
            if size < cap:
                keep = chunk[: cap - size]
                sink.append(keep)
                size += len(keep)
    except Exception:
        pass
    finally:
        try:
            pipe.close()
        except Exception:
            pass


def _write_stdin(pipe, data: bytes) -> None:
    try:
        pipe.write(data)
        pipe.flush()
    except Exception:
        pass
    finally:
        try:
            pipe.close()
        except Exception:
            pass


def _excerpt(raw: bytes) -> str:
    text = raw.decode("utf-8", "replace")
    if len(text) > EXCERPT_BYTES:
        return text[:EXCERPT_BYTES]
    return text


def _extract_result(stdout: bytes) -> dict | None:
    for line in reversed(stdout.decode("utf-8", "replace").splitlines()):
        if not line.startswith(RESULT_PREFIX):
            continue
        try:
            value = json.loads(line[len(RESULT_PREFIX):])
        except ValueError:
            return None
        return value if isinstance(value, dict) else None
    return None


def _failure(error: str, *, timeout_seconds: float, memory_limit_mb: int,
             duration_ms: int = 0, timed_out: bool = False,
             exit_code: int = -1, stdout: bytes = b"",
             stderr: bytes = b"") -> SandboxResult:
    return SandboxResult(
        ok=False, result=None, error=error, exit_code=exit_code,
        timed_out=timed_out, duration_ms=duration_ms,
        stdout_excerpt=_excerpt(stdout), stderr_excerpt=_excerpt(stderr),
        isolation=_isolation(),
        resource_limits=_resource_limits(timeout_seconds, memory_limit_mb),
    )


# ----------------------------------------------------------------------
# public pure function
# ----------------------------------------------------------------------


def run_python_plugin(source: bytes | str, *, entrypoint: str = "run",
                      payload: Any = None,
                      timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
                      memory_limit_mb: int = DEFAULT_MEMORY_MB,
                      env: dict | None = None) -> SandboxResult:
    """Execute one plugin source in a fresh child process.

    Returns a :class:`SandboxResult` for every runtime failure (plugin raised,
    bad entrypoint, oversized source, timeout, no result).  Only malformed
    *arguments* (wrong types) raise :class:`ValidationError`.  This function
    never raises because the plugin did something.
    """
    source_data = _source_bytes(source)
    entrypoint = _validate_entrypoint(entrypoint)
    timeout = _coerce_timeout(timeout_seconds)
    memory = _coerce_memory(memory_limit_mb)
    child_env = _child_environment(env)

    if len(source_data) > MAX_SOURCE_BYTES:
        return _failure(
            f"plugin source exceeds the {MAX_SOURCE_BYTES}-byte sandbox limit",
            timeout_seconds=timeout, memory_limit_mb=memory)
    try:
        request = json.dumps({
            "apiVersion": SANDBOX_API_VERSION,
            "entrypoint": entrypoint,
            "payload": payload,
        }, ensure_ascii=False, default=repr).encode("utf-8")
    except (TypeError, ValueError) as exc:
        return _failure(f"payload is not JSON-serialisable: {exc}",
                        timeout_seconds=timeout, memory_limit_mb=memory)

    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="onto_plugin_sandbox_") as tmp:
        source_path = os.path.join(tmp, "plugin.py")
        with open(source_path, "wb") as handle:
            handle.write(source_data)
        request_object = json.loads(request.decode("utf-8"))
        request_object["source"] = source_path
        request = json.dumps(request_object, ensure_ascii=False,
                             default=repr).encode("utf-8")
        return _run_child(request, tmp, timeout, memory, child_env, started)


def _run_child(request: bytes, workdir: str, timeout_seconds: float,
               memory_limit_mb: int, child_env: dict[str, str],
               started: float) -> SandboxResult:
    command = [sys.executable, "-I", "-c", _wrapper_source(MAX_OUTPUT_BYTES)]
    kwargs: dict[str, Any] = {
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "env": child_env,
        "cwd": workdir,
    }
    if os.name == "nt":
        # New process group so the whole tree can be killed on timeout; no
        # console window is created for the child.
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        )
    else:  # pragma: no cover - POSIX only
        kwargs["start_new_session"] = True
        kwargs["preexec_fn"] = _posix_limiter(timeout_seconds,
                                              memory_limit_mb)

    try:
        process = subprocess.Popen(command, **kwargs)
    except OSError as exc:
        return _failure(f"could not start the sandbox process: {exc}",
                        timeout_seconds=timeout_seconds,
                        memory_limit_mb=memory_limit_mb,
                        duration_ms=int((time.monotonic() - started) * 1000))

    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    reader_out = threading.Thread(
        target=_read_capped,
        args=(process.stdout, stdout_chunks, MAX_OUTPUT_BYTES + EXCERPT_BYTES),
        daemon=True)
    reader_err = threading.Thread(
        target=_read_capped,
        args=(process.stderr, stderr_chunks, MAX_OUTPUT_BYTES),
        daemon=True)
    writer = threading.Thread(target=_write_stdin,
                              args=(process.stdin, request), daemon=True)
    reader_out.start()
    reader_err.start()
    writer.start()

    timed_out = False
    try:
        exit_code = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(process)
        try:
            exit_code = process.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            exit_code = -1
    writer.join(10)
    reader_out.join(10)
    reader_err.join(10)

    stdout = b"".join(stdout_chunks)
    stderr = b"".join(stderr_chunks)
    duration_ms = int((time.monotonic() - started) * 1000)

    if timed_out:
        return _failure(
            f"sandbox timed out after {timeout_seconds:g}s",
            timeout_seconds=timeout_seconds,
            memory_limit_mb=memory_limit_mb, duration_ms=duration_ms,
            timed_out=True, exit_code=exit_code, stdout=stdout, stderr=stderr)

    payload = _extract_result(stdout)
    if exit_code != 0:
        detail = (payload or {}).get("error")
        error = f"sandbox exited with code {exit_code}"
        if detail:
            error += f": {detail}"
        return _failure(error, timeout_seconds=timeout_seconds,
                        memory_limit_mb=memory_limit_mb,
                        duration_ms=duration_ms, exit_code=exit_code,
                        stdout=stdout, stderr=stderr)
    if payload is None:
        return _failure(
            "sandbox produced no result",
            timeout_seconds=timeout_seconds,
            memory_limit_mb=memory_limit_mb, duration_ms=duration_ms,
            exit_code=exit_code, stdout=stdout, stderr=stderr)
    if payload.get("ok") is not True:
        return _failure(str(payload.get("error") or "plugin execution failed"),
                        timeout_seconds=timeout_seconds,
                        memory_limit_mb=memory_limit_mb,
                        duration_ms=duration_ms, exit_code=exit_code,
                        stdout=stdout, stderr=stderr)
    return SandboxResult(
        ok=True, result=payload.get("result"), error="", exit_code=exit_code,
        timed_out=False, duration_ms=duration_ms,
        stdout_excerpt=_excerpt(stdout), stderr_excerpt=_excerpt(stderr),
        isolation=_isolation(),
        resource_limits=_resource_limits(timeout_seconds, memory_limit_mb),
    )


# ----------------------------------------------------------------------
# use case
# ----------------------------------------------------------------------


class SandboxUseCases:
    """Workspace-facing entry: verify a signed package, then run it isolated.

    The package admission rules (external plugins enabled, signing secret,
    manifest, signature) stay in :mod:`external`; this use case only adds the
    out-of-process execution and an audit record.  The result is returned to
    the caller and is **not** registered with the plugin host.
    """

    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx

    def run_package(self, workspace_id: str, actor: str, package_dir,
                    signature: str, *, entrypoint: str = "run",
                    payload: Any = None,
                    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
                    memory_limit_mb: int = DEFAULT_MEMORY_MB) -> SandboxResult:
        self._ctx.require(actor, workspace_id, "editor")
        package = read_package(package_dir, signature)
        result = run_python_plugin(
            package.source, entrypoint=entrypoint, payload=payload,
            timeout_seconds=timeout_seconds,
            memory_limit_mb=memory_limit_mb)
        plugin_id = getattr(getattr(package, "manifest", None), "id", "") or ""
        self._ctx.audit_event(
            actor, workspace_id, "smartui.plugin_sandbox", plugin_id, {
                "plugin": plugin_id,
                "entrypoint": entrypoint,
                "ok": result.ok,
                "timed_out": result.timed_out,
                "duration_ms": result.duration_ms,
                "exit_code": result.exit_code,
                "error": result.error,
                "isolation": dict(result.isolation),
                "resource_limits": dict(result.resource_limits),
            })
        return result


__all__ = [
    "DEFAULT_MEMORY_MB",
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_MEMORY_MB",
    "MAX_OUTPUT_BYTES",
    "MAX_SOURCE_BYTES",
    "MAX_TIMEOUT_SECONDS",
    "RESULT_PREFIX",
    "SANDBOX_API_VERSION",
    "SandboxResult",
    "SandboxUseCases",
    "run_python_plugin",
]
