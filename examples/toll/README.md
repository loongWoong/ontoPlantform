# 高速收费站样例（examples/toll）

一个**极简但完整**的高速收费站业务样例，用来验证 OntoPlatform 的建模 →
发布 → 数据接入 → 投影 → 运行时状态这条链路。数据规模小（84 行）、可复现、
一条命令装载。

```
examples/toll/
├── business.md              # 一段自然语言业务说明（可喂给 LLM 做「一段话→本体」）
├── expected-ontology.json   # 样例本体标准答案：objects / relations / vocabulary
├── bootstrap.py             # 一键装载（默认对接 8100 HTTP API，--memory 离线跑）
├── tour.py                  # 全链路演练：一次串起平台七个领域并打印证据
├── console_flow.py          # 控制台视角：走 8100 HTTP 路由验证 STEP 3–5
├── data/
│   ├── build_db.py          # 生成 data/toll.db（stdlib sqlite3，固定种子）
│   └── toll.db              # 生成物（被 .gitignore 忽略，运行脚本即可重建）
└── docs/
    ├── data-dictionary.md   # 表/字段/语义映射/数据规模
    ├── business-rules.md    # 计费规则、拥堵判定、一致性约束
    └── operations.md        # 运营场景与 API 查询示例
```

## 快速开始

前置：`onto-platform` 已安装（`python -c "import onto_platform"` 可用），
`onto-api` 已在 `http://127.0.0.1:8100` 运行（**不要重启服务**）。

```bash
cd F:/piAgent/ontoPlantform/examples/toll

# 1) 生成样例库（幂等，会先删表再建；固定随机种子，数据可复现）
python data/build_db.py

# 2) 装载到运行中的平台（默认 http://127.0.0.1:8100）
python bootstrap.py

# 2') 离线验证：不依赖 8100，直接用内存平台跑完整流程
python bootstrap.py --memory

# 3) 可选：让平台 LLM 解析 business.md 生成本体（默认关闭，需服务端已配模型）
python bootstrap.py --use-llm

# 可选：指定别的 API 地址
python bootstrap.py --base-url http://127.0.0.1:8100
```

`bootstrap.py` 流程（与平台链路一一对应）：

```
创建工作空间「高速收费站样例」
  → 创建本体：默认写入 expected-ontology.json 的 4 对象 / 4 关系 / 15 术语
     （加 --use-llm 则改为 business.md → POST /ontology/draft，由 LLM 解析）
  → validate → publish（编译成 Kernel Schema）
  → 连接 sqlite 数据源 data/toll.db（4 个资产，每个一条 SELECT）
  → 创建投影（4 个对象映射 + 4 个关系映射）
  → run_projection（84 objects upserted / 138 links asserted）
  → state/find 回查，打印每个对象类型的对象数
```

成功时打印（数值为实测）：

```
[8/8] 回查 Runtime State（state/find）…
      TollStation        6 个对象
      Lane              18 个对象
      Vehicle           20 个对象
      TollTransaction   40 个对象

==============================================================
装载完成
  后端       HTTP API
  工作空间   wid = <wid>
  对象类型   4
  关系       4
  业务术语   15
  数据源     <datasource_id>
  投影       <projection_id>
  投影结果   upserts=84  links=138  changes=209
  对象计数   TollStation=6 · Lane=18 · Vehicle=20 · TollTransaction=40
  TollStation 样例  TollStation/ST001 {"name": "济南西收费站", "status": "congested", "province": "山东"}
==============================================================
```

**幂等友好**：平台没有按名查找工作空间，脚本每次新建一个工作空间并打印新 `wid`；
重复执行不会覆盖或改动旧工作空间。投影本身是 upsert，重复 `run` 第二次
`changes=0`（已实测）。

## 可选：一段话 → 本体（`--use-llm`）

`business.md` 是一段口语化业务说明，可以直接交给平台的 LLM 建模接口：

```bash
python bootstrap.py --use-llm
```

脚本会 `POST /api/workspaces/{wid}/ontology/draft`（`{"text": business.md,
"apply": true}`），由平台 LLM 解析成对象/关系/术语并写入草稿本体，再照常
validate → publish → 连数据源 → 投影 → 回查。实测输出示例：

```
[2/8] 把 business.md 交给 LLM 解析本体（POST /ontology/draft, apply=true）…
      applied=True · 对象 6 · 关系 7 · 术语 19 · warnings 0
      投影映射调整：Lane 跳过属性映射 ['lane_type', 'station_id']：LLM 本体未定义这些属性
      投影映射调整：跳过对象映射 TollTransaction：LLM 本体中没有该对象类型
      ...
      提示：LLM 解析出的标识符与 expected-ontology.json 不完全一致，本次按可用映射装载；
            默认模式（不加 --use-llm）可完整装载。
```

（LLM 每次解析结果会略有差异，上表为一次实测输出。）

注意：LLM 自己起名（例如把 `TollTransaction` 叫 `PassTransaction`、把 `plate`
叫 `plate_number`）是正常现象，平台会规范化并保留其语义；但 `data/toll.db`
的投影映射是按 `expected-ontology.json` 的规范名写的，因此 `--use-llm` 模式会
**自动裁剪**不匹配的映射并打印中文说明，通常只装载部分对象。
**默认模式（`python bootstrap.py`）才保证 84 对象 / 138 关系完整可复现。**
`--use-llm` 与 `--memory` 组合时，内存平台需自行设置 `ONTO_LLM_*` 环境变量，
否则会给出明确中文提示，建议直接对接 8100。

## 全链路演练：一次串起七个领域（`tour.py`）

`bootstrap.py` 只跑到「数据进运行时」，`tour.py` 继续把**能力、应用、智能问答、
治理**走完，15 步全绿即为一条完整证据链：

```bash
cd F:/piAgent/ontoPlantform/examples/toll
python tour.py                                  # 用服务端已配置的 LLM（默认 8100 平台）
python tour.py --no-llm                         # 不调模型：Agent 步骤跳过，其余照跑
python tour.py --memory                         # 内存平台，不进 .onto/platform.db
python tour.py --name "我的演练"                 # 指定工作空间名
python tour.py --base-url off                   # 不做最后一步 REST 回查
```

实测（`deepseek-v4-flash`，2026-10-28 一次运行，总耗时约 65 秒，15/15 通过）：

| 步 | 覆盖领域 | 实测证据 |
|----|----------|----------|
| 1 | workspace / governance | 建空间 `7c33935df1d1`；admin=owner、ops=editor、auditor=viewer |
| 2 | intelligence（LLM） | business.md（573 字）→ 草稿 5 对象 / 9 关系 / 26 术语，零 warnings，**预览不写入** |
| 3 | ontology | 标准本体 4 对象 / 4 关系 / 15 术语，validate 通过 |
| 4 | ontology → runtime → kernel | publish 成功，`objects=4 relations=4 functions=0` |
| 5 | governance | 发布后再写被拒：`published ontology is immutable` |
| 6 | data | sqlite 数据源 4 条资产 → 投影 84 upserts / 138 links；重跑 `changes=0` |
| 7 | state | TollStation 6 / Lane 18 / Vehicle 20 / TollTransaction 40；`related` 回查车道 |
| 8 | capability（python） | `daily_report` → 车道 3、出口交易 8、费用样本和 |
| 9 | capability（workflow） | `congestion_scan` → 拥堵站 2、本站车道 3 |
| 10 | capability（bounded write） | `ST006 free → busy`，`changes=1` |
| 11 | capability（agent） | `explain` → 一段自然语言拥堵解释（LLM 7 步） |
| 12 | application | 视图 / 查询 / 能力 / 助手装配 → publish → 查询命中 ST001、ST005 |
| 13 | intelligence | `ask` 命中术语；通用 Agent 7 步给出拥堵结论 |
| 14 | governance | 审计 50 条；viewer 写被拒、editor 写被生命周期拦住 |
| 15 | interfaces / console | REST 回查 health / runtime / state/find / audit 全部一致 |

成功尾巴长这样：

```
   ✓ REST /api/health → llm_configured=True store=sqlite
   ✓ REST runtime → objects=4 relations=4
   ✓ REST state/find?type=TollStation → 6 个（与本次演练一致）
   · 打开 http://127.0.0.1:8100/ → 选「收费站全链路演练（全功能）」即可看到全部结果
==========================================================================
完成：全部步骤通过，未跳过任何步骤。
工作空间 wid = 7c33935df1d1
==========================================================================
```

每一步都打印「上一步 X.Xs」，方便定位慢在哪；只有 LLM 步骤（草稿、agent 能力、
Agent 推理）是秒级到几十秒，其余都是毫秒级。演练结束的工作空间会留在
`.onto/platform.db` 里，在控制台工作台可见，不会覆盖已有工作空间。

## 控制台视角：走 HTTP 路由验证 STEP 3–5（`console_flow.py`）

`tour.py` 用进程内平台对象跑全链路，`console_flow.py` 则**只走 REST 路由**
（`http://127.0.0.1:8100/api/...`），也就是控制台页面按钮发出的那些请求，
用来证明「页面上看到的每一步」在 API 层同样成立：

```bash
python console_flow.py                     # 新建工作空间，跑完 STEP 3–5
python console_flow.py --name 我的演示
python console_flow.py --no-agent          # 跳过两个 LLM Agent 入口
```

它复用 `bootstrap.py` 的样例数据与映射，因此前半段就是 STEP 3–4（发布 + 数据），
后半段覆盖 STEP 5：四种能力（python / workflow / 写回 / agent）、应用装配与运行、
`ask` 与通用 Agent、成员角色与越权拦截、审计流水。实测一次 53.5s 全绿：

```
  ✓ Kernel Schema：objects=4 relations=4 functions=0
  ✓ find TollStation status=congested → ['ST001', 'ST005']
  ✓ 投影幂等重跑：upserts=84 links=138 changes=0
  ✓ daily_report(ST001) → {"lane_count": 3, "exit_transactions": 8, ...}
  ✓ 应用 b73c76f0a813 发布：lifecycle=published，视图 1 / 查询 1 / 能力 1 / 助手 1
  ✓ run_agent('值班助手') ok=True steps=5
  ✓ ask 命中术语 ['收费站', '拥堵'] → 对象 ['ST001']
  ✓ viewer 调能力被拒 → AccessDeniedError（'editor' is required）
  ✓ 审计 51 条：{"capability.invoke": 6, "capability.create": 4, ...}
控制台链路验证完成，用时 53.5s；工作空间 wid = d8de2fb7ba0b
```

## 用 API 验证数据真的进了 Runtime

把 `<wid>` 换成脚本打印的工作空间 id：

```bash
# 收费站（6 个）
curl "http://127.0.0.1:8100/api/workspaces/<wid>/state/find?type=TollStation"

# 只看拥堵（ST001 济南西、ST005 德州）
curl "http://127.0.0.1:8100/api/workspaces/<wid>/state/find?type=TollStation&status=congested"

# 通行交易（40 个，含 fee / entry_station / exit_station / ts）
curl "http://127.0.0.1:8100/api/workspaces/<wid>/state/find?type=TollTransaction"

# 收费站下的车道（关系 has_lane）
curl "http://127.0.0.1:8100/api/workspaces/<wid>/state/related?ref=TollStation/ST001&relation=has_lane"

# 本体 / 编译后的 Runtime Schema / 数据源 / 投影
curl "http://127.0.0.1:8100/api/workspaces/<wid>/ontology"
curl "http://127.0.0.1:8100/api/workspaces/<wid>/runtime"
curl "http://127.0.0.1:8100/api/workspaces/<wid>/datasources"
curl "http://127.0.0.1:8100/api/workspaces/<wid>/projections"
```

PowerShell 里含中文的过滤参数用 `[uri]::EscapeDataString("鲁A12345")` 先编码。
更多场景见 [`docs/operations.md`](docs/operations.md)。

## 本体标准答案（expected-ontology.json）

| 类型 | 内容 |
|------|------|
| 对象 | `TollStation`（名称/状态/省份）、`Lane`（方向/类型/所属站）、`Vehicle`（车牌/车型/归属）、`TollTransaction`（车牌/入口/出口/车道/费用/时间） |
| 关系 | `has_lane`（站→车道）、`enters`（车→入口站）、`exits`（交易→出口站）、`passes`（车→出口站） |
| 术语 | 收费站、车辆、车道、通行交易、拥堵、通行费、车牌、车型、入口站、出口站、通行时间、通行、驶入、驶出、包含车道 |

这份 JSON 同时是对「一段话 → 本体」LLM 解析结果的**离线对照答案**：
`business.md` 是输入，本文件是期望输出，`bootstrap.py` 直接按它装载。

## 说明与边界

- 本目录只写 `examples/toll/`，不改 `src/`、`tests/`、`frontend/`，不动
  `.onto/platform.db` 里其它工作空间；脚本不会重启 8100 服务。
- `data/toll.db` 是生成物，可随时 `python data/build_db.py` 重建；
  生成脚本固定种子 `20260506`，重建结果逐字节等价。
- 后续平台上线 `POST /api/workspaces/{wid}/ontology/draft`（一段话 → 本体）后，
  已在 `bootstrap.py` 提供可选 `--use-llm` 分支：把 `business.md` 交给该接口解析并
  写入本体；默认仍走 `expected-ontology.json`，保证幂等与可复现。
