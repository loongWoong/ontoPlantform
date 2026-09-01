# frontend/ React 控制台实现评审

> 评审对象：`frontend/`（React 18 + TS + Vite，约 3900 行，未提交 git）。
> 对照基准：`docs/frontend-redesign-react.md`（设计方案）、`docs/frontend-layout-plan.md`（布局验收）、
> `docs/completeness-review.md`（A1/A2/A3 缺口）、后端 `api.py` 实际契约。
> 验证方式：通读全部源码 + `tsc --noEmit`（通过）+ 与后端 agent_runner/api.py 逐字段比对。

## 0. 总体评价

整体质量**高于**被替换的 1495 行单文件控制台：类型化 API 层、strict TS 全过、
统一错误友好化、useAction 防重复提交、发布确认框、能力参数表单按 inputs 声明自动生成
（设计方案中价值最大的点已落地）、工作台五步主流程、Field 常驻 label 兜底 aria。

但存在 **3 个功能性 P0 缺陷**（其中一个让"演示模式"完全不可用）、
**4 处与设计方案的结构性偏差**（产生真实的重复请求与状态过期问题）、
以及若干健壮性隐患。逐条如下。

---

## 1. P0 功能缺陷（实测代码路径确认）

### 1.1 演示模式 Agent 脚本工具名全部错误 —— 演示模式不可用

`store.tsx toChoice()` 的 demo 脚本发送：

```ts
{ tool: "describe" }      // 后端无此工具
{ tool: "find_objects" }  // 后端无此工具
{ tool: "final", args: { answer } }  // 后端无此工具；结束应答是顶层 {"answer": ...}
```

后端 `agent_runner.py` 实际工具表是 `describe_ontology / describe_object / find /
related / get / invoke_capability`，结束方式是 `{"think": ..., "answer": ...}`（无 `final` 工具）。

**实际运行结果**：三步全部命中 `unknown tool` 观察 → 脚本耗尽后 EchoLLMProvider
返回 `{"answer": "无法继续推演"}` → 前端显示"无法继续推演"且标记**已完成**
（`AgentRun.ok` 默认 True）。演示模式是"无 LLM 用户"的主入口（设置页、TopBar、
LlmPicker 三处都在推它），当前是坏的。

### 1.2 `LS.nav` 一个键两个用途 —— "记住上次页签"永远失效

- `App.tsx`：折叠状态持久化 `localStorage.setItem(LS.nav, "collapsed"|"open")`，**每次挂载都会执行**；
- `store.tsx readHash()`：无 hash 时用 `LS.nav` 恢复上次页签。

时序：Provider 的 `readHash()` 先读 → Shell 的 useEffect 后写 `"open"`。
于是任何一次加载后 `LS.nav` 都变成 `"open"`，下次刷新 `readHash` 读到 `"open"`
→ 不是合法页签 → 回落工作台。**恢复上次页签功能从未生效过**；折叠一次后亦然。

### 1.3 切换工作空间不重置变更流水 —— 跨空间数据污染

`StateBrowser.tsx ChangesPanel`：`seq`/`events` 是组件本地 state，
`useEffect(() => void pull(), [wid])` 拉取时**沿用上一个空间的 seq**；
切换工作空间后：旧空间的事件仍显示在列表里、新空间只显示 `after=旧seq` 之后的增量。
`StateView` 的 `type/query/openRef` 同样跨空间残留（查询一个新空间可能不存在的类型）。
旧版控制台在 `switchWorkspace` 里显式清空了这些状态，React 版**回归**了此问题。

### 1.4 设置页宣传了不存在的环境变量

`Settings.tsx`："服务端也可以用环境变量固定：ONTO_LLM_BASE_URL / ONTO_LLM_MODEL /
ONTO_LLM_API_KEY"。后端 `api.py`/`bootstrap.py` **没有实现任何 ONTO_LLM_* 读取**。
用户按文案配置后不生效。

### 1.5 TopBar 模型徽标逻辑忽略服务端事实

`llm.mode !== "platform" ? "ok" : "warn"`：平台侧已配置默认 LLM（`llmReady=true`）
时仍显示黄色"平台默认"。健康检查专门返回了 `llm_configured`，此处没用上。

---

## 2. 与设计方案的结构性偏差（有真实代价）

### 2.1 没有服务端状态缓存层 —— 重复请求 + 失效不一致

方案规定 TanStack Query + 明确的失效映射；实现是手写 `useLoad`（每页各自拉）
+ `store.refreshRuntime`（4 个请求）。后果：

| 场景 | 实际请求 |
|------|---------|
| 进入工作台 | store 4 个 + Workbench 7 个 = **11 个请求**，其中 ontology/runtime/capabilities/applications 重复 |
| 发布能力后 | 只 `load.reload()` 能力列表；`refreshRuntime` 未调用 → **侧栏完成度计数、对象类型下拉过期**（只有 Ontology/Data 两页调了 refreshRuntime，Capability/Application 没调） |

失效职责散落在各页，正是方案用"失效映射表"要消灭的问题。

### 2.2 wid 不在 URL 里 —— 深链不可分享

方案：`/w/:wid/ontology`，wid 进路径；实现：hash 只有 `#/state?ref=...`，
wid 只存 localStorage。把链接发给别人/另一台机器，打开的是**他自己的默认工作空间**，
`?ref=Vehicle/V001` 指向错误的世界。React Router 也未使用（手写 hash 路由）。

### 2.3 路由出口不统一

`StateBrowser.tsx:121` 直接 `window.location.hash = ...` 绕过 `go()`；
`Application.tsx` 选中应用只 `setSelected` 不同步 URL（`?app=` 只进不出，
从深链进入后点击列表即丢失深链态）。

### 2.4 应用页"打开对象"跳走丢上下文

`onOpenObject={ref => go("state", { ref })}` —— 从应用查询结果点对象会**离开应用页**。
方案要求全局 DetailDrawer 就地打开（对象详情是全站复用件）。现有 Drawer 是每页局部
state，没有全局堆叠容器。

---

## 3. 健壮性与代码质量

| # | 问题 | 位置 |
|---|------|------|
| 3.1 | `useLoad` 的 `alive` ref 是死代码（赋值后从未读取）；`[...deps, nonce]` 动态展开依赖数组是被 eslint 禁止的模式；loader 闭包过期全靠调用方自觉 | ui.tsx |
| 3.2 | `useAction` 吞掉所有错误（catch→toast，不 rethrow）。ConfirmDialog 依赖"close() 不会执行"这个副作用保持弹窗打开——行为碰巧正确但极隐晦；`void toast;` 死代码 | store.tsx |
| 3.3 | 无 ErrorBoundary：任何渲染异常=整站白屏（现在只有 toast 通道） | 全局 |
| 3.4 | 对象详情抽屉拉**全量** `changes(after=0)` 再 `JSON.stringify().includes(ref)` 过滤——变更日志越大越慢，且 stringify 匹配可能误命中（如 ref "V1" 命中 "V11"） | StateBrowser.tsx |
| 3.5 | `buildFilters`：`"00123"` → number `123` 丢前导零；`in` 操作符发送 `"a,b"` 字符串而非数组，内核 `actual not in expected` 对字符串退化为子串匹配，语义错误 | ui.tsx |
| 3.6 | ChangesPanel 轮询：`pull` 依赖 `[wid, seq]`，seq 每次更新 → interval 销毁重建（计时器重置，节拍漂移）；busy guard 丢帧无提示 | StateBrowser.tsx |
| 3.7 | `PreviewDrawer` 里 `void wid;`、Ontology deps 里 `objectNames.join(",")` 等无效依赖/死代码若干 | 多处 |
| 3.8 | **零前端自动化测试**：方案规定 Vitest + Playwright；现有 `react_layout_probe.mjs` 是手动 CDP 探针（需手起 Edge headless），未进 CI；79 个 pytest 只守护 API 契约，不守护 UI | package.json |

## 4. 部署与工程

| # | 问题 |
|---|------|
| 4.1 | `vite.config.ts` proxy 指向 **8123**，而 `onto-api` 默认端口 **8100**——`npm run dev` 直连会"服务不可达"，文档两处端口不一致 |
| 4.2 | 双控制台并存：legacy `web/index.html`（1495 行）仍是 fallback 且被 pyproject 打包；没有记录下线时间点，长期双维护 |
| 4.3 | `dist/` 被 gitignore（合理），但 pyproject package-data 打包 dist——**CI 若不先 build 就打 wheel，会发布没有前端的包**且无告警（`console: "legacy"` 静默降级） |
| 4.4 | 未提交 git：frontend/ 整目录、api.py dist 托管改动、pyproject 改动均为 untracked/modified 状态，有丢失风险 |

## 5. 视觉协议小项（minimalist-ui）

- NAV 图标 `⚡`(U+26A1) `⚙`(U+2699) 是 emoji-presentation 字符，Windows/Android 常渲染成
  彩色 emoji，违反协议"no emoji"；应换 Radix Icons 或 SVG 原语（`◎◈▤▣⊙✦` 无问题）。
- 工作空间名同时出现在 TopBar 下拉、TopBar `brand-mini` 按钮、SideNav 底部三处，信息冗余。

## 6. 功能缺口延续（对照 completeness-review，本轮未解决）

| 原编号 | 状态 |
|--------|------|
| A1 成员管理无入口 | 仍未暴露（Settings 仅一句"后续版本提供"） |
| A2 deprecate/revise 无入口 | 仍未暴露 |
| A3 术语别名 | ✅ 已解决（Ontology 页有 aliases 输入，逗号/顿号分隔） |
| B4 chart/map 表格呈现 | 维持（UI 有明确注明，可接受） |

## 7. 修复优先级建议

| 优先级 | 项 | 理由 |
|--------|-----|------|
| P0 | 1.1 演示模式脚本工具名 | 主入口功能坏，改 3 行 JSON |
| P0 | 1.2 LS.nav 键拆分（nav 与 collapsed 分键） | 一次 reload 即复现 |
| P0 | 1.3 wid 切换重置 seq/events/query | 数据正确性 |
| P1 | 2.1 失效一致性：Capability/Application 发布后调 refreshRuntime（或引 Query） | 侧栏计数误导用户 |
| P1 | 2.2 wid 进 hash（`#/w/:wid/state?ref=`） | 深链可分享是方案核心承诺 |
| P1 | 3.8 最小前端测试：把 react_layout_probe.mjs 纳入 CI + Vitest 覆盖 store/api 层 | 现在改 UI 全靠手点 |
| P2 | 1.4 删环境变量文案或实现之；1.5 徽标用 llmReady；3.4 变更过滤改服务端；3.5 in 操作符发数组 | |
| P2 | 4.1 统一端口文档；4.3 wheel 构建加 dist 存在性断言 | |
| P3 | 5 图标替换；2.4 全局 DetailDrawer；3.1/3.2 清死代码 | |

## 8. 结论

React 化方向正确、骨架健康（类型层、防重复提交、确认框、参数表单生成都是对的），
但**当前状态不宜作为默认控制台发布**：演示模式坏、页签记忆失效、跨空间数据污染
三个 P0 都是用户第一次使用就会撞上的。P0 三项修复成本都很低（合计约半天），
修完并补上失效一致性与 wid 深链后，即可按 4.2 的计划让 dist 版转正、legacy 下线。
