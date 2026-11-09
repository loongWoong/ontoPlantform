"""P2.3: out-of-process sandbox for Smart-UI external plugins (L2).

These tests execute real child processes through the same code path production
uses; they deliberately do not mock ``subprocess``.  They also pin the honest
limits of the boundary (no network isolation, Windows has no rlimit).
"""

from __future__ import annotations

import json
import sys

import pytest

from onto_platform.application.smartui.external import sign_package
from onto_platform.application.smartui.sandbox import (
    DEFAULT_MEMORY_MB,
    DEFAULT_TIMEOUT_SECONDS,
    MAX_MEMORY_MB,
    MAX_OUTPUT_BYTES,
    MAX_SOURCE_BYTES,
    MAX_TIMEOUT_SECONDS,
    RESULT_PREFIX,
    SANDBOX_API_VERSION,
    SandboxResult,
    SandboxUseCases,
    run_python_plugin,
)
from onto_platform.errors import ValidationError


ECHO_SOURCE = "def run(payload):\n    return {'echo': payload}\n"


def _write_package(tmp_path, source: str, *, plugin_id: str = "acme.sandbox",
                   secret: str = "test-secret"):
    """A real signed package directory (manifest.json + plugin.py)."""
    raw = source.encode("utf-8")
    root = tmp_path / "pkg"
    root.mkdir()
    manifest = {
        "id": plugin_id,
        "kind": "view",
        "version": "1.0.0",
        "builtin": False,
        "permissions": ["view.render"],
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "plugin.py").write_bytes(raw)
    signature = sign_package(manifest, raw, secret)
    return root, manifest, signature


# ----------------------------------------------------------------------
# contract constants + result shape
# ----------------------------------------------------------------------


def test_public_contract_constants_and_result_shape():
    assert SANDBOX_API_VERSION == "1.0"
    assert DEFAULT_TIMEOUT_SECONDS == 10
    assert MAX_TIMEOUT_SECONDS == 60
    assert DEFAULT_MEMORY_MB == 256
    assert MAX_MEMORY_MB == 1024
    assert MAX_SOURCE_BYTES == 262144
    assert MAX_OUTPUT_BYTES == 65536

    result = run_python_plugin(ECHO_SOURCE, payload={"k": "v"})
    assert isinstance(result, SandboxResult)
    assert result.ok is True
    assert result.result == {"echo": {"k": "v"}}
    assert result.error == ""
    assert result.exit_code == 0
    assert result.timed_out is False
    assert result.duration_ms >= 0
    assert isinstance(result.stdout_excerpt, str)
    assert isinstance(result.stderr_excerpt, str)
    assert RESULT_PREFIX in result.stdout_excerpt

    payload = result.as_dict()
    assert payload["ok"] is True
    assert payload["result"] == {"echo": {"k": "v"}}
    assert payload["timedOut"] is False
    assert set(payload) == {
        "ok", "result", "error", "exitCode", "timedOut", "durationMs",
        "stdoutExcerpt", "stderrExcerpt", "isolation", "resourceLimits"}

    # The boundary description is static and must stay honest.
    assert result.isolation == {
        "process": True,
        "filesystem": "temporary-directory",
        "environment": "allowlist",
        "network": "not-enforced",
    }
    limits = result.resource_limits
    assert limits["platform"] == sys.platform
    assert limits["addressSpaceMb"] == DEFAULT_MEMORY_MB
    assert limits["cpuSeconds"] >= 1
    if sys.platform == "win32":
        # No resource module: only timeout + process-tree kill are enforced.
        assert limits["enforced"] is False
    else:  # pragma: no cover - exercised on POSIX CI only
        try:
            import resource  # noqa: F401
        except ImportError:
            assert limits["enforced"] is False
        else:
            assert limits["enforced"] is True


@pytest.mark.parametrize("source,expected", [
    ("def run(payload):\n    return 'hello'\n", "hello"),
    ("def run(payload):\n    return 42\n", 42),
    ("def run(payload):\n    return ['a', 1, None, True]\n",
     ["a", 1, None, True]),
    ("def run(payload):\n    return None\n", None),
])
def test_returns_scalars_and_json_containers(source, expected):
    result = run_python_plugin(source)
    assert result.ok is True, result.error
    assert result.result == expected


def test_print_output_does_not_pollute_the_result():
    source = (
        "def run(payload):\n"
        "    print('noisy output', payload)\n"
        "    print('more noise')\n"
        "    return 'clean'\n"
    )
    result = run_python_plugin(source, payload={"k": 1})
    assert result.ok is True, result.error
    assert result.result == "clean"
    assert "noisy output" in result.stderr_excerpt
    assert "noisy output" not in result.stdout_excerpt


# ----------------------------------------------------------------------
# plugin failures are values, not exceptions
# ----------------------------------------------------------------------


def test_plugin_exception_is_reported_not_raised():
    source = "def run(payload):\n    raise ValueError('boom-42')\n"
    result = run_python_plugin(source)
    assert result.ok is False
    assert result.timed_out is False
    assert "ValueError" in result.error
    assert "boom-42" in result.error
    assert "Traceback" in result.stderr_excerpt


def test_invalid_entrypoint_is_reported_as_a_failure():
    result = run_python_plugin(
        "def run(payload):\n    return 1\n", entrypoint="missing_entry")
    assert result.ok is False
    assert "missing_entry" in result.error
    assert "not a callable" in result.error


def test_dead_loop_times_out_and_does_not_hang():
    source = "def run(payload):\n    while True:\n        pass\n"
    result = run_python_plugin(source, timeout_seconds=0.5)
    assert result.ok is False
    assert result.timed_out is True
    assert "timed out" in result.error


def test_oversized_source_is_refused_before_execution():
    result = run_python_plugin(b"#" * (MAX_SOURCE_BYTES + 1))
    assert result.ok is False
    assert result.timed_out is False
    assert "exceeds" in result.error or "limit" in result.error


def test_argument_type_errors_raise_validation_error():
    with pytest.raises(ValidationError):
        run_python_plugin(ECHO_SOURCE, entrypoint=123)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        run_python_plugin(ECHO_SOURCE, timeout_seconds=0)
    with pytest.raises(ValidationError):
        run_python_plugin(ECHO_SOURCE, memory_limit_mb=0)
    with pytest.raises(ValidationError):
        run_python_plugin(ECHO_SOURCE, env={"X": 1})  # type: ignore[dict-item]
    with pytest.raises(ValidationError):
        run_python_plugin(123)  # type: ignore[arg-type]


# ----------------------------------------------------------------------
# environment allowlist
# ----------------------------------------------------------------------


def test_parent_secrets_never_reach_the_child(monkeypatch):
    monkeypatch.setenv("ONTO_TEST_SECRET", "super-secret-value")
    monkeypatch.setenv("ONTO_SMARTUI_PLUGIN_SECRET", "signing-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-parent")
    source = (
        "import os\n"
        "def run(payload):\n"
        "    return {\n"
        "        'onto': os.environ.get('ONTO_TEST_SECRET'),\n"
        "        'plugin': os.environ.get('ONTO_SMARTUI_PLUGIN_SECRET'),\n"
        "        'openai': os.environ.get('OPENAI_API_KEY'),\n"
        "        'path': bool(os.environ.get('PATH')),\n"
        "    }\n"
    )
    result = run_python_plugin(source)
    assert result.ok is True, result.error
    assert result.result == {
        "onto": None, "plugin": None, "openai": None, "path": True}


def test_explicit_env_can_add_only_non_sensitive_keys():
    source = (
        "import os\n"
        "def run(payload):\n"
        "    return {'extra': os.environ.get('SANDBOX_EXTRA')}\n"
    )
    result = run_python_plugin(source, env={"SANDBOX_EXTRA": "visible"})
    assert result.ok is True
    assert result.result == {"extra": "visible"}

    for key in ("SANDBOX_API_TOKEN", "PLUGIN_SECRET", "AWS_SECRET_ACCESS_KEY"):
        with pytest.raises(ValidationError):
            run_python_plugin(source, env={key: "x"})


# ----------------------------------------------------------------------
# use case: signature verification + audit
# ----------------------------------------------------------------------


def test_use_case_verifies_package_runs_isolated_and_audits(
        platform, empty_workspace, tmp_path, monkeypatch):
    monkeypatch.setenv("ONTO_SMARTUI_EXTERNAL_PLUGINS", "1")
    monkeypatch.setenv("ONTO_SMARTUI_PLUGIN_SECRET", "test-secret")
    source = "def run(payload):\n    return {'echo': payload}\n"
    root, manifest, signature = _write_package(tmp_path, source)

    result = SandboxUseCases(platform.ctx).run_package(
        empty_workspace, "admin", root, signature, payload={"k": 1})

    assert isinstance(result, SandboxResult)
    assert result.ok is True, result.error
    assert result.result == {"echo": {"k": 1}}

    entries = [
        entry for entry in platform.ctx.audit.list(empty_workspace)
        if entry.get("action") == "smartui.plugin_sandbox"
    ]
    assert entries, "the sandbox run must be audited"
    last = entries[-1]
    assert last["target"] == manifest["id"]
    assert last["actor"] == "admin"
    assert last["detail"]["plugin"] == manifest["id"]
    assert last["detail"]["entrypoint"] == "run"
    assert last["detail"]["ok"] is True
    assert last["detail"]["timed_out"] is False
    assert last["detail"]["duration_ms"] >= 0
    assert last["detail"]["isolation"]["network"] == "not-enforced"


def test_use_case_refuses_a_bad_signature_and_external_plugins_disabled(
        platform, empty_workspace, tmp_path, monkeypatch):
    source = "def run(payload):\n    return 1\n"
    root, manifest, signature = _write_package(tmp_path, source)
    use_cases = SandboxUseCases(platform.ctx)

    # external plugins are off by default in the test environment
    monkeypatch.delenv("ONTO_SMARTUI_EXTERNAL_PLUGINS", raising=False)
    monkeypatch.setenv("ONTO_SMARTUI_PLUGIN_SECRET", "test-secret")
    with pytest.raises(ValidationError):
        use_cases.run_package(empty_workspace, "admin", root, signature)

    # enabled, but the package is not signed with the configured secret
    monkeypatch.setenv("ONTO_SMARTUI_EXTERNAL_PLUGINS", "1")
    with pytest.raises(ValidationError):
        use_cases.run_package(empty_workspace, "admin", root, "0" * 64)

    entries = [
        entry for entry in platform.ctx.audit.list(empty_workspace)
        if entry.get("action") == "smartui.plugin_sandbox"
    ]
    assert entries == []
