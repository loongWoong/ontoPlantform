# 责任矩阵 + 验收基线模板

本模板把"知识中台"的推进责任与验收标准落成**可填写、可核对**的表格，字段严格对齐平台 `工程流` 的数据结构。每期立项填一轮，验收时逐项核对，发布前由平台硬门禁自动检查。

**使用方式**：复制下面的表格到立项文档 / 协作表格，按"填写说明"填；其中 CQ 部分可直接按模板导成 CSV 或 Markdown 一键导入平台（见[文末导入模板](#cq-导入模板)）。

## 0. 填写周期与角色

| 角色 | 平台角色 | 填什么 | 什么时候填 |
|---|---|---|---|
| 业务方（领域专家） | viewer（默认）/ editor | 提 CQ、判定语义、验收结果 | 立项 + 验收 |
| 知识工程师 | editor | 建模、形式化、约束、CI | 全程 |
| Owner（有决策权） | owner | 终审、拍板发布、对"不做什么"负责 | 立项 + 发布 |

## A. 范围卡（对齐 `scope`）

| 字段 | 平台字段 | 填写内容 |
|---|---|---|
| 一句话目的 | `goal` | 本轮要回答什么业务问题（一句话） |
| 范围内 | `scope.in_scope` | 覆盖哪些业务域 / 对象（逗号分隔） |
| 范围外（不做） | `scope.out_of_scope` | 明确不做什么 |
| 干系人 | `scope.actors` | 谁参与、谁拍板（逗号分隔） |
| 关键决策 | `scope.decisions` | 已拍板的口径 / 权威源 |

**示例**：`goal = 为高速收费运营回答"计费规则如何判定、异常通行如何识别"`；`out_of_scope = 不做实时告警、不接第三方支付`；`actors = 运营负责人, 计费规则岗, 知识工程师`。

## B. 门禁目标（对齐 `target`）

| 字段 | 平台字段 | 默认值 | 本轮取值 |
|---|---|---|---|
| CQ 数量下限 | `target.min_cq` | 5 |  |
| CQ 数量上限 | `target.max_cq` | 10 |  |
| P0 通过率 | `target.p0_pass_rate` | 100% |  |
| 总体通过率 | `target.overall_pass_rate` | 80% |  |

## C. 责任矩阵

| 工作项 | 责任人 | 平台角色 | 完成标志 | 审计事件 |
|---|---|---|---|---|
| 写范围与干系人 | Owner | owner | `scope.summary` 有值 | `round.created` |
| 提 5–10 条 CQ | 业务方 | editor | CQ ≥ `min_cq` | `cq.created` |
| 复用调研 | 知识工程师 | editor | 复用台账有决策 | — |
| 建模与形式化 | 知识工程师 | editor | 语义检查通过 | `round.submitted` |
| 样例实例 | 知识工程师 | editor | 样例覆盖 P0 | — |
| CQ 验收 | 业务方 + 知识工程师 | editor | 通过率达到目标 | `acceptance.run` |
| 评审结论 | 评审人 | owner | 独立评审结论 | `review.recorded` |
| 发布版本 | Owner | owner | 发布成功 | `round.published` |

## D. CQ 清单（对齐 `cq`）

每条 CQ 必须能追到来源、模型引用、样例、查询与期望，否则不计入通过率。

| 字段 | 平台字段 | 可选值 | 填写 |
|---|---|---|---|
| 编号 | `code` | CQ-001 … |  |
| 问题 | `question` | 自然语言 |  |
| 优先级 | `priority` | `P0 / P1 / P2` |  |
| 类型 | `type` | `scoping / competency / validation` |  |
| 来源 | `source` | 文档 / 访谈 / 工单 / 法规 |  |
| 理由 | `rationale` | 为什么问这条 |  |
| 期望类型 | `expected.kind` | `rows_contain / row_count / golden_rows / empty / boolean / manual` |  |
| 查询引擎 | `query.kind` | `fixture / runtime` |  |
| 对象类型 | `query.object_type` | 或 `query.relation + ref + direction` |  |
| 模型引用 | `model_refs` | 对象/关系名（逗号分隔） |  |
| 样例引用 | `sample_refs` | 样例集名 |  |

**期望类型速查**：

| `expected.kind` | 含义 | 必填字段 |
|---|---|---|
| `row_count` | 结果行数 | `expected.value` + `operator`（eq/gte/lte） |
| `rows_contain` | 结果包含指定行 | `expected.rows`（JSON 对象列表） |
| `golden_rows` | 结果完全等于黄金行 | `expected.rows` + `ordered` |
| `empty` | 结果为空 | — |
| `boolean` | 结果有/无 | `expected.value` |
| `manual` | 人工判定（不计入自动通过率） | `expected.note` |

## E. 复用台账（对齐 `reuse`）

| 候选标准/本体 | 决策 | 来源/版本 | 理由 | 负责人 |
|---|---|---|---|---|
|  | `reuse / extend / build / skip` |  |  |  |

## F. 验收基线（先立基线，再谈收益）

没有基线就无法验收。每期立项时记录**基线值**，期终记录**实测值**，差异就是可量化的价值：

| 指标 | 基线值 | 目标值 | 实测值 | 证据 |
|---|---|---|---|---|
| 因语义不一致的返工工时（人日/月） |  |  |  | 工单统计 |
| 跨系统对账差异笔数（笔/月） |  |  |  | 对账记录 |
| 跨系统取数/清洗耗时（人时/次） |  |  |  | 访谈 + 计时 |
| P0 CQ 通过率 | — | 100% |  | 验收运行 |
| 消费方使用量（次/周） |  |  |  | 平台使用日志 |

## G. 评审记录

| 字段 | 填写 |
|---|---|
| 评审人 |  |
| 结论 | 通过 / 要求修改 / 驳回 |
| 是否本轮最后一位模型编辑者 | 是 / 否（"是"记 warning） |
| 备注 |  |

---

## CQ 导入模板

平台支持 CSV / Markdown / JSON 三种导入（先校验后落库，编号重复整批拒绝）。列名同时接受中英文。

### 方式一：Markdown 表（复制即用）

```markdown
| code | question | priority | type | expected_kind | expected_value | expected_operator | query_kind | object_type | model_refs | source |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| CQ-001 | 一辆车从入口到出口经过哪些门架 | P0 | competency | rows_contain |  |  | fixture | Gantry | Vehicle,Gantry | 收费规则文档 3.2 节 |
| CQ-002 | 收费站对象应至少有多少条 | P1 | validation | row_count | 2 | gte | fixture | TollStation | TollStation | 访谈 |
| CQ-003 | 拥堵判定口径是否被业务认可 | P1 | scoping | manual |  |  |  |  | TollStation | 业务访谈 |
```

### 方式二：CSV（可下载）

完整 22 列表头与示例见可下载模板 [`cq-template.csv`](/cq-template.csv)，表头固定为：

```text
code,question,priority,type,expected_kind,expected_value,expected_operator,expected_rows,expected_ordered,expected_note,query_kind,object_type,relation,direction,ref,filters,limit,model_refs,sample_refs,source,rationale,notes
```

其中 `expected_rows`、`filters` 为 JSON（如 `[{"id":"G001"}]`、`{"status__eq":"congested"}`），含逗号时用引号包裹整格。`model_refs`、`sample_refs` 用逗号分隔多个值。
