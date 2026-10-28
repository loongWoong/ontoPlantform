# v0.3 控制回路修复进度与证据

版本：`0.3.0`  
基线：`v0.2.0`（后端 264 个测试通过）  
本文件记录实际落地的代码、测试与仍然存在的边界。所有数字来自本机执行结果，未测量的一律写“未测量”。

## 1. 执行摘要

控制回路报告中的 15 个问题已经全部进入代码和测试。13 项完成，2 项部分完成：P1-10 投影异步执行、P1-11 本地默认身份。剩余边界还包含内核删除与 upsert 仍分属两个内核事务。前端整改与构建结果见第 6 节，WP5 迁移与发布演练见第 7 节。

后端全量测试：

```text
$ python -m pytest -q
300 passed in 38.72s
```

真实 HTTP 冒烟：

```text
$ python scripts/e2e_v03.py
16 HTTP checks passed
```

发布演练与可访问性门禁：

```text
$ python scripts/rehearse_v03.py
REHEARSAL PASSED

$ python scripts/axe_v03.py
10 views, 0 violations, 0 serious/critical
```

## 2. 问题矩阵

| ID | 结论 | 代码落点 | 自动化证据 |
|---|---|---|---|
| P0-1 成功判定失真 | 已修复 | `ports/runtime.py` 的 `InvocationResult.ok` 只认 `succeeded`；后置条件失败回写 `failed`；超时回写 `unknown` | `tests/test_truth_contract.py`、`tests/test_contract_verifier.py` |
| P0-2 幂等与超时语义 | 已修复 | `application/execution_ledger.py` 的 CAS 认领、三态记录、未知解析；`execution_runner.py` 有界执行 | `tests/test_execution_ledger.py`（100 并发同键只执行一次） |
| P0-3 金丝雀不隔离 | 已修复 | `provider.py` 每个 `(workspace, ring)` 独立运行时；金丝雀不写当前环；提升要求新鲜检查 | `tests/test_release_rings.py`、`tests/test_release_recovery.py` |
| P0-4 SLO 零样本假成功 | 已修复 | `slo.py` 窗口、样本数、`no_data/insufficient/breach`；`metrics.py` 窗口过滤 | `tests/test_truth_contract.py`、`tests/test_limits_slo.py` |
| P1-5 发布崩溃半状态 | 已修复 | `deployment.pending/active` 指针、发布失败回滚指针、`recovery.py` 重启恢复 | `tests/test_release_recovery.py` |
| P1-6 对账假阳性与不收敛 | 已修复 | 归属索引、`mapping.filter` 过滤、`additive/authoritative` 模式、控制器连续 3 轮未收敛开待办 | `tests/test_reconciler_ownership.py`、`tests/test_controller.py` |
| P1-7 Outbox 假投递 | 已修复 | 无消费者记为 `no_subscribers`；失败退避；5 次进死信；`todo.opened` 内部消费者生成通知并记录消费者名 | `tests/test_outbox_delivery.py` |
| P1-8 目标只在读取时评估 | 已修复 | 调度器周期评估；`GET` 不再写样本；关闭待办前重新评估，越界拒绝 | `tests/test_objective.py`、`tests/test_scheduler.py` |
| P1-9 缺少调度器 | 已修复 | `application/scheduler.py`；健康接口与 `GET /api/scheduler` 显示任务状态、最后运行时间与下次计划；失败隔离 | `tests/test_scheduler.py` |
| P1-10 投影完整性 | 部分修复 | 删除随 `SemanticDelta.deletes` 进入网关；跳过运行也记录 `checked_at`；超过行数上限在应用前明确失败并记录失败运行；同步返回 `run_id`，可查询运行状态 | `tests/test_projection.py`；异步运行队列未实现，见第 5 节 |
| P1-11 身份边界 | 部分修复 | 令牌模式拒绝匿名并按令牌映射 actor；非回环必须令牌；本地模式仅回环 | `tests/test_truth_contract.py::test_token_mode_rejects_anonymous_http`；本地默认 actor 仍是 `admin`，见第 5 节 |
| P2-12 Agent 自证 | 已修复 | 目标谓词验收；最大步数返回失败；运行记录、步骤、耗时、用量字段持久化 | `tests/test_intelligence.py`（含 `agent_run`/`agent_step` 持久化断言） |
| P2-13 观测显示失真 | 已修复 | 前端 `ValueView`、`lib/time.ts`、快照时间；Vitest 覆盖格式化和时区 | 前端测试见第 6 节 |
| P2-14 模型配置全局且泄露密钥 | 已修复 | `application/llm_config.py` 工作空间级配置；`security.py` 加密；密钥只写不读；审计 | `tests/test_truth_contract.py` |
| P2-15 界面承诺超过实现 | 已修复 | 状态文案由 `status` 派生；无数据不显示绿色；未知不写成功；事件消费者缺失不写成“未配置” | 前端测试见第 6 节 |

## 3. 新增与修改的关键契约

### 3.1 执行三态

`execution` 文档状态为 `running | succeeded | failed | unknown`。`unknown` 既不等于成功也不等于失败，不会被自动重试。调用方收到 HTTP 202 与 `retryable=true`，必须显式解析：

```text
POST /api/workspaces/{wid}/executions/{execution_id}/resolve
{"user": "admin", "outcome": "applied|not_applied", "note": "证据"}
```

解析只允许发生在 `unknown` 且不在执行中的记录上。

### 3.2 发布环

```text
POST /api/workspaces/{wid}/releases/{release_id}/canary   部署到隔离环
POST /api/workspaces/{wid}/releases/canary/check          健康检查并记录时间
POST /api/workspaces/{wid}/releases/canary/promote        要求新鲜检查
POST /api/workspaces/{wid}/releases/rollback              回滚到指定 Release
```

`deployment/current` 是重启后的运行时事实来源。启动时 `recover_deployment` 完成未提交的部署或恢复 `base_release_id`，并发出 `schema.recovered` 或 `deployment.failed`。

### 3.3 工作空间模型配置

```text
GET /api/workspaces/{wid}/llm-config
PUT /api/workspaces/{wid}/llm-config
```

响应只包含 `api_key_masked`，明文密钥不返回。配置使用 `ONTO_SECRET_KEY` 或 `.onto/secret.key` 加密，审计动作 `llm.configured`。

### 3.4 投影运行查询

```text
GET /api/workspaces/{wid}/projections/{projection_id}/runs/{run_id}
```

同步运行仍返回 `run_id`，事后可用该接口查 `status`、`watermark`、`object_refs`、`error` 和 `checked_at`。超过 `row_limit` 时不写入任何对象，先记录一条 `failed` 运行再返回 400。

### 3.5 调度器

```text
GET /api/scheduler
{
  "running": true,
  "tasks": [
    {"name": "outbox.dispatch", "runs": 3, "failures": 0, "last_error": null,
     "last_run_at": "2026-10-28T08:00:00+00:00",
     "next_run_at": "2026-10-28T08:00:05+00:00"}
  ]
}
```

任务：`outbox.dispatch`、`objectives.evaluate`、`controller.cycle`。单个任务异常不会终止其他任务。状态包含最后运行时间与下一次计划时间；两者由服务端时钟计算，界面不自行推算。

### 3.6 优雅停止与 SQLite 审计

`Platform.close()` 先停止调度器，再释放 SQLite 仓储和审计连接；HTTP 服务的 `finally` 调用它，进程重启后不会留下占用句柄。审计日志与 `documents` 表共用同一 SQLite 文件时，审计写入复用仓储连接和事务，避免 `BEGIN IMMEDIATE` 期间第二个连接报 `database is locked`。

回归测试：`tests/test_persistence.py::test_sqlite_audit_joins_the_repository_transaction`、`tests/test_release_rings.py::test_sqlite_canary_check_works_while_audit_is_transactional`。

## 4. 迁移与升级

`scripts/migrate_v03.py` 直接读写 SQLite 的 `documents` 表，不启动平台、不发布 Release。

```bash
python scripts/migrate_v03.py --db .onto/platform.db          # 只预览
python scripts/migrate_v03.py --db .onto/platform.db --apply  # 写入
```

迁移规则：

| 文档 | 变更 |
|---|---|
| `execution` | `running` 变 `unknown` 且 `in_flight=false`；补齐 `attempt`、`updated_at` |
| `deployment` | 缺 `status` 补 `active` |
| `projection_run` | 缺 `object_refs` 补空列表，避免把旧运行误判为拥有全部对象 |
| `event` | 缺 `attempts` 补 0 |
| `release` | 缺 `fingerprint` 补空串，部署时重新计算 |

迁移可重复执行，第二次输出 `nothing to migrate`。测试：`tests/test_migration_v03.py`。

本机实际执行（2026-10-28）：

```text
$ python scripts/migrate_v03.py --db .onto/platform.db
11 document(s) would change; rerun with --apply to write
$ python scripts/migrate_v03.py --db .onto/platform.db --apply
migrated 11 document(s) to 0.3
$ python scripts/migrate_v03.py --db .onto/platform.db
nothing to migrate (364 documents inspected)
```

迁移前已把整个 `.onto` 目录备份到 `.onto-backup-v02-20261028-161831`（11,324 个文件，3,822,732 字节）。迁移后健康接口返回 `0.3.0` / `api_version=3`，调度器三个任务运行且 `failures=0`。

## 5. 明确未完成与残余边界

1. 内核删除与 upsert 仍是两个内核事务。平台网关在同一把锁内先执行删除、再执行 upsert，失败时做补偿 upsert；这保证了应用层不会出现“先写 upsert 再删失败”的顺序，但不是跨两个内核事务的原子提交。真正的原子性需要内核提供带删除语义的 `apply_delta`。
2. 本地单用户模式仍接受请求体里的 `user`，缺省为 `admin`。这是回环地址的兼容行为；非回环绑定在没有 `ONTO_API_TOKEN` 时直接拒绝启动。令牌模式已拒绝匿名并固定映射 actor。对象级 ACL 不在本次范围。
3. 投影运行是同步调用。接口返回 `run_id`，并记录 `checked_at`、`watermark`、`object_refs`，`GET /api/workspaces/{wid}/projections/{pid}/runs/{run_id}` 可查运行状态；但没有后台任务队列，大数据源仍会阻塞请求线程。
4. 控制器连续 3 轮未收敛会停止并开 `controller_non_convergence` 待办，但不会自动修复越界根因。
5. `agent_run.usage` 仅当供应商返回 usage 时记录，否则为 `null`。测试用的 echo 供应商不返回 token，因此显示为空，不估算。

## 6. 前端整改与证据

```text
$ npm test
Test Files  7 passed (7)
Tests       46 passed (46)

$ npm run build
4602 modules transformed
dist/assets/index-DdzzVFZn.js   427.31 kB
dist/assets/index-BWqglcF-.css   47.21 kB
```

`npm run build` 实际执行 `tsc --noEmit && vite build`，类型检查通过。

浏览器冒烟（Playwright 连接本机 headless Chrome，采样时间 2026-10-28 16:15 至 16:22）：

- 工作台、本体、数据、能力、应用、对象实例、运行、AI 助手、设置 9 个页面全部加载。
- 9 个页面控制台错误 0，页面异常 0。
- 桌面 1440 宽度下 `scrollWidth - innerWidth = -15`（滚动条宽度，无横向溢出）；移动端 390 宽度下为 -15，同样无横向溢出。修复前运行页移动端为 +142。
- 运行页三个分区可切换，发布分区显示“隔离验证环（不接真实流量）”；后台作业显示服务端返回的最后运行时间与下一次计划时间，不再显示“服务端未返回”。
- 设置页身份卡显示“本地操作者（回环地址，缺省 admin）”，不再声称本地模式不校验调用者。
- 全部工作空间 348 条事件记录中，不存在“投递状态已投递、消费者未配置”的自相矛盾行。
- 采样期间 API 4xx/5xx 0。
- `python scripts/axe_v03.py` 对 10 个视图运行 axe-core 4.14.0：0 violation，0 serious/critical。修复前每个页面有 `label-content-name-mismatch`（serious）与标题层级问题。结果保存在 `docs/axe-v0.3.json`。axe 不替代真实屏幕阅读器手动测试，本项没有屏幕阅读器测量值。

前端改动集中为：

- 唯一状态映射 `lib/status.ts`：只有 `succeeded` 绿色，`unknown/no_data/insufficient/unrouted` 都不显示成功。
- `lib/time.ts` 统一 UTC 到本地时间并显示 `UTC+08:00`；非法时间直接报错。
- `components/ValueView.tsx` 结构化展示对象、数组和时间值。
- `api.ts` 对齐 `/canary`、`/executions`、`/llm-config`、带窗口的 `/metrics|/slo`、`/scheduler`；移除固定 `user=admin`；令牌只进 `sessionStorage`；API Key 不落 `localStorage`。
- 能力执行区分成功、失败、未知、契约未通过；未知只提供“查证已生效/未生效”，没有自动重试链路。
- 数据页对服务端未返回的字段显示“未返回”，不推算。

## 7. WP5 发布演练与门禁

### 7.1 演练命令

```bash
python scripts/migrate_v03.py --db .onto/platform.db          # dry-run
python scripts/migrate_v03.py --db .onto/platform.db --apply  # 写入
python scripts/rehearse_v03.py                                # 发布/回滚/重启演练
python scripts/e2e_v03.py                                     # 16 个 HTTP 场景
python scripts/axe_v03.py                                     # 可访问性门禁
```

### 7.2 发布与回滚演练结果

`scripts/rehearse_v03.py` 使用临时 SQLite 平台，实际执行并断言：

- 发布新 Release 后 `deployment/current` 指向新版本。
- 回滚后 `deployment/current` 恢复上一版且状态为 `active`。
- 关闭旧平台后用同一数据库重新构建，运行时 schema 与回滚后的 Release 一致，未产生 `schema.fingerprint_mismatch`。
- 金丝雀部署不移动 `current`；金丝雀写入不进入服务环；健康检查通过后提升成功。
- 注入损坏 Release 后部署失败，`current` 保持在原 Release，并记录 `deployment.failed`。

结果：15 项断言全部 `PASS`，输出 `REHEARSAL PASSED`。

### 7.3 验收场景映射

| 计划场景 | 自动化证据 |
|---|---|
| 1 空工作空间不假绿 | `tests/test_limits_slo.py`；E2E 断言空工作空间成功率为 `no_data` 且 `ok=false`；无投影时新鲜度为 `not_applicable`，有投影但从未运行时为 `no_data` |
| 2 后置条件失败显示 `failed` | `tests/test_truth_contract.py`、`tests/test_contract_verifier.py` |
| 3 超时返回 `unknown` 且不自动重试 | `tests/test_execution_ledger.py`；E2E 的 `/executions/by-key/e2e-timeout` |
| 4 同键并发只执行一次 | `tests/test_execution_ledger.py`、`tests/test_concurrency_100.py` |
| 5 金丝雀隔离、检查后提升 | `tests/test_release_rings.py`；`scripts/rehearse_v03.py`；E2E canary 路由 |
| 6 回滚重启指纹一致 | `tests/test_release_recovery.py`；`scripts/rehearse_v03.py` |
| 7 无消费者事件记为 `unrouted` | `tests/test_outbox_delivery.py`；迁移后 348 条事件抽检无“已投递但无消费者” |
| 8 消费者失败重试、死信 | `tests/test_outbox_delivery.py` |
| 9 目标越界开单个待办、关闭被拒绝 | `tests/test_objective.py`、`tests/test_scheduler.py` |
| 10 投影删除失败不留半写、跳过刷新检查时间 | `tests/test_projection.py`（含行数上限先失败后记录） |
| 11 对账归属与控制器 3 轮未收敛 | `tests/test_reconciler_ownership.py`、`tests/test_controller.py` |
| 12 匿名 401、viewer 可读不可写 | `tests/test_truth_contract.py`、`tests/test_auth.py` |
| 13 Agent 自证不通过、有证据、trace 可查 | `tests/test_intelligence.py` |
| 14 对象详情无 `[object Object]`、as-of 本地时区 | 前端 `ValueView` / `lib/time.ts` Vitest |
| 15 模型密钥不回显、工作空间隔离 | `tests/test_truth_contract.py` |

### 7.4 门禁状态

| 门禁 | 结果 |
|---|---|
| 后端全量测试 | `300 passed` |
| 真实 HTTP E2E | `16 HTTP checks passed` |
| 前端 Vitest | `7 files / 46 tests passed` |
| 前端构建 | `tsc --noEmit && vite build` 通过 |
| axe-core | `10 views, 0 violations, 0 serious/critical` |
| 迁移 | dry-run 11 条，apply 11 条，二次 dry-run 0 条 |
| 发布演练 | `REHEARSAL PASSED` |
| 手工浏览器冒烟 | 9 页无控制台/API 错误，桌面与移动端无横向溢出 |

仍未闭环的边界见第 5 节，不在本文件里伪装成已完成。
