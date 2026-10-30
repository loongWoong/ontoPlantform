"""金融租赁场景的智能视图回归测试。

验证点不是“能不能渲染”，而是：同一个智能视图适配层在没有车牌、没有
收费站、没有通行交易的本体上，仍然能完成焦点选择、语义推导、关系图、
统计/状态/解释/动作编译，并真实执行平台能力。
"""

from __future__ import annotations

import pytest

from examples.finance_lease.scenario import seed_finance_lease
from onto_platform.bootstrap import PlatformConfig, build_platform
from onto_platform.interfaces.smart_ui import (
    compile_smart_ui,
    invoke_smart_action,
    smart_ui_meta,
)


@pytest.fixture()
def finance_workspace():
    platform = build_platform(PlatformConfig(store="memory"))
    wid = seed_finance_lease(platform)
    meta = smart_ui_meta(platform, wid, "admin")
    return platform, wid, meta


def test_finance_lease_focus_is_contract_not_vehicle(finance_workspace):
    _platform, _wid, meta = finance_workspace

    assert meta["ready"] is True
    assert meta["bridge"]["focusObject"] == "LeaseContract"
    assert meta["bridge"]["focusValue"] == "HT2025-003"
    assert meta["bridge"]["identityKey"] == "contract_id"
    assert meta["bridge"]["focusLabel"] == "租赁合同"
    assert "plate" not in (meta["bridge"].get("focus") or {})

    assert meta["counts"] == {
        "Lessee": 3,
        "Equipment": 5,
        "LeaseContract": 5,
        "RentalPayment": 29,
        "RiskAlert": 3,
    }
    assert "为什么" in meta["defaultQuestion"]


def test_finance_lease_compiles_full_generic_view_set(finance_workspace):
    platform, wid, meta = finance_workspace
    compiled = compile_smart_ui(
        platform, wid, "admin", meta["defaultQuestion"], ["owner"])
    engine = compiled["compiled"]

    assert engine["ok"] is True
    perspectives = [view["perspective"] for view in engine["viewMap"]]
    assert perspectives == [
        "identity", "relationship", "timeline",
        "statistics", "state", "rule", "action",
    ]
    titles = {view["perspective"]: view["title"] for view in engine["viewMap"]}
    assert titles["statistics"] == "租赁合同统计"
    assert titles["state"] == "租赁合同状态"
    assert "通行" not in engine["program"]
    assert "车牌" not in engine["program"]
    assert "车辆" not in engine["program"]

    bundle = compiled["bundle"]
    assert bundle["identity"]["objectType"] == "LeaseContract"
    assert bundle["identity"]["title"] == "HT2025-003"
    assert "合同编号" in [fact["label"] for fact in bundle["identity"]["facts"]]

    relation_types = {node["type"] for node in bundle["relationship"]["nodes"]}
    assert {"LeaseContract", "Lessee", "Equipment",
            "RentalPayment", "RiskAlert"} <= relation_types
    assert bundle["relationship"]["centerId"].startswith("focus_")

    assert bundle["statistics"]["seriesLabel"] == "合同金额趋势"
    assert bundle["state"]["state"] == "需要关注"
    assert any(rule["hit"] for rule in bundle["state"]["rules"])
    assert bundle["rule"]["title"] == "租赁合同状态解释"


def test_finance_lease_action_runs_against_real_contract(finance_workspace):
    platform, wid, meta = finance_workspace
    action = next(item for item in meta["actions"] if item["key"] == "risk_review")

    result = invoke_smart_action(
        platform, wid, "admin", "risk_review",
        {"focusValue": meta["bridge"]["focusValue"], "role": "owner"},
    )

    assert action["target"] == "LeaseContract"
    assert result["ok"] is True
    assert result["target"] == "LeaseContract/HT2025-003"
    assert result["result"]["output"]["risk_level"] == "high"

    gateway = platform.ctx.gateway_for(wid)
    contract = gateway.get_object("LeaseContract/HT2025-003")
    assert contract["properties"]["risk_level"] == "high"
