"""Phase 1.10: value objects and the error taxonomy."""

from __future__ import annotations

import pytest

from onto_platform.domain.values import ExecutionHandle, ObjectRef
from onto_platform.errors import (
    AccessDeniedError,
    ConcurrencyConflictError,
    ConnectorError,
    NotFoundError,
    ValidationError,
    classify,
)

from conftest import http_post, seed_workspace


def test_object_ref_is_a_structural_value():
    ref = ObjectRef.parse("Vehicle/V001")
    assert ref == ObjectRef("Vehicle", "V001")
    assert str(ref) == "Vehicle/V001"
    assert ObjectRef.parse(ref) is ref
    assert len({ref, ObjectRef("Vehicle", "V001"), ObjectRef("Vehicle", "V002")}) == 2

    with pytest.raises(ValidationError):
        ObjectRef.parse("Vehicle")
    with pytest.raises(ValidationError):
        ObjectRef.parse("Vehicle/V001/extra")
    with pytest.raises(ValidationError):
        ObjectRef("", "V001")
    with pytest.raises(ValidationError):
        ObjectRef("Ve/hicle", "V001")


def test_execution_handle_requires_an_identity():
    handle = ExecutionHandle("exec-1")
    assert handle.idempotency_key is None
    with pytest.raises(ValidationError):
        ExecutionHandle("")


def test_error_taxonomy_is_stable():
    cases = {
        ValidationError("bad"): ("validation", 400, False),
        AccessDeniedError("no"): ("access_denied", 403, False),
        NotFoundError("missing"): ("not_found", 404, False),
        ConcurrencyConflictError("stale"): ("conflict", 409, True),
        ConnectorError("down"): ("connector", 502, True),
    }
    for exc, (code, status, retryable) in cases.items():
        details = classify(exc)
        assert details["code"] == code
        assert details["http_status"] == status
        assert details["retryable"] is retryable
        assert details["type"] == type(exc).__name__

    internal = classify(RuntimeError("boom"))
    assert internal["code"] == "internal" and internal["http_status"] == 500
    assert internal["retryable"] is False


def test_http_errors_carry_code_and_retryable(platform, http_server):
    base, wid = http_server
    # an unknown route yields the classified validation error
    status, payload = http_post(base, "/api/workspaces/x/nope", {})
    assert status == 400
    assert payload["code"] == "validation"
    assert payload["retryable"] is False
