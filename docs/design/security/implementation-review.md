# 权限内核实现完成度 Review

> 对照文档：[permission-model.md](permission-model.md) · 逻辑图：[permission-architecture.svg](permission-architecture.svg) · 效果图：[permission-console.svg](permission-console.svg)
>
> 评审日期：2026-11-04 · 代码：`ontoPlantform` · 测试：`pytest` 599 passed / `vitest` 46 passed
>
> 本文档描述**当前实现**，不把计划中的能力写成已交付；未完成或有意保持轻量的部分在 §4 集中列出。

## 0. 结论

**权限内核的核心闭环与 V0.5 治理增强已落地，可以支撑“单工作空间、单部署实例”的企业治理场景。**

已实现的主链路：

- L1 Principal / L2 TenantContext / L3 RoleBinding + Group / L4 scope-relation-field 四层模型；
- 自带 PDP 固定判定顺序，未命中即 deny，`denyRoles` 优先于 allow；
- 策略 `draft → validated → approved → published → deprecated` 生命周期，作者与审批人职责分离；
- N-of-M 多级审批、审批人去重、乐观锁（`expected_version` / `If-Match`）；
- 所有 `build_platform` 创建的仓库统一包一层 `GovernedRepository`，从请求上下文固定租户与行 scope；
- 普通 allow 决策逐条落库，`decision_id` 写入审计事件，拒绝也有证据；
- 异步/调度任务携带 HMAC 签名并带 TTL 的 `AuthorizationContext`，执行前重新对当前策略求值；
- 导出、Webhook、Agent 的 LLM 观察上下文统一走同一个 masker / PEP；
- 组织树与物化 `org_path`、OIDC / LDAP / SCIM 身份目录、AI 策略草案；
- 控制台新增成员/组/关系、组织树、集成（SCIM、异步任务、Webhook、导出）等页签。

**本轮生产加固已落地（七项）**：租户→工作空间一对多聚合与租户角色；SQLite 持久化异步任务队列（重启恢复、执行后落状态）；SCIM Groups CRUD/Patch/过滤/分页；OIDC Discovery + JWKS 缓存与未知 `kid` 强制轮换；TOTP MFA + 双人限时 break-glass；决策缓存（TTL + 安全写全量失效）与 SQL 下推；审批委派、过期语义与风险分级审批人数。对应测试见 `tests/test_security_enterprise.py`（13 项）及各模块回归。

对外仍应准确表述边界：这是“核心 IAM + 企业治理增强 + 本轮生产加固”，不是完整商用 IAM；剩余差距（外部 CA、分布式调度、多副本缓存一致性、SCIM 批量/增量等）集中在 §4。

---

## 1. 验收矩阵（设计条目 → 实现 → 测试）

| 设计条目 | 实现锚点 | 验证 | 状态 |
|---|---|---|---|
| L1 Principal / 租户 / 资源 / 决策 / 义务模型 | `src/onto_platform/domain/security/model.py` | `test_decision_denies_unknown_action_and_outsider` | 完成 |
| L2 租户上下文，租户/工作空间服务端解析 | `src/onto_platform/application/security/service.py` (`tenant_context`) | `test_tenant_context_requires_membership` | 完成 |
| L3 RoleBinding、组闭包、继承、`denyRoles` | `service.py` (`roles_for`, `_group_closure`) | `test_role_binding_condition_uses_principal_attributes`、`test_deny_roles_beat_allow` | 完成 |
| L4 行 scope、ReBAC、字段掩码 | `domain/security/filters.py`、`application/security/masking.py` | `test_row_scope_and_field_mask_apply_at_query_boundary` | 完成 |
| PDP 固定顺序与缺省拒绝 | `service.py` (`decision_for`, `check_action`) | `test_decision_denies_unknown_action_and_outsider` | 完成 |
| 策略校验、规范化、风险提示 | `application/security/policy.py` | `test_policy_validation_rejects_unscoped_and_unsafe_values` | 完成 |
| 策略生命周期与 SoD | `service.py` (`create/validate/approve/publish`) | `test_multi_level_approval_needs_distinct_approvers` | 完成 |
| N-of-M 多级审批、进度展示 | `service.py` (`approve_policy`)、`Security.tsx` | 集成测试 + 控制台审批进度 | 完成 |
| 乐观锁 | `service.py` 各变更入口（`expected_version`）、HTTP `If-Match` | `test_stale_version_is_rejected` | 完成 |
| allow / deny 证据逐条落库 | `service.py` (`record_decision`)、审计关联 `decision_id` | `test_allow_decisions_are_evidence_and_audit_links_decision_id` | 完成 |
| 遗留角色检查的 allow 也落库 | `service.py` (`check_role` 合成决策并记录) | `test_legacy_role_allow_is_recorded_as_evidence` | 完成 |
| OIDC/LDAP 会话 token 认证 HTTP | `interfaces/api.py` (`_session_actor`) | `test_session_token_authenticates_http_requests` | 完成 |
| OIDC 只按 `(external_id, provider)` 关联本地用户 | `identity.py` (`oidc_login`) | `test_oidc_login_never_links_a_local_name_to_an_idp_subject` | 完成 |
| `/changes`、`/events`、`/audit` 不能绕过 PEP | `api.py` 对应 GET 路由 | `test_session_token_authenticates_http_requests`（无成员会话 403） | 完成 |
| 身份目录对普通读者按工作空间成员过滤 | `api.py` `identity/users` | `test_identity_directory_requires_security_manage` | 完成 |
| Agent 能力的输出/变更也过 masker | `agent_runner.py` (`invoke_capability`) | `test_agent_observations_are_passed_through_the_masker` 等 | 完成 |
| 策略编辑使旧审批失效 | `service.py` (`update_policy_draft`) | `test_editing_an_approved_policy_invalidates_existing_approvals` | 完成 |
| 智能视图焦点/生成数据过行/字段 mask | `smartui/bridge.py` (`_find`/`_select_focus`) | 智能视图现有 39 项测试 + 安全回归 | 完成 |
| 控制面行也固定租户；移除裸仓库委托 | `scoped_repo.py` (`_pin`、`transaction`/`close`) | `test_governed_repository_blocks_cross_workspace_access` | 完成 |
| 导出顶层字段脱敏失败即拒绝 | `distribution.py` (`_mask_top_level_fields`) | 导出测试 + 全量回归 | 完成 |
| SCIM 路由可达且未配置 token 时 403 | `api.py` GET 路由、`identity.py` | `test_scim_http_route_fails_closed_without_configured_token` | 完成 |
| Webhook 自动投递与 outbox 解耦 | `bootstrap.py` (`webhooks.deliver`)、`distribution.py` | `test_webhook_dispatch_survives_outbox_dispatch_and_is_scheduled` | 完成 |
| 系统调度任务不继承上一条决策 | `scheduler.py` (`_run` 前后 clear) | `test_scheduler_does_not_leak_authorization_context_between_tasks` | 完成 |
| 策略草案（不落库） | `application/security/draft.py`、`PolicyDraftService` | `test_policy_draft_is_not_stored_or_published` | 完成 |
| 仓库级租户/scope 固定 | `infrastructure/persistence/scoped_repo.py`、`GovernedRepository` | `test_scoped_repository_pins_tenant_and_filters_rows` | 完成 |
| 异步任务序列化授权上下文 | `application/security/async_tasks.py`、`scheduler.py` | `test_deferred_action_serializes_context_and_denies_tampering` | 完成 |
| 签名上下文防篡改与过期 | `application/security/context.py` | `test_signed_async_context_round_trip_revalidates_and_rejects_tampering` | 完成 |
| 去掉 `signature` 的上下文也拒绝 | `context.py` (`_decode`) | 同上（unsigned payload） | 完成 |
| 组织树与 `org_path` | `application/security/organization.py` | `test_org_tree_populates_principal_attributes` | 完成 |
| OIDC / LDAP / SCIM | `application/security/identity.py`、REST 适配 | `test_identity_directory_requires_security_manage` 等 | 完成（基础版） |
| SCIM 未配置 token 时拒绝而非匿名 | `identity.py` (`SCIMService._guard`) | `test_scim_without_configured_token_fails_closed` | 完成 |
| 导出统一走 PEP + masker | `application/security/distribution.py` (`ExportService`) | `test_export_masks_fields_and_never_mutates_input` | 完成 |
| Webhook 载荷脱敏 + 签名 | `distribution.py` (`WebhookService`) | `test_webhook_masks_payload_and_signs_delivery` | 完成 |
| Agent / AskOntology 观察脱敏 | `agent_runner.py` (`masker`)、`intelligence_usecases.py` | `test_agent_observations_are_passed_through_the_masker`、`test_ask_answer_is_composed_only_from_masked_rows` | 完成 |
| 控制台策略/预览/证据/成员/组织/集成 | `frontend/src/views/Security.tsx`、`SecurityPeople.tsx` | `npm test` + 构建 + DOM 冒烟 | 完成 |
| 真实多租户聚合与租户角色 | `application/security/tenancy.py`、`CreateWorkspace` | `test_tenant_groups_workspaces_and_tenant_roles_apply`、`test_tenant_routes_create_workspace_and_aggregate` | 完成 |
| 持久化异步队列 + 重启恢复 | `application/security/async_tasks.py`、SQLite `documents` | `test_async_queue_survives_restart_and_runs_once` | 完成 |
| SCIM Groups CRUD/Patch/过滤/分页 | `identity.py` (`SCIMService`) | `test_scim_groups_crud_filter_patch_and_delete`、`test_scim_groups_over_http` | 完成 |
| OIDC Discovery + JWKS 轮换 | `identity.py` (`OIDCVerifier`、`JWKSCache`) | `test_oidc_discovery_jwks_and_key_rotation` | 完成 |
| TOTP MFA + 双人限时 break-glass | `application/security/emergency.py` | `test_totp_replay_protection_and_recovery_codes`、`test_break_glass_requires_second_owner_and_is_time_boxed` | 完成 |
| 决策缓存 + SQL 下推 | `application/security/cache.py`、`service.py`、`scoped_repo.py`、`filters.py` | `test_decision_cache_hits_and_is_invalidated_by_security_write`、`test_sql_pushdown_avoids_loading_every_row` | 完成 |
| 审批委派、过期与风险分级 | `service.py` (`_effective_approver`)、`model.py` (`policy_risk_tier`) | `test_approval_delegation_counts_for_the_principal`、`test_risk_tier_raises_required_approvals_and_expired_delegate_is_ignored` | 完成 |

> 说明：测试名称以仓库中实际存在的为准；集成测试集中在 `tests/test_security_integration.py`，各安全子模块另有独立单测。

---

## 2. 关键实现

### 2.1 认证上下文与 PEP

- `AuthorizationContext` 由 Principal、租户、动作、资源、决策 id、scope、obligations 组成，序列化时带 HMAC 签名和 TTL（默认 900 秒）。
- 没有部署密钥时使用进程级 `_PROCESS_SECRET`：本地/单进程仍能验证签名，跨进程重启后签名自然失效。
- `activated_verified` 在 worker 执行前重新调用 `decision_for`；策略、角色、组成员变化会立即反映到已入队任务。
- HTTP 适配层在请求开始时清空旧 ContextVar，在鉴权后 `bind`，避免上一条请求的租户/主体泄漏到下一次调用。
- HTTP `Authorization: Bearer` 依次接受 `ONTO_API_TOKEN`（机器 token）和 OIDC/LDAP 登录签发的会话 token；会话主体必须仍存在于身份目录且处于启用状态，伪造、过期或已停用的 token 一律 401，绝不回退到本地 `admin`。OIDC 只按 `(sub, iss)` 关联已有目录用户；`preferred_username` 与本地账号重名时创建独立身份，绝不把 IdP 主体并入本地管理员。

### 2.2 策略生命周期、审批与锁

- `create_policy` 创建 `draft`；`validate_policy` 只做校验并推进到 `validated`；`approve_policy` 记录审批人并累计 N-of-M 进度；委派、过期与风险分级人数见 §2.7。
- 审批人不能是作者，重复审批被拒绝；未满足所需人数前 `publish_policy` 始终拒绝。编辑草案会改变文档 hash，任何已有审批立即清空，避免“先批后改、再补一票”绕过 N-of-M。
- 所有修改入口支持 `expected_version`；HTTP 侧对 `PUT/PATCH` 读取 `If-Match`，不一致返回冲突而不是覆盖。
- 每次 allow/deny 默认落一条 `authorization_decision`；`/authorize/preview` 和内部探针显式关闭记录，避免预览污染证据。
- 证据保留有上限（`MAX_DECISIONS_PER_WORKSPACE`），超出后批量清理，读取接口默认限制 500 条。

### 2.3 组织树、身份与 SCIM

- `OrganizationUnit` 使用物化路径（`/root/child/`），支持父子约束、循环防护、按路径/子树查询、成员集合。
- 删除有成员或子节点的组织单元会被拒绝；只有空子树允许 `cascade`。
- Principal 解析时从组织树读取真实 `org_path` / `org_unit`，请求方伪造的 `attributes` 不能覆盖。
- `IdentityDirectory` 默认平台级（适合一个 IdP 服务多个工作空间），提供用户 upsert、外部 id 查找、启用/禁用。
- `OIDCVerifier` 支持 HS256 与 RS256（Discovery/JWKS，见 §2.7）、`exp/nbf/iss/aud` 校验和时钟偏移；`LDAPAuthenticator` 通过可注入连接做认证；`SessionIssuer` 签发带 TTL 的会话 token。
- `SCIMService` 支持 Users 列表/创建/Patch，以及 Groups 全 CRUD、Patch、过滤与分页（见 §2.7）；SCIM 2.0 HTTP 入口使用独立 `ONTO_SCIM_BEARER_TOKEN`（未配置即拒绝），控制台使用的身份目录 REST 入口则分别要求 `security.manage` / `security.read`。

### 2.4 异步与调度任务

- `AsyncTaskService.enqueue` 先要求 `capability.invoke`，再生成任务并保存签名上下文；任务本身不携带 bearer 凭据。任务行持久化到 `async_task`，启动时 `restore()` 恢复未完成任务（见 §2.7）。
- 签名内容被修改、超过 TTL、或当前策略已不允许时，worker 在执行前拒绝，任务不会产生副作用；worker 侧重新求值的 allow/deny 同样写入决策证据。
- `Scheduler` 的一次性任务执行后移除；执行失败保留失败痕迹，不会重试成“成功”。
- `ExecutionRunner` 把请求 ContextVar 复制到 daemon worker，能力内部的仓库写入仍带原请求的租户/scope。

### 2.5 统一脱敏：导出、Webhook、LLM

- `ExportService.export_objects` 先调用 `filter_objects`（同一 PEP 做行 scope + 字段 mask），再修复扁平导出中的同名字段，JSON/CSV 都只输出已授权、已脱敏内容。
- 决策证据是审计数据，不额外套字段 mask，但导出前要求 `security.read`。
- `WebhookService.dispatch_webhooks` 由 scheduler 的 `webhooks.deliver` 周期调用；它只看独立的 `webhook_delivered_at`，不看 in-process outbox 的 `dispatched_at`，因此事件可同时被本地订阅者和外部 Webhook 消费。载荷用事件 actor 的 decision 计算 mask；mask 失败时只发最小载荷，不发送原始字段；请求带 `X-Onto-Signature`。
- Webhook secret 只写不读，HTTP 响应中固定返回 `"***"`。
- Agent 的 `find/related/get` 观察、`AskOntology` 的结果与“自然语言答案”都从脱敏后的行组装；agent-kind 能力在每次工具调用时按当前执行 actor 解析 masker，调度/无调用者上下文直接 fail closed；masker 异常时 Agent 返回 redacted 观察，Ask 不返回原始业务行。

### 2.6 前端

- `Security` 控制台共 7 个页签：总览、角色绑定、成员与组织、策略预览、策略生命周期、集成、决策证据。
- 成员页支持成员增删、组与组成员、组织树、关系元组；集成页支持 SCIM 目录、异步任务、Webhook、导出下载。
- 预览请求默认 `record=false`；策略表展示 N-of-M 审批进度；导出通过 blob 下载，避免 token/响应体泄漏到 URL。
- 视觉保持暖灰单色编辑风格，不引入重型图表库；构建产物写入 `src/onto_platform/interfaces/web/dist`。

### 2.7 第七轮生产加固（七项）

- **多租户聚合**：`TenantService` 在 `tenant`（全局 `None` 键）与 `tenant_membership`（租户键）两类控制面行上聚合工作空间；`CreateWorkspace` 可选择 `tenant_id` 并把创建者写成租户 owner/editor；租户角色通过 `SecurityService._membership_roles` 合并进该租户下每个工作空间。`/api/tenants`、`/api/tenants/{id}/workspaces|members|summary` 只返回该租户的工作空间，跨租户读取仍由 `GovernedRepository` 固定。
- **持久化异步队列**：任务写入 `async_task` 文档（`pending/running/done/failed`、`due_at`、`attempts`、`max_attempts`），启动时 `restore()` 把 `running` 回收为 `pending` 并重新挂到 scheduler；执行后写回状态与错误，不再只存在进程字典里。
- **SCIM Groups**：Groups `GET/POST/PUT/PATCH/DELETE`、`filter`（`eq/ne/co/sw/pr`、`and/or`、`members.value`）、`startIndex/count` 分页；`add` 成员为追加语义，`replace/remove` 为覆盖/删除；`externalId` 与 `meta.lastModified` 持久化。HTTP 入口与 Users 共用独立 SCIM bearer token，未配置仍 fail closed。
- **OIDC Discovery/JWKS**：`ONTO_OIDC_DISCOVERY_URL` 或 `ONTO_OIDC_JWKS_URI` 可启用 RS256；`JWKSCache` 按 TTL 缓存 JWKS，遇到未知 `kid` 强制重取一次；`iss/aud/exp/nbf` 与 HS256 旧模式共用同一校验路径。签发/验签不引入第三方依赖（PKCS#1 v1.5 SHA-256）。
- **MFA / break-glass**：TOTP（RFC 6238 SHA-1/30s/6 位）含时间步防重放与一次性恢复码；break-glass 必须由 owner/平台管理员发起，理由 ≥10 字符、最长 60 分钟、需第二人批准，已登记 MFA 时强制验证；批准后在 `roles_for` 动态生效，到期自动失效，全程审计。
- **决策缓存 + SQL 下推**：`DecisionCache` 以 `(tenant, workspace, principal, action, resource, policy_hash/epoch)` 为键，TTL 15s；所有 `security.*`、成员/租户/身份类审计写会清空缓存。`GovernedRepository.query` 把 PDP scope 与调用方过滤合并后编译为参数化 SQL（字段白名单、`json_extract` 访问 JSON），SQLite 直接下推，内存仓库走同一谓词，避免先全量加载再逐行过滤。
- **审批委派 / 风险分级**：`approval.delegates` 支持 `from/to/expiresAt/active`，过期或停用即按本人身份计数；审批记录同时保存 `actor` 与 `approver`，N-of-M、SoD 与去重都按“可追责主体”计算。`policy_risk_tier` 按通配动作、owner 授权、租户边界、字段掩码等确定性评估 `low/medium/high`；`approval.useRiskTiers=true` 时最低审批人数提升为 1/2/3，默认策略行为不变。

---

## 3. 补全与修复记录

| 问题 | 处理 |
|---|---|
| 普通 allow 不落库、预览污染证据 | allow 默认落库；preview/探针显式 `record=False`；审计关联 decision id |
| `ScopedRepository` 已实现但未全量替换 | `build_platform` 中统一包 `GovernedRepository`；控制面 kind 例外 |
| 异步/调度任务没有授权上下文 | 入队时签名快照；worker 端重新求值；篡改/过期/撤销都拒绝 |
| 导出、Webhook、LLM 上下文未统一脱敏 | 三条链路接入同一 `filter_objects` / masker；失败 fail closed |
| 组织树/`org_path` 缺失 | 物化路径树 + principal 属性解析 + 组织页签 |
| OIDC/LDAP/SCIM 缺失 | 基础验证器、认证器、SCIM 服务与 HTTP 入口 |
| 多级审批缺失 | N-of-M 审批进度、审批人去重、SoD |
| AI 策略草案缺失 | `PolicyDraftService`，支持自动/确定性模式，不落库不发布 |
| 成员/组/关系 UI 缺失 | `SecurityPeople` + `Security` 新页签 |
| 身份/组织/任务读取入口越权 | 为目录、组织树、异步任务读取补 `security.read`；写入口补 `security.manage` |
| AskOntology 答案绕过 masker | 答案改为从脱敏后的行组装，并加回归测试 |
| 遗留 `require()` 只 bind 不落库 | `check_role` 合成决策后也写 `authorization_decision`，与 action 检查一致 |
| OIDC/LDAP 登录后 token 不能访问 API | Bearer 解析加入会话目录校验；无效/停用 token 401，不回退本地 admin |
| Webhook 投递没有调度入口且被 outbox 抢先标记 | scheduler 增加 `webhooks.deliver`；投递只看独立的 `webhook_delivered_at` |
| 未配置 `ONTO_SCIM_BEARER_TOKEN` 时 SCIM 匿名可写 | 默认 fail closed；仅显式 `ONTO_SCIM_ALLOW_ANONYMOUS=1` 才允许本地匿名 |
| OIDC 按 `preferred_username` 合并本地账号导致接管 | 只按 IdP `(sub, iss)` 关联；重名时创建独立身份 |
| 变更/事件/审计路由未过 PEP | 补 `require(viewer)` / `security.read` |
| Agent 能力输出未脱敏 | `invoke_capability` 的 `output/changes` 也经过 masker |
| 策略编辑后旧的 N-of-M 审批仍有效 | 文档 hash 变化即清空审批 |
| 智能视图焦点数据未脱敏 | `_find` 统一走 `filter_objects` |
| 控制面仓库行可跨租户读取、`raw` 委托 | 控制面同样固定租户；显式 `transaction`/`close` |
| 导出顶层掩码异常时回退原文 | 异常即拒绝导出 |

### 3.1 第七轮生产加固记录

| 问题 | 处理 |
|---|---|
| `tenant_id == workspace_id`，无租户聚合 | `TenantService` + 租户角色合并 + `/api/tenants` 聚合接口；`CreateWorkspace` 支持 `tenant_id` |
| 异步任务只在内存，重启丢失 | `async_task` 持久化行、`restore()` 回收 `running`、执行后落 `done/failed` |
| SCIM 只有 Users，Groups 无 CRUD/过滤 | Groups 全 CRUD、Patch add/replace/remove、`eq/ne/co/sw/pr` 过滤与分页、`externalId` |
| OIDC 只有 HS256，无 Discovery/JWKS | Discovery 文档、JWKS TTL 缓存、未知 `kid` 强制刷新、RS256 手工验签 |
| 无 MFA/break-glass | TOTP + 恢复码 + 双人限时 break-glass，动态角色与到期自动失效 |
| 决策无缓存，列表逐行过滤 | TTL 决策缓存 + 安全写失效；scope/filter 编译 SQL 下推 SQLite |
| 审批无委派/风险分级 | 委派生效与过期语义、可追责主体去重、确定性风险分级与 1/2/3 审批人数 |

---

## 4. 仍未宣称完成的生产边界

本轮七项已实现，但以下能力仍不宣称达到商用 IAM 完整度，属于后续迭代：

1. **租户隔离是应用层 + 存储固定，不是数据库 RLS**：所有读经 `GovernedRepository`/显式租户聚合，SQLite 仍共享库文件；多租户 SaaS 若要求数据库级隔离，需要按租户分库或启用 RLS。
2. **决策缓存是单进程内缓存**：多副本部署没有跨节点失效总线；策略发布只在当前进程立即失效，其他副本需等 TTL 或接入 Redis/消息总线。批量授权、权限感知游标分页仍未实现。
3. **SQL 下推覆盖 SQLite 文档模型**：下推谓词经字段白名单与参数化编译，但 `json_extract` 不走表达式索引；超大表需要生成列/索引或物化读模型。
4. **审批是内核内工作流**：委派、风险分级与 N-of-M 已实现；没有 BPMN 引擎、超时升级、会签/或签编排、审批任务中心与通知。
5. **break-glass 是限时双人通道**：没有与 SIEM/工单系统联动的自动告警、会话录制、事后复盘流程；MFA 仅 TOTP，没有 WebAuthn/推送；TOTP secret 目前以文档行存储，生产应接入 KMS/加密列并限制控制面读取。
6. **SCIM 是 RFC 7644 核心子集**：Groups/Users CRUD、Patch、常用过滤器与分页已实现；没有 Bulk 端点、`/Me`、增量同步、跨 IdP 冲突合并与速率限制。
7. **OIDC 不涵盖完整 OP 侧能力**：支持 Discovery、JWKS 轮换、RS256/HS256 验签；没有前端通道登出、token introspection、DPoP、JWKS 多密钥滚动窗口的持久化缓存；LDAP 仍为注入式认证器。
8. **调度仍是单进程 scheduler**：任务已持久化并可重启恢复，但没有多副本抢占/租约、优先级队列、死信队列与可视化重试；生产多实例需外部队列。
9. **可观测性**：有审计与决策证据，但没有 SIEM 导出、指标、告警和证据分页/归档；`/security/cache/stats` 只是进程内计数。

---

## 5. 验证方式

```bash
# 后端全量
python -m pytest -q                     # 599 passed

# 权限专项
python -m pytest -q tests/test_security.py tests/test_security_distribution.py \
  tests/test_security_identity.py tests/test_security_organization.py \
  tests/test_security_integration.py tests/test_security_enterprise.py
                                        # 99 collected

# 前端
cd frontend
npm test -- --run                       # 46 passed
npm run build                           # tsc --noEmit + vite build
```

已人工验证：启动 API 后用管理员访问组织树、身份目录、Webhook/导出路由；用 viewer 访问身份写入口返回 403、读入口 200；控制台“成员与组织”“集成”页签完成 DOM 冒烟；新增租户/SCIM Groups/OIDC Discovery/break-glass/MFA/缓存/下推/委派均以回归测试覆盖。

---

## 6. 主要文件

| 层 | 文件 |
|---|---|
| 领域模型 | `src/onto_platform/domain/security/model.py`、`filters.py` |
| 策略与内核 | `src/onto_platform/application/security/service.py`、`policy.py`、`context.py`、`masking.py` |
| 组织/身份/租户 | `src/onto_platform/application/security/organization.py`、`identity.py`、`tenancy.py` |
| 分发/队列/应急 | `src/onto_platform/application/security/distribution.py`、`async_tasks.py`、`emergency.py`、`cache.py` |
| 仓库与下推 | `src/onto_platform/infrastructure/persistence/scoped_repo.py`、`sqlite_repo.py`、`domain/security/filters.py` |
| 装配与适配 | `src/onto_platform/bootstrap.py`、`src/onto_platform/interfaces/api.py` |
| 前端 | `frontend/src/views/Security.tsx`、`SecurityPeople.tsx`、`frontend/src/api.ts` |
| 测试 | `tests/test_security*.py`（99 个安全测试，含 `test_security_enterprise.py`） |

---

**最终判断**：核心权限模型、策略治理、审计证据、组织与身份、异步上下文、统一脱敏，以及本轮七项生产加固均已实现并有回归覆盖，达到“可在单实例/受控多工作空间环境落地，并对生产边界有明确说明”的完成度；距“完整商用 IAM/多副本 SaaS 平台”仍以 §4 列出的边界为准，不应按后者对外承诺。
