# React 前端重构实施记录

> 范围：把 1495 行单文件 vanilla-JS 控制台重构为 React + TypeScript + Vite 单页应用，
> **并保持 `python -m onto_platform.interfaces.api` 一条命令启动**（运行期零 Node）。
> 对照基准：`design.txt` §41/§42、`docs/frontend-layout-plan.md`（布局验收编号 L0–L5/C5）、
> `docs/frontend-react-review.md`（上一轮评审的 P0/P1 清单）、后端 `api.py` 实际契约。
> 本文记录**已实现内容 + 实测证据 + 明确未完成项**，不写愿景。

---

## 1. 架构与一键启动

```
frontend/                         源码（React 19 + TS strict + Vite 7）
  src/api.ts        与后端 REST 契约一一对应的类型化客户端 + 中文错误友好化
  src/store.tsx     全局状态：工作空间 / 连接 / 模型选择 / Toast / 确认框 / hash 路由
  src/ui.tsx        展示原语：Card Field Btn TableWrap Drawer Timeline RowEditor
                    FilterRowEditor JsonField Tag Empty Loading（尺寸全走令牌）
  src/views/*.tsx   8 个页面 + AiDrawer
  vite.config.ts    outDir = ../src/onto_platform/interfaces/web/dist
        │
        ▼  npm --prefix frontend run build
src/onto_platform/interfaces/web/dist/     构建产物（已提交进仓库）
        │
        ▼  python -m onto_platform/interfaces.api
api.py  _serve_web()：非 /api 路径 → dist 内解析 → 未知路径回落 dist/index.html（SPA 路由）
```

- **运行期不需要 Node/npm**：`dist/` 随包发布；`pyproject` 的 `package-data`
  打包 `web/dist/*` 与 `web/dist/assets/*`；`.gitignore` 对 `dist/` 做了定向豁免
  （`!src/onto_platform/interfaces/web/dist/**`），因此 clone 下来直接跑就是新版界面。
- **没构建也不能静默降级**：`serve()` 检测 `web/dist/index.html`，缺失时打印
  `WARN web/dist not found - serving the legacy console.`；
  `GET /api/health` 新增 `console: "react" | "legacy"` 字段，可被监控/探针断言。
- **旧版 `web/index.html` 只作兜底**，不再新增功能。
- 路径穿越防护：静态文件解析后校验 `resolve()` 仍在 `dist` 根内，
  实测 `/../../Windows/win.ini`、`%2e%2e%2f…pyproject.toml` 均回落到 `index.html`，不泄露文件。

`/api/health` 实测：

```json
{"status":"ok","version":"0.1.0","llm_configured":false,"backend":"sqlite",
 "runtime_store":"sqlite","llm_provider":"none","console":"react"}
```

设置模型也可以只用环境变量（本轮新增，见 §4.4）：`ONTO_LLM_BASE_URL` /
`ONTO_LLM_MODEL` / `ONTO_LLM_API_KEY` → `llm_configured: true`、
`llm_provider: "OpenAICompatProvider"`。

---

## 2. 信息架构（design.txt §41）

左侧 232px 常驻导轨（可折叠到 64px）+ 右侧自适应功能区 + 56px 吸顶工具条 +
右下角「问一问」抽屉（Ctrl/⌘ K）。

| 分组 | 页面 | 关键交互 |
|------|------|----------|
| — | 工作台 | 五步主流程（定义→发布→同步→能力→应用），真实计数 + 下一步动作 |
| 业务世界 | 本体 | 对象/关系/术语（含别名）行编辑器、校验、发布前二次确认 |
| 业务世界 | 数据 | 数据源与预览抽屉、投影映射行编辑器、同步前确认（会改业务状态） |
| 业务世界 | 能力 | python/编排/智能目标三种实现 + 模板、按 `inputs` 自动生成的执行表单、改动清单 |
| 业务世界 | 应用 | 视图/查询/智能体/挂载能力；**定义区与结果区分离**，改定义不清空结果 |
| 业务世界 | 对象实例 | 过滤查询、详情抽屉（属性/关系/相关记录）、变更流水实时跟随 |
| 平台 | AI 助手 | 检索问答（明确注明不调模型）+ 目标式智能体（推理链时间线） |
| 平台 | 设置 | 模型来源、工作空间、系统自检、操作记录、关于 |

**路由**：`#/w/<workspaceId>/<page>?k=v`。工作空间进 URL，链接可分享；
`?type=` `?ref=` `?app=` `?tab=` 为页内深链，刷新后状态可复现。
旧的 `#/<page>` 形式继续兼容（回落到本地记住的工作空间）。

---

## 3. 量化验收（真实浏览器，非自评）

`tests/manual/react_layout_probe.mjs`：自建夹具工作空间（本体 + 投影 + 已发布能力 +
应用 + 一个对象挂 3 条关系），8 个页面 × 3 种视口（1440/1280/1024）在 headless Edge
里逐项测量，**夹具创建失败即以退出码 2 终止**（不允许"因为没数据所以没失败"的假绿）。

```bash
python -m onto_platform.interfaces.api --port 8123
msedge --headless=new --remote-debugging-port=9333 --user-data-dir=… about:blank
node tests/manual/react_layout_probe.mjs http://127.0.0.1:8123 9333
```

本轮实测（24 个页面×视口组合，全部通过）：

| 指标 | 结果 | 门槛 |
|------|------|------|
| 整页横向滚动 | 0px（全部页面 × 全部视口） | ≤1px |
| 顶部固定条占高 | 56px | ≤60px |
| 侧栏 | 232px，`position: sticky`，主区从 232px 起 | 常驻导轨 ≤260px |
| 字段常驻 label | 100%（如应用页 8/8、本体页 5/5） | 100% |
| 内联 `style` | 0 处 | 0 |
| 表格横向滚动容器 / 吸顶表头 | 全部满足 | 必须 |
| 14 列宽表压力 | 表宽 3665px，页面横向滚动 **0px**，表头吸顶 | 不撑破页面 |
| 重复提交（连点 3 次提交） | 创建 **1** 个工作空间 | 恰好 1 |
| 结果被清（跑查询后新增定义） | 结果行数 1 → 1 | 不得清空 |
| 关系导航并发度 | 3 次请求跨 **5ms**，串行累计 14ms | span < sum×0.8 |

探针的度量口径调整过三处，均为"规则本身测错了"而非放宽门槛，并在脚本里注明：
input/textarea 的 `scrollWidth` 由内容决定（不计入溢出）；14px 勾选框不算过窄控件；
`align-items: end` 的行按底边基线判断、单列栅格与刻意竖排不算"表单行换行"；
带表单/按钮的空状态属于操作区，不计入"大块提示条"。

---

## 4. 对上一轮评审（`frontend-react-review.md`）的逐条处置

### 4.1 P0 功能缺陷 —— 全部修复并实测

| 编号 | 问题 | 处置 | 证据 |
|------|------|------|------|
| 1.1 | 演示模式脚本工具名全错（`describe`/`find_objects`/`final` 后端都不存在） | 重写为后端真实工具表：`describe_ontology` → `find` → 顶层 `{"think","answer"}`；无已发布类型时自动跳过 `find` 那步 | 直接打 `/agent`：`ok=true`，3 步，观察分别是本体索引与真实对象 `Probe/P001` |
| 1.2 | `LS.nav` 一键两用，页签记忆永不生效 | 拆成 `onto.nav`（页签）与 `onto.nav-collapsed`（折叠） | 探针断言 `onto.nav` 存的是页签名而非 `open/collapsed` |
| 1.3 | 切工作空间不清空 `seq/events/query/openRef`，跨空间污染 | 工作空间变化时重置查询条件、结果、抽屉、变更游标与列表 | 代码路径 `useEffect(…, [wid])`；变更游标移入 ref 后归零 |
| 1.4 | 设置页宣传未实现的 `ONTO_LLM_*` 环境变量 | **实现它**：`api.provider_from_env()` 在 `main()` 里读取并 `configure_llm` | 带环境变量启动 → `llm_configured: true`、`llm_provider: OpenAICompatProvider` |
| 1.5 | 顶栏模型徽标只看本地选择，忽略服务端事实 | `llmOk = llm.mode !== "platform" \|\| llmReady` | 代码 + 顶栏实测 |

### 4.2 结构性偏差

| 编号 | 处置 |
|------|------|
| 2.1 失效一致性 | **部分修复**：能力发布、应用发布后调用 `refreshRuntime()`，侧栏计数与下一步提示跟随事实。**未引入 TanStack Query**（见 §5） |
| 2.2 wid 不在 URL | **已修复**：`#/w/<wid>/<page>`，URL 优先于本地记忆；探针断言点导航后 URL 仍带工作空间 |
| 2.3 路由出口不统一 | **已修复**：全部经 `go()`；应用选中项写入 `?app=`，刷新后恢复（探针断言） |
| 2.4 全局 DetailDrawer | **未做**（P3）。当前"打开对象"跳到对象实例页并定位，上下文由 URL 承载，返回可回退 |

### 4.3 健壮性

| 编号 | 处置 |
|------|------|
| 3.1 `useLoad` 死代码 | `alive` ref 现在真正用于卸载后不 setState；`[...deps, nonce]` 保留（`useLoad` 的公开签名需要） |
| 3.2 `useAction` 吞异常 | 保留（调用方都是 `void run()`，错误经 toast 呈现），但**写清注释**并说明 `ConfirmDialog` 依赖"抛错不关闭"；删掉 `void toast;` 死代码 |
| 3.3 无 ErrorBoundary | **已加**：渲染异常降级为可读错误 + 重载/就地重试 |
| 3.4 变更过滤用 `JSON.stringify().includes()` | 改为对 `object/source/target/ref` 精确等值匹配，`V1` 不再误命中 `V11` |
| 3.5 `buildFilters` 语义错误 | `in` 发送数组（逗号/顿号分隔）；数字推断改为只在完整十进制时转换，`"00123"` 保持字符串 |
| 3.6 轮询计时器抖动 | `seq` 移入 ref，`pull` 身份稳定；`pending` 守卫避免请求堆积 |
| 3.7 死代码 | 清理（`void pname;` 改为把投影名写进同步回执；`void wid;`/`void r;` 删除） |
| 3.8 前端自动化测试 | **部分**：探针可复现、会失败退出，但仓库没有 CI 基建，未接流水线；无 Vitest 单测（见 §5） |

### 4.4 部署与工程

| 编号 | 处置 |
|------|------|
| 4.1 端口文档不一致 | vite 代理改为 `127.0.0.1:8100`（`onto-api` 默认端口），README 同步 |
| 4.2 双控制台并存 | 旧版仅兜底；`/api/health.console` + 启动 WARN 让降级可见 |
| 4.3 wheel 可能没有前端 | `dist/` 提交进仓库 + `.gitignore` 定向豁免 + 启动 WARN；不再有"静默发布无前端包" |
| 4.4 未提交 git | 本轮改动待提交（`frontend/`、`dist/`、`api.py`、`pyproject.toml`、探针与文档） |

### 4.5 视觉协议

- 图标 `⚡`/`⚙`（emoji presentation）→ 换为文本呈现的 `↯` / `☰`。
- 工作空间名从三处减到两处（顶栏下拉 + 侧栏底部，后者可点进设置）。

---

## 5. 明确未完成（诚实清单）

| 项 | 说明 |
|----|------|
| 服务端状态缓存层 | 仍是各页 `useLoad` + 手动 `refreshRuntime`。跨页失效靠显式调用，页面继续增多时会露馅；正解是 TanStack Query + 失效映射表 |
| 全局 DetailDrawer | 对象详情仍是页面级抽屉；从应用页跳转会离开原页（URL 可回退） |
| 前端自动化测试 | 无 Vitest/Playwright；探针需要手起 headless 浏览器，未进 CI（仓库当前无 CI 基建） |
| A1 成员管理 | 后端有 `AddMember` 用例但无路由；界面只在设置页注明"后续版本提供" |
| A2 deprecate/revise | 后端生命周期能力未在界面暴露 |
| chart/map 视图渲染 | 仍用表格呈现数据，界面已明确注明 |

---

## 6. 复核命令

```bash
npm --prefix frontend run build          # 构建（tsc strict + vite）
python -m pytest tests/ -q               # 336 passed（后端契约未被破坏）
node tests/manual/react_layout_probe.mjs http://127.0.0.1:8123 9333   # 0 项未达标
python tests/manual/e2e_probe.py         # 后端契约探针（全 PASS）
```

---

## 7. React 19 升级与智能视图联调（2026-10）

### 7.1 React 19

`frontend/package.json` 已升级到：

| 包 | 版本 |
|----|------|
| `react` / `react-dom` | `^19.3.1` |
| `@types/react` / `@types/react-dom` | `^19.3.0` |
| `vite` | `^7.3.0`（实际安装 7.3.7） |
| `@vitejs/plugin-react` | `^5.0.0`（实际 5.2.0） |

升级中唯一源码级破坏是 React 19 移除了全局 `JSX` 命名空间：
`App.tsx` 的视图表从 `Record<string, () => JSX.Element>` 改为
`Record<string, () => ReactElement>`（显式 `import type { ReactElement }`）。
`tsc --noEmit`、Vitest 46 项、Python 全量 310 项均通过。

**Windows 构建临时目录问题**：Vite 7/esbuild 在该机器上删除
`C:\Users\...\AppData\Local\Temp\esbuild-*` 会报 `Access is denied`。构建脚本改为
`frontend/scripts/vite-build.mjs`，在动态 `import("vite")` 之前把 `TMP`/`TEMP`
指向仓库内 `frontend/.vite-tmp`；`npm run build` 不再依赖外部 TEMP 权限。

### 7.2 智能视图（本体驱动 UI 接入主平台）

新增页面 `frontend/src/views/SmartUI.tsx` 与导航项「业务世界 → 智能视图」。
它不复制运行时 CSS，而是用**sandbox 隔离 iframe** 挂载独立构建的 OpenUI 运行时：

```
GET  /smart-ui/?workspace=<wid>&api=<console-origin>
     └─ web/smart-ui/（Vite base=/smart-ui/，由 runtime-web 的 build:platform 生成）
GET  /api/workspaces/{wid}/smart-ui/meta
POST /api/workspaces/{wid}/smart-ui/compile
POST /api/workspaces/{wid}/smart-ui/actions/{actionId}
```

iframe 不再授予 `allow-same-origin`：浏览器给它不透明源（`self.origin === "null"`），
父页面与 iframe 互相读不到 DOM/storage。控制台在 iframe `load` 和子页面的
`onto.smartui.ready` 消息上，用 `postMessage` 把 bearer token 交给运行时内存；运行时
带上该 token 跨源调用 API（CORS 已允许 `Content-Type / Idempotency-Key /
Authorization`）。令牌不写进 iframe URL，也不依赖 `sessionStorage` 同源可见。

后端 `interfaces/smart_ui.py` 是唯一适配层：

1. 平台已发布本体 + 当前实例 → 通用语义层：自动选择有实例的焦点对象，推导身份键、
   名称/时间/数值/状态字段与关联对象；只补 `is_abnormal` 这一个派生状态别名；
2. 平台能力（非 agent、已发布，含参数化能力）→ 引擎 `Action`，调用仍走
   `platform.capabilities.invoke`，保留授权、执行账本和审计；能力 `inputs` 转成
   `ActionParameter` 进入 L4 `ActionButton`，后端按声明校验并转换 `args`；
3. 每次 compile 从当前工作空间投影实时构造 `viewPayloads`（画像、关系、时间线、
   位置、统计、状态、解释、动作），不是引擎示例 fixture；焦点/关联对象有经纬度时
   提供 `view_location`，没有坐标时不生成地图；
4. 任何行业本体只要有已发布对象类型和实例即可编译；不要求车辆/车牌/收费站。
   没有实例时 `meta.ready=false` 并返回下一步指引，不伪造数据。
5. 角色从 actor 的真实工作空间成员关系推导：`meta.actorRole` + 只含不高于该角色的
   `roles`，编译与动作都做“请求角色 ≤ actor 角色”校验；写动作只对 `editor/owner`
   开放，避免把 `viewer` 广告成可执行身份。

运行时 `runtime-web/src/platform.ts` 提供 meta/compile/action 客户端；`bridge.ts` 负责
父子 postMessage 令牌握手，`platformAction.tsx` 提供参数化动作执行上下文。
`toolProvider.ts` 的 `ToolHooks.onMutation` 继续处理无参数动作，动作失败会抛错而不是
显示成功。所有动作都先弹出应用内确认弹窗（`ConfirmActionDialog`，含参数输入、类型
校验、错误提示），不再使用 `window.confirm`；动作请求带幂等键，成功后重新 compile
刷新平台数据。

浏览器实测（Chrome headless + CDP）：

- 控制台 `#/w/7c33935df1d1/smartui`：iframe sandbox 为
  `allow-scripts allow-forms allow-popups`，子页面 `self.origin === "null"`，
  读取父页面 `document`/`sessionStorage` 均抛 `SecurityError`；主页面无
  `allow-scripts + allow-same-origin` 沙箱告警；
- 实时数据：`鲁C56789`、84 个实例、3 个可执行动作（含参数化「状态处置」）；
- 点击「站点日报」：确认弹窗无参数，确认后 `daily_report` POST 200；
- 点击「状态处置」：弹窗渲染 `status` 输入；空值被前端拦截为「请填写…」；
  填 `busy` 后 POST `set_status` 200，请求带 `Authorization: Bearer <token>`
  （令牌由父页面 postMessage 传入），随后 compile 刷新；
- 0 个 iframe console error，父页面 0 security 告警；
- 角色从 `owner` 切到 `editor` 会重新编译，`congestion_scan` 仍可用；
  `viewer` 只能请求 `viewer` 视角且在编译期被裁掉写动作（L3/权限）；实际平台鉴权
  仍以登录 actor 为准，运行时 `role` 不能提升后端权限。

### 7.3 已修复项与剩余边界

| 项 | 说明 |
|----|------|
| 非 toll 本体的通用性 | 已由通用语义层覆盖：焦点对象、身份键、时间/数值/状态字段、关联对象都从本体与实例推导；不再要求 `plate`。自动化测试含 `Device/Site` 无车牌本体、高速 `Vehicle`、AI 建模 `PassageTransaction`，以及金融租赁 `LeaseContract`（合同/租赁物/账单/预警）场景 |
| 参数化动作 | 已修复：能力 `inputs` 经适配层 → 引擎 `Action.parameters` → L4 `ActionButton` 的 `parameters` JSON 进入运行时；确认弹窗按 `type` 渲染输入并校验，后端 `_action_args` 拒绝未知/缺失/类型不符的 `args`。无参数动作仍走原 mutation 路径 |
| 动作后数据刷新 | 已修复：动作成功（`ok !== false`）后运行时以当前问题重新 compile，刷新平台真实状态；动作请求带幂等键 |
| iframe 沙箱告警 | 已修复：去掉 `allow-same-origin`，运行时在 opaque origin 中执行；令牌经 postMessage 传入内存，API 走 CORS。浏览器验证父/子 DOM 与 storage 互相不可读，页面无沙箱逃逸告警 |
| 确认弹窗可测试性 | 已修复：`window.confirm` 换成应用内 `role="dialog"` 弹窗，包含参数输入与错误区；CDP e2e 覆盖取消、空值校验、成功执行 |
| 平台角色 vs 运行时角色 | 运行时默认使用 `meta.actorRole`，切换只影响编译视角；后端校验“请求角色 ≤ actor 角色”，真实写权限仍由平台 actor 决定 |

---

## 8. 四个管理域的删除能力（2026-10）

控制台此前只有新增与查询；本次补齐本体 / 数据 / 能力 / 应用的删除链路，UI 一律走应用内
`confirm` 弹窗，服务端用 `DELETE` 路由和用例层引用检查，不把删除逻辑放进视图。

### 8.1 API 与客户端

| 域 | 路由 | 行为 |
|----|------|------|
| 本体 | `DELETE /api/workspaces/{wid}/ontology/objects/{name}` | 删除草稿对象；被关系/术语/能力/映射/应用引用时 400 |
| 本体 | `DELETE .../ontology/relations/{name}` | 删除草稿关系；被映射/查询展开/术语引用时 400 |
| 本体 | `DELETE .../ontology/vocabulary/{term}` | 删除草稿术语 |
| 数据 | `DELETE .../datasources/{sid}` | 仍被映射引用时 400 |
| 数据 | `DELETE .../projections/{pid}` | 删除映射及其同步运行记录；运行时对象保留 |
| 能力 | `DELETE .../capabilities/{cid}` | 草稿直接删；已发布按最近发布本体重编译安装剩余能力，再解绑应用并切新 Release；更新中的草稿本体保持不变 |
| 应用 | `DELETE .../applications/{aid}` | 删除应用聚合 |
| 应用 | `DELETE .../applications/{aid}/views/{alias}`（queries / agents 同理） | 移除组成项；查询被视图引用时 400 |
| 应用 | `DELETE .../applications/{aid}/capabilities/{cid}` | 仅取消挂载 |

`frontend/src/api.ts` 新增 `del()` 与 `DeleteResult`，各页删除按钮调用类型化客户端；
`Access-Control-Allow-Methods` 增加 `DELETE`，`/api/health.features` 增加
`resource.delete`，客户端可探测服务端是否支持删除。

### 8.2 交互位置

- 本体：对象表、关系表新增「删除」操作列，术语 chip 内的 `×`；已发布本体隐藏删除入口，
  提示先「修订」。
- 数据：数据源与映射表格的「操作」列；映射删除后同步结果 banner 立即清空。
- 能力：能力表格操作列；已发布能力的确认弹窗明确说明运行时重发与应用解绑。
- 应用：页头「删除应用」；视图/查询/智能体行内「移除」，挂载能力 chip 的 `×`。

### 8.3 验收

`tests/test_delete.py` 覆盖四个域的引用保护、级联修复、已发布能力重发与 Release、
HTTP DELETE/CORS（8 项）；`npm --prefix frontend run build` 通过 `tsc --noEmit`，
控制台 46 项单测通过；全量后端 336 项通过。

真实浏览器（CDP）实测：发布态不显示删除入口 →「修订」出草稿后出现删除列；删除仍被
能力引用的对象被服务端拒绝并提示；删除术语、关系、无引用对象成功；删除映射后同步回执
清空，再删除数据源成功；在存在更新草稿时删除已发布能力成功（运行时动作消失、应用解绑）；
应用内视图/查询/智能体逐个移除后整体删除应用成功。
