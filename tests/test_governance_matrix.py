"""Governance matrix additions (plan-v2 1.8).

The lifecycle/access unit tests live in ``test_governance.py``; these cover
the application-level additions: the action/role matrix, the last-owner
invariant and audited agent writes.
"""

from __future__ import annotations

import pytest

from onto_platform.errors import AccessDeniedError, ValidationError
from onto_platform.infrastructure.llm.echo import EchoLLMProvider

from conftest import seed_workspace


def test_action_matrix_requires_the_documented_role(platform, empty_workspace):
    wid = empty_workspace
    platform.workspaces.add_member(wid, "bob", "viewer", "admin")
    platform.workspaces.add_member(wid, "eve", "editor", "admin")

    # a viewer may read but never author
    with pytest.raises(AccessDeniedError):
        platform.workspaces.add_member(wid, "mallory", "viewer", "bob")
    with pytest.raises(AccessDeniedError):
        platform.workspaces.remove_member(wid, "eve", "bob")
    with pytest.raises(AccessDeniedError):
        platform.ontologies.create(wid, "ont", "bob")

    # an editor may draft ontology and capabilities, a viewer may not
    platform.ontologies.create(wid, "ont", "eve")
    platform.ontologies.upsert_object(
        wid, "eve", "Hub", [{"name": "code", "type": "string"}])
    with pytest.raises(AccessDeniedError):
        platform.ontologies.upsert_object(
            wid, "bob", "Nope", [{"name": "code", "type": "string"}])
    with pytest.raises(AccessDeniedError):
        platform.ctx.require_action("admin", wid, "no.such.action")


def test_last_owner_cannot_be_removed_or_demoted(platform):
    wid = seed_workspace(platform)
    with pytest.raises(ValidationError):
        platform.workspaces.remove_member(wid, "admin", "admin")
    with pytest.raises(ValidationError):
        platform.workspaces.add_member(wid, "admin", "viewer", "admin")

    # promote two more owners, then the first one may step down
    platform.workspaces.add_member(wid, "second", "owner", "admin")
    platform.workspaces.add_member(wid, "third", "owner", "admin")
    removed = platform.workspaces.remove_member(wid, "second", "admin")
    assert removed["removed"] is True
    # an owner may be removed by another owner...
    platform.workspaces.remove_member(wid, "admin", "third")
    # ...but the sole remaining owner cannot leave
    with pytest.raises(ValidationError):
        platform.workspaces.remove_member(wid, "third", "third")
    members = {m["user"]: m["role"]
               for m in platform.workspaces.list_members(wid, "third")}
    assert members == {"third": "owner"}


def test_agent_writes_are_audited_as_capability_invocations(platform):
    wid = seed_workspace(platform)
    script = [
        '{"think": "充值", "action": {"tool": "invoke_capability", "args": '
        '{"function": "Vehicle.recharge", "target": "Vehicle/V002", "args": {"amount": 5}}}}',
        '{"think": "完成", "answer": "已充值"}',
    ]
    run = platform.intelligence.agent(
        wid, "admin", "给鲁B67890充值5元", provider=EchoLLMProvider(script))
    assert run["ok"] is True

    # the agent's write went through InvokeCapability: execution record,
    # domain event and audit all carry the requesting actor
    executions = platform.ctx.repo.list("execution", wid)
    recharge = [e for e in executions
                if e.get("function") == "Vehicle.recharge"
                and e.get("actor") == "admin"]
    assert recharge, "agent action must be recorded as an audited invocation"
    types = [e["type"] for e in platform.events.list(wid)]
    assert "capability.invoked" in types
    audits = [a for a in platform.ctx.audit.list(wid)
              if a["action"] == "capability.invoke"]
    assert any(a["actor"] == "admin" for a in audits)
