# 无大模型条件下的规则驱动智能 UI 方案

> 状态：**P0–P2 已实现并验证**（2026-11-03，见 §18）。
>
> 范围：在 **不依赖任何 LLM 服务** 的前提下，用「本体事实 + 声明式规则 + 确定性打分」完成从业务问题到 Smart UI 蓝图的生成。
>
> 结论：不新造一套编译器，而是把现有 L3 中隐含的关键词/固定表规则**外化为可版本化的规则包**，新增 `rule` 生成模式，继续复用现有平台裁决、L4 编译、真实数据 payload 和沙箱预览。

## 0. 结论先行

1. **规则只提议，编译器裁决。** 规则引擎输出 `Blueprint`（或 proposal），不能直接生成程序、布局、样式或脚本。最终仍走 `adjudicate → compile_blueprint → payload → preview`。
2. **本体是唯一事实源。** 对象角色、属性类型、关系、业务规则、动作权限都从已发布本体派生；规则不复制一份类型系统。
3. **无模型不是降级体验。** `rule` 与 `llm` 是并列的生成器；没有模型时默认走 `rule`，页面明确标注「规则生成 · 无模型调用」，而不是显示“回退”。
4. **规则可解释。** 每次生成保存 `matched / skipped / pruned` 规则轨迹，界面能回答“为什么有这个视图 / 为什么没有时间线 / 为什么动作不可用”。
5. **规则是数据，不是代码。** 规则包只允许白名单谓词与效果，不允许表达式执行、网络访问、布局坐标、脚本或 URL。
6. **默认包兼容现状。** 内置默认规则包必须复刻当前 `detect_goal + GOAL_PERSPECTIVES + derive_capabilities` 的行为，现有测试与固定问题集不回归。

## 1. 现状与可复用基线

当前系统已经具备“无 LLM 智能视图”的大部分底座，缺的是把散落规则变成可配置、可解释、可扩展的规则层。

| 层 | 现有能力 | 代码位置 | 本方案如何复用 |
|---|---|---|---|
| L1 本体 | 对象、属性、关系、规则、动作 | `engine/src/onto_ui_engine/models.py` | 作为规则事实源 |
| L2 能力派生 | 8 类视角及其本体证据 | `capabilities.py:derive_capabilities` | 生成 `UiFacts.capabilities`，规则只在此基础上选择 |
| L3 规划 | 关键词 → goal → 固定视角表；按角色裁剪动作 | `planner.py:detect_goal / plan_application` | 替换为规则包求值；默认包保持原行为 |
| L4 编译 | 蓝图 → OpenUI Lang 程序，确定性 | `compiler.py / emitter.py` | 完全不变 |
| 平台裁决 | Schema、对象、视角白名单、选项、动作权限、布局键剪除 | `smartui/adjudication.py` | 完全不变；规则输出仍需二次裁决 |
| 数据适配 | 聚焦对象、语义键推断、payload 组装 | `smartui/bridge.py` | 规则可使用同一份 `UiFacts`，避免语义漂移 |
| 生成器插件 | deterministic / llm 两个生成器 | `smartui/generators.py` | 新增 `rule` 生成器，或增强 deterministic |
| 工作空间 Pack | 视角白名单、视图预设、提示词包 | `smartui/packs.py` | 增加 `rulePack` 引用，不把代码放进 Pack |

现状缺口：

- `detect_goal` 是固定 if 顺序，新增意图必须改代码；
- `GOAL_PERSPECTIVES` 是硬编码表，不能按行业/工作空间调整；
- 规则的“为什么命中/为什么剪除”没有结构化输出；
- 没有规则版本、校验、试跑、发布与回滚；
- 规则只能影响 goal，不能声明视图权重、标题、选项来源；
- 缺少歧义澄清：对象/意图不明确时直接落到默认对象。

## 2. 目标与非目标

### 目标

- 无 LLM 配置时，业务问题可稳定生成可预览的 Smart UI。
- 规则显式引用本体对象/属性/关系/业务规则/动作权限，而不是只匹配关键词。
- 同一输入（问题、本体版本、规则包、角色、聚焦对象）产生同一蓝图与同一 `programHash`。
- 规则可配置、可测试、可审计、可回滚，但不降低现有安全边界。
- 保留 LLM 路径；规则引擎同时可作为 LLM 提议的校验/兜底。

### 非目标

- 不做开放域自然语言理解；不承诺任意长尾问法都能命中。
- 不让规则生成布局坐标、样式、脚本、SQL 或组件代码。
- 不替代 L4 编译器，不绕过平台裁决。
- 本期不引入 RETE/复杂推理机；规则规模小，简单求值 + 有界派生足够。

## 3. 总体架构

```text
                    ┌─────────────────────────────────────────────┐
                    │  已发布本体 + 数据快照 + 工作空间 Pack       │
                    └──────────────────────┬──────────────────────┘
                                           │
                          ┌────────────────▼────────────────┐
                          │ 1. 事实构建 UiFacts             │
                          │    对象/属性语义/关系/规则/动作  │
                          │    权限/可用组件/数据概况        │
                          └────────────────┬────────────────┘
                                           │
        ┌──────────────────────────────────▼──────────────────────────────────┐
        │ 2. 规则引擎（纯函数、无 I/O）                                       │
        │    Layer A 本体事实规则（代码派生，不可编辑）                       │
        │    Layer B 意图规则（词表/别名/标识符/对象提及 → goal/object/focus） │
        │    Layer C 组合与呈现规则（视角权重/顺序/标题/选项）                 │
        └──────────────────────────────────┬──────────────────────────────────┘
                                           │  RuleResult: intent + view candidates + trace
                          ┌────────────────▼────────────────┐
                          │ 3. 规划器（确定性）             │
                          │    能力交集 / 权重排序 / 预算    │
                          │    歧义澄清 / 置信度             │
                          └────────────────┬────────────────┘
                                           │  Blueprint
                          ┌────────────────▼────────────────┐
                          │ 4. 平台裁决（不变）             │
                          │    Schema / 白名单 / 选项 / 权限 │
                          └────────────────┬────────────────┘
                                           │
                          ┌────────────────▼────────────────┐
                          │ 5. L4 确定编译 → 真实数据 payload│
                          │    → 沙箱预览 → 发布/资产化      │
                          └─────────────────────────────────┘
```

关键边界：

- **规则引擎不做数据查询**。事实来自本体快照与轻量数据概况；真实数据仍由编译后的 payload 获取。
- **规则引擎不做权限判断**。它只能“建议动作”，动作是否保留由 `plan_application` 的角色交集中最后决定。
- **规则引擎不输出布局**。布局仍由编译器/运行时按组件能力自动生成。

## 4. 规则分层与事实模型

### 4.1 Layer A：本体事实规则（不可编辑）

这些不是用户规则，而是把本体信息规范化成稳定事实，供 Layer B/C 引用。

```text
UiFacts {
  object: {
    name, displayName, role, isQueryRoot,
    propertyCount, numericCount, temporalCount, geoCount,
    statusCount, derivedBooleanCount, linkCount,
    labels: [...], identifiers: [...]
  }
  properties: [{
    name, displayName, dataType,
    semanticRole: id | label | time | numeric | status | geo | boolean | text,
    required, derived, dictionaryDomain
  }]
  relations: [{name, source, target, cardinality, direction, pathDepth}]
  capabilities: [{perspective, viewType, component, evidence, title}]
  ontologyRules: [{name, relatedFields, level, causalChain, businessCause}]
  actions: [{actionId, primaryObjectType, roles, parameters, enabled}]
  dataProfile?: {rowCount, nullRatioByProperty, distinctByProperty}
}
```

事实生成原则：

- `semanticRole` 优先复用平台现有的 `_semantic_keys` 推断（时间、数值、状态、标识、标签）；
- 地理能力继续使用引擎的经纬度证据与一度关系可达性；
- 规则视角继续使用 `Rule.related_fields` 与 `Rule.description` 的关联判断；
- 维度对象先解析查询根，再派生能力（现有 `resolve_query_root`）；
- 所有事实带 `evidence`，便于解释。

### 4.2 Layer B：意图规则

输入：问题文本、UI 显式选择、聚焦对象/标识符、角色、Pack。
输出：`goal`、`object`、`focusKey/focusValue`、候选对象列表、歧义项、置信度。

规则信号：

| 信号 | 示例 | 用途 |
|---|---|---|
| `question.containsAny` | “异常 / 根因 / 为什么” | 命中异常分析 |
| `question.containsAll` | “最近” + “异常” | 降低误命中 |
| `question.excludes` | “正常”排除异常 | 抑制冲突 |
| `question.regex` | 通用标识符模式（车牌、设备号） | 提取 focus 值 |
| `object.alias` | “车辆 / 车 / 车牌” | 对象提及 |
| `property.semanticRole` | `id / label / time` | 标识符与对象线索 |
| `focus.object` | 页面已选设备 | 最高优先级 |
| `context.role` | `operator` | 动作与视角偏好 |
| `pack.defaultGoal` | 行业默认 | 无命中时的起点 |

对象解析顺序（确定性）：

1. 显式 `focus.object`（页面已选对象）；
2. 问题中的对象名/显示名/别名，最长匹配优先；
3. 问题中的标识符，按属性语义匹配 `id/plate/code/device_id`；
4. 数据聚焦启发式（现有 `_select_focus` 的评分）；
5. 仍不唯一时，返回 top 2–3 个对象候选，交给 UI 澄清。

### 4.3 Layer C：组合与呈现规则

输入：`UiFacts` + `goal` + 角色 + Pack。
输出：视角候选集、每个视角的权重/标题/选项、裁剪原因、最终 view 列表。

优先级与预算：

- 每个视角维护 `score`，由目标基线、能力证据、问题线索、Pack 预设、规则加分组成；
- 默认最多 6 个视角，Pack 可配置为 4–8；
- `identity` 作为对象锚点，能力存在时优先保留；
- 排序仍使用 `PERSPECTIVE_ORDER`，不按分数改变布局顺序；
- 分数相同按 `规则优先级 → 规则 ID → 视角顺序` 决胜，保证确定性；
- 所有入选视角必须能通过能力交集和组件可用性检查。

### 4.4 治理过滤（不可被规则绕过）

规则求值结束后，固定执行：

1. 视角必须在 `derive_capabilities` 中；
2. 视角必须在 Pack 的 `perspectives` 白名单中；
3. 动作必须满足角色交集；
4. 组件/renderer 必须已安装；
5. 选项必须通过现有 `normalize_view_options` 与 Schema；
6. 布局/样式/脚本键一律剪除。

这层不是规则，不能被工作空间规则覆盖；它只产生 `pruned` 原因。

### 4.5 规则来源与生效优先级

建议用单一整数 `effectivePriority` 表达层级，避免“层级覆盖”和“规则优先级”两套语义打架：

| 来源 | 优先级区间 | 说明 |
|---|---|---|
| 内置语义规则 | 0–199 | 对象/属性能力到视角的默认映射 |
| 行业规则包 | 200–399 | 交通、设备、财务等行业词表与组合 |
| 工作空间规则包 | 400–599 | 同义词、隐藏视角、默认显示、标题 |
| 请求级覆盖 | 600–799 | 用户本次显式选择（“我要看轨迹”） |
| 治理规则 | 800–999 | 权限/白名单/组件可用性，只读、不可编辑 |

同一目标冲突时：`effectivePriority` 高者胜；同优先级比较条件具体度（叶子条件数、精确匹配优先）；仍相同按规则 ID 字典序。集合型效果（加视角/选项）按规则顺序合并，移除效果在同等优先级下优先，避免“隐藏视角”被低优先级规则重新加回。

## 5. 规则 DSL

### 5.1 规则包结构

```yaml
id: onto.rules.default
name: 默认规则包
version: 1.0.0
ontologyProfile: ""
extends: null          # 仅允许单继承，禁止环
budget:
  maxViews: 6
rules:
  - id: intent.anomaly
    priority: 130
    when:
      all:
        - question.containsAny: [为什么, 异常, 根因, 原因, 告警]
    then:
      setGoal: anomaly_analysis
      addConfidence: 0.20
      reason: 问题包含异常/根因语义
```

### 5.2 条件谓词白名单

| 谓词 | 值 | 说明 |
|---|---|---|
| `question.containsAny` | `string[]` | 任一命中 |
| `question.containsAll` | `string[]` | 全部命中 |
| `question.excludes` | `string[]` | 命中则条件失败 |
| `question.regex` | 受限正则 + 最大长度 | 仅用于标识符；编译时限制回溯 |
| `object.nameIn` | `string[]` | 显式对象名 |
| `object.roleIn` | `string[]` | `aggregate_root/fact/dimension` |
| `object.hasProperty` | `string` | 属性存在 |
| `object.numericCountAtLeast` | `int` | 数值属性数量 |
| `object.hasTemporal` | `bool` | 时间属性 |
| `object.hasGeo` | `bool` | 经纬度证据 |
| `object.hasStatus` | `bool` | 状态/字典属性 |
| `object.hasDerivedBoolean` | `bool` | 派生布尔 |
| `property.semanticRole` | `id/label/time/numeric/status/geo` | 属性语义 |
| `capability.exists` | `perspective` | 能力存在 |
| `capability.evidenceIncludes` | `string` | 证据前缀 |
| `relation.reachable` | `{object, maxDepth}` | 关系可达 |
| `ontologyRule.forField` | `property` | 业务规则关联字段 |
| `action.roleIntersects` | `string[]` | 动作角色与当前角色有交集 |
| `role.in` | `string[]` | 当前角色 |
| `pack.id` / `pack.profile` | `string` | 工作空间 Pack |
| `data.rowCountAtLeast` | `int` | 可选的数据概况 |
| `goal.in` | `string[]` | 前序规则产生的 goal |

组合器仅 `all / any / not`，嵌套深度上限 5，单包规则数上限 500。

### 5.3 效果白名单

| 效果 | 参数 | 说明 |
|---|---|---|
| `setGoal` | `string` | 目标 |
| `setObject` | `string` | 显式对象 |
| `setFocus` | `{key, value}` | 聚焦标识 |
| `addView` | `{perspective, weight, title?, options?}` | 加候选视角 |
| `removeView` | `perspective` | 移除视角 |
| `boostView` | `{perspective, weight}` | 加权 |
| `setViewOptions` | `{perspective, values}` | 设置声明式选项 |
| `setTitle` | `{perspective, template}` | 标题模板 |
| `addConfidence` | `number` | 置信度调整，范围受限 |
| `addClarify` | `{kind, options, question}` | 歧义候选 |
| `annotate` | `string` | 解释文本 |

明确禁止：`layout`、`style`、`css`、`script`、`url`、`sql`、`code`、任意函数调用、任意属性写入。

### 5.4 模板变量

标题/选项只允许有限变量：

```text
{object.name} {object.displayName}
{property.<role>.0.name} {property.<role>.0.displayName}
{focus.value} {pack.name}
```

变量解析失败时保留字面量并写 warning，不抛错阻断生成。

### 5.5 安全校验

发布前必须通过：

- YAML/JSON Schema 校验；
- 谓词/效果白名单；
- 规则 ID 唯一、优先级范围、继承无环；
- 引用的对象/属性/角色/视角存在（允许通配谓词，但显式名称必须可解析）；
- 正则长度与复杂度限制；
- 包大小、规则数、嵌套深度限制；
- dry-run 不产生异常、不超时；
- 规则包 hash 写入生成 artifacts。

## 6. 执行流程

### 6.1 求值

1. 从已发布本体构建 `UiFacts`；同一 `ontologyVersion + rulePackHash` 可缓存。
2. 应用请求级显式选择（对象/目标/视角），形成初始 `IntentDraft`。
3. 按 `effectivePriority` 从高到低求值规则；每条规则记录 `matched` 或 `skipped` 及原因。
4. 合并标量效果（goal/object/focus）与集合效果（view/options）。
5. 做能力交集、Pack 白名单、权限、组件可用性过滤，记录 `pruned`。
6. 视角评分、预算裁剪、稳定排序。
7. 生成 `Blueprint` 与 `ruleTrace`。
8. 交给现有 adjudicator 编译；若裁决失败，错误信息回填 trace，不静默重试。

不做无限前向链：最多两轮——第一轮产出 `goal/object/focus`，第二轮让依赖 `goal` 的视图规则生效。避免规则互相触发导致不可预测。

### 6.2 意图评分

不使用浮点概率作为承诺，只用可解释权重：

```text
support  = Σ 命中规则权重
conflict = Σ 冲突规则权重（excludes/remove/否定）
coverage = 命中信号数 / 期望信号数
score    = support - conflict
confidence = clamp(0.35, 0.95, 0.5 + 0.4 * (support - conflict) / max(support + conflict, 1))
```

对象/目标 top1 与 top2 的分差小于阈值（例如 15%）时，不静默选择，返回 `clarify`：

```json
{
  "kind": "goal",
  "question": "你想看哪类视图？",
  "options": [
    {"goal": "trace", "label": "轨迹追溯", "evidence": ["问题含“最近”，对象有时间属性"]},
    {"goal": "anomaly_analysis", "label": "异常分析", "evidence": ["对象有业务规则"]}
  ]
}
```

### 6.3 视图选择

每个候选视角初始分：

| 来源 | 权重 |
|---|---|
| 目标基线（如 anomaly 默认包含 rule/state） | 40 |
| 能力证据（对象自有属性/关系/规则/动作） | 20 |
| 问题显式线索（“地图/轨迹/趋势/对比”） | 20 |
| Pack 预设 | 15 |
| 行业/工作空间规则 | 0–30 |

规则引擎不直接决定最终列表，而是输出带分数的候选；规划器执行：

- 去重；
- 能力交集；
- 预算裁剪（保底 `identity`）；
- 至少一个视图，否则返回可解释失败；
- 稳定排序。

### 6.4 选项派生

选项只从本体属性和 Pack 预设派生，规则只能“选择/覆盖”，不能写任意结构：

| 视角 | 选项来源 |
|---|---|
| `statistics` | `numericKeys[0]` → metric；`statusKey`/`labelKey` → groupBy；Pack → chartType/limit |
| `location` | Pack → display/basemap/layers；`numericKeys` → heightField/colorField；`statusKey` → colorField |
| `table` | `labelKey/timeKey/numericKeys/statusKey` → columns；`timeKey` → sort；Pack → limit |
| `timeline` | `timeKey` → 时间轴；`statusKey` → 分组；Pack → limit |
| `state` | `statusKey` / 派生布尔 → 状态卡 |
| `identity` | `labelKey/idKey` + 前几个属性 |
| `rule` | 业务规则 `relatedFields` + `businessCause` |
| `action` | 动作参数与角色 |

最终仍由 `normalize_view_options` + 引擎 Schema 二次校验。

### 6.5 数据可用性（可选增强）

Schema 存在不等于有数据。第二阶段可加入轻量 `dataProfile`：

- 每个对象：行数、属性空值率、基数、时间覆盖、地理覆盖；
- 由平台数据用例按快照计算并缓存；
- 只对 top 候选视角做 probe，限制并发与超时；
- 无数据视角降权或标记 `empty`，但不伪造数据；
- 数据不可用时退回 Schema 规则，不阻塞生成。

MVP 不做逐视角查询，避免规则引擎变成数据访问层。

## 7. 本体对象/属性 → 视图映射

这是“规则结合本体”的核心表。内置默认规则包只使用语义谓词，不硬编码业务对象名。

| 本体信号 | 默认视角 | 说明/选项 |
|---|---|---|
| 任意已发布对象 | `identity` | 对象画像；`labelKey/idKey` + 属性 |
| `object.role=dimension` | 先解析查询根 | 维度对象不能直接查询；规则写 `resolveQueryRoot` 事实 |
| 存在关系（一度） | `relationship` | 关系图；按关系名称与基数 |
| `property.semanticRole=time` | `timeline` | 时间线；优先 `timeKey` |
| `property.semanticRole=numeric` ≥ 1 | `statistics` | 图表；metric/groupBy 来自语义键 |
| 经纬度证据（自身或一度关联） | `location` | 默认 `GeoMap`；Pack 可切 `gis/scene25d/twin3d` |
| `property.semanticRole=status` 或字典域 | `state` | 状态面板；状态色/分组 |
| 派生布尔属性 | `state` | 告警/异常状态 |
| 本体存在相关 `Rule` | `rule` | 业务解释；`relatedFields` 必须与对象/属性相交 |
| 存在启用动作且角色有交集 | `action` | 处置动作；无权限则只记录原因，不展示 |
| 多个数值属性 | `statistics` 加权 | 支持对比/趋势选项 |
| `goal=anomaly_analysis` | 全能力交集，优先 `rule/state` | 但受预算限制 |
| `goal=trace` | `identity/relationship/timeline/location` | 轨迹追溯 |
| `goal=trend` | `timeline/statistics/state` | 趋势 |
| `goal=operation` | `identity/state/rule/action` | 处置 |

对象/属性语义的优先级：

1. 显式声明（本体 `description`/字典/类型）；
2. 平台语义键推断（`_semantic_keys`）；
3. 命名启发式（`_time/_at/_date`、`status/state`、`lon/lat`）；
4. 数据采样（仅在数据概况阶段使用）。

## 8. 示例

### 8.1 本体片段

```yaml
objects:
  Device:
    role: fact
    properties:
      device_id: {type: string}
      name: {type: string}
      status: {type: string, dictDomain: device_status}
      temperature: {type: number}
      last_seen: {type: datetime}
      longitude: {type: number}
      latitude: {type: number}
    relations:
      located_at: {target: Site, cardinality: many-to-one}
rules:
  - name: DeviceOverheat
    relatedFields: [temperature]
    businessCause: 制冷失效
actions:
  - actionId: dispatch_repair
    primaryObjectType: Device
    roles: [operator]
```

### 8.2 问题与规则命中

问题：`最近温度异常的设备在哪里？`

| 规则 | 命中依据 | 效果 |
|---|---|---|
| `intent.anomaly` | 含“异常” | goal=anomaly_analysis |
| `intent.location` | 含“哪里/位置” | 加 location 权重 |
| `object.mention.Device` | 显示名“设备” | object=Device |
| `view.timeline.from_temporal` | `last_seen` 为 time | timeline |
| `view.statistics.from_numeric` | `temperature` 数值 | statistics |
| `view.state.from_status` | `status` 状态 | state |
| `view.rule.from_ontology` | `DeviceOverheat.relatedFields` 相交 | rule |
| `view.action.from_role` | `operator` 命中动作角色 | action |
| `option.statistics.metric` | 首个数值属性 | metric=temperature |

角色为 `viewer` 时：`action` 在治理过滤阶段被剪除，trace 记录 `role viewer not in [operator]`，不报错。

### 8.3 输出与解释

```json
{
  "context": {"object": "Device", "goal": "anomaly_analysis"},
  "views": [
    {"perspective": "identity", "score": 60},
    {"perspective": "location", "score": 55},
    {"perspective": "statistics", "score": 52},
    {"perspective": "state", "score": 48},
    {"perspective": "rule", "score": 46},
    {"perspective": "timeline", "score": 40}
  ],
  "ruleTrace": {
    "matched": [
      {"ruleId": "intent.anomaly", "reason": "问题含异常语义"},
      {"ruleId": "view.rule.from_ontology", "reason": "DeviceOverheat 关联 temperature"}
    ],
    "pruned": [
      {"perspective": "action", "reason": "角色 viewer 不在动作角色 [operator] 中"}
    ]
  }
}
```

## 9. 规则包治理

### 9.1 存储

新增通用仓储记录，不改本体/数据文档：

```text
kind: smartui_rule_pack
{
  id, workspace_id, name, version, status: draft|published|archived,
  ontology_profile, extends, rules[], hash, created_by, created_at,
  published_at, published_by
}
```

- 一个工作空间同一时间只有一个 `published` 默认规则包；
- 生成时把 `rulePackId + hash` 写入 generation 行和 artifacts；
- Pack 只引用 `rulePackId`，不内嵌规则代码；
- 发布不可变，修改 = 新版本；回滚 = 重新绑定旧版本。

### 9.2 校验与试跑

- `POST /rule-packs/{id}/validate`：Schema、白名单、引用、正则、深度、大小；
- `POST /rule-packs/{id}/dry-run`：传问题/角色/聚焦对象，返回 blueprint + trace，不写 generation；
- 试跑必须覆盖内置 golden 问题集，展示与当前默认包的差异；
- 发布前要求至少一次成功 dry-run 和差异审阅。

### 9.3 审计

- `smart_ui.rule_pack.create / update / validate / publish / rollback` 审计事件；
- 生成行保存 `rulePackHash`，历史可复现；
- 规则包与生成记录都可导出 JSON，便于离线评测。

## 10. API 与数据模型

### 10.1 生成请求

```json
POST /api/workspaces/{wid}/smart-ui/generations
{
  "question": "最近温度异常的设备在哪里？",
  "roles": ["operator"],
  "mode": "rule",
  "rulePackId": "onto.rules.default",
  "focus": {"object": "Device", "key": "device_id", "value": "DEV-001"}
}
```

`mode` 建议扩展为 `auto | llm | rule | deterministic`：

- `rule`：强制规则生成，要求规则包可用；
- `auto`：有可用模型按配置选择 LLM，否则 rule；用户可在 UI 强制 rule；
- `deterministic`：保留现有语义，作为无规则包时的最小安全网；
- `llm`：保持现有行为。

### 10.2 生成结果

在现有 generation 响应上增加：

```json
{
  "mode": "rule",
  "rulePack": {"id": "...", "version": "1.0.0", "hash": "sha256:..."},
  "ruleTrace": {
    "factsSummary": {"object": "Device", "capabilities": ["identity", "timeline"]},
    "matched": [],
    "skipped": [],
    "pruned": [],
    "candidates": []
  },
  "clarify": null
}
```

`validation.checks` 增加：

- `规则包已加载/校验通过`；
- `对象/目标识别`；
- `能力交集`；
- `动作权限`；
- `视图预算`。

### 10.3 与现有接口的兼容

- 不新增必填请求字段；`rulePackId` 可省略，默认取工作空间绑定包；
- 不改变 `blueprint / program / artifacts` 结构；
- 不改变 preview/publish 接口；
- 旧 `mode=deterministic` 仍可调用。

## 11. UI/UX

### 11.1 生成台

- 模式标识：`规则生成 · 无模型调用`；
- 问题输入 + Pack 提供的示例问题 chips；
- 命中歧义时显示候选卡片，而不是直接生成默认视图；
- 右栏「裁决」增加规则轨迹：
  - 命中的规则与证据；
  - 被剪除的视角及原因；
  - 当前规则包版本/hash；
- 预览与发布流程不变。

### 11.2 结构化修改

无 LLM 时不要假装支持开放式多轮自然语言。用受控操作覆盖长尾：

- 加/减视角（来自能力列表）；
- 改目标（概览/画像/异常/轨迹/趋势/对比/处置）；
- 改聚焦对象/标识符；
- 改分组、指标、排序、条数；
- 改空间呈现（默认地图/GIS/2.5D/3D）。

这些操作直接映射为请求级规则（600–799），可解释、可复现。

### 11.3 规则管理（管理员）

- 规则包列表、版本、状态；
- YAML 编辑器 + Schema 校验 + 实时错误；
- dry-run 面板：问题 → 意图/视角/trace；
- 与默认包的 diff；
- 发布/回滚/归档；
- 权限：仅工作空间管理员可编辑，普通用户只读。

## 12. 兼容与迁移

1. **默认包复刻现状**：把 `detect_goal` 的判断顺序和 `GOAL_PERSPECTIVES` 表转成默认包规则，固定问题集输出必须一致。
2. **Feature flag**：`ONTO_SMART_UI_RULE_ENGINE=off|shadow|on`：
   - `off`：现有行为；
   - `shadow`：规则引擎并行计算并记录差异，不影响结果；
   - `on`：规则结果生效。
3. **分阶段替换**：先让 `deterministic` 生成器内部调用规则引擎；稳定后再新增 `rule` 模式。
4. **LLM 共存**：`rule` 作为 LLM 不可用时的默认路径；规则 trace 也可用于校验 LLM 提议（视角无能力证据时要求重试或剪除）。
5. **回滚**：规则包版本回滚 + flag 回滚，不改数据。

## 13. 测试与验收

### 13.1 测试层次

| 层 | 用例 |
|---|---|
| 规则单元 | 谓词真假、组合器、优先级、冲突、模板变量、白名单拒绝 |
| 事实单元 | 对象角色、属性语义、关系可达、规则/动作证据、维度→查询根 |
| 规划 golden | 各行业 Pack × 目标 × 角色 → 期望 object/goal/views/options |
| 兼容 | 默认包对现有固定问题集与 `detect_goal` 结果一致 |
| 负向 | 无能力、无视图、未知对象、无权限动作、非法规则、正则超限、空数据 |
| 确定性 | 同输入重复 100 次 `programHash` 一致；规则顺序打乱不改变结果（除显式优先级） |
| 性能 | 500 规则 × 100 对象 < 10ms（纯内存） |
| 安全 | 规则不能注入布局/脚本/URL；发布校验拒绝；权限过滤不可绕过 |
| 端到端 | 无模型配置下 HTTP 生成 → ready → preview payload 非空；规则 trace 可查 |

### 13.2 MVP 验收

- 无 LLM 配置时，输入四个行业 Pack 的 golden 问题均生成 ready 视图；
- 每个视图都能在 trace 中找到至少一条规则或能力证据；
- 不支持的视角/动作被剪除且有原因；
- 同一输入的 `programHash` 稳定；
- 现有后端、引擎、运行时、前端测试全部通过；
- 规则包发布/回滚有审计记录；
- 不引入远程请求、脚本执行或布局坐标。

## 14. 分阶段落地

| 阶段 | 工作 | 交付 | 预估 |
|---|---|---|---|
| P0 规则内核 | `RulePack/Rule/UiFacts/RuleEvaluator`；默认包复刻现状；`ruleTrace` | 引擎单测 + 兼容 golden；无 UI 变化 | 2–3 天 |
| P1 规则生成模式 | 平台 `rule` generator/mode；Pack 引用规则包；Studio 模式标识 + trace 面板 | 无模型端到端可预览 | 3–5 天 |
| P2 规则运营 | 规则包仓储/校验/dry-run/发布/回滚；歧义澄清；结构化修改 | 管理员可配置；行业包 | 3–5 天 |
| P3 数据感知与混合 | 轻量 dataProfile、空视图降权；规则校验 LLM 提议；A/B | 质量提升与灰度 | 3–5 天 |

建议先做 P0 + P1，形成“无模型也能稳定出图”的闭环，再投入规则运营界面。

## 15. 风险与取舍

| 风险 | 影响 | 缓解 |
|---|---|---|
| 规则维护成本高 | 新意图要写规则 | 语义谓词 + 行业模板 + golden 回归 + dry-run |
| 覆盖不了长尾问法 | 用户觉得“不智能” | 明确规则生成定位；歧义澄清 + 结构化修改；不承诺开放域 |
| 规则冲突 | 结果不可预测 | 单一 effectivePriority；同优先级具体度/ID 决胜；冲突 trace |
| 过度硬编码对象名 | 换行业失效 | 基础包只用语义谓词；对象名只允许出现在行业/工作空间包 |
| 数据探测变慢 | 生成时延 | 快照缓存、top-N probe、超时降级；MVP 不做 |
| 规则注入 | 安全边界破坏 | 白名单 DSL、无 eval、无 URL/脚本/布局、发布校验、权限最后过滤 |
| 置信度误导 | 用户过度信任 | 置信度只用于排序；界面展示证据与规则来源；不显示伪概率 |
| 与 LLM 路径分叉 | 双份逻辑漂移 | 共用 Blueprint/裁决/编译；规则 trace 也用于 LLM 校验 |

## 16. 待确认决策

1. 规则包归属：工作空间管理员可编辑，还是平台运营统一维护？（建议：基础包平台维护，行业/工作空间包管理员维护）
2. `auto` 模式有模型时是否优先 LLM？（建议：是；用户可强制 `rule`）
3. 默认最大视角数 6 还是 8？（建议 6，Pack 可调）
4. 是否在 MVP 引入 dataProfile？（建议 P3，先保证 Schema 驱动）
5. 规则 DSL 用 YAML 编辑、JSON 存储？（建议 YAML 编辑，JSON 为规范存储）
6. 是否需要规则包签名/导入导出？（建议 P2 支持导入导出，签名后置）

## 17. 代码落点

| 位置 | 改动 |
|---|---|
| `engine/src/onto_ui_engine/rules.py`（新增） | RulePack、条件求值、效果合并、trace |
| `engine/src/onto_ui_engine/facts.py`（新增） | `UiFacts` 构建、属性语义、关系可达、数据概况接口 |
| `engine/src/onto_ui_engine/planner.py` | `detect_goal` 改为默认包包装；`plan_application` 接受 `rule_pack` |
| `engine/src/onto_ui_engine/models.py` | Intent/PlanResult 增加 trace/candidates（向后兼容） |
| `platform/application/smartui/rules.py`（新增） | 规则包仓储、校验、dry-run、发布 |
| `platform/application/smartui/generators.py` | 新增 `RuleGenerator`，或让 deterministic 调用规则引擎 |
| `platform/application/smartui/studio.py` | `mode=rule` 分支；trace 写入 generation |
| `platform/application/smartui/packs.py` | Pack 增加 `rulePack` 引用与预算字段 |
| `platform/application/smartui/bridge.py` | `compile_smart_ui` 接受规则包并传给 planner |
| `frontend/src/views/SmartUIStudio.tsx` | 模式标识、trace 面板、澄清卡片、结构化修改 |
| `docs/design/llm-smart-ui/` | 本文档 + 架构图更新 |

> 原则：规则引擎与平台解耦，纯函数、无 I/O；规则包存储/权限/审计在平台层；编译与预览完全不改。

## 18. 实施状态（P0 + P1 + P2 已落地）

> 2026-11-03。以下为已实现并验证的落点，设计 §1–§17 仍为规范来源；未列出的 P3 项（dataProfile、规则校验 LLM 提案、交互式规则编辑器）保持未做。

### 18.1 已完成

| 阶段 | 交付 | 代码 |
|---|---|---|
| P0 规则内核 | `UiFacts` 事实模型；白名单 DSL；确定性求值；默认包复刻 `detect_goal`/`GOAL_PERSPECTIVES`；`pack_hash` | `engine/src/onto_ui_engine/facts.py`、`rules.py`、`planner.py`（`DEFAULT_RULE_PACK`、`plan_application(rule_pack=)`） |
| P1 生成模式 | `rule` generator + `mode=rule`；生成记录保存 `rule_pack_id/hash`、`rule_trace`、`clarify`；Studio 模式选择、规则包下拉、决策轨迹面板、hash 展示；澄清选项可回填问题 | `smartui/generators.py`、`studio.py`、`frontend/src/views/SmartUIStudio.tsx`、`frontend/src/api.ts`、`frontend/src/styles.css` |
| P2 规则运营 | 规则包列表/详情/创建/更新/校验/dry-run/克隆/发布/回滚/归档/绑定；owner 才能发布；published 不可原地改；同工作空间唯一 published；审计事件；HTTP 路由 | `smartui/rules.py`、`smartui/packs.py`、`interfaces/api.py` |
| 工作空间绑定 | `WorkspacePack.rulePack` 与独立 `rule_pack_id` 配置；解析顺序请求 override → pack → 工作空间 → 内置默认；改 view pack 不覆盖规则绑定 | `smartui/packs.py`、`studio.py` |
| 兼容 | 默认包对固定问题集保持原 goal/视角/顺序；`auto`/`llm`/`deterministic` 行为不变；异常分析 8 视角 + `maxViews=12` 预算兼容 | `engine/tests/test_rules.py`、`tests/test_smart_ui.py`、`tests/test_smart_ui_studio.py` |
| 文档 | 本文档；`docs/smart-ui-llm-generation.md` §15.5 | — |

### 18.2 验证结果

- 后端全量：**500 passed**（含 `tests/test_smart_ui_rules.py` 3 项：规则模式生成、规则包治理、HTTP 契约）。
- 引擎：**38 passed**（含 7 项规则测试：默认包兼容、事实纯函数、哈希稳定、效果合并、非法 DSL、预算裁剪、对象覆盖）。
- 运行时：**19 passed**；前端：**46 passed**；`pnpm build`（`tsc --noEmit` + Vite）通过。
- 无 `git commit`。

### 18.3 与设计的已知差异

1. 规则包编辑目前是 API/JSON 载荷，未做独立“规则管理页 + YAML 编辑器”；Studio 只提供生成时选择与 dry-run API。
2. `auto` 模式下有模型时仍优先 LLM；`rule` 需显式选择，不做自动“模型不可用就切规则”（模型不可用仍走 deterministic 回退）。
3. 规则 DSL 的 `setViewOptions` 只透传已受控 `options` 白名单，不开放布局/坐标/样式键。
4. 规则包导入/导出、签名未做；审计已覆盖创建/更新/发布/回滚/归档/绑定。
5. `smart_ui_meta` 未内嵌完整规则包列表；Studio 通过 `GET /smart-ui/rule-packs` 单独加载。
