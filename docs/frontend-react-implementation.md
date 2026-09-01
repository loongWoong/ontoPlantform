# React 前端重构实施记录

> 范围：把 1495 行单文件 vanilla-JS 控制台重构为 React + TypeScript + Vite 单页应用，
> **并保持 `python -m onto_platform.interfaces.api` 一条命令启动**（运行期零 Node）。
> 对照基准：`design.txt` §41/§42、`docs/frontend-layout-plan.md`（布局验收编号 L0–L5/C5）、
> `docs/frontend-react-review.md`（上一轮评审的 P0/P1 清单）、后端 `api.py` 实际契约。
> 本文记录**已实现内容 + 实测证据 + 明确未完成项**，不写愿景。

---

## 1. 架构与一键启动

```
frontend/                         源码（React 18 + TS strict + Vite）
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
python -m pytest tests/ -q               # 79 passed（后端契约未被破坏）
node tests/manual/react_layout_probe.mjs http://127.0.0.1:8123 9333   # 0 项未达标
python tests/manual/e2e_probe.py         # 后端契约探针（全 PASS）
```
