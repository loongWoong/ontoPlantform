# OntoPlatform 前端重构设计方案：左侧抽屉导航 + 右侧自适应功能区（React）

> 目标：把现有 1495 行单文件 vanilla-JS 控制台（8 个页签平铺堆叠）重构为
> 「左侧抽屉功能菜单 + 右侧自适应功能区」的 SPA 架构，并给出每个功能域的
> React 组件拆解、状态归属与交互流程设计。
> 本文档只做设计，不改代码。风格基线延续 minimalist-ui 暖色单色编辑风 tokens。

---

## 1. 总体布局架构

### 1.1 三层 Shell

```
┌─────────────┬──────────────────────────────────────────────┐
│  Sidebar    │  ContentArea                                 │
│  (可折叠)    │  ┌────────────────────────────────────────┐ │
│             │  │ PageHeader（面包屑+标题+主操作）          │ │
│  品牌区      │  ├────────────────────────────────────────┤ │
│  工作空间切换 │  │                                        │ │
│  ────────   │  │   <Outlet/>  路由功能区                  │ │
│  构建        │  │   （页面级滚动，Sidebar 固定）            │ │
│   概览       │  │                                        │ │
│   本体   ●   │  └────────────────────────────────────────┘ │
│   数据       │  ┌────────────────────────────────────────┐ │
│   能力       │  │ DetailDrawer（右侧滑出，主从详情）        │ │
│   应用       │  └────────────────────────────────────────┘ │
│  运行        │                                              │
│   业务数据    │                                              │
│   AI 助手    │                                              │
│  治理        │                                              │
│   审计       │                                              │
│  ────────   │                                              │
│  ● v0.1     │                                              │
│  LLM 徽标    │                                     [问 AI ✦]│
└─────────────┴──────────────────────────────────────────────┘
```

- **Sidebar（240px，可折叠到 64px 图标态）**：固定定位，不随内容滚动。
  分三组导航（构建/运行/治理），对应 design.txt 的「设计时 → 运行时 → 治理」心智模型。
  底部常驻：服务健康点、版本、LLM 徽标、折叠开关。
- **ContentArea**：`flex: 1` 自适应；内部由 PageHeader（sticky）+ 滚动区组成。
  每个功能区自行决定内部布局（主从两栏 / 向导 / 单列表格）。
- **DetailDrawer（右侧滑出 480-640px）**：全局唯一的详情抽屉容器，
  任何页面的"查看详情"都推入这里（对象详情、能力详情、视图预览），可堆叠、Esc 关闭、深链可达。

### 1.2 响应式规则

| 断点 | Sidebar | ContentArea | DetailDrawer |
|------|---------|-------------|--------------|
| ≥1200px | 常驻展开 | 两栏主从可用 | 480px 滑出 |
| 980-1200px | 常驻折叠（图标） | 单栏堆叠 | 480px |
| <980px | 覆盖式抽屉 + 遮罩（汉堡按钮唤出） | 单栏 | 全屏 sheet |

### 1.3 为什么是"分组抽屉"而不是平铺页签

- 现有 8 个页签平铺无层次，与 §41 主流程（建模→数据→能力→应用）的**顺序性**不匹配；
  分组后天然表达"先构建、再运行、后治理"。
- 导航项可以携带**状态徽标**（本体已发布 ●、能力 N 个、变更流 live），
  让用户不进入页面就能感知世界状态——这是平铺页签做不到的。
- 折叠态给功能区让出 176px，业务数据/应用详情这类主从布局受益最大。

---

## 2. 技术选型与工程结构

### 2.1 选型

| 层 | 选择 | 理由 |
|----|------|------|
| 构建 | Vite + React 18 + TypeScript | 现有代码无类型，DTO 先行定义可防前后端契约漂移 |
| 路由 | React Router v6（嵌套路由 + URL 参数） | wid/选中对象/抽屉内容全部深链化 |
| 服务端状态 | TanStack Query v5 | 缓存、失效、轮询（变更流/健康检查）开箱即用 |
| 客户端 UI 态 | React Context（Sidebar/Drawer/Toast 三个小 Context） | 不引入 redux/zustand——UI 态很薄 |
| 样式 | CSS Modules + 现有 `:root` tokens 原样迁移 | 保持编辑风；不引入组件库（避免破坏 minimalist 视觉纪律） |
| 图标 | Radix UI Icons（stroke 统一） | minimalist-ui 协议指定；不用 emoji |
| 测试 | Vitest（组件）+ Playwright（旅程冒烟） | e2e_probe.py 的 32 项断言作为契约回归 |

### 2.2 目录结构

```
web/
├── src/
│   ├── main.tsx / App.tsx          # Router + QueryClientProvider + Shell
│   ├── api/
│   │   ├── client.ts               # fetch 封装：15s 超时/错误友好化/类型化
│   │   ├── types.ts                # Ontology/Capability/InvocationResult… DTO
│   │   └── hooks.ts                # useOntology/useRuntime/useChanges… 全部查询
│   ├── components/                 # 跨领域通用件
│   │   ├── shell/  AppShell Sidebar PageHeader DetailDrawer
│   │   ├── ui/     StatusTag EmptyState ErrorBanner ConfirmDialog
│   │   │            JsonField KeyValueTable ObjectRefChip Toasts
│   ├── features/                   # 按领域垂直切分（每域一个路由段）
│   │   ├── overview/  ontology/  data/  capability/
│   │   ├── application/  runtime/  assistant/  audit/
│   └── styles/tokens.css           # 现有 minimalist tokens 平移
└── vite.config.ts                  # dev 代理 → :8100；build.outDir 由 api.py 托管
```

### 2.3 后端配合点（唯一）

`api.py._serve_web()` 目前只回 `index.html`。需改为：`/` 与 `/app/*` 返回
`dist/index.html`，`/assets/*` 按扩展名回 JS/CSS/SVG（SPA fallback 到 index.html）。
其余 API 零改动——现有 32 项 e2e 契约全部保持。

---

## 3. 路由与状态设计

### 3.1 路由表（wid 进路径，选中态进查询参数）

```
/                                    → 重定向（无工作空间→/onboarding；有→第一个 wid）
/onboarding                          → 创建第一个工作空间
/w/:wid/overview                     概览
/w/:wid/ontology                     本体
/w/:wid/data                         数据
/w/:wid/capabilities                 能力
/w/:wid/applications                 应用列表
/w/:wid/applications/:appId          应用详情（视图/查询/Agent/挂载）
/w/:wid/data                         业务数据（?type=&ref= 驱动主从）
/w/:wid/assistant                    AI 助手
/w/:wid/audit                        审计
```

深链规则：`?ref=Vehicle/V001` 打开对象详情抽屉；`?cap=<id>&tab=invoke` 打开能力抽屉的调用页。
现有 `#ontology` hash 与 localStorage 工作空间记忆由路由层兼容接管。

### 3.2 状态三分法（现 `S` 对象的拆解）

| 现状 | 归属 | 实现 |
|------|------|------|
| `S.workspaces/ontology/runtime/capabilities/datasources/projections/appsById` | **服务端状态** | React Query 缓存，key 形如 `['ontology', wid]` |
| `S.wid` | **URL 状态** | `/w/:wid/*` 路径参数；切换 = 导航 |
| `S.changes/lastSeq/liveTimer` | **服务端状态+轮询** | `useChanges(wid, live)`：`refetchInterval: live ? 2500 : false`，累积合并（现修复逻辑平移进 hook） |
| `S.llm`（健康） | **服务端状态** | `useHealth()`：20s 轮询，Sidebar 底部消费 |
| 折叠/抽屉开关/Toast | **UI 状态** | 三个 Context |

**失效映射**（保证跨页数据新鲜，替代现在的手动 `loadXxx()`）：

```
publishOntology     → invalidate ontology + runtime + audit
runProjection       → invalidate changes + state + audit
invoke              → invalidate changes + state
publishCapability   → invalidate capabilities + runtime
应用类变更           → invalidate applications / application(appId)
```

---

## 4. 各功能域组件设计与交互逻辑

### 4.1 概览 Overview —— 「主流程驾驶舱」

**组件**：`OnboardingChecklist`（主流程五步进度条）、`WorkspaceCard`、`RuntimeIndexCard`、`PipelineCards`（三链路）。

**交互逻辑**：
- 无工作空间 → 整页 Onboarding（创建表单 + 三链路说明），替代现在的"空态横幅"。
- 有工作空间 → Checklist 从缓存推导进度：本体已建？已发布？数据源已连？投影已跑？能力已发布？应用已建？每步可点击直达对应页。
- 这是对 design.txt §41「创建工作空间→定义本体→连接数据→创建能力→构建应用」主流程的产品化。

### 4.2 本体 Ontology —— 「列表 + 编辑抽屉 + 发布门」

**组件**：`OntologySummary`（对象/关系/术语三个分段计数）、`ObjectTable`、`ObjectEditorDrawer`、`RelationTable`、`VocabularyChips`、`PublishBar`（校验+发布）、`ValidationReport`、`PublishConfirmDialog`。

**交互逻辑**（修复现在"发布后表单散乱禁用"的体验）：
1. Draft：右上 `添加对象` → 编辑抽屉（名称/描述/属性行编辑器，非裸 JSON 文本域）→ 保存即失效 `['ontology']`。
2. 校验：`PublishBar.校验` → `ValidationReport` 内联展示问题清单（现在只有 toast 一闪而过）。
3. 发布：点击 → `PublishConfirmDialog` **明确警告"发布后不可修改"** → 确认后按钮变 `已发布 ✓`，整页切只读模式（顶部锁定横幅 + 所有编辑入口禁用并带 tooltip 原因）。
4. Published：点击对象行 → 只读详情抽屉（展示属性/被哪些投影映射）。
5. Sidebar 的「本体」项出现 ● 已发布徽标。

### 4.3 数据 Data —— 「两段向导 + 映射构建器」

**组件**：`DataSourceList`（卡片）、`DataSourceWizard`（3 步：类型→配置→资产预览）、`AssetPreviewTable`、`ProjectionList`、`ProjectionBuilderDrawer`、`FieldMappingEditor`、`RunProjectionButton`。

**交互逻辑**：
1. 连接数据源：向导选 kind（static/sqlite/rest）→ 动态配置表单（sqlite 出 path 输入，rest 出 path/params）→ 资产声明（static 出行编辑表格，sqlite 出 SQL 输入 + 「预览前 5 行」）→ `test` 通过才允许保存（后端已支持 test_connection）。
2. 创建投影：选数据源 → 选资产 → **双栏映射编辑器**（左：源字段实测列名；右：本体属性下拉）——替代现在让用户手写 JSON；「从资产推断」按钮按字段名相似度生成初稿（现"生成模板"的升级）。
3. 运行：按钮带 loading → 成功 toast 显示 `N 对象 / M 关系 / K 变更` + 「查看变更流」链接（跳业务数据页并展开变更面板）。

### 4.4 能力 Capability —— 「表格 + 详情抽屉 + 参数化调用」

**组件**：`CapabilityTable`（kind/状态筛选）、`CapabilityDrawer`（详情+发布）、`CreateCapabilityDrawer`、`PythonCodeEditor`（带校验的代码域）、`WorkflowStepBuilder`、`ArgForm`、`TargetPicker`、`InvokeResultPanel`。

**交互逻辑**（本方案中价值最大的改造点）：
1. 创建：kind 选择器切换三种表单——python（代码域+「插入模板」）、workflow（**结构化步骤编辑器**：添加步骤→选 kind→按字段契约出动态字段 `ref/properties/filter…`，替代手写 JSON）、agent（目标文本域）。
2. 发布：行内按钮 → 成功后函数名 `Type.key` 出现在可调用清单。
3. **调用（参数化）**：选能力 → `TargetPicker`（基于 `state/find` 的搜索下拉，输入即查）→ `ArgForm` **按 inputs 声明自动生成表单**（`amount:number` → 数字输入框）——用户不再手写 `{"amount": 100}`。
4. 结果：`InvokeResultPanel` 展示 ok/output/**变更时间线**（set_property 新旧值、link 边），变更里的对象引用是 `ObjectRefChip`，点击直达业务数据详情抽屉——闭环 §49 Action→Change→State。

### 4.5 应用 Application —— 「列表页 + 详情页二级路由」

**组件**：`ApplicationList`、`ApplicationDetailPage`（二级路由）、`ViewGrid`/`ViewCard`/`ViewPreview`、`QueryList`/`QueryResultPanel`、`AgentList`/`AgentRunPanel`、`AttachCapabilitySelect`。

**交互逻辑**：
1. 列表页：卡片网格（名称/生命周期/组成计数），点击进详情页（URL 变化，可分享）。
2. 详情页四个分段：**视图 / 查询 / Agent / 挂载能力**（替代现在一张卡片塞三列）。
3. 视图：`ViewCard` 显示定义 + 「打开」→ `ViewPreview` 在 DetailDrawer 渲染对象表格（table/card 直渲染，chart/map 注明 v0.1）。
4. 查询：行内「运行」→ 结果面板（对象表格 + expand 关系分组，复用 `objectTable` 逻辑）。
5. Agent：「运行」→ 步骤时间线 + 最终答案（复用现 `renderAgentRun`）。
6. 应用是最终产品面（§21-23）：详情页顶部提供「进入运行视图」——把该应用的默认视图全屏化为"应用运行态"（v0.2 预留）。

### 4.6 业务数据 Runtime —— 「主从两栏 + 变更流底坞」

**组件**：`TypeSelector`（chips）、`FilterBuilder`（属性+操作符下拉+值输入，替代手写 `prop__gt=1`）、`ObjectTable`、`ObjectDetailPanel`（右栏，非抽屉——本页是主从布局）、`RelationNavigator`（分组可展开）、`ChangesDock`（底部可折叠面板 + Live 徽标）。

**交互逻辑**：
1. 左栏：类型选择 → 过滤构建器 → 结果表；行点击 → 右栏详情（URL `?ref=` 同步，刷新/分享不丢）。
2. 右栏：属性 kv 表 + 关系导航（每组关系一行，展开显示边，边是 `ObjectRefChip` → 点击替换右栏内容并更新 URL，形成**关系图游走**体验）。
3. 变更流：底部 `ChangesDock`，Live 开关轮询 2.5s，累积渲染（现修复逻辑）；其它页 toast 的「查看变更」链接跳来并自动展开。
4. 本页数据源：`useRuntime(wid)` 提供 schema（类型/关系），`useFind(type, filters)`、`useObject(ref)`、`useRelated(ref)`。

### 4.7 AI 助手 Assistant —— 「专属页 + 全局浮动入口」

**组件**：`AssistantPage`（模式切换 Ask/Agent）、`AskComposer`/`AskResultCard`、`AgentComposer`/`AgentStepsTimeline`、`LlmConfigPopover`、`AssistantFab`（全局）。

**交互逻辑**：
1. 专属页：上下对话式布局（客户端保留会话历史），Ask 结果 = 答案块 + 术语 chips + 命中对象表 + 关联 chips（全部对象可点）。
2. Agent：提交前 LLM 预检（`useHealth`），未配置时 `LlmConfigPopover` 直接在 composer 上方弹出——不再藏在折叠框。
3. **全局 FAB「问 AI ✦」**（§42 第三原则"每个页面都能表达帮我完成这个事情"）：任何页面右下角唤起 `AssistantDrawer`，自动携带上下文（如业务数据页带 `当前对象 Vehicle/V001`，本体页带 `当前本体 traffic`）预填问题。

### 4.8 审计 Audit —— 「可过滤流水表」

**组件**：`AuditFilterBar`（操作者/动作类型）、`AuditTable`（时间/操作者/动作/目标/详情展开行）。
交互最简单的一页：服务端已支持 limit；客户端加动作类型过滤即可。

---

## 5. 跨域复用组件（一次建成，八处受益）

| 组件 | 职责 | 使用处 |
|------|------|--------|
| `ObjectRefChip` | `Type/Id` 可点击引用 → 打开对象详情 | 业务数据/能力结果/Ask 结果/应用查询/变更流 |
| `DetailDrawer` | 统一右侧滑出容器（堆叠/Esc/焦点圈定） | 对象详情/能力详情/编辑器/视图预览/AI 浮层 |
| `StatusTag` | lifecycle 语义色（draft 黄/published 绿/deprecated 灰） | 本体/能力/应用 |
| `ConfirmDialog` | 不可逆操作确认 | 发布本体/发布能力/删除（v0.2） |
| `JsonField` | 受控 JSON 文本域（失焦校验 + 错误内联） | 数据源 config/高级映射 |
| `EmptyState` | 标题+说明+主行动按钮 | 全部列表空态 |
| `ErrorBanner` | `friendly()` 中文化错误 + 重试 | 全部查询错误态 |
| `KeyValueTable` | 属性/详情渲染 | 对象详情/运行时索引 |

---

## 6. 关键用户旅程（新交互下的走查）

**J1 新用户首次使用**
打开 → `/onboarding` 创建「高速运营」→ 跳概览，Checklist 第 1 步高亮 → 点「定义本体」直达本体页。

**J2 建模到发布**
本体页添加 Vehicle（抽屉内行编辑属性）→ 校验（报告内联 0 问题）→ 发布（确认对话框警告不可变）→ 成功：按钮变已发布 ✓、Sidebar 出现 ●、Checklist 第 2 步打勾。

**J3 数据接入闭环**
数据页向导连 static 源（资产表格预览）→ 投影构建器双栏映射（自动推断初稿）→ 运行 → toast `2 对象/0 关系/2 变更` → 点「查看变更流」→ 业务数据页 ChangesDock 已展开并显示 2 条。

**J4 能力调用闭环**
能力页 → recharge 行「调用」→ TargetPicker 搜 `V001` → ArgForm 数字框填 100 → 执行 → 结果面板显示 balance 50→150 变更行 → 点 `Vehicle/V001` → 对象详情抽屉确认新值。

**J5 应用消费**
应用详情 → 视图「打开」→ 抽屉渲染车辆表格 → 点某行引用 → 对象详情 → 「问 AI」FAB 预填"Vehicle/V001 的余额为什么这么低？"。

---

## 7. 迁移策略（四阶段，全程可回退）

| 阶段 | 内容 | 验收 |
|------|------|------|
| P0 脚手架 | Vite+tokens+api 层+Shell+路由骨架；`/legacy` 保留旧控制台；api.py 托管 dist | Shell 可导航，健康徽标活 |
| P1 高价值页 | 本体（发布门）+ 业务数据（主从+变更坞） | J2/J3 走通；e2e 契约不破 |
| P2 构建域 | 数据向导+映射构建器；能力三表单+参数化调用 | J4 走通 |
| P3 收尾 | 应用二级路由、AI 页+FAB、审计、概览 Checklist；删 `/legacy` | J1/J5 走通；Playwright 冒烟入 CI |

每阶段结束跑 `tests/manual/e2e_probe.py`（32 项 API 契约）+ 新增 Playwright 旅程冒烟。

---

## 8. 风险与对策

| 风险 | 对策 |
|------|------|
| 引入 Node 工具链改变"零构建"部署 | 构建产物 `dist/` 仍由 stdlib http 服务托管；CI 中 `npm run build`；源码变更不要求运行环境装 Node |
| api.py 静态托管需支持 SPA 资产 | P0 内完成（唯一后端改动，~20 行） |
| React Query 缓存与跨页新鲜度 | 严格按 §3.2 失效映射；关键动作（发布/运行/调用）后主动 invalidate |
| 轮询滥用 | 变更流仅在 live 开且 `document.visibilityState==='visible'` 时轮询；页面离开自动暂停 |
| innerHTML 时代的隐式跳转（activateTab）| 全部替换为 router navigate；`ObjectRefChip` 统一出口 |
| 视觉回归 | tokens 原样平移；每阶段截图对比（Keynote 风格基线） |

---

## 9. 工作量估算（供排期参考）

| 阶段 | 内容 | 人日 |
|------|------|------|
| P0 | 脚手架+Shell+api 层+通用件 | 3-4 |
| P1 | 本体+业务数据 | 4-5 |
| P2 | 数据+能力 | 5-6 |
| P3 | 应用+AI+审计+概览+清理 | 4-5 |
| 合计 | | **16-20 人日** |
