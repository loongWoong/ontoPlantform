"""P1 Integration-B: REST routes for rules, business events and app spec.

Drives the real HTTP handler on a socket (``http_server`` fixture) against the
seeded traffic workspace: Vehicle/V001 has balance 100, Vehicle/V002 balance
20, and both are materialized by the seeded projection.

Route-disambiguation cases are covered on purpose: ``POST rules/evaluate`` vs
``POST rules/{rid}/publish``, ``POST event-contracts/{name}/publish`` (dotted
name) vs ``POST events/emit``, ``GET applications/{aid}/spec`` vs the generic
``GET applications/{aid}``, and interaction DELETE vs the other ``remove_*``
routes.
"""

from __future__ import annotations

from conftest import http_delete, http_get, http_post


def _url(wid: str, path: str = "") -> str:
    return f"/api/workspaces/{wid}{path}"


def _create_rule(base: str, wid: str, **overrides):
    body = {
        "user": "admin",
        "name": "余额非负",
        "object_type": "Vehicle",
        "condition": {"field": "balance", "op": "lt", "value": 0},
        "message": "余额不能为负",
    }
    body.update(overrides)
    return http_post(base, _url(wid, "/rules"), body)


# ----------------------------------------------------------------------
# rules
# ----------------------------------------------------------------------

def test_rules_create_publish_list_and_evaluate_over_http(http_server):
    base, wid = http_server
    status, rule = _create_rule(base, wid)
    assert status == 200
    assert rule["id"]
    assert rule["lifecycle"] == "draft"
    assert rule["object_type"] == "Vehicle"
    rule_id = rule["id"]

    status, listed = http_get(base, _url(wid, "/rules"))
    assert status == 200
    assert [item["id"] for item in listed["rules"]] == [rule_id]

    status, drafts = http_get(base, _url(wid, "/rules?lifecycle=draft"))
    assert status == 200 and len(drafts["rules"]) == 1
    status, published = http_get(
        base, _url(wid, "/rules?lifecycle=published"))
    assert status == 200 and published["rules"] == []

    status, published = http_post(
        base, _url(wid, f"/rules/{rule_id}/publish"), {"user": "admin"})
    assert status == 200
    assert published["lifecycle"] == "published"

    status, published = http_get(
        base, _url(wid, "/rules?lifecycle=published"))
    assert status == 200 and len(published["rules"]) == 1

    # "rules/evaluate" is a literal route, not a rule id: the publish route
    # (len == 3) and evaluate route (len == 2) must not shadow each other.
    status, result = http_post(
        base, _url(wid, "/rules/evaluate"),
        {"user": "admin", "object_type": "Vehicle"})
    assert status == 200
    assert result["passed"] is True
    assert result["violations"] == []
    assert result["object_count"] == 2

    status, by_ref = http_post(
        base, _url(wid, "/rules/evaluate"),
        {"user": "admin", "refs": ["Vehicle/V001"]})
    assert status == 200 and by_ref["object_count"] == 1


# ----------------------------------------------------------------------
# business events: contract -> publish -> subscribe -> emit
# ----------------------------------------------------------------------

def test_business_event_chain_over_http(http_server):
    base, wid = http_server
    schema = {
        "vehicle_id": {"type": "string", "required": True},
        "station_id": {"type": "string", "required": False},
    }
    status, contract = http_post(
        base, _url(wid, "/event-contracts"),
        {"user": "admin", "name": "traffic.vehicle_ping",
         "aggregate_kind": "Vehicle", "payload_schema": schema,
         "description": "车辆驶入"})
    assert status == 200
    assert contract["lifecycle"] == "draft"
    assert contract["payload_schema"]["vehicle_id"]["type"] == "string"

    status, listed = http_get(base, _url(wid, "/event-contracts"))
    assert status == 200
    assert [item["name"] for item in listed["contracts"]] == [
        "traffic.vehicle_ping"]

    status, published = http_post(
        base, _url(wid, "/event-contracts/traffic.vehicle_ping/publish"),
        {"user": "admin"})
    assert status == 200 and published["lifecycle"] == "published"

    status, subscription = http_post(
        base, _url(wid, "/event-subscriptions"),
        {"user": "admin", "name": "billing",
         "event_type": "traffic.vehicle_ping",
         "filter": {"station_id": "ST001"},
         "handler_key": "billing_handler"})
    assert status == 200
    assert subscription["handler_key"] == "billing_handler"
    sub_id = subscription["id"]

    status, listed = http_get(base, _url(wid, "/event-subscriptions"))
    assert status == 200
    assert [item["id"] for item in listed["subscriptions"]] == [sub_id]

    status, event = http_post(
        base, _url(wid, "/events/emit"),
        {"user": "admin", "event_type": "traffic.vehicle_ping",
         "aggregate_kind": "Vehicle", "aggregate_id": "Vehicle/V001",
         "payload": {"vehicle_id": "V001", "station_id": "ST001"}})
    assert status == 200
    assert event["event_id"]
    assert event["contract"] == "traffic.vehicle_ping"
    assert event["event_version"] == 1
    assert [sub["id"] for sub in event["matched_subscriptions"]] == [sub_id]

    status, events = http_get(
        base, _url(wid, "/events?type=traffic.vehicle_ping"))
    assert status == 200
    assert [row["id"] for row in events] == [event["event_id"]]

    status, deleted = http_delete(
        base, _url(wid, f"/event-subscriptions/{sub_id}"))
    assert status == 200 and deleted["deleted"] is True
    status, listed = http_get(base, _url(wid, "/event-subscriptions"))
    assert listed["subscriptions"] == []


# ----------------------------------------------------------------------
# application spec + interactions
# ----------------------------------------------------------------------

def test_application_spec_and_interactions_over_http(http_server):
    base, wid = http_server
    status, app = http_post(base, _url(wid, "/applications"),
                            {"user": "admin", "name": "HTTP 规格应用"})
    assert status == 200
    app_id = app["id"]

    status, spec = http_get(
        base, _url(wid, f"/applications/{app_id}/spec"))
    assert status == 200
    assert set(spec) == {"spec", "diagnostics", "valid"}
    assert spec["spec"]["interactions"] == []

    status, updated = http_post(
        base, _url(wid, f"/applications/{app_id}/interactions"),
        {"user": "admin", "interaction_id": "dispatch", "kind": "workflow",
         "title": "调度"})
    assert status == 200
    assert [item["id"] for item in updated["interactions"]] == ["dispatch"]

    status, spec = http_get(
        base, _url(wid, f"/applications/{app_id}/spec"))
    assert status == 200
    assert [item["id"] for item in spec["spec"]["interactions"]] == [
        "dispatch"]
    assert spec["valid"] is True

    # the generic GET application route still resolves (len == 2 vs spec len 3)
    status, fetched = http_get(
        base, _url(wid, f"/applications/{app_id}"))
    assert status == 200 and fetched["name"] == "HTTP 规格应用"

    status, removed = http_delete(
        base, _url(wid, f"/applications/{app_id}/interactions/dispatch"))
    assert status == 200
    assert removed["interactions"] == []
    status, spec = http_get(
        base, _url(wid, f"/applications/{app_id}/spec"))
    assert spec["spec"]["interactions"] == []


# ----------------------------------------------------------------------
# negative cases (400 / 404)
# ----------------------------------------------------------------------

def test_http_negative_cases(http_server):
    base, wid = http_server

    # 1) a rule naming a field the ontology does not define -> 400
    status, body = _create_rule(
        base, wid, condition={"field": "ghost", "op": "eq", "value": 1})
    assert status == 400
    assert "not a property" in body["error"]

    # 2) emitting through a draft (unpublished) contract -> 400
    http_post(base, _url(wid, "/event-contracts"),
              {"user": "admin", "name": "traffic.draft_only",
               "aggregate_kind": "Vehicle",
               "payload_schema": {"vehicle_id": {"type": "string",
                                                 "required": True}}})
    status, body = http_post(
        base, _url(wid, "/events/emit"),
        {"user": "admin", "event_type": "traffic.draft_only",
         "aggregate_kind": "Vehicle", "aggregate_id": "Vehicle/V001",
         "payload": {"vehicle_id": "V001"}})
    assert status == 400
    assert "publish" in body["error"]

    # 3) interactions on a published application are immutable -> 400
    status, app = http_post(base, _url(wid, "/applications"),
                            {"user": "admin", "name": "已发布应用"})
    assert status == 200
    app_id = app["id"]
    http_post(base, _url(wid, f"/applications/{app_id}/views"),
              {"user": "admin", "alias": "vehicles", "title": "车辆",
               "kind": "table", "object_type": "Vehicle",
               "properties": ["plate", "balance"]})
    status, published = http_post(
        base, _url(wid, f"/applications/{app_id}/publish"),
        {"user": "admin"})
    assert status == 200
    assert published["lifecycle"] == "published"
    status, body = http_post(
        base, _url(wid, f"/applications/{app_id}/interactions"),
        {"user": "admin", "interaction_id": "late", "kind": "workflow"})
    assert status == 400
    assert "published" in body["error"]

    # 4) deleting an interaction that does not exist -> 404
    status, app = http_post(base, _url(wid, "/applications"),
                            {"user": "admin", "name": "删除测试应用"})
    assert status == 200
    status, body = http_delete(
        base, _url(wid, f"/applications/{app['id']}/interactions/missing"))
    assert status == 404

    # 5) a non-numeric contract version is a client error, not a 500
    status, body = http_post(
        base, _url(wid, "/event-contracts"),
        {"user": "admin", "name": "traffic.bad_version",
         "aggregate_kind": "Vehicle", "payload_schema": {},
         "version": "not-a-number"})
    assert status == 400
    assert "version" in body["error"]
