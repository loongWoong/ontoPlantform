# 智能 UI 生成能力（LLM Studio）方案

> 版本 v0.2 · 2026-11-02 · 状态：**M1 + 部分 M2/M3 已实施（见 §15）**
> 范围：在现有「智能视图（确定性编译）」之上，新增一条 **LLM 提议 → 平台裁决 → 确定性编译 → 沙箱预览 → 资产化** 的生成链路。
> 配套原型稿：`docs/design/llm-smart-ui/01-studio.svg`、`02-iterate.svg`、`03-library.svg`、`04-architecture.svg`；实机截图：`live-aiview.png`、`live-aiview-history.png`、`live-smartui.png`、`live-run.png`（同目录）。

---

## 0. 结论先行

**现有「智能视图」作为确定性产品是完整的 v1，但作为「LLM 驱动的智能 UI 生成」并不完整。**

- 完整的是**执行侧**：本体适配、L3 规划、L4 编译、L5 渲染、角色/动作权限、真实数据、沙箱隔离、动作回写与审计，都有代码与测试覆盖；
- 缺的是**生成侧**：自然语言到界面的意图理解、生成过程与迭代、LLM 输出的校验闸门、生成资产的版本化与发布。当前整条链路明确写着「无 LLM 调用」，`question` 只影响确定性关键词规划，无法表达布局、分组、筛选、组件组合等长尾需求；
- 平台其实已经具备 LLM 基础设施（Provider Port、工作空间级模型配置、Agent 循环、AI 建模的「提议-裁决」范式），**只是没有接到智能 UI 上**。

建议：**不替换现有编译器**，新增一层 **Studio（生成台）**。LLM 只产出受 Schema 约束的「视图蓝图提案」，平台校验通过后才交给现有 L3/L4 编译；LLM 不可用或校验失败时自动回退到确定性视图。这样既补上生成能力，又不破坏已有的安全不变量与验收项。

---

## 1. 现状盘点：智能 UI 已经走到哪一步

### 1.1 已具备的能力（有代码 / 测试 / 浏览器实测）

| 层 | 能力 | 证据 |
|----|------|------|
| L0 适配 | 平台本体 → 引擎本体；对象/属性/关系/身份键/状态/时间/数值全部从当前工作空间推导，不依赖车牌等固定样例 | `interfaces/smart_ui.py:879` `build_engine_ontology`；`tests/test_smart_ui.py` 通用本体 / 无车牌用例 |
| L1–L2 | 语义视角能力派生；8 类视角（身份/关系/时间线/位置/统计/状态/规则/动作） | `engine/src/onto_ui_engine/capabilities.py`；`docs/spec.md` §2.2–2.3 |
| L3 规划 | 确定性意图识别（关键词 → goal → 视角集合）、按角色裁剪、动作权限决策 | `engine/src/onto_ui_engine/planner.py:138` `detect_intent`、`:210` `plan_application` |
| L4 编译 | 蓝图层 → OpenUI Lang 程序；工具闭包；程序哈希 | `engine/src/onto_ui_engine/compiler.py`；`smart_ui.py:1887` `compile_blueprint` |
| L5 渲染 | 隔离 iframe 中挂载 OpenUI Renderer；图表/地图/关系图/时间线/动作面板真实渲染 | `runtime-web/src/App.tsx`；`runtime-web/src/library.tsx` |
| 数据 | 每个视角绑定平台真实 payload；空工作空间明确拒绝生成，不伪造数据 | `smart_ui.py:1304` `build_bundle`、`:1744` `smart_ui_meta`；`tests/test_smart_ui.py` |
| 角色/权限 | 请求角色 ≤ actor 角色；viewer 不能请求 owner 视角；动作按角色与已发布能力二次校验 | `smart_ui.py:1734` `_require_roles_at_most`、`:2073` `invoke_smart_action` |
| 动作闭环 | 参数化动作、类型化参数校验、幂等键、写后重新编译、审计 | `smart_ui.py:2073`；`tests/test_smart_ui.py` 参数/越权用例 |
| 隔离 | 不透明源 iframe；令牌与 API 经父页面 postMessage 代理；`Origin: null` 直连 API 仍被 403 | `frontend/src/views/SmartUI.tsx:44`；`runtime-web/src/bridge.ts` |
| 可观测 | 意图 / 蓝图 / 程序 / 工具闭包 / 诊断抽屉；program hash；活动日志 | `runtime-web/src/App.tsx` Inspector；`tests/test_smart_ui.py` |

### 1.2 关键缺口

| # | 缺口 | 现状 | 影响 |
|---|------|------|------|
| G1 | **没有 LLM 参与生成** | 引擎明确无 LLM（`emitter.py:157` “No LLM is consulted here”）；运行时底部写死「确定性编译 · 无 LLM 调用」（`App.tsx:405`）；`spec.md:88` 预留了 `parseIntent` 的 LLM 段，但实现未接 | 只能覆盖预设的 7 种 goal；「按站点分组」「加时间范围」「换成值班视角」等需求无法表达 |
| G2 | **自然语言只影响意图关键词** | `planner.detect_intent` 用正则/关键词匹配 goal 与焦点 | 提示词稍一变化就回落到默认视角，用户会感到「不听话」 |
| G3 | **蓝图表达力有限** | 蓝图 Schema 只有 `context / views[].perspective / actions / layout / theme`，无 `filters / groupBy / sort / limit / chartType`（`blueprint-schema.yaml`） | 即便 LLM 接进来，也只能在既有视角集合里做选择，不能真正「设计界面」 |
| G4 | **没有生成态与迭代** | 只有同步 `POST /smart-ui/compile`；控制台请求超时 15s（`frontend/src/api.ts:773`），LLM 调用通常更慢 | 无法展示生成进度、重试、取消、多轮修改 |
| G5 | **没有「提议-裁决」治理** | 没有对 LLM 输出的二次校验、成本/时延预算、prompt/响应审计、失败回退 | 直接接模型会引入幻觉组件、越权动作、prompt injection、费用失控 |
| G6 | **没有生成资产** | 生成结果不落库；应用模型只有声明式 views/queries（`application/application_usecases.py`），没有 UI 程序快照 | 无法保存、复用、对比、回滚、发布为应用 |
| G7 | **没有评测基线** | 无 golden prompts、渲染成功率、动作正确率、人工采纳率 | 无法判断「换模型/换提示词」是变好还是变坏 |

**判定**：执行侧完整；生成侧 0 → 1。目标不是重写，而是补上 G1–G7。

---

## 2. 目标与非目标

### 目标

1. 用户用一段业务语言描述想要的界面，得到**可交互、绑定真实数据**的视图，而不是静态截图。
2. 生成结果必须**可解释、可校验、可回退**：每一步都能在界面上看到依据与裁决结果。
3. 生成结果可以**保存为草稿、迭代版本、发布到应用**，并保留审计。
4. 默认安全：LLM 不直接执行任何数据写入；权限、动作、数据范围仍由平台裁决。
5. 与现有确定性链路**共存**：LLM 不可用/超时/校验失败时，用户仍能拿到确定性视图。

### 非目标（本期不做）

- 不做任意 HTML/CSS/JS 生成（拒绝把 LLM 输出直接当代码执行）。
- 不做自由拖拽的所见即所得编辑器（后续可做，但不是本方案前提）。
- 不做跨工作空间共享的 UI 市场。
- 不改变现有「应用」的发布生命周期语义（published 不可原地编辑，克隆后发布）。

---

## 3. 设计原则

1. **LLM 只提议，平台裁决。** 与 AI 建模一致：模型输出只是一份 draft，规范化和校验由平台做（`application/ontology_draft.py:7` 的既有范式）。LLM 输出**永远不直接进入渲染器**。
2. **蓝图是唯一接口。** LLM 输出 `BlueprintIntent`（受 `blueprint-schema.yaml` 约束），由现有 `validate_blueprint` + `compiler.compile_blueprint` 生成程序。表现层字段（坐标/颜色/脚本）继续由 `FORBIDDEN_LAYOUT_KEYS` 拒绝（`engine/.../blueprint.py:22`）。
3. **确定性兜底。** 模型不可用、超时、Schema 不合法、视角无本体依据时：降级到现有 `plan_application(question)`，并在界面明示「已降级为确定性视图」。
4. **可复现。** 每次生成保存 `model / prompt_hash / proposal / program_hash / ontology_version / engine_version`；同一 proposal 应编译出同一程序（L4 本身是确定性的）。
5. **渐进增强。** M1 只让 LLM 在既有语义空间内选择与排序；M2 才扩展 Schema（筛选/分组/表格等）；M3 做多轮与资产化。避免一次性改渲染器。

---

## 4. 总体架构

```
┌──────────────────────────── 控制台（同源父页面） ────────────────────────────┐
│  Studio 页面                                                                │
│   ├─ 生成对话 / 约束（角色、只读/含动作、设备、风格）                          │
│   ├─ 生成进度（L0→L5 + LLM 步骤，轮询/SSE）                                  │
│   ├─ 预览（sandbox iframe，复用现有 bridge 同源代理）                         │
│   ├─ 裁决面板（Schema / 组件白名单 / 数据范围 / 动作权限 / 成本）              │
│   └─ 版本与发布（草稿、对比、回滚、发布到应用）                                │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    │ REST（同源，Bearer/actor）
┌───────────────────────────────────▼─────────────────────────────────────────┐
│ 应用层 SmartUiStudio（新 use case）                                          │
│  1) 组装上下文包：L0 本体 + L1 能力 + L2 模板 + 焦点/计数 + actor 权限        │
│  2) LLM 提议：JSON Schema 约束的 BlueprintIntent（提示词含禁用项与示例）      │
│  3) 规范化：未知对象/视角/动作 → 剔除并记 warning；缺失 goal → 默认           │
│  4) 平台裁决：validate_blueprint + 权限剪裁 + 预算/时延 + prompt 注入防护     │
│  5) 编译：复用 L3/L4（确定性）→ payload bundle（真实数据）                    │
│  6) 落库：generation / steps / draft；审计事件                                │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    │
              LLMProvider（工作空间级配置）      Engine（L0–L5）
```

失败路径：

- 模型超时/不可用 → 直接跑确定性 `plan_application(question)`，`status=fallback`；
- Schema/语义校验失败 → 带错误反馈重试 1 次，仍失败则 `status=failed`，展示可修复项，不渲染半成品；
- 动作越权 → 剪除动作并 `warning`，不静默保留。

---

## 5. LLM 提议协议（草案）

### 5.1 输入：上下文包（由平台组装，LLM 只读）

```json
{
  "workspace": {"id": "7c33935df1d1", "name": "收费站全链路演练（全功能）"},
  "actor": {"role": "owner", "allowedRoles": ["owner", "editor", "viewer"]},
  "ontology": {
    "version": "…",
    "objects": [{"name": "TollTransaction", "label": "通行交易",
                 "properties": [{"name": "fee", "type": "number"},
                                {"name": "ts", "type": "datetime"},
                                {"name": "exit_station", "type": "string"}]}],
    "relations": [{"name": "passes", "source": "Vehicle", "target": "TollStation"}]
  },
  "capabilities": [{"key": "daily_report", "name": "站点日报", "target": "TollStation"}],
  "actions": [{"key": "set_status", "name": "状态处置", "target": "TollStation",
               "roles": ["owner"], "parameters": [{"name": "status", "type": "string"}]}],
  "focus": {"objectType": "TollTransaction", "key": "plate", "value": "鲁C56789"},
  "counts": {"TollStation": 6, "Lane": 18, "Vehicle": 20, "TollTransaction": 40},
  "presentation": {"device": "desktop", "density": "comfortable", "theme": "warm"},
  "limits": {"maxViews": 8, "maxActions": 3}
}
```

注意：**实例值不进提示词**（只给类型、属性名、计数与焦点），避免把业务数据发给外部模型；需要展示值时由渲染阶段从 payload 取。

### 5.2 输出：BlueprintIntent

```json
{
  "intent": {
    "goal": "anomaly_analysis",
    "object": "TollTransaction",
    "focus": {"key": "plate", "value": "鲁C56789"},
    "confidence": 0.82,
    "rationale": "用户要按站点看异常金额与状态，属于异常分析 + 统计分组"
  },
  "views": [
    {"perspective": "statistics", "title": "按站点异常金额", "hint": "按 exit_station 分组"},
    {"perspective": "location", "title": "异常站点分布"},
    {"perspective": "timeline", "title": "最近通行记录"},
    {"perspective": "action", "title": "处置"}
  ],
  "actions": ["set_status"],
  "layout": "auto",
  "meta": {"generatedBy": "ai_planner", "confidence": 0.82, "sourceQuestion": "…"}
}
```

约束（提示词与校验器双重约束）：

- `goal` ∈ 现有 7 个枚举；`object` 必须是 ontology 中可作查询根的对象；
- `views[].perspective` ∈ 8 个视角词表，且必须能被该对象的 L1 能力支撑（这是「本体驱动」与「大屏生成器」的分界，`blueprint-schema.yaml:262`）；
- `actions[]` 只能引用已发布、当前 actor 可执行的能力；
- 禁止输出任何布局坐标、颜色、脚本、表达式、HTML；`FORBIDDEN_LAYOUT_KEYS` 会拒绝；
- 不得新增查询/数据源；生成物只能使用上下文包中已有的能力与属性；
- 输出必须是**单个 JSON 对象**，无 Markdown 包裹（沿用 AI 建模的 `_extract_json`，`ontology_draft.py:220`）。

### 5.3 M2：扩展表达力（Schema v1.1，向后兼容）

在 `views[]` 增加经过白名单校验的表现意图，而不是自由布局：

```yaml
views:
  - perspective: statistics
    title: 按站点异常金额
    options:
      groupBy: exit_station          # 必须是该对象属性
      metric: {field: fee, agg: sum}
      filters: [{field: ts, op: gte, value: "-7d"}]
      sort: {field: value, order: desc}
      limit: 8
  - perspective: timeline
    options: {limit: 20}
```

同时给运行时补 2 个受控组件：`FilterBar`（字段/操作符白名单来自本体类型）、`DataTable`（列 = 本体属性，排序/分页）。所有 options 都必须过 `validate_blueprint` 的新跨字段规则；编译器负责把 options 编进程序，渲染器仍只认识受控组件。

---

## 6. 后端接口

| 方法/路径 | 用途 | 说明 |
|---|---|---|
| `POST /api/workspaces/{wid}/smart-ui/generations` | 创建生成任务 | body: `{prompt, device?, roles?, mode?, constraints?, focus?}`；返回 `202 {id, status}` |
| `GET /api/workspaces/{wid}/smart-ui/generations/{gid}` | 查询任务 | `status` ∈ `queued/running/validating/compiling/ready/failed/fallback`；含 `steps[]`、`proposal`、`validation[]`、`result`（ready 时含 compiled/bundle）、`usage` |
| `GET /api/workspaces/{wid}/smart-ui/generations?limit=` | 生成历史 | 按时间倒序，支持 `status` / `object` 过滤 |
| `POST /api/workspaces/{wid}/smart-ui/generations/{gid}/refine` | 多轮修改 | body: `{prompt, baseVersion?}`；生成新版本，旧版本保留 |
| `POST /api/workspaces/{wid}/smart-ui/generations/{gid}/accept` | 存为草稿 | 生成 `ui_draft`（版本号 + program_hash），可继续改名/发布 |
| `POST /api/workspaces/{wid}/smart-ui/generations/{gid}/publish` | 发布 | body: `{application_id?, name?}`；写入应用版本并返回快照 |
| `GET /api/workspaces/{wid}/smart-ui/drafts` | 草稿列表 | 供 Studio 历史面板 |
| `GET /api/workspaces/{wid}/smart-ui/generations/{gid}/events` | SSE（可选） | 逐步骤推送；不支持 SSE 时用 800ms 轮询 |

**执行模型**：生成是异步任务（后台线程/执行器），HTTP 先返回任务 id。原因：LLM 调用通常 3–30s，控制台现有默认超时 15s（`frontend/src/api.ts:773`），同步接口会把「正在生成」误报成失败。任务状态持久化，刷新页面可恢复。

**取消**：`DELETE .../generations/{gid}` 标记取消；已进入编译阶段则忽略取消并返回 `ready`（编译很快，语义更简单）。

**兼容**：现有 `POST /smart-ui/compile` 保持不变；Studio 在 `accept/publish` 前不改变任何已有接口行为。

---

## 7. 数据模型（新增通用仓储记录）

| 集合 | 关键字段 |
|---|---|
| `ui_generation` | `id, workspace_id, actor, prompt, status, model, provider, prompt_hash, proposal_json, validation_json, program_hash, compiled_json, bundle_json, usage_json, error, created_at, updated_at, version` |
| `ui_generation_step` | `id, generation_id, layer (L0/L1/L2/llm/validate/L3/L4/L5), status, started_at, ms, detail` |
| `ui_draft` | `id, workspace_id, generation_id, name, version, program_hash, blueprint_json, status (draft/accepted/archived), created_at, created_by` |
| `application`（扩展） | `ui_artifact: {generation_id, draft_version, program_hash}`；`publish` 时写快照，运行 `GET /smart-ui?application=` 时优先读快照（与声明式 views 共存） |
| 事件 | `audit`: `smart_ui.generate / refine / accept / publish / fallback`；`EventLog`: `smart_ui.changed` |

存储沿用现有 memory/SQLite 通用 repo，不需要迁移框架；`ui_generation` 记录不包含模型密钥与完整提示词正文（只存 hash + 脱敏摘要），避免敏感信息落库。

---

## 8. 界面方案（对应原型稿）

### 8.1 信息架构

- 入口：**独立一级菜单「AI视图」**（业务世界分组），与原有「智能视图」并列；「智能视图」保持确定性编译/运行，不再混入生成入口。AI视图页内再分段 `AI 生成台 | 生成记录`，深链 `#/w/{wid}/aiview?tab=studio|history&generation=<gid>`；旧 `smartui?mode=studio|history` 链接自动跳转到新页面。
- 生成台三栏：**描述与约束（左）→ 预览（中）→ 轨迹与裁决（右）**。
- 底部版本条：`v1 → v2 → v3`，当前版本高亮；支持「与上一版对比」。
- 发布：右侧抽屉/卡片，选择目标应用与版本，明确提示 published 不可原地编辑、需克隆后发布。

### 8.2 交互状态机

```
空态 ──输入──▶ 可生成 ──提交──▶ generating(L0…L5 进度)
                                   ├─ ready ──▶ 预览 / 继续修改 / 保存草稿 / 发布
                                   ├─ failed ──▶ 错误 + 修复建议 + 重试 / 改确定性视图
                                   └─ fallback ──▶ 确定性视图 + 明确降级提示
```

细节：

- 生成中允许取消；输入框与提交按钮互斥禁用，避免重复任务；
- 进度用 `aria-live="polite"` 播报当前步骤，不以颜色为唯一状态；
- 预览沿用 sandbox iframe（不透明源 + bridge），设备宽度切换只改 iframe 容器，不重新生成；
- 「继续修改」把上一轮 proposal 作为上下文，生成新版本，不覆盖旧版本；
- 校验面板显示 **通过 / 剪除 / 警告** 三类，剪除项要写原因（如「set_status 需要 owner，当前 editor」）；
- 失败时不渲染半成品；展示可操作建议（换角色 / 去掉不存在的能力 / 改为确定性视图）。

### 8.3 视觉语言

沿用控制台现有设计语言：暖单色画布（`#f7f6f3`）、1px 结构线、衬线标题（Newsreader）、等宽元数据（Geist Mono）、低饱和语义色；不做渐变/重阴影/大色块。预览 iframe 内部沿用运行时自己的暖色主题（`runtime-web/src/styles.css`），保持「平台壳 + 真实运行时」两种质感可区分。

### 8.4 原型与实现的对应

原型 `01/02/03.svg` 按 **M2 目标态**绘制，便于一次看清“生成 + 迭代 + 审计”的完整体验；实施时按里程碑裁剪：

| 原型元素 | 交付阶段 | 说明 |
|---|---|---|
| 左栏描述/约束、右栏 L0–L5 轨迹、裁决五行、生成历史 | M1 | 全部基于现有 `meta / compile` 能力与新增 generation 任务 |
| 预览中的地图/统计/时间线 | M1 | 运行时已有 `GeoMap / StatisticChart / EventTimeline` 等 8 类组件 |
| 筛选器（时间范围/站点/状态）、按站点分组表格、阈值高亮 | M2 | 依赖 Blueprint v1.1 options + 新增 `FilterBar / DataTable`，属于受控扩展而非自由布局 |
| 版本条、对比、发布抽屉、回滚 | M2–M3 | 依赖 `ui_draft` 与 `application.ui_artifact` 落库 |

预览画布刻意保留运行时的 VIBE 顶栏、角色与动作计数，提醒评审：**预览里看到的就是最终运行时**，不是另做一套皮肤。

---

## 9. 权限、安全与治理

| 威胁 | 对策 |
|---|---|
| LLM 输出越权动作 | 只允许引用当前 actor 可执行动作；`validate_blueprint` + `_require_roles_at_most` 双重剪裁；越权项降级为 warning，不渲染 |
| 提示词注入（本体描述/实例文本） | 上下文包只放类型与元数据；实例值不进提示词；输出仍受 Schema 与禁用字段约束；LLM 无权调用工具 |
| 数据外带 | 不把真实实例值发给模型；返回的 proposal 仅含字段名与视角；prompt_hash 审计不存正文 |
| 幻觉组件/字段 | 组件白名单 + 视角词表 + 属性存在性校验；未知项拒绝并给出诊断 |
| 成本失控 | 每工作空间预算（token/次数/并发）；超预算直接 fallback；usage 记账 |
| 时延失控 | 60s 硬超时 + 1 次重试；超时 fallback；控制台异步任务，不受 15s 限制 |
| 模型不可用 | 健康探测 + 确定性回退；UI 明示降级原因 |
| 发布后不可追责 | generation/draft/publish 全链路审计；program_hash 与版本绑定；发布人/时间可见 |
| 密钥泄漏 | 只用工作空间级配置（`llm_config`），前端不回显密钥；请求体不接受 `base_url/api_key`（与安全复盘结论一致） |

---

## 10. 性能与预算

| 指标 | 目标 |
|---|---|
| 生成 P50 / P95 | ≤ 8s / ≤ 25s（含 1 次校验重试） |
| 首屏进度反馈 | ≤ 300ms 出现任务状态 |
| 超时 | 单次 LLM 60s，总任务 120s |
| Token | 单次 ≤ 8k input / 2k output；工作空间日预算可配 |
| 预览切换 | 设备/角色切换不重新调用 LLM；角色变化才重新编译（确定性） |
| 前端超时 | 生成任务用轮询，不受 15s 默认超时影响 |

---

## 11. 兼容与回退

- 现有 `smart-ui/meta|compile|actions` 与 `运行视图` 不变；Studio 是增量页面。
- 生成失败/超时：自动调用现有确定性 `compile`，返回 `status=fallback`，页面提示「模型不可用，已用确定性编译器生成」。
- 发布：只新增 `application.ui_artifact` 快照；不改变现有 applications 的 views/queries 行为。
- 关闭开关：`ONTO_SMART_UI_STUDIO=off` 时导航隐藏 Studio 入口，API 返回 404，完全回到当前形态。

---

## 12. 测试与验收

**后端（pytest）**

- 生成任务状态机：queued→running→ready / failed / fallback；取消；重复轮询幂等；
- LLM 假实现（脚本化 Provider）：合法 proposal → 编译成功；非法 JSON → 重试 1 次 → failed；越权动作 → 剪除 + warning；超时 → fallback；
- Schema 校验：未知视角/对象/动作/表现层字段被拒；
- 权限：viewer 不能生成含动作的视图；请求角色不得超过 actor；
- 资产：accept / publish / 版本号递增 / program_hash 稳定；
- 预算：token 超限 fallback；工作空间隔离；
- HTTP：202/轮询/404/403；现有 429 个测试不回归。

**前端（vitest + browser）**

- 状态机渲染：空态/生成中/ready/failed/fallback；
- 三栏布局在 1440/1024/768 宽度下不溢出；
- iframe 预览经 bridge 拿到 meta/compile，无 403；
- 键盘：⌘↵ 提交、Esc 取消、焦点管理；
- 发布抽屉：published 应用提示克隆。

**质量基线（M3）**

- 20 条 golden prompt（覆盖 4 类本体、3 种角色、只读/含动作），人工评分 + 渲染成功率 ≥ 95%；
- 对比：确定性 vs LLM 的「采纳率 / 编辑距离 / 时延 / 成本」。

---

## 13. 里程碑

| 阶段 | 交付 | 退出条件 |
|---|---|---|
| **M1 生成主链路**（约 3–4 天） | 后端 generation 任务 + 上下文包 + LLM proposal(Schema 约束) + 校验/重试/fallback；Studio 页面（输入、进度、预览、裁决）；现有 compile 复用 | 假 Provider 与真实 Provider 各跑通一次；失败注入全部走 fallback；无新增 403/安全回归 |
| **M2 迭代与表达力**（约 4–5 天） | refine 多轮；版本对比；Blueprint v1.1（filters/groupBy/table/chartType）+ FilterBar/DataTable；保存草稿 | 多轮修改保留旧版本；新 options 全部有校验测试；浏览器验收 |
| **M3 资产化与评测**（约 3–4 天） | 发布到应用快照；生成历史；审计视图；golden prompts + 成本面板 | 发布/回滚可用；评测报告；文档更新 |

> 建议先只做 M1 的**只读视图**（不含动作），把安全边界跑稳，再开动作。

---

## 14. 待确认的决策（请评审）

1. **入口形态**：`智能视图` 内分段（推荐）还是新增一级导航「生成台」？
2. **生成边界**：M1 只生成只读视图（推荐），还是一开始就允许带动作？
3. **表达力路线**：继续「LLM 只出语义蓝图、平台扩展受控 options」（推荐，安全可复现），还是允许 LLM 直接产出 OpenUI Lang 布局（灵活但难以审计，不建议）？
4. **模型策略**：只使用工作空间已配置模型（推荐），还是允许用户在生成时临时选择模型/端点？

---

## 15. 实施记录（M1 + 部分 M2/M3）

### 15.1 实际交付范围

| 里程碑 | 已交付 | 未交付 / 降级 |
|---|---|---|
| M1 生成主链路 | 异步 generation 任务、上下文包、LLM 提议、平台裁决、L4 编译、预览、确定性回退、Studio 页面、生成历史；取消/并发上限/重启恢复 | SSE 推送改为 700ms 轮询 |
| M2 迭代与表达力 | refine 多轮（父版本 + version+1，继承父 focus）、草稿重命名/删除、版本链与 blueprint diff；Blueprint v1.1 `options`（`chartType/display/groupBy/metric/sort/limit/columns/filters`）；引擎侧 `DataTable` + 服务端分组/筛选/排序/limit 行数据（含 `-7d` 相对时间、布尔/`contains`/`in` 过滤）；Studio 实体焦点选择器与 HTTP `/smart-ui/entities`；历史检索/分页/删除（`q/status/object/offset/limit`） | 交互式 FilterBar 以 DataTable 内置关键字过滤/列头排序替代（字段/操作符仍来自本体白名单，服务端执行）；图表 `chartType` 目前只区分 table / 非 table，bar/line/pie 共用 StatisticChart |
| M3 资产化与评测 | 发布为应用 `ui_artifact`（含 blueprint/program/question/roles/版本指纹）、应用「AI视图」页签、生成历史、prompt/program 指纹、审计事件、草稿列表；生成记录含 usage，可选费率生成 cost；可选保留策略；提示词契约测试 | golden prompt 评测集、采纳率/发布打开率、月度预算闸门、历史导出未做 |

### 15.2 后端实现（代码索引）

| 能力 | 位置 |
|---|---|
| 任务/草稿仓储与状态机 | `src/onto_platform/interfaces/smart_ui_studio.py` `studio_enabled`、`start_generation`（`focus/parent_id/instruction/mode`）、`_run_generation`、`get_generation`（`diff/lineage`）、`update_draft`/`delete_draft`、`cancel_generation` |
| 上下文包与提示词 | `_context_pack`、`_prompt`；实例明细不进提示词，只含对象/属性/能力/焦点值；`views[].options` 白名单与约束在提示词中声明 |
| 平台裁决 | `_normalize_proposal`：对象存在性 → 维度对象查询根解析 → 视角白名单/去重 → `options` 字段与聚合白名单 → 动作权限交叠 → 焦点属性校验；每项剪除写原因 |
| 确定性回退 | `_fallback`，复用 `compile_smart_ui`，标记 `mode="fallback"` |
| 多轮迭代 | `refine_generation`：保留父版本，`version = parent.version + 1`，继承父 `focus`（也可显式传入） |
| 草稿与发布 | `accept_generation`、`publish_generation`：发布前自动 accept，创建或克隆应用，挂 `ui_artifact`，同一草稿不可重复发布；published 应用拒绝写入；published 草稿可重命名、拒绝删除 |
| 历史与成本 | `list_generations`（`q/status/object/mode/limit/offset` → `{items,total,offset,limit,all}`）、`delete_generation`（未发布才可删，清理未发布草稿）、`_prune_generations`（`ONTO_SMARTUI_HISTORY_RETENTION_DAYS`，默认关闭、不删已发布/运行中）、`_usage_cost`（`ONTO_SMARTUI_INPUT_COST_PER_1K`/`OUTPUT_COST_PER_1K`/`COST_CURRENCY`） |
| L4 编译复用 | `src/onto_platform/interfaces/smart_ui.py` `compile_smart_ui_blueprint`，返回 `compiled + bundle`；`build_bundle(..., blueprint=...)` 按 `options` 生成 `view_statistics.rows[0].table = {columns,columnLabels,rows,total}`；`_table_payload` 支持 filters/groupBy/metric/sort/limit/columns，`_table_match` 支持数值、布尔、`contains`、`in`、`-7d` 相对时间 |
| 应用扩展 | `domain/application/model.py` 增 `ui_artifact`；`application/application_usecases.py` 增 `AttachUiArtifact`；`bootstrap.py` 注册 |
| HTTP 路由 | `src/onto_platform/interfaces/api.py`：GET list（`q/status/object/mode/limit/offset`）/detail/view/drafts/entities、POST create/refine/accept/publish/cancel、PUT/DELETE drafts、DELETE generation、GET application ui-artifact/view、POST llm-config/check；`/api/health` features 增 `smart-ui.studio` |

实际接口与 §6 的差异：

- `POST .../generations` body 为 `{question, roles?, mode?, focus?}`，`mode ∈ auto|llm|deterministic`；`focus = {object?, key, value}`；返回 `202` 任务摘要。
- 状态枚举：`queued | running | succeeded | failed | cancelled | interrupted`；模式 `llm | fallback | deterministic`；步骤 `context → propose → adjudicate → compile → ready`。
- `GET .../generations/{gid}/view` 按当前真实数据重编译并返回 `{generation, compiled, bundle}`；`GET .../generations/{gid}` 额外返回 `lineage` 与 `diff`。
- `POST .../generations/{gid}/cancel` 协作式取消；`GET /smart-ui/entities?object=&q=&limit=` 提供实体候选；`PUT/DELETE /smart-ui/drafts/{did}` 管理草稿；`GET /smart-ui/generations?q=&status=&object=&mode=&limit=&offset=` 服务端检索分页；`DELETE /smart-ui/generations/{gid}` 删除未发布记录并清理其未发布草稿。
- 未实现 `/events` SSE；鉴权错误遵循现有 403，Studio 关闭时返回 404。

### 15.3 前端与控制台

- `frontend/src/views/SmartUIStudio.tsx:120` `SmartUIStudio` 三栏（描述/预览/轨迹与裁决），`:553` `SmartUIGenerationHistory` 历史表。
- `frontend/src/views/AIView.tsx` 独立页面：页内 `AI 生成台 | 生成记录`，深链 `?tab=studio|history&generation=<gid>`；`frontend/src/nav.tsx` 新增「AI视图」菜单，与「智能视图」并列。
- `frontend/src/views/SmartUI.tsx` 恢复为纯确定性运行页（不再含生成分段）；旧 `?mode=studio|history` 做一次向后兼容跳转。
- `frontend/src/smartui-bridge.ts`：抽出 iframe 同源桥，预览 iframe 通过父页面代理调用 `/api/workspaces/{wid}/smart-ui/`。
- 运行时 `vibe-可视化engine/runtime-web`：`?generation=<gid>` 只读快照模式，`loadGenerationView` 拉取重编译结果；提问与重新编译禁用，明确标注「AI 生成快照 · 只读」。
- 样式：`frontend/src/styles.css` 末尾 Studio 组件样式与 1280/960 响应式断点。

### 15.4 测试与实机验证

- `pytest tests/test_smart_ui.py tests/test_smart_ui_studio.py -q`：**45 passed**（Studio 27 项：确定性链路、LLM 裁决剪除、维度对象→查询根、不可达模型回退、非法 proposal 失败、refine 版本、accept/publish 不可变与重复发布拒绝、HTTP 202/400、viewer 403、v1.1 table/options 剪除、相对时间/布尔过滤、历史检索分页/删除/保留、usage/cost/耗时、原始响应诊断、提示词契约）。
- 全量 pytest：**471 passed**（38.8s），无既有回归。
- 引擎 pytest（`engine` + `PYTHONPATH=src`）：**29 passed**，含 `test_e2e_24_table_options_switch_component` 与未知 options 拒绝。
- 前端 `npm run build`：通过；产物写入 `src/onto_platform/interfaces/web/dist`（当前 `index-B23SWgp3.js` / `index-HlkOmfmo.css`）。
- 实机（工作空间 `7c33935df1d1`，临时把该工作空间模型指向本机可用 id，验证后已恢复 `provider=none`）：
  - `mode=llm` 生成成功：提示词“按出口站分组统计通行交易数量，用表格展示，按数量降序” → 模型给出 `chartType/display=table`、`groupBy=exit_station`、`metric=count`、`sort`、`columns`；
  - 编译产物含 `DataTable(`，服务端 `table.rows` 返回 6 个出口站分组及计数（ST005=10、ST001=8、ST006=7、ST003=7、ST002=5、ST004=3）；
  - generation 记录 usage（prompt 2070 / completion 7788 / total 9858），`cost` 由模型端点 usage 自带字段返回；平台费率未配置时 `cost=null`；
  - `GET .../generations?mode=llm` 服务端过滤命中该记录；`DELETE .../generations/{gid}` 删除成功且不影响其他记录；
  - 早前一次带“最近 7 天”提示词的生成也正确得到 `filters:[ts gte -7d]`，因示例数据时间较早返回空表，属于真实筛选结果而非链路失败。

### 15.5 已知限制

1. 本地 `127.0.0.1:8000` 的模型 id 可能带部署后缀（如 `deepseek/deepseek-v4-flash-…`）；工作空间/平台里保存的裸 `deepseek-v4-flash` 会 404。平台已把 404 归类为 `not_found` 并回退，部署侧应提供稳定别名或同步配置。
2. 示例工作空间的本体把 `TollStation/Lane/Vehicle` 标为 `dimension`，只有 `TollTransaction` 是查询根；Studio 已做根解析并在裁决面板展示「维度对象 → 查询根」，不是静默替换。
3. 预览已支持 M2 的受控筛选/分组/排序/DataTable；`options.filters` 由服务端执行（支持数值、布尔、`contains`、`in`、`-7d` 相对时间），`DataTable` 内提供客户端关键字过滤/列头排序，但没有交互式 FilterBar。图表 `chartType` 除 `table` 外仍共用 StatisticChart，尚未按 bar/line/pie 切换图形。
4. 发布写入的是应用 `ui_artifact` 快照，应用「AI视图」页签从 artifact 重编译，不改变应用声明式 `views`。
5. 成本为可选能力：`ONTO_SMARTUI_INPUT_COST_PER_1K` / `ONTO_SMARTUI_OUTPUT_COST_PER_1K` 未配置时 `cost=null`；模型端点 usage 自带的 `cost` 会原样保留在 `usage`，平台不混算。
6. 历史保留策略默认关闭；开启后只清理超过阈值的未发布、非运行中记录，已发布 artifact 与运行中任务永不清理。

---

## 附录 A：现状证据索引

- 确定性无 LLM：`vibe-可视化engine/engine/src/onto_ui_engine/emitter.py:157`、`runtime-web/src/App.tsx:405`、`frontend/src/views/SmartUI.tsx:191`
- 预留 LLM 段：`vibe-可视化engine/docs/spec.md:88-105`
- 现有编译入口：`src/onto_platform/interfaces/smart_ui.py:1849` `compile_smart_ui`；`api.py:71-73` 路由
- 蓝图 Schema：`vibe-可视化engine/docs/blueprint-schema.yaml`（`views` / `layout` / `theme` / `meta.generatedBy`）
- 禁用表现层字段：`engine/src/onto_ui_engine/blueprint.py:22`
- 提议-裁决先例：`application/ontology_draft.py:79` `DraftOntologyFromText`
- LLM Provider Port：`ports/llm.py`；工作空间配置：`application/llm_config.py`；解析器：`application/common.py:97` `llm_for`
- 控制台超时：`frontend/src/api.ts:773`（默认 15s）、`:774`（模型接口 90s）
- 现有应用生命周期：`application/application_usecases.py`（published 不可原地编辑）

## 附录 B：原型稿

- `docs/design/llm-smart-ui/01-studio.svg` — 生成台（结果态：三栏 + 真实数据预览 + 裁决轨迹）
- `docs/design/llm-smart-ui/02-iterate.svg` — 多轮修改（对话、差异高亮、版本条、发布抽屉）
- `docs/design/llm-smart-ui/03-library.svg` — 生成历史与版本审计（列表 + 详情 + 回滚）
- `docs/design/llm-smart-ui/04-architecture.svg` — 生成链路与安全闸门
- 生成脚本：`docs/design/llm-smart-ui/build_mockups.py`（可复现、可审）
