# 智能 UI 完整度评估与优化方案

> 评估日期：2026-11-02
> 评估基线：当前工作区代码；`pytest tests/test_smart_ui.py tests/test_smart_ui_studio.py` 45 passed；全量 pytest 471 passed。
> 评估视角：不是“代码有没有”，而是“一个业务用户能不能从配置模型开始，生成视图、反复修改、发布并在应用里日常使用”。

---

## 0. 结论

**当前实现是“可演示的生成原型 + 完整度较高的执行内核”，还不是可日常交付的 AI 视图产品。**

- **执行侧完整度高**：本体适配、L3/L4、真实数据、角色与动作权限、iframe 隔离、动作回写、审计都有；
- **生成侧主链路可用**：输入描述 → 异步任务 → 模型提议 → 平台裁决 → L4 编译 → 预览 → 版本 → 草稿，已能跑通真实模型；
- **但产品闭环有三个硬缺口**：
  1. **发布到应用后打不开**：`ui_artifact` 只写不读，应用页没有 AI 视图入口；
  2. **模型状态不可信**：Studio 显示的是平台全局模型状态，不是工作空间配置；没有真实连通性预检；
  3. **生成能力和交互深度不足**：只能从既有语义视角里做选择，不能筛选、分组、排序、表格化，也不能指定具体对象实例；
- 因此当前适合“单对象、默认焦点、只读看板”的内测场景；不适合“多对象、指定实例、可操作、可发布”的业务场景。

从实际使用角度给出一个粗略完整度：

| 维度 | 完整度 | 判断 |
|---|---:|---|
| 模型配置与真实调用 | 70% | 调用已修通；状态展示、错误分类、预检、成本缺失 |
| 生成主链路 | 75% | 异步、裁决、回退、编译可用；取消、并发、重启恢复缺失 |
| 表达力（LLM 能设计什么） | 35% | 仅视角子集 + 动作子集 + 标题；无筛选/分组/排序/表格 |
| 数据交互 | 35% | 单焦点 + 聚合；无实体选择、无列表/表格/搜索/分页 |
| 迭代与版本 | 50% | 有版本链；无 diff、无修改说明展示、无对比 |
| 资产化与发布 | 20% | 有 artifact 元数据；应用侧不可渲染，发布链路是死路 |
| 历史与治理 | 40% | 有列表与审计；无删除、归档、检索、保留策略 |
| 质量评测 | 15% | 有单测；无 golden prompt、采纳率、模型对比 |
| 安全隔离 | 85% | 裁决、白名单、沙箱、权限边界设计较好 |
| 可观测 | 45% | 有步骤/指纹/耗时；无 token、费用、真实使用指标 |

---

## 1. 实际使用路径复盘

### 1.1 已跑通的路径

1. 在设置中保存工作空间模型；真实模型可被工作空间配置调用（已实测 `mode=llm`、`trace.model=deepseek-v4-flash`）。
2. AI视图 → AI 生成台输入描述 → 任务异步执行 → 生成过程五步可见 → 预览 iframe 渲染。
3. 模型不可达时自动走确定性回退，右侧显示回退原因。
4. 生成结果可继续修改，生成新版本；可保存草稿。
5. 确定性「智能视图」与 AI「AI视图」菜单互相独立，互不影响。

### 1.2 实际会卡住的路径

| # | 用户动作 | 实际结果 | 根因 |
|---|---|---|---|
| U1 | 配好模型后看 Studio | 可能显示“未配置”或“平台默认模型” | Studio 使用全局 health，不是工作空间配置 |
| U2 | 点“检查模型配置” | 只检查有没有保存，不发真实请求 | 没有连通性预检 |
| U3 | 模型 404/401 | 只显示“模型不可用（HTTPError）”，不知道为什么 | 错误被压成异常类型名 |
| U4 | 生成中想取消 | 没有取消入口 | 没有 cancel/delete 接口 |
| U5 | 服务重启时正在生成 | 记录可能永远停在“生成中” | 后台线程无恢复机制 |
| U6 | 模型输出非法但端点正常 | 生成直接 failed，不自动回退 | 只有传输失败才 fallback |
| U7 | 想“只看某辆车/某站点” | 预览仍显示默认焦点 | 没有实体选择；blueprint.focus 只进 intent，不参与 bundle |
| U8 | 想“按站点分组、最近 7 天、表格” | 无法表达 | 蓝图 Schema 没有 filters/groupBy/sort/limit/table |
| U9 | 修改后看差异 | 只能看新预览，没有 diff、没有修改说明 | `instruction` 存了但前端不显示 |
| U10 | 发布到应用 | 创建了一个 0 视图/0 查询的空应用 | artifact 只写不读；应用页没有 AI 视图 tab |
| U11 | 去应用里打开生成的 UI | 打不开 | 同上 |
| U12 | 想删除/归档生成记录 | 没有入口 | 没有 delete/retention |

---

## 2. 关键问题与证据

### P0-1 发布到应用是死路（最严重）

**现象**：AI 生成台发布后，应用详情里仍然只有“视图 / 查询 / 智能体 / 能力 / 目标”页签；生成的 UI 没有入口。

**证据**：

- `publish_generation` 只把元数据写进 `ui_artifact`：`src/onto_platform/interfaces/smart_ui_studio.py:875-884`
- 应用模型支持 `ui_artifact`：`src/onto_platform/domain/application/model.py:126`
- 但前端应用页只有常规页签，没有 artifact 渲染：`frontend/src/views/Application.tsx:282-283`、`:400-402`
- 全前端只有 3 处 `ui_artifact` 引用，都是 Studio 文案和类型定义：`frontend/src/api.ts:307`、`frontend/src/views/SmartUIStudio.tsx:268`、`:675`
- 实测已发布应用：

```text
86eb52f991aa 收费站运营视图（生成） views 0 queries 0 artifact {...}
d4bf0762ad1c 收费站值班总览       views 0 queries 0 artifact {...}
```

**进一步后果**：

- 新建的应用没有 views/queries，而应用发布校验要求至少一个视图或查询：`src/onto_platform/application/application_usecases.py:452-453`，所以这个应用本身也无法发布。
- 后端 `publish_generation` 支持 `application_id` 附加到已有应用，但前端永远新建应用，没有选择器：`frontend/src/views/SmartUIStudio.tsx:260-279`。
- artifact 只有 `generation_id/draft_id/version/program_hash`，没有 `blueprint/program/ontologyVersion` 快照；即便应用页想渲染，也只能依赖那条 generation 记录继续存在。

**影响**：用户以为“发布成应用”了，实际得到一个空应用；生成资产不能进入真实业务入口。这是当前最大的完整性缺口。

### P0-2 Studio 的模型状态不是工作空间真实状态

**现象**：Studio 右上角“模型 · …”和“生成会回退”的提示，取决于平台全局模型，而不是当前工作空间保存的模型。

**证据**：

- 控制台全局状态：`setLlmReady(!!h.llm_configured)`，来源是 health：`frontend/src/store.tsx:200`
- Studio 用这个全局值决定标签：`frontend/src/views/SmartUIStudio.tsx:125-129`
- health 的 `llm_configured` 来自平台默认 provider：`src/onto_platform/interfaces/api.py:438-456`
- `/smart-ui/meta` 只返回 `studio.enabled`，没有工作空间模型状态：`src/onto_platform/interfaces/api.py:578-581`
- “检查模型配置”只读取配置是否存在：`frontend/src/LlmPicker.tsx:128-130`

**影响**：

- 工作空间已配置、平台未配置时：Studio 显示“未配置”，用户被误导；
- 工作空间未配置、平台已配置时：Studio 显示“平台默认模型”，用户不知道本次会走确定性回退；
- 真实失败（404/401/超时）只能等生成结束才知道。

### P0-3 生成任务没有可控生命周期

**现象**：生成中只能等；不能取消；服务重启后任务可能永久卡住；并发没有上限。

**证据**：

- 每次生成直接起 daemon thread：`src/onto_platform/interfaces/smart_ui_studio.py:531-538`
- 没有 `DELETE /generations`、没有取消状态：`src/onto_platform/interfaces/api.py:582-598`；实施记录也明确“取消接口未做”：`docs/smart-ui-llm-generation.md:371`
- 没有启动恢复：代码库没有对 `ui_generation` 的 `running` 记录做启动扫描（搜索结果只有生成过程本身）
- 没有并发/队列/预算：每次请求都可以起线程；`MAX_PROMPT_BYTES` 只记录、不强制
- 模型 provider 超时 120s，期间没有 elapsed timer、没有 cancel

**影响**：用户无法止损；服务重启后 UI 会一直轮询“生成中”；多用户或多标签页可能同时打爆模型端点。

### P1-1 非法模型输出不会回退

**现象**：模型端点正常但返回的 JSON 不合法、字段不存在、视角不合法时，生成直接失败；不会像传输失败那样回退到确定性视图。

**证据**：

- 传输异常时 `except` 后调用 `_fallback`：`src/onto_platform/interfaces/smart_ui_studio.py:655-665`
- 裁决/编译异常直接 `row["status"]="failed"`：`:672-673`
- 方案目标原本写的是“LLM 不可用/超时/校验失败时，用户仍能拿到确定性视图”：`docs/smart-ui-llm-generation.md:62`
- 实施记录也承认状态枚举/回退只覆盖传输失败：`docs/smart-ui-llm-generation.md:371-373`

**影响**：用户明明有确定性引擎可兜底，却得到一个失败页；尤其换模型/换提示词时体验差。

### P1-2 表达力仍停留在“选视角”，不是“设计界面”

**证据**：

- 提示词约束只要求输出 `context/views[].perspective/title/actions/confidence`：`src/onto_platform/interfaces/smart_ui_studio.py:230-257`
- 落库 blueprint 也只有这些字段：`:412-428`
- M2 的 `filters/groupBy/sort/limit/chartType` 和 `FilterBar/DataTable` 未交付：`docs/smart-ui-llm-generation.md:372`、`:416-420`
- 运行时组件白名单没有表格/筛选器：`vibe-可视化engine/runtime-web/src/library.tsx:8-18`

**影响**：

- “只看拥堵站点”“按站点分组”“最近 7 天”“用表格展示”“按金额排序”等真实业务需求无法表达；
- 模型只能在 8 个视角里做删减，结果常常像“确定性视图的另一个子集”，用户感知不到“AI 设计了界面”。

### P1-3 没有实体选择，焦点是平台默认值

**证据**：

- 焦点由 `_select_focus` 按打分/时间自动选择：`src/onto_platform/interfaces/smart_ui.py:650-714`
- 确定性编译接口只接收 `question/roles`：`src/onto_platform/interfaces/api.py:72`
- 生成任务只接收 question/roles/mode，没有 focus 参数：`src/onto_platform/interfaces/smart_ui_studio.py:473-488`
- `compile_smart_ui_blueprint` 虽然从 blueprint 读出 focus，但只用于 `Intent`/审计：`:2034-2043`；随后 `build_bundle(... context=context)` 仍用默认焦点：`:2084-2087`
- 生成上下文包只给出默认焦点，不提供实例列表：`:202-220`

**影响**：

- 多实例工作空间里，用户不能选择“鲁SX0579”“某站点”；
- 模型即使写了 `focus`，预览和动作目标也不会跟着变；
- 业务用户会觉得“生成的视图不是我要看的那条记录”。

### P1-4 迭代缺少差异与说明

**证据**：

- refine 会创建新版本并保留父版本：`src/onto_platform/interfaces/smart_ui_studio.py:781-791`
- `instruction` 已落库：`:504`，前端类型也有：`frontend/src/api.ts:197`
- 但 Studio 详情/历史没有展示 `instruction`；全前端搜索只在该类型和 API 调用中出现
- 没有 blueprint diff、没有前后预览对比、没有版本树；历史只有“迭代”一行：`frontend/src/views/SmartUIStudio.tsx:630-653`

**影响**：多轮修改后，用户无法回答“这次到底改了什么”“上一版为什么被替换”，也难以回滚到正确版本。

### P1-5 数据形态仍是“单焦点看板”，不是“业务列表/工作台”

**证据**：

- bundle 的 identity/statistics/timeline 都以一个 `focus_record` 为中心：`src/onto_platform/interfaces/smart_ui.py:1308-1335`
- `view_payloads` 的多数视角只返回单行/聚合：`:1681-1697`
- 没有 DataTable/FilterBar 组件：`runtime-web/src/library.tsx:8-18`
- 没有分页、搜索、列配置、导出

**影响**：适合“看一条记录”，不适合“管理一批记录”；而真实业务 UI 很大一部分是列表、筛选、批量操作。

### P2 治理与可观测缺口

| 缺口 | 证据 | 影响 |
|---|---|---|
| 没有 token/费用 | `LLMResponse` 只有 `content`：`src/onto_platform/ports/llm.py:19-22` | 无法做预算、成本、模型对比 |
| 没有删除/归档/保留策略 | 无 DELETE 路由；`docs/...:394` 明确未实现 | 问题和生成内容永久保留，含潜在敏感信息 |
| 历史无搜索/分页/服务端过滤 | 最多 100 条：`smart_ui_studio.py:749-755`；前端一次拉 100 | 长期使用后不可用 |
| 无真实评测 | 无 golden prompts、采纳率、成功率基线 | 换模型/改提示词无法判断好坏 |
| 错误信息不分类 | 只记录异常类型；`fallbackReason` 是 `"模型不可用（HTTPError）"` | 404/401/超时/限流无法区分 |
| 裁决动作与实际程序可能不一致 | 裁决使用 actor 可执行动作 `:382-393`；编译器按请求角色再过滤（engine `compiler.py:100`） | 面板写“保留”，实际程序里被静默移除 |
| 显式 deterministic 显示成 fallback | `_fallback` 统一写 `mode="fallback"` | 用户选择确定性模式，却看到“回退” |
| 失败响应原文不留存 | 执行记录只留 `responseBytes`，解析失败时原始响应丢弃 | 模型输出问题难以复盘 |

---

## 3. 优化方案

建议按“先补闭环，再补表达力，最后补治理”的顺序做，不要先做自由拖拽编辑器。

### 阶段 A：P0 最小可用闭环（建议 3–5 人日）

目标：**发布能用、模型状态可信、失败可止损**。

#### A1. 打通“发布 → 应用 → 打开”闭环

设计建议：

1. `ui_artifact` 落库时至少保存：
   - `blueprint`
   - `program`（或 `compiled_program`）
   - `program_hash`
   - `engine_version` / `ontology_version`
   - `roles`、`question`、`focus`
2. 应用详情新增「AI 视图」页签：
   - 若 artifact 有可渲染内容，嵌入 `/smart-ui/?workspace=...&artifact=...` 或复用 generation view；
   - 没有 artifact 时显示空态和“去 AI视图生成”。
3. 增加 artifact 只读接口，例如 `GET /api/workspaces/{wid}/applications/{app_id}/ui-artifact`，从应用记录读取，不依赖 generation 是否还存在。
4. 发布弹窗增加“新建应用 / 附加到已有草稿应用”选择；后端已有 `application_id` 参数，可直接复用。
5. 应用发布校验允许“有 ui_artifact”的应用通过，或要求附加到已有 views/queries 的应用（推荐后者，语义更稳）。

验收标准：

- AI视图发布 → 应用列表出现应用 → 应用详情「AI 视图」能真实渲染；
- 刷新页面、服务重启后仍能打开；
- 生成记录被归档/清理后，已发布应用仍可打开（artifact 自包含）；
- 应用本身可以发布。

#### A2. 让 Studio 只展示“工作空间真实模型状态”

设计建议：

1. `/smart-ui/meta` 增加脱敏模型状态：

```json
{
  "model": {
    "provider": "openai",
    "model": "deepseek-v4-flash",
    "configured": true,
    "hasKey": true,
    "baseUrlHost": "127.0.0.1:8000",
    "source": "workspace"
  }
}
```

2. Studio 用该字段决定标签，不再用全局 `llmReady`；未配置时直接提示“将走确定性回退”，并给“去设置”入口。
3. 新增真实连通性预检：`POST /api/workspaces/{wid}/llm-config/check`，用最小 prompt 真实调用，返回 `ok/latency/status/errorType`；设置页“检查模型配置”调用它。
4. provider 抛错保留 HTTP 状态和响应摘要，错误分类为 `auth / not_found / timeout / rate_limit / bad_response`。

验收标准：

- 工作空间有配置、平台无配置时，Studio 显示工作空间模型；
- 工作空间无配置时，Studio 明确提示回退；
- 404/401/超时在预检和生成结果里显示可操作的错误类型，而不是 `HTTPError`。

#### A3. 生成任务可控

设计建议：

1. 用有界执行器替代裸 daemon thread：每工作空间最多 1–2 个运行中任务，其余 `queued`。
2. 增加 `DELETE /api/workspaces/{wid}/smart-ui/generations/{gid}`：
   - `queued`：直接标记 `cancelled`；
   - `running`：设置取消标记，模型返回后不再写结果；状态 `cancelled`。
3. 启动时扫描 `queued/running` 且超过阈值的记录，标记 `interrupted` 并写原因；前端不再无限轮询。
4. 生成页增加“取消”“重试”“用确定性视图打开”三个动作。
5. `auto` 模式下裁决/编译失败自动回退；`llm` 模式显示失败原因并提供“回退”按钮，尊重用户显式选择。

验收标准：

- 生成中可以取消，状态进入 `cancelled`；
- 重启服务后不存在永久 `running`；
- 模型输出非法时，`auto` 能拿到确定性回退结果；
- 同一工作空间并发不会无限起线程。

### 阶段 B：P1 表达力与迭代（建议 8–12 人日）

目标：让模型真的能“设计界面”，而不是“选视角”。

#### B1. Blueprint v1.1：受控表达力

在 `views[]` 增加白名单 options，而不是自由布局：

```json
{
  "perspective": "statistics",
  "title": "按站点异常金额",
  "options": {
    "chartType": "bar",
    "groupBy": "exit_station",
    "metric": {"field": "fee", "agg": "sum"},
    "filters": [{"field": "ts", "op": "gte", "value": "-7d"}],
    "sort": {"field": "value", "direction": "desc"},
    "limit": 8
  }
}
```

实现说明（2026-11-02）：`sort.field` 支持真实属性名；分组结果还支持 `value` / `count`。
`table` 不是新增视角，而是 `statistics` 视角的受控渲染形态（`chartType/display = "table"`），
由运行时 `DataTable` 组件承接；`FilterBar` 未做自由交互面板，改为 `DataTable` 内置
关键字搜索 + 列头排序，筛选/分组/排序/limit 由模型写进 `options` 并由服务端执行。

同时新增：

- `table` 视角 + `DataTable` 组件（列 = 本体属性，排序/分页/列宽）；
- `FilterBar` 组件（字段、操作符、值类型全部来自本体白名单）；
- 服务端生成 table 行数据，而不是单焦点行；
- 裁决器增加跨字段校验：字段存在、操作符与类型匹配、聚合合法、禁止脚本/表达式。

验收标准：

- “只看拥堵站点，按站点分组，最近 7 天，用表格展示”能生成对应筛选/分组/表格；
- 非法 options 被剪除并给出原因，不进入编译器；
- 性能：1 万行以内列表首屏可接受，提供分页/limit。

#### B2. 实体选择与焦点

1. `POST generations` / `smart-ui/compile` 增加可选 `focus: {object, key, value}`。
2. 后端校验实例存在，并把它作为 `_select_focus` 的显式输入；`build_bundle` 使用选中实例。
3. Studio 增加对象/实例选择器（可搜索），或允许从当前智能视图带入焦点。
4. 生成页展示“本次焦点：TollStation / 某某站”，动作执行目标使用同一焦点。

验收标准：

- 选择一辆车后生成画像，预览显示该车；
- 动作目标指向该车，而不是默认最新记录；
- 焦点写入 generation/artifact，可复现。

#### B3. 迭代体验

1. 生成详情显示“本次修改要求”（`instruction`），历史行也显示。
2. 增加 blueprint diff（上下文、视角、动作、标题），高亮新增/删除。
3. 支持版本树/父版本跳转、从旧版本继续、回滚草稿。
4. 草稿支持重命名、删除、复制；发布选择版本。

验收标准：

- 多轮修改后能看清改了什么；
- 能从旧版本继续或回滚；
- 历史详情包含修改说明和版本关系。

#### B4. 数据规模与交互

1. table/statistics 支持服务端 limit/分页/聚合；
2. 增加刷新按钮与可选自动刷新；
3. 大列表不一次性塞进 bundle；
4. 对超大工作空间给出明确上限与提示。

### 阶段 C：P2 治理、成本与评测（建议 5–8 人日）

1. **历史与保留**：删除/归档、按 actor/object/mode/status 过滤、分页、导出、保留策略。
2. **成本与 usage**：扩展 `LLMResponse` 携带 `usage`；记录 token/费用/模型；设置工作空间月度预算。
3. **评测**：golden prompts（每类业务问题 20–50 条），统计生成成功率、裁决剪除率、人工采纳率、发布后打开率；换模型/提示词前后对比。
4. **质量闸门**：模型 confidence 阈值、视角数量上限、重复视角、空动作、低置信度提示；对 `views` 过多/过少给出质量提示。
5. **一致性修复**：裁决面板按“编译后实际允许的动作”展示；`deterministic` 模式显示“确定性编译”而不是“回退”；失败时保存原始响应摘要。
6. **可观测**：生成耗时 P50/P95、失败分类、回退率、模型调用量；接入现有 metrics/audit。

---

## 4. 建议的路线图

| 阶段 | 内容 | 预计 | 完成标志 |
|---|---|---|---|
| A（P0） | 发布闭环 + 模型状态/预检 + 任务可控 | 3–5 人日 | 能从 AI视图发布并在应用里打开；模型状态真实；可取消/可恢复 |
| B1（P1） | Blueprint v1.1 + FilterBar/DataTable | 5–7 人日 | 能生成筛选/分组/表格视图 |
| B2（P1） | 实体选择 + 焦点贯通 | 2–3 人日 | 能指定实例生成和操作 |
| B3（P1） | diff/版本树/草稿管理 | 2–3 人日 | 多轮迭代可解释、可回滚 |
| C（P2） | 治理、成本、评测、一致性 | 5–8 人日 | 可运营、可度量、可审计 |

**建议顺序**：A1 → A2 → A3 → B1 → B2 → B3 → C。
不要先做自由拖拽/WYSIWYG；当前瓶颈不是“编辑器自由度”，而是“发布不可用、模型不可信、表达力不足”。

---

## 5. 不建议现在做的事

- 不做任意 HTML/CSS/JS 生成：现有裁决/白名单/沙箱是优势，不要放弃。
- 不做自由拖拽编辑器：在 table/filter/entity 这些基础能力缺失时，拖拽只会放大不可用。
- 不做跨工作空间 UI 市场：先把单工作空间发布闭环做扎实。

---

## 6. 验收清单（建议直接转成测试）

- [x] 工作空间模型配置后，`/smart-ui/meta` 返回真实模型状态；
- [x] 模型 404/401 时，预检和生成结果显示可操作错误类型；
- [x] 模型输出非法时，`auto` 模式自动回退；`llm` 模式可一键回退；
- [x] 生成可取消，服务重启后无永久 `running`；
- [x] 发布后应用详情可打开 AI 视图，刷新/重启/生成记录清理后仍可打开；
- [x] 应用可发布（artifact-only 或附加到已有视图）；
- [x] 指定实体生成后，预览与动作目标都是该实体；
- [x] “筛选 + 分组 + 排序 + 表格”提示词能生成对应视图；
- [x] 多轮修改可看到 instruction 和 blueprint diff；
- [x] 历史可检索/分页/归档；
- [x] 生成记录包含 token/费用/耗时统计（费用需配置费率，见 §7.5）。

---

## 7. 本轮实现状态（2026-11-02）

按 §4 的 A → B1 → B2 → B3 → C 顺序落地；测试结果见 §7.7，未提交 git。

### 7.1 阶段 A（P0）

| 工作项 | 状态 | 落地位置 / 证据 |
|---|---|---|
| A1 发布→应用→打开 | 完成 | `publish_generation` 写入自包含 `ui_artifact`（blueprint/program/question/roles/ontology_version/engine_version）；`GET .../applications/{id}/ui-artifact/view` 从 artifact 重编译；前端 `Application.tsx` 的 `AI视图` 页签 + `useSmartUiBridge`；测试 `test_smart_ui_studio.py::test_http_flow...` 断言应用视图可打开且 `artifact.generation_id` 一致 |
| A2 模型真实状态 | 完成 | `/smart-ui/meta.model` 返回 `{configured,source,provider,model,base_url_host,has_key,will_use_model}`；`POST /llm-config/check` 真实 10s 预检；`openai_compat.py` 将 401/404/429/超时/不可达/坏响应分类为 `auth/not_found/rate_limit/timeout/unreachable/bad_response`；测试 `test_llm_calls.py` 覆盖状态与错误分类 |
| A3 任务可控 | 完成 | 有界线程池 + 每工作空间并发 2 + 队列；`POST .../cancel`；15 分钟 stale 扫描标记 `interrupted`；`auto` 裁决失败自动确定性回退、`llm` 模式保留失败；前端取消/重试/回退入口；测试 `test_generation_can_be_cancelled*`、`test_cancelled_generation...`、`test_recover_stale...` |

### 7.2 B1（受控表达力 + table）

- Schema：`views[].options` 白名单 `chartType/display/groupBy/metric/sort/limit/columns/filters`；`metric.agg ∈ sum|avg|count|min|max`；未知字段/非法 op/超界 limit 被裁决器剪除并写 `validation.pruned`（`smart_ui_studio.py::_normalize_view_options`）。
- 引擎：`vibe-可视化engine` 的 schema/loader/compiler/emitter 同步支持 `options`；statistics 表形态输出 `DataTable`（`emitter.py`），运行时 `DataTable` 支持关键字搜索、列头排序、空结果提示（`runtime-web/src/components/table.tsx`）。
- 数据：`smart_ui.py::_table_payload` 在服务端执行 filters/groupBy/metric/sort/limit/columns，分组聚合返回 `columns=[groupBy,"__value"]` 与中文 `columnLabels`，`total` 为分组数；数值/布尔/`contains`/`in`/`-7d` 等相对时间比较已支持（`_table_match`）。
- 验收测试：`test_table_options_produce_data_table`、`test_unknown_table_options_are_pruned`、`test_table_filter_semantics_support_bool_and_relative_time`；引擎 `test_e2e_24_table_options_switch_component`、`test_e2e_25_unknown_option_is_rejected`。
- 偏差：交互式 `FilterBar` 未实现；以 `DataTable` 客户端搜索/排序替代，筛选条件由生成结果固定。这是当前 B1 的已知表达力边界。

### 7.3 B2（实体选择与焦点）

- `POST generations` / `compile` 接收可选 `focus: {object,key,value}`；`_focus_request` 兼容 `object/objectType`、`key/identityKey`、`value/focusValue`；`_select_focus` 显式输入优先；`build_bundle` 使用选中实例。
- `GET /smart-ui/entities?object=&q=&limit=` 返回可选实例（`smart_ui_focus_options`）；前端 Studio 增加对象/实例选择器，生成/重试/refine 透传焦点；generation/artifact 持久化焦点。
- 测试：`test_smart_ui.py` 焦点选择；`test_smart_ui_studio.py` 焦点透传与实体接口。

### 7.4 B3（迭代体验）

- 生成详情返回 `lineage`（祖先链）与 `diff`（对象/目标/焦点/视角/动作/标题/options 变化，`kind` + `before/after`）；历史行展示 instruction。
- 前端「相对上一版」卡片：版本链跳转、diff 新增/移除配色、无变化/首版文案；`refine` 继承父焦点并可显式覆盖。
- 草稿支持重命名（PUT）、删除（DELETE，published 拒绝）；发布 Drawer 保留。
- 测试：`test_generation_diff_and_lineage`、`test_draft_rename_and_delete`、HTTP 流中 draft rename/delete 断言。

### 7.5 阶段 C（治理、成本、评测）

- 历史：`GET /smart-ui/generations` 支持 `q/status/object/mode/offset/limit`，返回 `{items,total,offset,limit,all}`；`DELETE /smart-ui/generations/{gid}` 删除未发布生成并清理未发布草稿，运行中/已发布引用拒绝；可选保留策略 `ONTO_SMARTUI_HISTORY_RETENTION_DAYS`（默认关闭，不删已发布与运行中）。
- 成本/usage：`LLMResponse.usage` 进入 generation `usage/metrics`；`ONTO_SMARTUI_INPUT_COST_PER_1K` / `ONTO_SMARTUI_OUTPUT_COST_PER_1K` / `ONTO_SMARTUI_COST_CURRENCY` 配置后生成 `cost`；未配置返回 `null`，前端应显示“未配置费率”。
- 评测基线：`test_prompt_exposes_v11_options_to_the_model` 断言提示词暴露 `options` 词汇与用户问题；`test_usage_and_cost_are_recorded` 断言 usage/cost 落库。golden prompt 集与采纳率统计仍未接入。
- 一致性：`deterministic`/`fallback`/`llm` 模式在 API 与前端区分；失败保留 `validation.fallbackReason`/`steps`/`trace`，JSON 解析失败额外保留 `trace.rawPreview`（≤2000 字符）；动作裁决仍以确定性编译器允许集为准。
- 相关测试：`test_history_retention_prunes_old_unpublished`、`test_list_generations_searches_and_paginates`、`test_delete_generation_*`。

### 7.6 仍未完成 / 明确不承诺

1. 交互式 `FilterBar` 与用户自由改列/改聚合：当前只由 LLM 写入 `options`，客户端仅搜索/排序。
2. 完整 golden prompt 评测集、采纳率/发布打开率指标与换模型 A/B；目前只有提示词契约测试。
3. workspace 月度预算/硬闸门：只有 token/费用记录，无预算拒绝。
4. 历史导出、跨版本回滚草稿（可从旧版本继续生成，但没有“把旧版本设为当前草稿”按钮）。
5. 真实模型生成依赖工作空间或平台配置的 model id；本轮 live 验证时 `127.0.0.1:8000` 的模型 id 带部署后缀，工作空间原有 `deepseek-v4-flash` 已 404，说明部署层需要模型别名或配置同步（平台已把 404 归类为 `not_found` 并回退）。

### 7.7 验证命令与结果

- 后端：`python -m pytest -q` → **471 passed**（38.78s）。
- 引擎：`cd F:\piAgent\vibe-可视化engine\engine; $env:PYTHONPATH='src'; python -m pytest -q` → 全绿（29 passed）。
- 前端：`cd frontend; npm run build` → 通过；构建产物写入 `src/onto_platform/interfaces/web/dist`，并同步平台运行时资源。
- 专项：`python -m pytest tests/test_smart_ui.py tests/test_smart_ui_studio.py -q` → **45 passed**（Studio 27 项）。

### 7.8 本轮真实 HTTP 验证（摘要）

- 使用临时工作空间把模型指向实际可用 id 后，`mode=llm` 生成成功，trace 模型与 usage 落库，蓝图 `views[0].options` 为 `{chartType:"table",display:"table",groupBy:"exit_station",metric:{field:"plate",agg:"count"},sort:{...},columns:[...]}`，编译产物含 `DataTable(`。
- 服务端 table 返回 6 个出口站分组及计数（ST005=10、ST001=8、ST006=7、ST003=7、ST002=5、ST004=3），证明筛选/分组/排序/表格链路真实可用。
- `GET .../generations?mode=llm` 命中该记录；`DELETE .../generations/{gid}` 成功删除；工作空间模型配置验证后已恢复 `provider=none`。
- 说明：另一次提示词带“最近 7 天”，生成 `filters:[{ts,gte,-7d}]`；示例数据时间较早，表格按真实筛选结果为空，不是表格链路失败。
