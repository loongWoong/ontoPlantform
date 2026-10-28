# OntoPlatform

**本体智能应用平台** —— 把 Ontology Runtime Kernel 变成业务人员可以使用的平台：
建模业务世界 → 接入现实数据 → 沉淀业务能力 → 组装应用 → 让 AI 在同一张本体上工作。

与 [ontoRK](../ontoRK)（Ontology Runtime Kernel）的关系：

> **Kernel 是引擎，Platform 是整车。**
> Kernel 只回答五个问题：世界长什么样（describe）、对象是什么（get）、
> 找出对象（find）、对象之间有什么关系（related）、做一件事（invoke）。
> Platform 负责其余的一切：建模、发布、数据接入、能力沉淀、应用组装、
> 语义检索、Agent 编排与治理 —— 并通过**防腐层**把这一切编译成 Kernel 的语言。

## 架构

```
interfaces/        CLI · REST API · Web 控制台（薄适配器，无业务逻辑）
application/       用例层：按用户意图组织（CreateOntology / RunProjection / AskOntology ...）
domain/            七大领域模型 + OntologyCompiler（纯 Python，零依赖）
ports/             RuntimeGateway · DataSource · LLM · Repository · WorkflowRunner
infrastructure/    kernel 网关 · sqlite/memory 持久化 · 连接器 · LLM 供应商 · 执行器
```

七大 domain（每个都有独立的模型与用例）：

| 领域 | 职责 | 关键概念 |
|------|------|----------|
| workspace | 隔离容器 + 成员治理 | Workspace / Membership |
| ontology | 业务世界的概念模型 | ObjectType / Relation / Vocabulary |
| data | 现实数据进入 Kernel | DataSource / Mapping / Projection |
| capability | 会改变世界的行为 | python 实现 / workflow 编排 |
| application | 面向业务人员的视图 | View / QuerySpec |
| intelligence | AI 是横向能力 | SemanticIndex / AskOntology / AgentRun |
| governance | 生命周期 / 访问 / 审计 | Lifecycle / AccessPolicy / AuditLog |

## 三条不变的链路

```
链一（设计 → 运行）  Ontology + Capabilities --OntologyCompiler--> Kernel Schema
链二（数据 → 状态）  DataSource --Mapping/Projection--> SemanticDelta --> Kernel State
链三（语言 → 状态）  用户语言 --Vocabulary/SemanticIndex--> 概念 --> Runtime State
```

## v0.3 控制回路修复

v0.3 不是新增业务页面，而是把“看起来在闭环、其实没有闭环”的地方改成真实状态：

- 执行结果三态：`succeeded / failed / unknown`。`unknown` 不重试，必须有解析动作。
- 幂等键先原子认领再执行。同一键 100 并发只产生一次世界变化和一条执行记录。
- 发布分环：`current` 服务真实流量，`canary` 是可丢弃的影子环；提升要求新鲜健康检查。
- 重启以 `deployment/current` 为事实来源；未提交的部署会被完成或回退。
- SLO 按窗口和样本数计算，空样本显示 `no_data`，不足样本显示 `insufficient`。
- 对账只把投影自己物化的对象算作归属，`authoritative` 模式下才把孤儿算进来。
- Outbox 无消费者记为 `no_subscribers`，失败退避，超过 5 次进死信。
- 工作空间级模型配置，密钥加密存储、只写不读、返回掩码。

验收命令：

```bash
python -m pytest -q                       # 300 passed
python scripts/migrate_v03.py --db <db>   # 先预览，--apply 写入
python scripts/rehearse_v03.py            # 发布/回滚/重启演练
python scripts/e2e_v03.py                 # 16 个真实 HTTP 检查
python scripts/axe_v03.py                 # 可访问性门禁，需 playwright + axe-core
```

逐项证据和残余边界见 [docs/progress-v0.3.md](docs/progress-v0.3.md)。

## 设计原则（design.txt）

1. **Kernel 是唯一事实来源** —— 平台不缓存业务状态；发布 Schema 不动 State。
2. **一切通过能力改变世界** —— Agent 与应用都走 `Capability -> RuntimeGateway -> Kernel`。
3. **平台不 import Kernel** —— 只有 `KernelRuntimeGateway` 一处认识两个世界，kernel 懒加载。
4. **用户看到业务语言** —— 术语表（Vocabulary）把"收费站"翻译成 `TollStation`，
   Ask/Agent 永远不猜表名。
5. **治理内建** —— 生命周期状态机、工作区角色、审计日志是骨架的一部分，不是插件。

## 快速开始

```bash
pip install -e F:/piAgent/ontoRK      # 先装内核
pip install -e F:/piAgent/ontoPlantform
```

### 完整示例（推荐入口）

```bash
python examples/traffic/demo.py
```

一条命令跑通七个领域：建模 → 发布 → 投影 → 能力调用 → 应用视图 → Ask/Agent → 治理。

### CLI

```bash
# 建模并发布
onto workspace create 高速运营
onto --workspace <wid> ontology create traffic
onto --workspace <wid> ontology add-object Vehicle --prop plate:string --prop balance:number
onto --workspace <wid> ontology add-relation passes Vehicle TollStation
onto --workspace <wid> ontology add-term 收费站 TollStation
onto --workspace <wid> ontology publish

# 数据投影
onto --workspace <wid> data add-source 交易库 --kind static --asset vehicles=@rows.json
onto --workspace <wid> data add-projection 主数据 --source <src> --object-mapping '{...}'
onto --workspace <wid> data run-projection <pid>

# 能力（python 源码文件或 workflow）
onto --workspace <wid> capability create recharge --target Vehicle --input amount:number --source recharge.py
onto --workspace <wid> capability publish <cid>
onto --workspace <wid> capability invoke Vehicle.recharge --target Vehicle/V001 --arg amount=90

# 运行时状态 / 提问 / 审计
onto --workspace <wid> state find TollStation --filter status=congested
onto --workspace <wid> ask "济南西收费站为什么拥堵？"
onto --workspace <wid> audit
```

默认库为 `.onto/platform.db`（sqlite）；运行时状态跟随平台存储，
跨进程/跨天仍然在场（`--runtime-store` 可显式指定）。

### REST API + Web 控制台

```bash
onto-api --port 8100
# Web 控制台:  http://127.0.0.1:8100/
# API 根:      http://127.0.0.1:8100/api/health
```

路由覆盖全部用例（workspaces / ontology / datasources / projections /
capabilities / invoke / applications / state / changes / ask / agent / audit），
平台错误映射为 HTTP 状态码（400/403/404/409/502/503）。

Web 控制台是 **React + TypeScript + Vite** 单页应用（`frontend/`），构建产物
提交进包内 `src/onto_platform/interfaces/web/dist/`，因此**运行期不需要 Node**：
`python -m onto_platform.interfaces.api` 一条命令即同时提供 API 与界面
（`GET /api/health` 的 `console` 字段会告诉你是 `react` 还是 `legacy`）。

左侧栏信息架构（design.txt §41），右侧是自适应功能区：

| 分区 | 页面 | 覆盖能力 |
|------|------|----------|
| — | 工作台 | 五步主流程（定义→发布→同步→能力→应用）＋真实计数与下一步 |
| 业务世界 | 本体 | 对象/关系/术语（含别名）编辑、校验、发布（发布前二次确认） |
| 业务世界 | 数据 | 数据源连接与预览、投影（映射行编辑器）、同步（确认后再改状态） |
| 业务世界 | 能力 | python/workflow/agent 三种实现、按 inputs 自动生成的执行表单、改动清单 |
| 业务世界 | 应用 | 视图/查询/智能体/挂载能力；定义区与结果区分离，改定义不清结果 |
| 业务世界 | 对象实例 | 过滤查询、详情抽屉（属性/关系/相关记录）、变更流水实时跟随 |
| 平台 | AI 助手 | 检索问答（不调模型，明确注明）＋目标式智能体（推理链时间线） |
| 平台 | 设置 | 模型来源（平台/OpenAI 兼容/演示）、工作空间、系统自检、操作记录 |

改前端（只有改前端时才需要 Node）：

```bash
npm --prefix frontend install
npm --prefix frontend run dev      # 5173 开发，/api 代理到 127.0.0.1:8100
npm --prefix frontend run build    # 产物写入 src/onto_platform/interfaces/web/dist/
```

布局与交互用真实浏览器量化验收（`node tests/manual/react_layout_probe.mjs`，
需先起服务与 headless Edge）：8 个页面 × 3 种视口，检查横向溢出、控件尺寸、
字段常驻 label、表格滚动容器与吸顶表头、重复提交、结果被清、关系导航并发度。
当前门槛全部通过；实测数字与遗留项见 `docs/frontend-react-implementation.md`。
未构建 `dist` 时后端会打印 WARN 并回落到旧版单文件控制台
（`web/index.html`，仅作兜底，不再新增功能）。

常见后端错误统一映射为中文指引＋下一步动作（未配置模型 / 本体已发布不可变 /
运行时未就绪等）；无工作空间时全站引导创建；对象引用在应用页与对象实例页之间
可跳转。完整可用性审计见 `docs/frontend-audit.md`，重构实施记录见
`docs/frontend-react-implementation.md`。

模型服务也可以只用环境变量固定（无需改代码）：`ONTO_LLM_BASE_URL` /
`ONTO_LLM_MODEL` / `ONTO_LLM_API_KEY`。

### 作为库使用

```python
from onto_platform.bootstrap import PlatformConfig, build_platform
from onto_platform.infrastructure.llm.echo import EchoLLMProvider

platform = build_platform(PlatformConfig(store="memory"))
ws = platform.workspaces.create("高速运营", actor="admin")
platform.ontologies.create(ws["id"], "traffic", actor="admin")
...
answer = platform.intelligence.ask(ws["id"], "admin", "济南西收费站为什么拥堵？")
run = platform.intelligence.agent(ws["id"], "admin", "目标", provider=EchoLLMProvider([...]))
```

LLM 通过 Port 接入：内置 `EchoLLMProvider`（脚本化，测试/演示用）与
`OpenAICompatProvider`（任何 OpenAI 兼容端点）。

## Workflow 能力

不写代码的能力：用 `find / related / get / count / set / set_property / invoke`
七种步骤编排，`{{path}}` 模板引用 `args / target / steps / vars`，别名为 `output`
的步骤即最终输出。Workflow 由平台执行（`GatewayWorkflowRunner`），其中 invoke
步骤回到 Kernel 执行——编排逻辑在平台，世界突变在 Kernel。

`set_property` 是唯一的有界变更原语：工作流可以写回**已存在对象**的属性，
且必须经过网关的校验变更门（属性必须已在本体中定义）——design.txt §49 的
Action → Change → State 闭环由此对无代码用户开放。

## Agent 能力与应用 Agent

- **Agent 能力**（§19）：`implementation.kind = "agent"`，只需一段 `goal`。
  编译为 kernel 函数绑定 `agent://{capability_id}`；运行时由平台侧 runner
  执行（goal + 本体上下文 + LLM），Agent 循环观察世界、调用其它能力行动。
  未配置 LLM 时调用返回干净的错误结果，不会崩溃。
- **应用 Agent**（§50）：`Application = View/Query/Agent`。应用通过
  `add-agent` 挂载命名 Agent 入口（只存引用与目标措辞，领域仍在
  Intelligence），`run-agent` 即可执行。

## MCP 接入（§36/§46）

```bash
pip install "onto-platform[mcp]"
onto-mcp --db .onto/platform.db --workspace <wid>   # stdio JSON-RPC
```

暴露 `workspace_describe / workspace_find / workspace_get / workspace_related /
workspace_ask / capability_invoke / agent_run` 七个工具——MCP 只翻译协议，
逻辑全部留在用例层（§40）。

## v0.1 的有意简化（design.txt §9/§20/§38）

- **Review 步骤**折入 publish（发布即编译 + 校验 + 审计）；
- **Test 步骤**（§20 能力测试）未建——测试由 pytest 套件承担；
- **CreateMapping**（§38）折入 create_projection（投影即映射）。

这些是 v0.1 的边界而非遗漏，升级路径已在治理模型中预留。

## 开发

```bash
python -m pytest tests/ -q        # 79 个测试：compiler / gateway / projection /
                                  # capability / application / governance /
                                  # intelligence / persistence / api / cli / mcp /
                                  # frontend-fixes（expand/contains/变更日志持久化）
```

测试覆盖三条链路的端到端断言、防腐层契约（编译产物必须能被 Kernel 的
`parse_schema` 接受）、能力调用的原子性（失败不留痕）与治理规则。

## 目录

```
src/onto_platform/
├── errors.py                  # 平台错误体系（API 状态码映射的依据）
├── ports/                     # 依赖倒置：domain/application 只认识这里
├── domain/                    # 七大领域 + ontology/compiler.py
├── application/               # 用例 + SchemaPublisher + AgentRunner + bootstrap
├── infrastructure/            # kernel 网关 / 持久化 / 连接器 / LLM / 执行器
└── interfaces/                # cli.py · api.py · mcp.py · web/dist(React 构建产物) · web/index.html(兜底)
frontend/                      # React + TS + Vite 控制台源码（构建产物提交进包内，运行期零 Node）
examples/traffic/demo.py       # design.txt §48 完整业务实例
```
