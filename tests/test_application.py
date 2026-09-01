"""Application layer tests: views / queries / agents (design.txt §21-§23, §50)."""

from __future__ import annotations

import pytest

from onto_platform.infrastructure.llm.echo import EchoLLMProvider


@pytest.fixture()
def app_id(platform, workspace_id):
    app = platform.applications.create(workspace_id, "admin", "运营台")
    return app["id"]


def test_application_agents_reference_intelligence(platform, workspace_id, app_id):
    """§50: Application = View/Query/Agent; the agent domain stays in
    Intelligence - the application stores a named reference only."""
    updated = platform.applications.add_agent(
        workspace_id, app_id, "admin", "值班助手",
        goal="检查拥堵收费站并给出处置建议", description="运营值班")
    agents = updated["agents"]
    assert len(agents) == 1
    assert agents[0]["alias"] == "值班助手"
    # re-adding the same alias replaces, never duplicates
    platform.applications.add_agent(workspace_id, app_id, "admin", "值班助手", goal="新目标")
    app = platform.applications.get(workspace_id, app_id, "admin")
    assert len(app["agents"]) == 1
    assert app["agents"][0]["goal"] == "新目标"


def test_application_agent_requires_goal(platform, workspace_id, app_id):
    with pytest.raises(Exception, match="goal"):
        platform.applications.add_agent(workspace_id, app_id, "admin", "空目标", goal="  ")


def test_run_app_agent_delegates_to_intelligence(platform, workspace_id, app_id):
    platform.configure_llm(EchoLLMProvider([
        '{"think": "查", "action": {"tool": "find", "args": '
        '{"object_type": "TollStation", "filter": {"status": "congested"}}}}',
        '{"think": "答", "answer": "ST001 拥堵，建议派员疏导"}',
    ]))
    platform.applications.add_agent(workspace_id, app_id, "admin", "值班助手", goal="检查拥堵")
    run = platform.applications.run_agent(workspace_id, app_id, "admin", "值班助手")
    assert run["ok"] is True
    assert "ST001" in run["answer"]


def test_run_app_agent_goal_override(platform, workspace_id, app_id):
    platform.configure_llm(EchoLLMProvider([
        '{"think": "答", "answer": "临时目标完成"}',
    ]))
    platform.applications.add_agent(workspace_id, app_id, "admin", "助手", goal="默认目标")
    run = platform.applications.run_agent(
        workspace_id, app_id, "admin", "助手", goal_override="临时目标")
    assert run["answer"] == "临时目标完成"


def test_run_unknown_app_agent_is_404_style(platform, workspace_id, app_id):
    from onto_platform.errors import NotFoundError

    with pytest.raises(NotFoundError):
        platform.applications.run_agent(workspace_id, app_id, "admin", "不存在")
