# 智能层控制论诊断：LLM/Agent 作为"带噪声、带时滞、无保证的控制器提议者"

> 范围：`application/agent_runner.py`、`application/intelligence_usecases.py`、`infrastructure/execution/{platform_executor,workflow_runner}.py`、
> `infrastructure/llm/*`、`ports/{llm,execution}.py`、`application/application_usecases.py`（RunAppQuery/RunAppAgent）、
> `tests/{test_intelligence,test_capability}.py`；旁证文件在正文标注。只读源码，不改源码、不启动服务；行号取自当前工作树
> （HEAD `fe56f9a` + 未提交的 draft/frontend 改动），所有推断均显式标注。
> 判定基准：钱学森《工程控制论》的回路语言——设定值、传感器（精度/时滞/噪声/带宽）、执行器（限幅/饱和/死区/可逆性）、控制器（校正/稳定性边界）、扰动与反馈。
> 一句话结论：**平台把"提议"与"执行"分开了（做对了），但没有把"验证"与"权限"接进回路。LLM 是一个没有反馈校正、没有饱和限幅、
> 没有审批通道的控制器；它读到的传感器读数有损有偏，它收到的"完成"信号由它自己宣布。**

---

## 1. 智能层在控制回路中的位置

三条链路（Ask / Agent / Agent 能力）共用同一个执行器与内核，回路的实际拓扑是：

```text
   用户目标 / 问题
         │
         ▼
  ┌──────────────┐   提议：严格 JSON 文本     ┌─────────────────────────┐
  │ LLM（控制器） │ ─────────────────────────▶ │ 平台：解析/校验/授权/审计 │
  └──────────────┘ ◀───────────────────────── └─────────────────────────┘
         ▲        观测：JSON 文本，≤4000 字符            │
         │                                              ▼
         │                                   ┌─────────────────────────┐
         │                                   │ Kernel（执行器）         │
         │                                   │ schema 校验 + 逐次原子提交│
         │                                   └─────────────────────────┘
         │                                              │ State / Change
         │                                              ▼
         └────── design.txt:1957-1985 的回边 "Change → AI" ──────┘
                 （实际未接线：observe_changes 只被 UI/CLI 消费）
```

| 环节 | 落在哪 | 关键约束/证据 | 控制论判读 |
|---|---|---|---|
| 设定值 | 用户 `goal` / `question` 字符串 | `agent_runner.py:66-80`；`intelligence_usecases.py:31` | 无结构化目标，无成功判据 |
| 控制器（提议） | LLM，每步一个 JSON 动作 | `agent_runner.py:2-10,39-55` | 无模型、无保证、可任意抖动 |
| 批准 | **无** | `application_usecases.py:144` 只需 `viewer`；`agent_runner.py:151-156` 直连 `gateway.invoke`，跳过 `InvokeCapability` 的 `require` 与审计 | 授权点缺失 |
| 执行 | Kernel + PlatformExecutor | `platform_executor.py:34-71`；`kernel_gateway.py:235-262` | 三个绑定三种错误语义（见 2.3） |
| 验证 | 只有 schema/参数级 | `ontoRK/runtime/runtime.py:161-213,299-327` | 验"合法"，不验"达成目标" |
| 观测 | 工具返回值 → 裸文本 | `agent_runner.py:113-116,171-175` | 带宽 4000 字符，无精度声明 |
| 反馈（跨轮） | 无：只回灌观察；Change 流不接回 | `agent_runner.py:108-116`；`observe_changes` 仅被 `api.py:258-260`、`cli.py:365-377` 调用 | 设计有回边（design.txt:1980-1984），实现开环 |

**判断 1：提议-执行分离是真实成立的。** Agent 不直接写内核，动作必须落到已发布能力或基础观察
（`agent_runner.py:151-164`；design.txt:946-989）。这是全项目最正确的控制论决策：控制器输出必须
经过执行器才作用于被控对象。

**判断 2：批准环节不存在。** 三处证据叠加：发起 Agent 只需要 `viewer`（`intelligence_usecases.py:106`、
`application_usecases.py:144`）；Agent 的行动不经过 `InvokeCapability` 用例（`agent_runner.py:152`
直接调 `gateway.invoke`），因此既无逐动作 `actor` 审计、也无将来的策略收紧入口
（`capability_usecases.py:117-124` 才是唯一带 actor 的入口）；Agent 能调用工作区里**所有**已发布能力，
没有动作白名单。推断：一旦治理把写能力收紧到 `editor`，Agent 路径会成为静默旁路。

**判断 3：验证环节缺失，且"完成"由控制器自报。** `finish` 一出现即采纳答案
（`agent_runner.py:100-106`），平台不问"目标条件是否满足"、不核对答案是否有 observation 依据、
不要求动作后读回状态。`AgentRun.ok` 默认 `True`（`domain/intelligence/model.py:75`），只在该跳过
`max_steps` 或 provider 抛异常时置 `False`（`agent_runner.py:84-87,117-119`）——它是"活着"而不是"成功"。

**判断 4：设计意图里的 `Change → AI` 回边没有落地。** design.txt:1957-1985 明确画了
Action → Change → Runtime State → AI 的闭环；实现里 `observe_changes()` 只服务 UI/CLI，
AgentRunner、AskOntology、RunAppQuery 从不调用（全仓 grep 结果仅 `api.py:258-260`、`cli.py:365-377`）。
推断：这是"设计基线有、代码未实现"的缺口，而不是实现 bug——但它让"本体智能闭环"只存在于图里。

### 1.1 哪些是 v0.1 有意简化，哪些是缺陷

| 事项 | 判定 | 依据 |
|---|---|---|
| `EchoLLMProvider` 只用于测试/演示 | 有意（但见 L4：它经 HTTP body 暴露成了生产面，属缺陷） | `echo.py:1-5`；README:165 |
| Ask 不调模型、确定性检索 | 有意 | `intelligence_usecases.py:19-26`；README:125 |
| 单 provider、非流式、单轮 `complete` | 推断有意（v0.1 的 Port 只定义了一个方法） | `ports/llm.py:24-29` |
| Capability 生命周期的 Test 步骤未建 | 有意，测试交给 pytest | README:200-206 |
| 无跨步事务、无补偿、无预算、无 HITL 审批、无 token 计量 | **缺陷**（文档只声明了上面两类边界，这些没有被声明为 v0.1 边界） | README:200-206 仅三条；design.txt:993-999 治理只含 Access/Lifecycle/Audit |

---

## 2. 逐项诊断

### 2.1 Agent 回路是否闭环

**A1 · `finish` 是自报完成，没有效果验证**
- 现象：LLM 输出 `answer` 即终止，平台不核对前置动作是否成功、不核对答案与观察是否一致、不读回被改对象。
- 证据：`agent_runner.py:100-106`（answer 直接采纳，step 记为 `{"tool": "finish"}`）；`agent_runner.py:117-119`（唯一失败条件是超步）；`domain/intelligence/model.py:75`（`ok=True` 默认）。
- 控制论解释：闭环的终止判据应由"误差=0"给出，此处由控制器自己声明"误差=0"；系统缺少独立的比较器。
- 后果：`run["ok"] is True` 不能作为业务成功证据；测试本身就演示了反例——一次失败的 `invoke_capability` 之后仍 `ok is True`（`tests/test_intelligence.py:62-76`）。

**A2 · Agent 能力把失败包装成成功函数调用**
- 现象：Agent 能力绑定返回 `{"ok": False, "answer": ...}`，PlatformExecutor 不做判断，直接 `make_result(output)`，内核看到 `error=None`。
- 证据：`agent_runner.py:235-239`；`platform_executor.py:56-67`（agent 分支无 `ok` 检查）；对照 python 分支的失败是异常→`FunctionExecutionError`（`platform_executor.py:40-41`）。
- 控制论解释：执行器把控制器的"内部报错"当成"正常输出"上报，等于把传感器故障信号接到了输出通道上。
- 后果：`/api/.../invoke`、MCP `capability_invoke`、审计三者都会报告成功；只有读懂 `output.ok` 的人才知道失败（`tests/test_capability.py:209-213` 只断言 `output.answer`）。

**A3 · 动作/参数/目标没有平台侧校验，错误只以文本回灌**
- 现象：`tool` 名不校验、`args["name"]`/`args["object_type"]`/`args["ref"]` 直接下标访问、`invoke_capability` 的 function/target/args 原样透传；任何 `KeyError`/内核错误都变成一句 `"tool error: ..."` 文本。
- 证据：`agent_runner.py:129-168`（136、144、150 直接下标；151-156 透传；165-168 兜底）；`TOOLS_SPEC`（`agent_runner.py:24-37`）只是提示词，没有 schema。
- 控制论解释：控制器输出没有限幅与类型整形，错误全部依赖末端（内核）硬停机，且停机信号被降级成文本噪声。
- 后果：幻觉动作只能靠 LLM"看懂"错误文本来自我纠正，没有确定性拒斥；同一错误可重复出现直到步数耗尽。

**A4 · 行动后不复查状态，且复合绑定的 `changes` 恒为空**
- 现象：`invoke_capability` 只把 `{ok,error,output,changes}` 序列化成文本回灌，Agent 不主动 `get` 复核；对 workflow/agent 绑定，内核返回的 `changes` 被覆盖为空列表。
- 证据：`agent_runner.py:151-164`；`platform_executor.py:48-55,63-67`（workflow/agent 只 `make_result(execution_id, output)`）；`ontoRK/runtime/runtime.py:205-213`（`result.changes` 被内核 `context.take_mutations()` 覆盖）；内层提交只落在 `kernel_gateway.py:255-256`。
- 控制论解释：执行器对"复合动作"的实际位移不可观测（给控制器的反馈恒为"无变化"），形成静态增益误差；Agent 若相信 `changes` 会得出"没改成"的错误结论。
- 后果：`test_intelligence.py:46-59` 里"余额 100"的验证是**脚本要求 LLM 自己 `get`** 才发生的，平台没有任何自动复查；换成真实模型漏掉这一步，答案就没有状态依据。

**A5 · 无重试、退避、去重与进度检测**
- 现象：循环只有一个 `for index in range(max_steps)`；坏 JSON 消耗一步后 `continue`；相同动作可以无限重复；无"重复动作/无进展"检测。
- 证据：`agent_runner.py:81,88-98`；`tests/test_intelligence.py:79-87`（同一 `describe_ontology` 连发 5 次，只靠步数截断）。
- 控制论解释：缺少抗扰恢复策略与死区/停滞判据，控制器抖动会直接放大成执行器重复动作。
- 后果：读动作重复=浪费时间与 token；写动作重复=重复副作用（无幂等键，见 W2）；瞬时 provider 故障则整轮失败（`agent_runner.py:84-87`）。

**A6 · 无超时、取消与预算（只有 `max_steps`，且请求体不设上界）**
- 现象：无每步/总时长，无取消通道；HTTP 同步阻塞到整轮结束；`max_steps` 由请求体任意指定。
- 证据：`agent_runner.py:60,81`；`api.py:373,379,390`（`int("max_steps", 8)` 无范围校验）；`openai_compat.py:20,42`（单次 120 s）；React 控制台默认 15 s 超时（`frontend/src/api.ts:114,259`）。推断：最坏 8×120 s≈16 min；客户端 15 s 即 abort，服务端继续执行并可能随后写入状态。
- 控制论解释：执行时间界是稳定性的必要条件；无界时滞 + 无取消 = 系统在操作者以为停止后继续作用于被控对象。
- 后果：写操作可能在"已超时/用户离开"之后完成；`max_steps=10^6` 可被打成拒绝服务。

**A7 · 观测与目标模板都不是"数据通道"，可被数据侧注入**
- 现象：observation 以裸文本拼进 user 消息（无围栏、无"以下是数据"标注）；能力 goal 模板用 `str.replace` 逐个替换 `{arg}`，后替换的键会改写前面已插入文本；`make_agent_runner` 不检查占位符是否全部解析。
- 证据：`agent_runner.py:113-116`（`OBSERVATION:\n{observation}`）；`agent_runner.py:228-232`；`agent_runner.py:224-227`（只对缺 provider 报错）。
- 控制论解释：把被控对象的状态和指令放进同一个输入信道，等于让扰动伪装成设定值（间接提示注入）。
- 后果：投影进来的业务数据（如车牌、站点名）只要含指令性文本，就可能改变后续动作；`{a}` 的值含 `{b}` 时二次替换（推断：`args` 字典序靠后的键会命中）。
- 证据等级说明：此条为静态代码推断，未做运行时注入实验。

### 2.2 观测质量

**O1 · `_dump` 在 JSON 中间截断，传感器输出"坏帧"**
- 现象：统一 4000 字符截断，截断点无结构、无剩余量、无获取剩余数据的指引。
- 证据：`agent_runner.py:22`（`MAX_OBSERVATION_CHARS = 4000`）；`agent_runner.py:171-175`（`text[:4000] + " ...(truncated)"`）。
- 控制论解释：传感器带宽被硬限幅，但限幅方式破坏可解析性；控制器拿到的是被截断的采样，不是降采样。
- 后果：大 `describe`/大 `invoke` 结果会让 LLM 无法解析前半段 JSON，只能凭猜测继续；且它不知道被截断了多少。

**O2 · `find` 静默只取前 20 条，Agent 不知道总量**
- 现象：`objects[:20]` 直接丢弃，无 `total`、无 offset/limit 提示；工具集里也没有 `count`（workflow 有 `count`，Agent 没有）。
- 证据：`agent_runner.py:141`；`TOOLS_SPEC`（`agent_runner.py:24-37`，仅 6 个工具）。
- 控制论解释：采样偏差：控制器把"前 20 条"当成"全部"，且没有任何"存在更多"的信号。
- 后果：对>20 对象的类型，Agent 的统计/判断系统性偏差；"为什么拥堵"类问题可能漏掉真正相关的对象。

**O3 · 无 token 计量、无上下文预算、无观测优先级；`describe` 还是双通道双保真**
- 现象：`LLMResponse` 只有 `content`；请求不设 `max_tokens`；8 步历史（每步最多 4000 字符观察）无压缩/淘汰，所有观测等权；同时 `describe()` 全量 JSON 塞进 system prompt（不截断），作为工具时却被截断到 4000。
- 证据：`ports/llm.py:13-22`；`openai_compat.py:31-37`；`agent_runner.py:81-116`（消息只增不减）；`agent_runner.py:68-80`（`runtime_index` 全量）与 `agent_runner.py:133-134,171-175`（工具路径截断）；`domain/intelligence/model.py:37-49`（`context_text` 遍历全部对象/关系/术语）；`intelligence_usecases.py:114`。
- 控制论解释：控制器输入没有预算、没有"关键观测优先"的调度，同一传感器还有两条精度不同的通道；一旦超出模型窗口，退化方式不可控（服务端 400 → 整轮失败）。
- 后果：成本与延迟不可观测、不可预算；本体增长到某阈值后 Agent 从"降级"变成"直接失败"，失败点不可预知（推断）；重复注入的索引信息也白耗 token。

### 2.3 执行编排（workflow / executor）

**W1 · workflow 无跨步事务：第 3 步失败时前 2 步已提交；外层 `changes` 还是空的**
- 现象：每个 `invoke` 步骤单独走 `gateway.invoke` 并内核逐次提交；后续步骤失败只抛 `WorkflowStepError`，不回滚。最终 `FunctionResult.changes` 被内核 `take_mutations()` 覆盖为空——真实写操作只落在 gateway change log，调用方看到的 `changes` 是 `[]`。
- 证据：`workflow_runner.py:42-51,58-69`；`kernel_gateway.py:250-256`（每次 invoke 立即 `_record`）；`platform_executor.py:48-55,63-67`（只 `make_result(execution_id, output)`）；`ontoRK/runtime/runtime.py:161-213`（原子性与 `changes` 都只覆盖单次 invoke）；`capability_usecases.py:120-128`（审计里的 `changes` 同样为空）。
- 控制论解释：复合执行器内部含"积分"（不可逆写入），失败无法回到初始条件；同时位移报告通道只传 output——执行结果与观测解耦。
- 后果：与 `ports/execution.py:23-24` 的 "never a partial success" 直接矛盾；调用方无法用 `changes` 验证（A4 的根因）；`tests/test_capability.py:104-117` 只测"第一步失败"，恰好避开部分提交。

**W2 · 无补偿/回滚/幂等键/每步超时**
- 现象：`run()` 是裸 `for` 循环，无 undo、无幂等键、无 per-step timeout；重跑整条 workflow 会重复已提交步骤。
- 证据：`workflow_runner.py:34-54`；`workflow_runner.py:98-117` 是唯一写入原语，但只前向写、无逆操作；`kernel_gateway.py:268-357` 无删除/撤销入口（只有 `unlink`）。
- 控制论解释：执行器不可逆、无重试幂等，等于把"重试"变成"重复扰动"。
- 后果：运维只能靠人工修数；推断：若把 "重试整条 workflow" 加进去会立刻造成重复充值/重复扣减这类事故。

**W3 · `set_property` 绕过执行器直写 delta，且目标不存在时"写"变"建"**
- 现象：workflow 的 `set_property` 不经过 Function 执行（属性名/类型仍由网关校验 `kernel_gateway.py:289-301`，但没有函数参数校验、没有 function 级 execution_id、没有 `capability.inputs` 校验），直接 `gateway.apply_delta`；而 `apply_delta` 对不存在的 `Type/Id` 会走 `seed` 创建对象，仅带给定属性。
- 证据：`workflow_runner.py:98-117`；`kernel_gateway.py:287-305`（`existing is None → creates.append(Object(...))`）；`kernel_gateway.py:341-350`（`seed` 落库）。
- 控制论解释：一个"写"原语暗含"建"的动作，执行器增益不可预期；拼错 ref 不是报错而是产生新状态。
- 后果：与 README:175-177 的承诺"写回**已存在对象**的属性"相反；一个 typo 的 ref 会造出只有 `status` 的幽灵对象，并把污染带进后续所有查询。`tests/test_capability.py:149-188` 只覆盖"已存在对象"和"ref 格式非法"，未覆盖"格式合法但对象不存在"。

**W4 · 模板解析：作用域限定做对了，但观测结果可整表注入参数**
- 现象：引用根被限定为 `args/target/steps/vars`，非法根会拒绝（好设计）；但 `{{steps.x}}` 可把整个观测数组当参数塞给下一步 `invoke`，`_TEMPLATE.sub(..., str())` 让运行时数据进入字符串参数，无长度/类型约束；capability 声明的 `inputs` 在 workflow 里从不校验。
- 证据：`workflow_runner.py:20,127-158`；`tests/test_capability.py:120-131`（覆盖了拒绝 `evil.path`）；`domain/capability/model.py:74-86`（`CapabilityInput` 只是元数据）；`domain/capability/model.py:169-183`（`validate_steps` 只检查必需字段）。
- 控制论解释：把"测量值"当"控制量"直接使用，没有量纲/范围整形；观测噪声会原样进入执行器。
- 后果：大列表会撑爆下游函数的入参/输出，类型错误只在运行期由内核兜底（`ontoRK/runtime/runtime.py:299-327`），变成又一条文本错误。

### 2.4 LLM Provider

**L1 · 无重试、无错误分类、无 backoff；Echo 让这些都没被测到**
- 现象：单次 `urlopen`；429/5xx/网络抖动一律以原始异常抛出；上层只有一次粗粒度 `except Exception` 决定整轮失败。测试替身 `EchoLLMProvider` 顺序回放、无延迟，耗尽后返回固定 `answer`。
- 证据：`openai_compat.py:28-45`；`ports/llm.py:26-29`（契约只承诺"transport 错误时抛出"，不分类）；`agent_runner.py:82-87`；`echo.py:10-19`；`tests/test_intelligence.py:8,28-34,47-52,63-68,80-82`。
- 控制论解释：没有"可重试扰动"与"不可重试故障"的区分，稳定性边界由偶发网络决定；测试也只证明了"协议与解析可跑"，没证明回路在噪声/时滞/部分失败下稳定。
- 后果：一次瞬时 429 让整轮 Agent 失败（写操作可能已发生一半）；`ok` 与 `answer="LLM provider failed: ..."` 一起返回，调用方更容易误判；`tests/` 中没有任何 429/超时/截断/非确定输出模拟，"脚本耗尽→无法继续推演"的演示行为也从未被断言。

**L2 · 请求/响应面太薄：无 `max_tokens`、无 `response_format`、无 usage**
- 现象：只发 `model/messages/temperature`；响应只取 `choices[0].message.content`；假设 content 一定是非空字符串；无 token 用量回传。
- 证据：`openai_compat.py:31-44`；`ports/llm.py:19-22`（`LLMResponse` 只有 `content`）。
- 控制论解释：没有输出饱和限制、没有结构化输出保证、没有成本/延迟观测量。
- 后果：内容过滤/推理模型返回 `content=None` 时 `LLMResponse(content=None)` 触发后续 `str`/JSON 解析异常（推断）；预算无从做，PD 控制（比例-微分）所需的时滞与代价信息缺失。

**L3 · `factory` 环境契约窄且有不安全回退；MCP 未接线**
- 现象：只认 `ONTO_LLM_BASE_URL/MODEL/API_KEY(KEY)`；无 timeout/temperature/max_tokens；有 key 无 base_url 时静默回退到公网 `https://api.openai.com/v1`；MCP 的 `main()` 不调用 `provider_from_env()`。
- 证据：`factory.py:13-35`（`DEFAULT_BASE_URL` 回退）；`api.py:439-442` 与 `cli.py:270-274`（有接线）对照 `mcp.py:126-135`（无接线）；`mcp.py:117-121`（`agent_run` 直接调 `intelligence.agent`）。
- 控制论解释：默认值即执行器权限：配置缺省时系统自发地把数据发往外部端点。
- 后果：推断 MCP 的 `agent_run` 永远返回 "no LLM provider configured"；而误配 key 时会把业务提示词发往 OpenAI 而非私有网关。

**L4 · 请求级 provider 注入（生产面上的任意 base_url 与"脚本化 LLM"）**
- 现象：HTTP body 的 `llm` 字段可指定 `provider=openai` + 任意 `base_url/model/api_key`；`llm.script` 可直接装一个 `EchoLLMProvider`；HTTP 层没有认证，`user` 由 `body.pop("user","admin")` 自声明，React 控制台甚至把它硬编码为 `"admin"`。
- 证据：`api.py:400-412`；`api.py:285`；`frontend/src/api.ts:146,150-154`（`LlmChoice` 把 openai/base_url/api_key 与 script 做成前端可选项）；对照 `echo.py:1-5`（自称"tests and demo"）。
- 控制论解释：这是**有意提供的调试/演示开关**，但在无鉴权部署里等于允许外部请求替换控制器实现；默认值 `"admin"` 让授权判断失去意义。
- 后果：任意调用者可（a）把本体+运行时数据（含 `goal`）发到自己端点——数据外带/SSRF（推断）；（b）用 `script` 让 Agent 说出任意答案或执行任意已发布能力——策略与模型护栏全部绕过；（c）`api_key` 经明文 body 传输。判定：功能意图合理，**缺陷在没有鉴权/白名单/开关默认关闭**（身份认证属治理层议题，此处只标注它与智能层的耦合点）。

### 2.5 AskOntology：确定性检索，不是诊断

**Q1 · 定位正确但能力边界没有暴露给用户**
- 现象：实现是"词汇匹配 → 找对象 → 一跳关系展开 → 模板拼装答案"，不含因果/聚合/对比；文档也如实写明"deterministic ... no LLM required"。
- 证据：`intelligence_usecases.py:19-26,31-94`；README:125（检索问答、不调模型）。
- 控制论解释：这是一台精度可预期、带宽极低的传感器；问题在于产品把它当"为什么"问题（诊断器）使用。
- 后果：`tests/test_intelligence.py:11-20` 的"为什么拥堵"答案只是"术语+对象+一跳邻居"，却以诊断口吻输出（`_compose_answer`，`intelligence_usecases.py:141-162`）。

**Q2 · `find` 全量加载 + 应用层子串匹配，无分页/limit**
- 现象：对每个候选类型 `gateway.find_objects(type)` 取全量，再在 Python 里用字符串子串匹配。
- 证据：`intelligence_usecases.py:55-66`；`_mentions`（`intelligence_usecases.py:133-138`）。
- 控制论解释：传感器扫描全量、无索引下推，时滞随对象数线性增长。
- 后果：大工作区上单次 Ask 成本不可控；`_mentions` 只看字符串属性，ID/数字提及无法定位（如"给 V002 充值"式问题）。

**Q3 · 无匹配时回退"前 10 个对象"，把"无证据"包装成证据；property 命中被丢弃**
- 现象：`mentioned or candidates[:10]`——问题里什么都没提及时，直接给前 10 个对象；`property_hits` 收集后从未参与过滤，答案只显示术语/概念；关系命中只取 `source` 端。
- 证据：`intelligence_usecases.py:66`；`intelligence_usecases.py:40-46`（收集 `property_hits`）对照 `intelligence_usecases.py:85,141-162`（只消费 `hits` 的 term/concept）；`intelligence_usecases.py:50-53`（`relation` 命中只取 `source`）。
- 控制论解释：无信号时输出噪声而不是"无数据"标志（缺失的饱和/死区），且已解析出的"拥堵→TollStation.status"通道被丢进垃圾桶。
- 后果：`tests/test_intelligence.py:22-24` 的"今天天气"之所以通过，只是因为词汇表没命中；若问题含"收费站"而不含具体站点，会返回任意 10 个站点作为"运行时状态"（误导）。

**Q4 · N+1 + 仅一跳 + 静默截断**
- 现象：`found[:10]` × 全部相关关系 × 每条边再 `get_object`；只展开一跳；`related[:10]` 截断。
- 证据：`intelligence_usecases.py:68-84,160`；`kernel_gateway.py:216-229`（每次调用一次网关/一次内核锁）。
- 控制论解释：观测带宽与调用次数随关系数乘积增长，缺少"按需下钻"的二阶观测通道。
- 后果：`tests/test_intelligence.py:18-19` 的断言只有一跳（Vehicle 通过），无法检验多跳/环路/大图行为；推断：大图上单次 Ask 可能数十次内核往返。

### 2.6 应用层 Agent/Query 执行链：失败可见性

**P1 · `RunAppAgent`：`viewer` 就能让 LLM 写世界，且绕过逐动作审计**
- 现象：应用 Agent 只要求 `viewer`；它执行的写能力不经过 `InvokeCapability`（唯一带 actor 校验+审计的入口），Agent 自己的行动也没有逐条审计。
- 证据：`application_usecases.py:141-153`（`require(..., "viewer")`）；`intelligence_usecases.py:103-119`（审计只有一条 `intelligence.agent`，detail 仅 `goal/ok/steps`）；`agent_runner.py:151-156`；对照 `capability_usecases.py:117-124`。
- 控制论解释：权限是执行器的一部分；读权限被隐式提升为写权限，且写动作没有逐次可追溯的 actor。
- 后果：审计里"谁让 LLM 改了什么"只能从 change log 猜；`viewer`（本应只读的观察者）可触发真实充值/写状态。

**P2 · `RunAppQuery`：无验证、无分页、N+1，且几乎未被测试**
- 现象：`find_objects(object_type, filters)` 全量返回、无 limit；每个对象每个 expand 关系再 `related` + `get_object`；返回原始对象不裁剪；`AddQuery` 只校验 `object_type`，`filters` 属性名与 `expand` 关系名都不校验。
- 证据：`application_usecases.py:175-202`；`application_usecases.py:70-87`；错误只在内核运行期暴露（`ontoRK/runtime/runtime.py:140-143,316-327`），非 `PlatformError` → 500（`api.py:86-92`）。
- 控制论解释：设计期没有可验证的前置条件，执行期才硬失败；观测无上限。
- 后果：`tests/test_application.py` 完全没有 `run_query` 用例；唯一覆盖是 `tests/test_frontend_fixes.py:42-52`（2 辆车、1 个关系、0 失败路径）。

**P3 · 失败可见性依赖适配器，不依赖契约**
- 现象：核心用例返回 `dict` 而不抛异常，HTTP 层一律 200；同一失败在应用层是 `{"ok": false}`、在能力绑定层却是 `ok: true + output.ok: false`；MCP 的 `agent_run` 甚至没有 provider 接线，`workspace_ask` 固定 `actor="admin"`。
- 证据：`api.py:374-390`（一律 200）；`platform_executor.py:63-67`；`mcp.py:100-103`（hard-code admin）；`mcp.py:117-121,126-135`。
- 控制论解释：错误信号在边界处被"翻译"掉，且一层一个 `ok` 语义，操作者拿不到稳定可判的故障指示。
- 后果：脚本/前端/LLM 三类消费者需要各自实现 `ok` 判断；`tests/test_application.py:37-46` 只断言成功场景，推断 MCP 的 `agent_run` 实际不可用、`workspace_ask` 审计主体失真。

---

## 3. 测试断言了什么、没断言什么

**已断言（有效，但也暴露了设计边界）**

| 测试 | 断言 | 位置 |
|---|---|---|
| Ask 术语/对象/一跳 | 词汇命中、对象按名称值定位、展开到 Vehicle | `test_intelligence.py:11-19` |
| Ask 无匹配 | 答案含"未找到匹配对象" | `test_intelligence.py:22-24` |
| Agent 三轮闭环 | 观察确实回灌（ST001、Vehicle/V001 出现在 observation） | `test_intelligence.py:27-43` |
| Agent 调能力 | 需要 LLM 自己发 `get` 才验证余额=100 —— **恰好证明平台没有自动复查** | `test_intelligence.py:46-59` |
| 坏 JSON + 工具错误 | 工具错误变文本；`run["ok"] is True`（失败不影响成败） | `test_intelligence.py:62-76` |
| max_steps | 超步后 `ok False` + 固定文案 | `test_intelligence.py:79-87` |
| 无 provider | 抛 `ValidationError`（应用层路径） | `test_intelligence.py:90-92` |
| python 能力原子性 | 失败调用不改状态 | `test_capability.py:46-67` |
| workflow 单步失败 | 第一步就失败 → `ok False` | `test_capability.py:104-117` |
| set_property | 已存在对象被写、bad ref 被拒 | `test_capability.py:149-188` |
| agent 能力 | e2e、无 LLM、重启后重挂 runner | `test_capability.py:196-254` |
| 应用 Agent | 成功场景 + goal 覆盖 + 未知 alias | `test_application.py:37-62` |
| 应用 Query | 仅 expand 字符串归一化回归（2 对象） | `test_frontend_fixes.py:42-52` |

**没有断言（与缺陷一一对应）**
1. **超时/延迟**：`openai_compat` 无任何测试；不存在"provider 慢/超时"用例（`ports/llm.py` 的测试替身只有 Echo）。
2. **瞬态失败**：无 429/5xx/网络异常注入；唯一 provider 失败用例是"未配置"（`test_intelligence.py:90-92`）。
3. **幻觉动作/参数**：无未知 tool、缺 `args`、参数类型错误、target 半合法的用例；只有未知函数（`test_intelligence.py:62-76`）。
4. **预算/成本**：无 token 计量断言，无 cost/耗时上限。
5. **`finish` 效果验证**：没有"动作失败但 `ok` 仍为真"的反向断言（现有断言反而把这一行为固化）；没有"答案必须有 observation 依据"的检查。
6. **workflow 部分提交**：没有"第 2 步失败、第 1 步已提交"的回归；没有幂等/重跑用例；没有每步超时。
7. **`set_property` 建对象**：没有"目标对象不存在"的用例（这个洞当前是敞开的）。
8. **观测有损**：没有 `_dump` 截断、`find>20`、大本体 system prompt、上下文超限的用例。
9. **注入**：没有"属性值包含指令/模板占位符"的用例，也没有 goal 模板二次替换的用例。
10. **并发**：网关有 `RLock`（`kernel_gateway.py:120-122`），但无并发 invoke/agent 测试（推断风险未验证）。
11. **失败可见性**：没有"agent 能力失败 → HTTP 应返回 error 而不是 ok"的用例；`RunAppQuery` 没有任何失败路径测试。

**建议补齐的四类测试（按性价比排序）**
- 故障注入 provider（超时/429/返回 `content=None`/非法 JSON），断言 Agent 只重试可重试错误且最终 `ok` 与真实结果一致；
- 状态断言型闭环：`finish` 后由测试（不是 LLM）读回目标条件；
- 预算测试：`max_steps` 上界、总耗时上限、token 计数（需要先有 usage 字段）；
- workflow 部分提交回归：两步写、第二步失败，断言"要么全改、要么全不改"，或断言补偿路径。

---

## 4. 智能层 Top 问题排序（P0/P1/P2）

> 排序兼顾"后果严重度 × 被触发的容易程度 × 修复成本"。P0 是"会让调用方/审计得到错误事实"或"越权写世界"的问题。

| # | 级别 | 问题 | 证据（文件:行） | 一句话后果 |
|---|---|---|---|---|
| 1 | **P0** | Agent 能力失败被包装成成功函数调用 | `platform_executor.py:63-67`；`agent_runner.py:221-239` | 调用方/审计/上层编排全部误判成功 |
| 2 | **P0** | workflow/agent 绑定的 `changes` 恒空，写操作与观测脱节 | `platform_executor.py:48-67`；`ontoRK/runtime/runtime.py:205-213` | 无法用返回值验证写操作，A4/W1 的根因 |
| 3 | **P0** | `viewer` 可驱动 LLM 写世界，且 Agent 路径绕过 `InvokeCapability` 的授权与逐动作审计 | `application_usecases.py:144`；`intelligence_usecases.py:106,117-118`；`agent_runner.py:151-156` | 权限/审计边界洞，写动作不可追溯到 actor |
| 4 | **P0** | 请求级 provider 注入：任意 `base_url`/`api_key`/`script`，HTTP 无认证 | `api.py:285,400-412`；`echo.py:1-5` | 数据外带/SSRF；用脚本化 LLM 绕过模型策略 |
| 5 | **P0** | `set_property` 对不存在的 `Type/Id` 会创建对象 | `workflow_runner.py:98-117`；`kernel_gateway.py:287-305,341-350` | 拼错 ref 制造幽灵对象，污染状态与后续查询 |
| 6 | **P1** | `finish` 自报完成；行动后不复查状态；`ok` 是存活标志 | `agent_runner.py:100-106,151-164`；`model.py:75`；`test_intelligence.py:72` | 幻觉答案无法被平台发现 |
| 7 | **P1** | workflow 无跨步事务/补偿/幂等/每步超时 | `workflow_runner.py:34-54`；`ports/execution.py:23-24` | 第 k 步失败=前 k-1 步永久生效 |
| 8 | **P1** | 观测有损无优先级：4000 字符截断、`find` 前 20、无 token 计量 | `agent_runner.py:22,141,171-175`；`ports/llm.py:19-22` | 控制器读坏帧/有偏采样，本体增长后从降级变失败 |
| 9 | **P1** | Provider 无重试/错误分类/usage/`max_tokens` | `openai_compat.py:13-45`；`ports/llm.py:26-29` | 一次 429 毁掉整轮；成本/延迟不可观测 |
| 10 | **P1** | Ask 无匹配回退前 10、property 命中丢弃、一跳 N+1 | `intelligence_usecases.py:40-46,55-84` | 检索冒充诊断，无证据时输出噪声 |
| 11 | **P1** | 无超时/取消，`max_steps` 无上界 | `api.py:373-390`；`agent_runner.py:60,81`；`openai_compat.py:20,42` | 最坏 ~16 min 静默执行，超时后仍写世界 |
| 12 | **P2** | 动作/参数无平台侧 schema 校验，错误只文本回灌 | `agent_runner.py:129-168` | 幻觉只能靠 LLM 看懂错误自纠 |
| 13 | **P2** | 提示注入面：观察裸文本、goal 模板二次替换 | `agent_runner.py:113-116,228-232` | 业务数据可改写后续动作 |
| 14 | **P2** | workflow 可把整表观测注入参数，`capability.inputs` 从不校验 | `workflow_runner.py:148-158`；`domain/capability/model.py:74-86` | 观测噪声原样进执行器，类型错误后置 |
| 15 | **P2** | `RunAppQuery` 无界/N+1/未测试；`AddQuery` 只校验 object_type | `application_usecases.py:70-87,175-202` | 运行期 500，大表拖垮网关 |
| 16 | **P2** | factory 契约窄 + 静默回退公网；MCP 未接 provider | `factory.py:13-35`；`mcp.py:117-135` | 误配发外网；MCP `agent_run` 不可用 |

### 4.1 优化路线（按控制论顺序，不是按功能顺序）

1. **先修观测（传感器）**：`LLMResponse` 增加 `usage` 与 `finish_reason`（`ports/llm.py:19-22`）；观测改为"紧凑摘要 + 可下钻游标"（`find` 返回 `{items,total,next}`，`_dump` 截断时返回可续取的结构化提示）；把 `observe_changes` 接回 Agent 作为动作后的独立反馈通道（`kernel_gateway.py:397-399` 已有，只需在 `AgentRunner` 消费）。
2. **再修验证（比较器）**：`finish` 前要求"答案引用 observation 中的 ref/changes"（可在 `AgentRunner` 内做字符串/引用校验，见 `ontology_draft.py:119-131` 的"提议→规范化→裁决→warnings"模式）；`AgentRun` 拆成 `ok`（执行完成）与 `verified`（目标条件满足）两个字段；Agent 能力绑定在 `ok=False` 时返回 `FunctionResult(error=...)`（`platform_executor.py:63-67`），让内核与调用方得到真实失败。
3. **给执行器加约束**：workflow 引入"要么全改要么全不改"的语义（平台侧事务日志 + 失败时补偿，或让每步 mutation 走同一次内核 buffer）；`set_property` 在 `apply_delta` 前显式 `get_object` 存在性检查（`workflow_runner.py:98-117`）；每步加 timeout 与幂等键。
4. **最后收权限与预算**：Agent 发起需 `editor`，Agent 写动作走 `InvokeCapability`（`capability_usecases.py:106-124`）以获得逐动作 actor 审计；`max_steps` 设上界与总时长预算；请求体禁止 `base_url/api_key/script`，provider 只从服务端配置解析（`api.py:400-412`）。

### 4.2 一句话总结

平台的骨架是对的：**LLM 只提议、内核才执行**。缺的是控制器工程的三件套——**量程（观测带宽与精度）、限幅（动作/预算/超时约束）、比较器（独立验证与真实完成信号）**；而权限与审计没有覆盖 LLM 发起的写动作，使"提议者"事实上拥有了"批准者"的空白授权。
