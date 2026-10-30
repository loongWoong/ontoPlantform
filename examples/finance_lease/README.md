# 金融租赁智能风控样例

这是继高速收费、设备物联之后新增的第三个本体场景，用来验证智能视图
不依赖任何特定行业字段（车牌、收费站、通行交易）。

## 业务模型

```
承租企业 Lessee
    └── belongs_to_lessee ── 租赁合同 LeaseContract
                               ├── leases ──────── 租赁物 Equipment
                               ├── has_payment ─── 租金账单 RentalPayment
                               └── has_alert ───── 风险预警 RiskAlert
```

| 对象 | 关键属性 | 说明 |
| --- | --- | --- |
| `Lessee` | `lessee_id` `lessee_name` `industry` `credit_score` `status` | 承租企业 |
| `Equipment` | `equipment_id` `equipment_name` `original_value` `book_value` `condition_status` | 租赁物 |
| `LeaseContract` | `contract_id` `contract_amount` `monthly_rent` `start_date` `overdue_days` `risk_level` | 合同，智能视图的聚合根 |
| `RentalPayment` | `payment_id` `due_date` `due_amount` `paid_amount` `payment_status` | 租金账单 |
| `RiskAlert` | `alert_id` `severity` `alert_status` `raised_at` | 风险预警 |

## 测试数据

- 3 家承租企业：正常、关注、风险各一家
- 5 台租赁物：机床、运输、新能源产线、机器人、仓储机组
- 5 份合同：2 份逾期（45 / 62 天）、2 份履约中、1 份已结清
- 29 期租金账单：已缴、待缴、逾期混合
- 3 条风险预警：critical / high / low

默认焦点为 `LeaseContract / HT2025-003`（逾期 62 天、关联 critical 预警），
不是车辆或通行记录。

## 装载

```bash
# 写入运行中 API 使用的本地库，随后刷新控制台
python -m examples.finance_lease.bootstrap --db .onto/platform.db --verify

# 不落库，只在内存中构建并自检
python -m examples.finance_lease.bootstrap --memory --verify
```

自检会打印对象数量、焦点、7 个视角标题，并真实执行一次 `risk_review`
平台能力。

## 智能视图验证点

- `ready: true`，焦点 `LeaseContract / HT2025-003`
- 7 个视角：`对象画像 / 关系网络 / 事件时间线 / 租赁合同统计 /
  租赁合同状态 / 业务解释 / 处置动作`
- 关系图包含 `LeaseContract / Lessee / Equipment / RentalPayment / RiskAlert`
  五类节点
- 统计标题从对象名生成为「租赁合同统计」，不出现「通行」「车牌」
- 状态视角读取预警 `severity=critical`，给出「需要关注」
- `risk_review` 动作可执行，目标解析为 `LeaseContract/HT2025-003`

自动化回归见 `tests/test_finance_lease_smart_ui.py`。
