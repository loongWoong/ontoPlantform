"""Ontology workflow: rounds, CQ evidence and the publish gate."""

from __future__ import annotations

import json

import pytest

from onto_platform.errors import GateError, ValidationError
from tests.conftest import http_get, http_post


def _round(platform, wid, **overrides):
    payload = {
        "name": "报销链路",
        "goal": "打通车辆到收费站的查询链路",
        "scope": {"summary": "只覆盖车辆与收费站", "in_scope": ["车辆", "收费站"]},
    }
    payload.update(overrides)
    return platform.workflow.create_round(wid, "admin", payload)


def _cq(platform, wid, rid, code, question, expected, query,
        *, priority="P1", model_refs=None):
    return platform.workflow.add_cq(wid, "admin", rid, {
        "code": code,
        "question": question,
        "priority": priority,
        "type": "competency",
        "expected": expected,
        "query": query,
        "model_refs": model_refs or [],
    })


def _sample(platform, wid, rid):
    return platform.workflow.add_sample(wid, "admin", rid, {
        "name": "最小样例",
        "objects": {
            "Vehicle": [
                {"id": "V001", "plate": "沪A12345", "balance": 100},
                {"id": "V002", "plate": "沪B67890", "balance": 20},
            ],
            "TollStation": [
                {"id": "ST001", "name": "浦东", "status": "畅通"},
                {"id": "ST002", "name": "虹桥", "status": "拥堵"},
            ],
        },
        "relations": [
            {"name": "passes", "source": "Vehicle/V001",
             "target": "TollStation/ST001"},
        ],
    })


def _five_cqs(platform, wid, rid):
    _cq(platform, wid, rid, "CQ-01", "有哪些车辆？",
        {"kind": "row_count", "value": 2},
        {"object_type": "Vehicle"}, priority="P0",
        model_refs=["Vehicle"])
    _cq(platform, wid, rid, "CQ-02", "沪A12345 是否存在？",
        {"kind": "rows_contain", "rows": [{"plate": "沪A12345"}]},
        {"object_type": "Vehicle", "filters": {"plate": "沪A12345"}},
        priority="P0", model_refs=["Vehicle.plate"])
    _cq(platform, wid, rid, "CQ-03", "V001 经过了哪个收费站？",
        {"kind": "rows_contain", "rows": [{"ref": "TollStation/ST001"}]},
        {"relation": "passes", "ref": "Vehicle/V001", "direction": "out"},
        model_refs=["passes"])
    _cq(platform, wid, rid, "CQ-04", "有没有不存在的车牌？",
        {"kind": "empty"},
        {"object_type": "Vehicle", "filters": {"plate": "不存在"}})
    _cq(platform, wid, rid, "CQ-05", "拥堵站是否存在？",
        {"kind": "boolean", "value": True},
        {"object_type": "TollStation", "filters": {"status": "拥堵"}},
        model_refs=["TollStation.status"])


def test_round_lifecycle_and_publish_gate(platform, workspace_id):
    rid = _round(platform, workspace_id)["id"]
    detail = platform.workflow.detail(workspace_id, "admin", rid)
    assert detail["round"]["seq"] == 1
    assert detail["steps"][0]["status"] == "done"
    assert detail["steps"][1]["status"] == "current"
    # starting a round on a published ontology revises it into a draft
    assert detail["ontology"]["lifecycle"] == "draft"

    check = platform.workflow.check(workspace_id, "admin", rid)
    assert check["validation"]["ok"] is True
    assert "没有任何 CQ" in "；".join(check["publish_check"]["blockers"])

    _five_cqs(platform, workspace_id, rid)
    _sample(platform, workspace_id, rid)
    check = platform.workflow.check(workspace_id, "admin", rid)
    assert check["publish_check"]["ok"] is False
    assert any("验证" in blocker for blocker in check["publish_check"]["blockers"])

    run = platform.workflow.run_acceptance(workspace_id, "admin", rid)
    assert run["status"] == "passed", run["results"]
    assert run["summary"]["pass_rate"] == 100.0
    assert run["summary"]["p0_pass_rate"] == 100.0
    assert run["evidence"]["sample_hash"]

    # approval is only allowed after a passing run
    review = platform.workflow.submit_review(
        workspace_id, "admin", rid, {"decision": "approve", "comment": "ok"})
    assert review["decision"] == "approve"
    detail = platform.workflow.detail(workspace_id, "admin", rid)
    assert detail["round"]["status"] == "in_review"
    assert detail["steps"][6]["status"] == "done"
    assert detail["steps"][7]["status"] == "current"

    result = platform.workflow.publish(workspace_id, "admin", rid)
    assert result["round"]["status"] == "accepted"
    assert result["round"]["published_version"] >= 1
    assert result["ontology"]["lifecycle"] == "published"

    # a new round starts a new draft version, ready for the next batch
    nxt = platform.workflow.next_round(
        workspace_id, "admin", rid, {"name": "第二轮"})
    assert nxt["seq"] == 2
    assert nxt["status"] == "active"
    assert platform.workflow.detail(
        workspace_id, "admin", nxt["id"])["ontology"]["lifecycle"] == "draft"


def test_publish_blocks_stale_evidence_and_failed_cq(platform, workspace_id):
    rid = _round(platform, workspace_id)["id"]
    _five_cqs(platform, workspace_id, rid)
    _sample(platform, workspace_id, rid)
    platform.workflow.check(workspace_id, "admin", rid)
    platform.workflow.run_acceptance(workspace_id, "admin", rid)

    # changing the model after the run must invalidate the evidence
    platform.ontologies.upsert_object(
        workspace_id, "admin", "Vehicle",
        properties=[{"name": "plate", "type": "string"},
                    {"name": "balance", "type": "number"},
                    {"name": "color", "type": "string"}])
    check = platform.workflow.publish_check(workspace_id, "admin", rid)
    assert check["ok"] is False
    assert any("证据已过期" in blocker for blocker in check["blockers"])

    with pytest.raises(GateError):
        platform.workflow.publish(workspace_id, "admin", rid)

    # a failing CQ is a hard blocker, not a warning
    platform.workflow.check(workspace_id, "admin", rid)
    platform.workflow.update_cq(
        workspace_id, "admin", rid,
        platform.workflow._cqs(workspace_id, rid)[0]["id"],
        {"expected": {"kind": "row_count", "value": 99}})
    run = platform.workflow.run_acceptance(workspace_id, "admin", rid)
    assert run["status"] == "failed"
    check = platform.workflow.publish_check(workspace_id, "admin", rid)
    assert check["ok"] is False
    assert any("未通过" in blocker or "通过率" in blocker
               for blocker in check["blockers"])


def test_only_one_open_round_per_workspace(platform, workspace_id):
    _round(platform, workspace_id)
    with pytest.raises(ValidationError):
        _round(platform, workspace_id, name="second")


def test_cq_or_sample_change_invalidates_evidence(platform, workspace_id):
    rid = _round(platform, workspace_id)["id"]
    _five_cqs(platform, workspace_id, rid)
    sample = _sample(platform, workspace_id, rid)
    platform.workflow.check(workspace_id, "admin", rid)
    run = platform.workflow.run_acceptance(workspace_id, "admin", rid)
    assert run["status"] == "passed"

    rows = {name: [{"id": row["id"], **row["properties"]}
                   for row in values]
            for name, values in sample["objects"].items()}
    rows["Vehicle"][1]["balance"] = 21
    platform.workflow.update_sample(
        workspace_id, "admin", rid, sample["id"],
        {"objects": rows, "relations": sample["relations"]})
    check = platform.workflow.publish_check(workspace_id, "admin", rid)
    assert any("样例已变更" in blocker for blocker in check["blockers"])

    cq = platform.workflow._cqs(workspace_id, rid)[0]
    platform.workflow.update_cq(
        workspace_id, "admin", rid, cq["id"],
        {"expected": {"kind": "row_count", "value": 3}})
    check = platform.workflow.publish_check(workspace_id, "admin", rid)
    assert any("查询或期望已变更" in blocker for blocker in check["blockers"])


def test_new_run_after_review_requires_review_again(platform, workspace_id):
    rid = _round(platform, workspace_id)["id"]
    _five_cqs(platform, workspace_id, rid)
    _sample(platform, workspace_id, rid)
    platform.workflow.check(workspace_id, "admin", rid)
    platform.workflow.run_acceptance(workspace_id, "admin", rid)
    platform.workflow.submit_review(
        workspace_id, "admin", rid, {"decision": "approve"})

    platform.workflow.run_acceptance(workspace_id, "admin", rid)
    check = platform.workflow.publish_check(workspace_id, "admin", rid)
    assert any("重新确认评审" in blocker for blocker in check["blockers"])

    # re-review is allowed even though the prior approval points at older evidence
    platform.workflow.submit_review(
        workspace_id, "admin", rid, {"decision": "approve"})
    result = platform.workflow.publish(workspace_id, "admin", rid)
    assert result["round"]["status"] == "accepted"


def test_model_refs_must_exist(platform, workspace_id):
    rid = _round(platform, workspace_id)["id"]
    with pytest.raises(ValidationError):
        _cq(platform, workspace_id, rid, "CQ-X", "未知引用",
            {"kind": "empty"}, {"object_type": "Vehicle"},
            model_refs=["NoSuchType.plate"])


def test_review_rejects_without_passing_run(platform, workspace_id):
    rid = _round(platform, workspace_id)["id"]
    _five_cqs(platform, workspace_id, rid)
    _sample(platform, workspace_id, rid)
    platform.workflow.check(workspace_id, "admin", rid)
    with pytest.raises(GateError):
        platform.workflow.submit_review(
            workspace_id, "admin", rid, {"decision": "approve"})


def test_workflow_rest_routes_round_trip(http_server):
    """The console talks REST, not the use case object: prove that path."""
    base, wid = http_server
    status, body = http_post(
        base, f"/api/workspaces/{wid}/ontology/workflow/rounds",
        {"name": "REST round", "goal": "prove the route", "user": "admin"})
    assert status == 200, body
    rid = body["id"]

    status, overview = http_get(
        base, f"/api/workspaces/{wid}/ontology/workflow?user=admin")
    assert status == 200
    assert overview["active_round_id"] == rid

    status, detail = http_get(
        base, f"/api/workspaces/{wid}/ontology/workflow/rounds/{rid}?user=admin")
    assert status == 200
    assert detail["round"]["id"] == rid

    first_cq = None
    for index in range(1, 6):
        status, cq = http_post(
            base,
            f"/api/workspaces/{wid}/ontology/workflow/rounds/{rid}/cqs",
            {"code": f"CQ-0{index}", "question": f"any vehicles? {index}",
             "priority": "P0", "expected": {"kind": "row_count", "value": 2},
             "query": {"object_type": "Vehicle"}, "user": "admin"})
        assert status == 200
        first_cq = first_cq or cq["id"]

    status, exported = http_get(
        base,
        f"/api/workspaces/{wid}/ontology/workflow/rounds/{rid}/cqs/export?format=csv&user=admin")
    assert status == 200
    assert exported["count"] == 5 and "any vehicles" in exported["content"]

    status, imported = http_post(
        base,
        f"/api/workspaces/{wid}/ontology/workflow/rounds/{rid}/cqs/import",
        {"format": "csv", "content":
            "code,question,priority,expected_kind,expected_value,query_kind,object_type\n"
            "CQ-06,imported through REST,P1,row_count,2,fixture,Vehicle\n",
         "user": "admin"})
    assert status == 200, imported
    assert imported["imported"] == 1

    status, _ = http_post(
        base,
        f"/api/workspaces/{wid}/ontology/workflow/rounds/{rid}/samples",
        {"name": "REST sample", "objects": {
            "Vehicle": [{"id": "V001", "plate": "A"}, {"id": "V002", "plate": "B"}],
        }, "user": "admin"})
    assert status == 200

    status, preview = http_post(
        base,
        f"/api/workspaces/{wid}/ontology/workflow/rounds/{rid}/cqs/{first_cq}/run",
        {"user": "admin"})
    assert status == 200
    assert preview["result"]["status"] == "passed"

    status, checked = http_post(
        base,
        f"/api/workspaces/{wid}/ontology/workflow/rounds/{rid}/check",
        {"user": "admin"})
    assert status == 200, checked
    assert checked["validation"]["ok"] is True

    status, run = http_post(
        base,
        f"/api/workspaces/{wid}/ontology/workflow/rounds/{rid}/runs",
        {"user": "admin"})
    assert status == 200
    assert run["status"] == "passed"

    status, _ = http_post(
        base,
        f"/api/workspaces/{wid}/ontology/workflow/rounds/{rid}/reviews",
        {"decision": "approve", "comment": "evidence is enough", "user": "admin"})
    assert status == 200

    status, published = http_post(
        base,
        f"/api/workspaces/{wid}/ontology/workflow/rounds/{rid}/publish",
        {"user": "admin"})
    assert status == 200, published
    assert published["round"]["status"] == "accepted"

    status, report = http_get(
        base,
        f"/api/workspaces/{wid}/ontology/workflow/rounds/{rid}/report?user=admin")
    assert status == 200
    assert "# " in report["markdown"]


def test_cq_import_export_round_trip(platform, workspace_id):
    from onto_platform.application.workflow_cq_io import parse_cqs

    rid = _round(platform, workspace_id)["id"]
    csv_content = "\n".join([
        "code,question,priority,type,expected_kind,expected_value,query_kind,object_type,model_refs",
        "CQ-01,有哪些车辆？,P0,competency,row_count,2,fixture,Vehicle,Vehicle",
        "CQ-02,沪A12345 是否存在？,P0,competency,row_count,1,fixture,Vehicle,Vehicle.plate",
        "CQ-03,V001 经过了哪个收费站？,P1,competency,row_count,1,fixture,passes,passes",
        "CQ-04,有没有不存在的车牌？,P2,validation,empty,,fixture,Vehicle,",
        "CQ-05,运行时有多少车辆？,P1,competency,row_count,2,runtime,Vehicle,",
    ])
    result = platform.workflow.import_cqs(
        workspace_id, "admin", rid,
        {"format": "csv", "content": csv_content})
    assert result["imported"] == 5
    assert [cq["code"] for cq in result["cqs"]] == [
        "CQ-01", "CQ-02", "CQ-03", "CQ-04", "CQ-05"]

    exported = platform.workflow.export_cqs(workspace_id, "admin", rid, "csv")
    assert exported["count"] == 5
    assert "有哪些车辆" in exported["content"]
    parsed = parse_cqs(exported["content"], "csv")
    assert len(parsed) == 5
    assert parsed[4]["query"]["kind"] == "runtime"

    markdown = platform.workflow.export_cqs(
        workspace_id, "admin", rid, "markdown")
    assert "| code | question |" in markdown["content"]
    assert len(parse_cqs(markdown["content"], "markdown")) == 5

    with pytest.raises(ValidationError):
        platform.workflow.import_cqs(
            workspace_id, "admin", rid,
            {"format": "csv",
             "content": "code,question\nCQ-01,重复编号不能导入\n"})

    # Spreadsheets often title-case headers; the parser must not silently drop
    # the question column and then reject every row.
    title_case = parse_cqs(
        "Code,Question,Priority,Expected_Kind,Expected_Value,Query_Kind,Object_Type\n"
        "CQ-06,大写表头也能导入,P1,row_count,1,fixture,Vehicle\n", "csv")
    assert title_case[0]["question"] == "大写表头也能导入"
    json_title = parse_cqs(json.dumps([{
        "Code": "CQ-07", "Question": "JSON 大写键也认", "Priority": "P2",
        "Expected_Kind": "row_count", "Expected_Value": 1,
        "Query_Kind": "fixture", "Object_Type": "Vehicle",
    }]), "json")
    assert json_title[0]["question"] == "JSON 大写键也认"

    # The JSON branch must enforce the same import cap as CSV/Markdown.
    too_many = {"cqs": [
        {"code": f"J-{index}", "question": f"q{index}"}
        for index in range(201)
    ]}
    with pytest.raises(ValidationError):
        parse_cqs(json.dumps(too_many), "json")


def test_runtime_cq_preview_and_acceptance_without_samples(platform, workspace_id):
    rid = _round(platform, workspace_id)["id"]
    cq = _cq(platform, workspace_id, rid, "CQ-01", "运行时有多少车辆？",
             {"kind": "row_count", "value": 2},
             {"kind": "runtime", "object_type": "Vehicle"},
             priority="P0", model_refs=["Vehicle"])
    preview = platform.workflow.preview_cq(
        workspace_id, "admin", rid, cq["id"])
    assert preview["engine"] == "runtime"
    assert preview["result"]["status"] == "passed", preview
    assert preview["result"]["actual"]["row_count"] == 2

    run = platform.workflow.run_acceptance(workspace_id, "admin", rid)
    assert run["status"] == "passed", run["results"]
    assert run["engine"] == "runtime"
    assert run["evidence"]["sample_ids"] == []
    assert run["results"][0]["actual"]["rows"][0]["properties"]["plate"]

    # Runtime-only evidence does not require a sample set, but it still needs
    # the normal CQ count and semantic check before the gate can clear.
    check = platform.workflow.publish_check(workspace_id, "admin", rid)
    assert not any("没有样例实例" in blocker for blocker in check["blockers"])


def test_runtime_only_round_can_publish(platform, workspace_id):
    rid = _round(platform, workspace_id)["id"]
    for index in range(1, 6):
        _cq(platform, workspace_id, rid, f"CQ-0{index}",
            f"运行时车辆问题 {index}",
            {"kind": "row_count", "value": 2},
            {"kind": "runtime", "object_type": "Vehicle"},
            priority="P0", model_refs=["Vehicle"])
    check = platform.workflow.check(workspace_id, "admin", rid)
    assert not any("没有样例实例" in blocker for blocker in check["publish_check"]["blockers"])
    run = platform.workflow.run_acceptance(workspace_id, "admin", rid)
    assert run["status"] == "passed", run["results"]
    platform.workflow.submit_review(
        workspace_id, "admin", rid, {"decision": "approve"})
    published = platform.workflow.publish(workspace_id, "admin", rid)
    assert published["round"]["status"] == "accepted"
    assert published["ontology"]["lifecycle"] == "published"


def test_sample_validation_and_configurable_target(platform, workspace_id):
    rid = _round(platform, workspace_id)["id"]
    sample = platform.workflow.add_sample(workspace_id, "admin", rid, {
        "name": "有拼写错误的样例",
        "objects": {"Vehicle": [{"id": "V001", "plte": "沪A12345"}]},
    })
    assert sample["validation"]["ok"] is False
    assert any("plte" in problem for problem in sample["validation"]["problems"])

    updated = platform.workflow.update_round(
        workspace_id, "admin", rid,
        {"target": {"min_cq": 2, "max_cq": 4,
                    "p0_pass_rate": 100, "overall_pass_rate": 90}})
    assert updated["target"]["min_cq"] == 2
    _cq(platform, workspace_id, rid, "CQ-01", "有多少车辆？",
        {"kind": "row_count", "value": 1},
        {"object_type": "Vehicle"}, priority="P0")
    check = platform.workflow.publish_check(workspace_id, "admin", rid)
    assert any("少于本轮目标 2" in blocker for blocker in check["blockers"])
    assert any("与本体不一致" in warning for warning in check["warnings"])



