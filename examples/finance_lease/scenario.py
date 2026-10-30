"""金融租赁场景：本体、测试数据与平台装载逻辑。

这个样例刻意避开高速收费领域的字段（车牌、收费站、通行交易），用来
验证 OntoPlatform 的智能视图是否真正由「本体 + 实例 + 关系」驱动：

    承租企业 Lessee
        └── 租赁合同 LeaseContract
              ├── 租赁物 Equipment
              ├── 租金账单 RentalPayment
              └── 风险预警 RiskAlert

装载后会得到 3 家承租企业、5 台租赁物、5 份合同、20 多期租金账单和
3 条风险预警。默认焦点应落在风险最高的租赁合同上，而不是任何车辆。
"""

from __future__ import annotations

from typing import Any

ACTOR = "admin"
WORKSPACE_NAME = "金融租赁智能风控"
ONTOLOGY_NAME = "finance-lease"

# ---------------------------------------------------------------------------
# 本体：对象、属性、关系、术语
# ---------------------------------------------------------------------------

OBJECT_TYPES = [
    {
        "name": "Lessee",
        "description": "承租企业，融资租赁合同的付款主体",
        "identity": ["lessee_id"],
        "properties": [
            {"name": "lessee_id", "type": "string", "description": "承租企业编号", "required": True},
            {"name": "lessee_name", "type": "string", "description": "承租企业名称"},
            {"name": "industry", "type": "string", "description": "所属行业"},
            {"name": "region", "type": "string", "description": "注册区域"},
            {"name": "credit_score", "type": "integer", "description": "信用评分"},
            {"name": "status", "type": "string", "description": "合作状态：normal/watch/risk"},
        ],
    },
    {
        "name": "Equipment",
        "description": "租赁物，融资租赁合同的标的资产",
        "identity": ["equipment_id"],
        "properties": [
            {"name": "equipment_id", "type": "string", "description": "租赁物编号", "required": True},
            {"name": "equipment_name", "type": "string", "description": "租赁物名称"},
            {"name": "category", "type": "string", "description": "设备分类"},
            {"name": "original_value", "type": "number", "description": "原始价值"},
            {"name": "book_value", "type": "number", "description": "账面价值"},
            {"name": "condition_status", "type": "string", "description": "设备状态"},
            {"name": "location", "type": "string", "description": "存放地点"},
        ],
    },
    {
        "name": "LeaseContract",
        "description": "融资租赁合同，连接承租企业、租赁物、租金账单与风险预警",
        "identity": ["contract_id"],
        "properties": [
            {"name": "contract_id", "type": "string", "description": "合同编号", "required": True},
            {"name": "lessee_id", "type": "string", "description": "承租企业编号"},
            {"name": "equipment_id", "type": "string", "description": "租赁物编号"},
            {"name": "contract_amount", "type": "number", "description": "合同金额"},
            {"name": "monthly_rent", "type": "number", "description": "月租金"},
            {"name": "start_date", "type": "datetime", "description": "起租日"},
            {"name": "end_date", "type": "datetime", "description": "合同到期日"},
            {"name": "status", "type": "string", "description": "合同状态：active/overdue/closed"},
            {"name": "overdue_days", "type": "integer", "description": "当前逾期天数"},
            {"name": "risk_level", "type": "string", "description": "风险等级：low/medium/high"},
        ],
    },
    {
        "name": "RentalPayment",
        "description": "租金账单，按合同账期记录应缴与实缴",
        "identity": ["payment_id"],
        "properties": [
            {"name": "payment_id", "type": "string", "description": "账单编号", "required": True},
            {"name": "contract_id", "type": "string", "description": "所属合同编号"},
            {"name": "due_date", "type": "datetime", "description": "应缴日期"},
            {"name": "paid_at", "type": "datetime", "description": "实缴时间"},
            {"name": "due_amount", "type": "number", "description": "应缴金额"},
            {"name": "paid_amount", "type": "number", "description": "实缴金额"},
            {"name": "payment_status", "type": "string", "description": "缴费状态：paid/pending/overdue"},
            {"name": "overdue_days", "type": "integer", "description": "账单逾期天数"},
        ],
    },
    {
        "name": "RiskAlert",
        "description": "合同风险预警，由租金逾期、设备异常或征信变化触发",
        "identity": ["alert_id"],
        "properties": [
            {"name": "alert_id", "type": "string", "description": "预警编号", "required": True},
            {"name": "contract_id", "type": "string", "description": "关联合同编号"},
            {"name": "alert_type", "type": "string", "description": "预警类型"},
            {"name": "severity", "type": "string", "description": "严重程度：low/medium/high/critical"},
            {"name": "alert_status", "type": "string", "description": "预警状态：open/closed"},
            {"name": "raised_at", "type": "datetime", "description": "预警时间"},
        ],
    },
]

RELATIONS = [
    # 先声明预警关系，让状态视角优先读取风险预警的 severity。
    {"name": "has_alert", "source": "LeaseContract", "target": "RiskAlert",
     "description": "合同产生风险预警"},
    {"name": "has_payment", "source": "LeaseContract", "target": "RentalPayment",
     "description": "合同包含租金账单"},
    {"name": "leases", "source": "LeaseContract", "target": "Equipment",
     "description": "合同租赁的设备"},
    {"name": "belongs_to_lessee", "source": "Lessee", "target": "LeaseContract",
     "description": "承租企业签署的合同"},
]

VOCABULARY = [
    ("租赁合同", "LeaseContract", "object"),
    ("承租企业", "Lessee", "object"),
    ("租赁物", "Equipment", "object"),
    ("租金账单", "RentalPayment", "object"),
    ("风险预警", "RiskAlert", "object"),
    ("逾期", "LeaseContract.overdue_days", "property"),
    ("风险等级", "LeaseContract.risk_level", "property"),
    ("信用评分", "Lessee.credit_score", "property"),
]

# ---------------------------------------------------------------------------
# 测试数据
# ---------------------------------------------------------------------------

LESSEES = [
    {"lessee_id": "L001", "lessee_name": "济南重工装备制造有限公司", "industry": "高端装备",
     "region": "山东济南", "credit_score": 78, "status": "normal"},
    {"lessee_id": "L002", "lessee_name": "青岛远洋冷链物流有限公司", "industry": "冷链物流",
     "region": "山东青岛", "credit_score": 62, "status": "watch"},
    {"lessee_id": "L003", "lessee_name": "郑州新能源科技有限公司", "industry": "新能源",
     "region": "河南郑州", "credit_score": 45, "status": "risk"},
]

EQUIPMENT = [
    {"equipment_id": "E001", "equipment_name": "数控龙门铣床", "category": "机床",
     "original_value": 2800000, "book_value": 2100000, "condition_status": "in_use",
     "location": "济南"},
    {"equipment_id": "E002", "equipment_name": "冷藏运输车", "category": "运输设备",
     "original_value": 680000, "book_value": 520000, "condition_status": "in_use",
     "location": "青岛"},
    {"equipment_id": "E003", "equipment_name": "光伏组件生产线", "category": "新能源设备",
     "original_value": 4200000, "book_value": 3950000, "condition_status": "maintenance",
     "location": "郑州"},
    {"equipment_id": "E004", "equipment_name": "六轴工业机器人", "category": "自动化设备",
     "original_value": 1200000, "book_value": 980000, "condition_status": "idle",
     "location": "潍坊"},
    {"equipment_id": "E005", "equipment_name": "冷链仓储机组", "category": "仓储设备",
     "original_value": 900000, "book_value": 610000, "condition_status": "in_use",
     "location": "青岛"},
]

CONTRACTS = [
    {"contract_id": "HT2023-001", "lessee_id": "L001", "equipment_id": "E001",
     "contract_amount": 2500000, "monthly_rent": 78000, "start_date": "2023-01-15",
     "end_date": "2027-01-14", "status": "active", "overdue_days": 0, "risk_level": "low"},
    {"contract_id": "HT2023-002", "lessee_id": "L002", "equipment_id": "E002",
     "contract_amount": 600000, "monthly_rent": 18000, "start_date": "2023-06-01",
     "end_date": "2027-05-31", "status": "overdue", "overdue_days": 45, "risk_level": "high"},
    {"contract_id": "HT2025-003", "lessee_id": "L003", "equipment_id": "E003",
     "contract_amount": 3800000, "monthly_rent": 110000, "start_date": "2025-02-20",
     "end_date": "2030-02-19", "status": "overdue", "overdue_days": 62, "risk_level": "high"},
    {"contract_id": "HT2024-004", "lessee_id": "L001", "equipment_id": "E004",
     "contract_amount": 1050000, "monthly_rent": 32000, "start_date": "2024-03-10",
     "end_date": "2028-03-09", "status": "active", "overdue_days": 0, "risk_level": "low"},
    {"contract_id": "HT2022-005", "lessee_id": "L002", "equipment_id": "E005",
     "contract_amount": 900000, "monthly_rent": 25000, "start_date": "2022-09-01",
     "end_date": "2026-08-31", "status": "closed", "overdue_days": 0, "risk_level": "low"},
]

# 账期按合同生成，避免手写二十多行时出现日期/金额不一致。
PAYMENTS: list[dict[str, Any]] = []


def _add_payments(contract_id: str, start_month: int, count: int,
                  due_amount: float, overdue_from: int | None = None) -> None:
    for index in range(count):
        month = start_month + index
        year = 2025 + (month - 1) // 12
        month_no = (month - 1) % 12 + 1
        due_date = f"{year:04d}-{month_no:02d}-15"
        overdue = overdue_from is not None and index >= overdue_from
        status = "overdue" if overdue else "paid"
        payment = {
            "payment_id": f"{contract_id}-P{index + 1:02d}",
            "contract_id": contract_id,
            "due_date": due_date,
            "due_amount": due_amount,
            "paid_amount": 0 if overdue else due_amount,
            "payment_status": status,
            "overdue_days": (index - overdue_from + 1) * 30 if overdue else 0,
        }
        if not overdue:
            payment["paid_at"] = f"{year:04d}-{month_no:02d}-14T09:30:00"
        PAYMENTS.append(payment)


_add_payments("HT2023-001", 1, 6, 78000)
_add_payments("HT2023-002", 1, 6, 18000, overdue_from=4)
_add_payments("HT2025-003", 3, 8, 110000, overdue_from=5)
_add_payments("HT2024-004", 4, 5, 32000)
_add_payments("HT2022-005", 1, 4, 25000)

ALERTS = [
    {"alert_id": "A001", "contract_id": "HT2025-003", "alert_type": "租金逾期超60天",
     "severity": "critical", "alert_status": "open", "raised_at": "2026-04-20T08:30:00"},
    {"alert_id": "A002", "contract_id": "HT2023-002", "alert_type": "连续两期未缴",
     "severity": "high", "alert_status": "open", "raised_at": "2026-03-15T10:00:00"},
    {"alert_id": "A003", "contract_id": "HT2023-001", "alert_type": "常规租后巡检",
     "severity": "low", "alert_status": "closed", "raised_at": "2025-12-01T14:00:00"},
]

ASSETS = [
    {"name": "lessees", "config": {"rows": LESSEES}},
    {"name": "equipment", "config": {"rows": EQUIPMENT}},
    {"name": "contracts", "config": {"rows": CONTRACTS}},
    {"name": "payments", "config": {"rows": PAYMENTS}},
    {"name": "alerts", "config": {"rows": ALERTS}},
]

OBJECT_MAPPINGS = [
    {
        "asset": "lessees", "object_type": "Lessee", "id_field": "lessee_id",
        "fields": [
            {"source_field": "lessee_id", "property": "lessee_id"},
            {"source_field": "lessee_name", "property": "lessee_name"},
            {"source_field": "industry", "property": "industry"},
            {"source_field": "region", "property": "region"},
            {"source_field": "credit_score", "property": "credit_score"},
            {"source_field": "status", "property": "status"},
        ],
    },
    {
        "asset": "equipment", "object_type": "Equipment", "id_field": "equipment_id",
        "fields": [
            {"source_field": "equipment_id", "property": "equipment_id"},
            {"source_field": "equipment_name", "property": "equipment_name"},
            {"source_field": "category", "property": "category"},
            {"source_field": "original_value", "property": "original_value"},
            {"source_field": "book_value", "property": "book_value"},
            {"source_field": "condition_status", "property": "condition_status"},
            {"source_field": "location", "property": "location"},
        ],
    },
    {
        "asset": "contracts", "object_type": "LeaseContract", "id_field": "contract_id",
        "fields": [
            {"source_field": "contract_id", "property": "contract_id"},
            {"source_field": "lessee_id", "property": "lessee_id"},
            {"source_field": "equipment_id", "property": "equipment_id"},
            {"source_field": "contract_amount", "property": "contract_amount"},
            {"source_field": "monthly_rent", "property": "monthly_rent"},
            {"source_field": "start_date", "property": "start_date"},
            {"source_field": "end_date", "property": "end_date"},
            {"source_field": "status", "property": "status"},
            {"source_field": "overdue_days", "property": "overdue_days"},
            {"source_field": "risk_level", "property": "risk_level"},
        ],
    },
    {
        "asset": "payments", "object_type": "RentalPayment", "id_field": "payment_id",
        "fields": [
            {"source_field": "payment_id", "property": "payment_id"},
            {"source_field": "contract_id", "property": "contract_id"},
            {"source_field": "due_date", "property": "due_date"},
            {"source_field": "paid_at", "property": "paid_at"},
            {"source_field": "due_amount", "property": "due_amount"},
            {"source_field": "paid_amount", "property": "paid_amount"},
            {"source_field": "payment_status", "property": "payment_status"},
            {"source_field": "overdue_days", "property": "overdue_days"},
        ],
    },
    {
        "asset": "alerts", "object_type": "RiskAlert", "id_field": "alert_id",
        "fields": [
            {"source_field": "alert_id", "property": "alert_id"},
            {"source_field": "contract_id", "property": "contract_id"},
            {"source_field": "alert_type", "property": "alert_type"},
            {"source_field": "severity", "property": "severity"},
            {"source_field": "alert_status", "property": "alert_status"},
            {"source_field": "raised_at", "property": "raised_at"},
        ],
    },
]

RELATION_MAPPINGS = [
    {"asset": "alerts", "relation": "has_alert",
     "source_field": "contract_id", "target_field": "alert_id"},
    {"asset": "payments", "relation": "has_payment",
     "source_field": "contract_id", "target_field": "payment_id"},
    {"asset": "contracts", "relation": "leases",
     "source_field": "contract_id", "target_field": "equipment_id"},
    {"asset": "contracts", "relation": "belongs_to_lessee",
     "source_field": "lessee_id", "target_field": "contract_id"},
]

RISK_REVIEW_SOURCE = """
def execute(ctx):
    contract = ctx.object()
    overdue_days = int(contract.get("overdue_days") or 0)
    level = "high" if overdue_days >= 30 else ("medium" if overdue_days > 0 else "low")
    ctx.set(contract.ref, "risk_level", level)
    return {
        "success": True,
        "contract_id": contract.get("contract_id"),
        "overdue_days": overdue_days,
        "risk_level": level,
    }
"""


def seed_finance_lease(platform: Any, actor: str = ACTOR,
                       workspace_name: str = WORKSPACE_NAME) -> str:
    """Create the finance-lease workspace and project its test data.

    Returns the new workspace id.  Re-running creates a new workspace; the
    platform intentionally has no "find by name" mutation.
    """
    workspace = platform.workspaces.create(
        workspace_name, actor=actor,
        description="融资租赁全链路：承租企业、租赁物、合同、租金账单与风险预警",
    )
    wid = workspace["id"]

    platform.ontologies.create(
        wid, ONTOLOGY_NAME, actor=actor,
        description="金融租赁本体：合同为中心，关联承租企业、租赁物、租金账单和风险预警",
    )
    for spec in OBJECT_TYPES:
        platform.ontologies.upsert_object(
            wid, actor, spec["name"],
            properties=spec["properties"],
            description=spec["description"],
            identity=spec["identity"],
        )
    for relation in RELATIONS:
        platform.ontologies.upsert_relation(
            wid, actor, relation["name"], relation["source"], relation["target"],
            description=relation["description"],
        )
    for term, concept, kind in VOCABULARY:
        platform.ontologies.add_term(wid, actor, term, concept, kind=kind)
    platform.ontologies.publish(wid, actor)

    source = platform.data.connect_source(
        wid, actor, "租赁业务库", "static", assets=ASSETS,
    )
    projection = platform.data.create_projection(
        wid, actor, "融资租赁主数据投影", source["id"],
        object_mappings=OBJECT_MAPPINGS,
        relation_mappings=RELATION_MAPPINGS,
    )
    platform.data.run_projection(wid, projection["id"], actor)

    capability = platform.capabilities.create(
        wid, actor, key="risk_review", name="合同风险复核",
        target_type="LeaseContract",
        description="按当前逾期天数重新核定合同风险等级",
        inputs=[],
        implementation={"kind": "python", "source": RISK_REVIEW_SOURCE},
    )
    platform.capabilities.publish(wid, capability["id"], actor)
    return wid
