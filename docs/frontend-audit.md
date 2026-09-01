# OntoPlatform 前端完整性与可用性审计报告

> 本文件记录**第一轮**（接口正确性 + 功能缺口）修复。
> 界面完整性 / 布局尺寸 / 交互可用性问题的实测证据与优化方案见
> [`frontend-layout-plan.md`](frontend-layout-plan.md)，验收脚本 `tests/manual/layout_probe.mjs`。

> 审计方式：通读 design.txt 全文 + 前后端全部源码；启动真实服务（sqlite 模式），
> 用与前端完全相同的 HTTP 调用跑通「建模 → 发布 → 投影 → 能力 → 应用 → 智能 → 治理」
> 全旅程，逐项核对前端渲染所需字段与后端实际返回。

## 一、结论总览

后端 72 个单测全过、demo 全链路可跑，**领域层质量良好**；问题集中在
**接口适配层（api.py）的 4 个真实缺陷** 和 **Web 控制台（index.html）的
完整性缺口**：页面大多是「定义表单」而非「可用产品」，多个核心特性因
前端状态管理缺陷静默失效。

| 编号 | 级别 | 问题 | 影响 |
|------|------|------|------|
| B1 | P0 | 变更流不持久（网关内存 list，重启清零） | 业务数据页变更流重启后永远为空，与审计页矛盾 |
| B2 | P0 | 应用查询 `expand` 传字符串数组 → 500 | 关系展开功能不可用，前端也未暴露 |
| B3 | P0 | `state/find` 过滤值被 `_parse_scalar` 强转 JSON | `plate__contains=1001` 永远查不到（int in str TypeError） |
| B4 | P1 | `/api/health` 不暴露 LLM 配置状态 | 前端无法预判 Agent 可用性 |
| F1 | P0 | 业务数据页对象详情的关系导航依赖 `S.runtime`，但该页从不加载 runtime | 直接进入该页 → 关系导航静默消失 |
| F2 | P0 | 变更流"实时跟随"每次轮询**整表替换**为增量 | 开启实时后旧事件消失 |
| F3 | P0 | 投影运行提示读 `r.applied`（字段不存在） | 永远显示"已执行"，无真实计数 |
| F4 | P1 | Agent 开箱即坏：平台默认无 LLM，`/agent` 400 英文报错，配置项藏在折叠框 | 第一次点"执行"必然失败且无引导 |
| F5 | P1 | 已发布本体后表单仍可提交 → 后端拒绝（英文 LifecycleError） | 用户困惑；无"如何演进"的解释 |
| F6 | P1 | 无工作空间时所有页签静默无响应 | 新用户无引导 |
| F7 | P1 | 应用页只显示"返回 N 个对象"，不渲染数据表格；视图从不被渲染 | 应用是最终产品面（design.txt §21-23），目前只有定义没有使用 |
| F8 | P1 | workflow 能力创建的字段 schema 全靠猜（`kind/as/ref/properties`） | 实测第一次必错；提示文案只有 kind 枚举 |
| F9 | P2 | 切换工作空间残留上一空间的查询结果/调用输出/变更序号 | 数据串页 |
| F10 | P2 | 页签命名违反 design.txt §42（"运行时/智能"是技术词） | 应为业务语言（AI 助手/业务数据） |

## 二、问题详情与证据

### B1 变更流不持久（实测）

`KernelRuntimeGateway._changes` 是进程内 list。sqlite 模式下语义状态跨重启保留，
但变更日志清零。实测：重启后 `vehicles: 2`（状态在）、`changes: 0`（日志丢）、
`audit: 15`（审计在）——三个"历史"视图互相矛盾。
违反 design.txt §29「Audit 利用 Kernel 的 Change」的观察链假设。

### B2 expand 500（实测）

`POST /applications/{aid}/queries` 带 `"expand": ["passes"]`：
`AddQuery` 原样存入 → `QuerySpec.to_dict` 执行 `dict("passes")` →
`ValueError: dictionary update sequence element #0 has length 1; 2 is required` → 500。
模型只接受 `[{relation, direction}]`，但没有任何一层做归一化，前端也完全没暴露 expand。

### B3 contains 过滤失效（实测）

api.py `do_GET` 对**所有** query 参数做 `_parse_scalar`（JSON 解析）。
`plate__contains=1001` → `1001` 变成 int → 内核 `expected not in actual`
对字符串属性抛 TypeError → match 返回 False → 永远 0 结果。
而 `balance=100`（数值 eq）又依赖这个 JSON 解析。需要按操作符区分。

### F1-F3 前端状态缺陷（代码核实）

- `loadRuntimeTab()` 只拉 ontology，从不拉 `/runtime`；`showObject()` 读
  `S.runtime.relations` 决定是否渲染关系导航 → 直接进该页时 `S.runtime` 为 null。
- `refreshChanges()` 拉 `after=lastSeq` 的**增量**后调 `renderChanges(events)`
  整表替换 → 实时模式下历史消失。
- `runProjection` 的 toast 用 `r.applied ?? "已执行"`；API 实际返回
  `objects_upserted / links_asserted / changes`。

### F4 Agent 无 LLM 时不可用（实测）

`RunAgent` 在 `provider is None` 时抛 `ValidationError`（400，英文）。
前端 LLM 配置在折叠 `<details>` 里，且无任何预检提示。
design.txt §24 要求 AI 是"平台的自然语言入口"，现状是入口第一次点击即失败。

### F5 已发布本体的 UI 矛盾

领域模型正确实现了"published 不可变"（`Ontology.upsert_object` 抛错，demo 验证），
但前端：发布后表单照常可填可提交（点了才报英文错）、"发布到运行时"按钮仍可点
（409）、无任何"已锁定 + 如何演进"的解释。

### F7 应用页没有"使用"面

design.txt §21-23：Application 是面向最终用户的产品，View 是 Ontology → UI 的投影。
现状：查询运行后只显示 `「alias」返回 N 个对象` banner；视图列表只有定义行，
没有"打开视图"看数据的能力。业务用户在应用页看不到任何业务数据。

### F8 workflow schema 靠猜（实测踩坑）

创建 workflow 能力时，提示只写了 7 种 kind 枚举。实际字段契约是
`kind / as / ref / properties / object_type / filter / relation / direction /
function / target / args / list / name / value`，`{{path}}` 只允许
`args/target/steps/vars` 四个根。第一次手写必错（`step` vs `kind`、
`property` vs `properties`、`alias` vs `as`）。

## 三、修复方案

### P0 正确性（本次已实施）

1. **B1**：`KernelRuntimeGateway` 增加可选 `change_log_path`；sqlite 运行时按
   workspace 落盘 `changes_<wid>.jsonl`（append-only），启动时回放恢复 `_seq/_changes`。
   memory 运行时行为不变。
2. **B2**：`QuerySpec` 增加 `_coerce_expand`：字符串 → `{relation, direction:"out"}`；
   `AddQuery` 与 `from_dict` 双入口归一化。
3. **B3**：api.py `state/find` 按操作符解析：`contains/in` 强制保留原始字符串，
   其余保留 JSON 标量解析（数值 eq/gt/lt 仍可用）。
4. **F1**：`loadRuntimeTab` 先拉 `/runtime` 存入 `S.runtime`（类型下拉也改用
   runtime 的已发布类型），失败再回退 ontology。
5. **F2**：`S.changes` 累积（上限 200 条），`renderChanges(S.changes)` 全量渲染。
6. **F3**：toast 显示真实计数 `N 对象 / M 关系 / K 变更`。
7. **F9**：`switchWorkspace` 清空 `S.changes/S.lastSeq` 与各输出区、查询输入。

### P1 可用性（本次已实施）

8. **B4+F4**：`/api/health` 返回 `llm_configured`；顶栏显示 LLM 徽标；
   智能页未配置 LLM 时显示常驻警告条；`runAgent/runAppAgent` 提交前预检并给出中文引导。
9. **错误信息中文化**：`friendly()` 映射常见错误（LLM 未配置 / 本体已发布不可变 /
   生命周期非法 / 运行时未就绪 / 无本体 / 校验失败）。
10. **F5**：本体发布后禁用创建/添加/发布按钮 + 显示"已发布不可变"警告条。
11. **F6**：无工作空间时概览页显示创建引导，其它页签点击提示"请先创建工作空间"。
12. **F7**：应用页查询结果渲染为对象表格（含展开关系分组）；每个视图增加
    「打开」按钮 → 按视图定义拉取数据渲染（table/card 直渲染，chart/map v0.1
    以表格呈现并注明）；对象引用可点击跳转业务数据页详情。
13. **F8**：能力创建增加「插入模板」按钮（python/workflow/agent 三份可运行模板，
    来自 demo.py 验证过的写法）；提示文案补全字段契约。
14. **投影创建增加「生成模板」**：按所选数据源的资产 + 本体对象类型生成映射骨架。
15. **F10**：页签更名「智能→AI 助手」「运行时→业务数据」。

### P2 记录在案（本次不实施）

- 本体版本化 / `revise`·`deprecate` 的 API 与 UI 流程（v0.1 用"新建工作空间"过渡）；
- 对象/工作空间删除；find 分页；投影映射可视化构建器；chart/map 真实渲染；
- 应用查询结果在视图内联分页；多本体 per workspace（v0.2）。

## 四、验证

- `python -m pytest tests/ -q` 全绿（含新增回归：expand 归一化、contains 过滤、
  变更日志持久化）；
- `tests/manual/e2e_probe.py` 全旅程 27 项断言全过；
- `examples/traffic/demo.py` 端到端输出正常。
