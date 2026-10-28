/** 与后端 REST 契约一一对应的类型与客户端。
 *  错误在这里统一翻译成业务语言 + 下一步指引（design.txt §42 第一原则）。 */

export type Lifecycle = "draft" | "validated" | "published" | "deprecated" | "none";

export interface Workspace { id: string; name: string; description: string; created_at: string; runtime_config?: Record<string, unknown> }
export interface PropertySpec { name: string; type: string; description?: string }
export interface ObjectTypeSpec { name: string; description: string; properties: Record<string, PropertySpec> }
export interface RelationSpec { name: string; source: string; target: string; description?: string; cardinality?: string }
export interface VocabTerm { term: string; concept: string; kind: string; aliases?: string[] }
export interface Ontology {
  name: string; description: string; version: number; schema_version?: number; lifecycle: Lifecycle;
  objects: Record<string, ObjectTypeSpec>;
  relations: Record<string, RelationSpec>;
  vocabulary: Record<string, VocabTerm>;
}
/* ---------------- AI 快速建模：一段业务描述 → 对象/关系/术语草稿 ---------------- */
export interface DraftProperty { name: string; type?: string; description?: string }
export interface DraftObject { name: string; description?: string; properties?: DraftProperty[] }
export interface DraftRelation {
  name: string; source: string; target: string; description?: string; cardinality?: string;
}
export interface DraftVocabTerm { term: string; concept: string; kind?: string; aliases?: string[] }
export interface OntologyDraft {
  objects?: DraftObject[]; relations?: DraftRelation[]; vocabulary?: DraftVocabTerm[];
}
/** POST /ontology/draft 的响应：apply=false 只预览，apply=true 同时写入草稿本体。 */
export interface DraftResult {
  applied: boolean;
  draft: OntologyDraft;
  warnings?: string[];
  ontology?: Ontology;
}

/** POST /ontology/draft 的请求：预览传 text；写入把预览得到的 draft 原样回传，
 *  后端不再调一次模型 –– 写进去的就是用户看到的那份。 */
export type DraftRequest =
  | { text: string; apply?: boolean; llm?: LlmChoice }
  | { draft: OntologyDraft; apply?: boolean; llm?: LlmChoice };

export interface RuntimeDescribe {
  ontology?: { name?: string; version?: number };
  objects: { name: string; description?: string; properties?: PropertySpec[] }[];
  relations: { name: string; source: string; target: string }[];
  functions: { name: string; description?: string }[];
}
export interface DataAsset { name: string; description?: string; config?: Record<string, unknown> }
export interface DataSource { id: string; name: string; kind: string; config: Record<string, unknown>; assets: Record<string, DataAsset> | DataAsset[] }
export interface ObjectMapping { asset: string; object_type: string; id_field: string; filter?: Record<string, unknown>; fields: { source_field: string; property: string }[] }
export interface RelationMapping { asset: string; relation: string; source_field: string; target_field: string }
export interface Projection {
  id: string; name: string; datasource_id: string;
  object_mappings: ObjectMapping[]; relation_mappings: RelationMapping[];
  mode?: "additive" | "authoritative" | string;
  managed_types?: string[];
  sync_interval_seconds?: number;
  last_check_at?: string | null;
  last_change_at?: string | null;
}
export interface CapabilityInput { name: string; type: string; description?: string }
export interface Capability {
  id: string; key: string; name: string; target_type: string; description: string;
  inputs: CapabilityInput[]; implementation: { kind: string; [k: string]: unknown };
  lifecycle: Lifecycle;
}
export interface AppView { alias: string; title: string; kind: string; object_type: string; properties: string[]; query_alias: string }
export interface AppQuery { alias: string; title: string; object_type: string; filters: Record<string, unknown>; expand: { relation: string; direction: string }[] }
export interface AppAgent { alias: string; goal: string; description?: string }
export interface ObjectiveSpec {
  metric: string; target: number; band: number; window: number;
  direction: string; samples?: { at: string; value: number }[];
  scope?: "workspace" | "application" | string;
  min_samples?: number;
}
export interface ObjectiveEvaluation {
  application_id?: string;
  application_name?: string;
  objective: { metric: string; target: number; band: number; window: number; direction: string } | null;
  current: number | null; target: number | null; band: number | null; deviation: number | null;
  trend: "up" | "down" | "flat"; trend_delta: number | null;
  ok: boolean;
  /** 没有样本时 available=false；此时不能显示达标。 */
  available?: boolean;
  evaluated?: boolean;
  reason?: string | null;
  status?: string | null;
  scope?: "workspace" | "application" | string;
  min_samples?: number;
  window_seconds: number; samples: { at: string; value: number }[];
  todo: Todo | null; generated_at: string;
  last_evaluation?: string | null;
}
export interface Todo {
  id: string; application_id: string; metric: string; title: string;
  detail: string; current: number; target: number; band: number;
  deviation: number; status: "open" | "resolved";
  created_at: string; updated_at: string; resolved_at: string | null;
  resolved_by?: string | null; evidence?: string | null;
}
export interface Application {
  id: string; name: string; description: string; lifecycle: Lifecycle;
  views: Record<string, AppView>; queries: Record<string, AppQuery>;
  agents: AppAgent[]; capabilities: string[];
  objective?: ObjectiveSpec;
}
export interface Obj { type: string; id: string; properties: Record<string, unknown> }
export interface ChangeEvent { seq: number; ts: string; source: string; function?: string; changes: Record<string, unknown>[] }
export interface StateSnapshot {
  ref: string; as_of_seq: number; head_seq: number; oldest_seq: number;
  exists: boolean; properties: Record<string, unknown>; changes_replayed: number;
}
export interface ObjectHistory {
  ref: string; head_seq: number; oldest_seq: number; changes: ChangeEvent[];
}
export interface AuditEvent { ts: string; actor: string; action: string; target: string; detail?: Record<string, unknown> }

/* ---------------- 发布 / 部署 / 观测 ---------------- */
export type DeploymentStatus = "pending" | "active" | "failed" | "superseded";
export interface CanaryCheckItem { name: string; ok: boolean; detail?: unknown }
export interface CanaryCheck {
  ok: boolean;
  release_id?: string | null;
  fingerprint?: string | null;
  checked_at?: string | null;
  state_mode?: string | null;
  checks?: CanaryCheckItem[];
}
export interface CanaryStatus extends CanaryCheck {
  /** 隔离环的运行时状态，例如 isolated / cloned；不接真实流量。 */
  state_mode?: string | null;
  promoted?: boolean;
  fresh?: boolean;
  check?: CanaryCheck | null;
}
export interface Release {
  id: string; workspace_id: string; schema_version: number | null;
  fingerprint: string; capability_ids: string[];
  previous_release_id: string | null;
  created_at: string; actor: string; note: string;
}
export interface Deployment {
  id: string; release_id: string; schema_version?: number | null;
  status?: DeploymentStatus | string | null;
  fingerprint?: string | null;
  state_mode?: string | null;
  checked_at?: string | null;
  checks?: CanaryCheckItem[];
  deployed_at?: string; actor?: string; promoted?: boolean; promoted_at?: string;
}
export interface ReleaseList {
  releases: Release[];
  deployment: Deployment | null;
  canary: Deployment | null;
  canary_check?: CanaryCheck | null;
  recovery?: { recovered_at?: string; action?: string; detail?: string }[];
}
export interface DomainEvent {
  id: string; event_id: string; type: string; event_version?: number;
  ts: string; workspace_id: string; actor: string;
  aggregate: { kind: string; id: string; version?: number | null };
  execution_id?: string | null;
  payload: Record<string, unknown>;
  dispatched_at: string | null; attempts: number; last_error: string | null;
  delivery_status?: "pending" | "retrying" | "delivered" | "unrouted" | "dead" | string | null;
  delivery?: string | null;
  consumer?: string | null;
  next_attempt_at?: string | null;
  dead_lettered_at?: string | null;
}
export interface WorkspaceDataQuality {
  projection_runs?: number | null;
  projection_skipped?: number | null;
  projection_failed?: number | null;
  open_todos?: number | null;
  unknown_executions?: number | null;
  events_unrouted?: number | null;
  events_dead_lettered?: number | null;
  last_check_at?: string | null;
  last_change_at?: string | null;
}
export interface WorkspaceMetrics {
  workspace_id: string; generated_at: string;
  window_seconds?: number;
  window_mode?: string;
  deployment: {
    release_id: string | null; schema_version: number | null;
    fingerprint?: string | null; status?: string | null;
  };
  indicators: Record<string, number | null>;
  slo?: SloReport;
  data_quality?: WorkspaceDataQuality;
  last_projection: { projection_id?: string; status?: string; watermark?: number | null; finished_at?: string; checked_at?: string } | null;
}
export interface SloIndicator {
  value: number | null;
  target: number | null;
  direction: string;
  samples: number;
  min_samples?: number;
  status: "no_data" | "insufficient" | "ok" | "breach" | "not_applicable" | string;
  reason?: string | null;
  available?: boolean;
  applicable?: boolean;
  ok: boolean;
}
export interface SloReport {
  workspace_id: string; generated_at: string;
  window_seconds?: number;
  targets: Record<string, number>;
  indicators: Record<string, SloIndicator>;
  breached?: string[];
  blocked?: string[];
  reason?: string | null;
  ok: boolean;
}
export interface VerifyProblem {
  kind: string; object_type: string; id?: string; property?: string;
  duplicate_of?: string; identity?: string[]; error?: string;
}
export interface VerifyResult {
  ok: boolean; checked: number; problems: VerifyProblem[];
  object_type: string | null; sampled: boolean; sample_size: number | null;
}
export interface ReconcileFinding {
  kind: string; projection_id?: string; object_type?: string;
  missing_in_runtime?: string[]; missing_in_source?: string[];
  /** additive / authoritative；后端未返回时界面不推断。 */
  ownership?: string;
}
export interface ReconcileResult {
  ok: boolean; findings: ReconcileFinding[];
  checked_at?: string | null; mode?: string | null; scope?: string | null;
}
export interface ControllerProposal {
  action: string; projection_id?: string; reason?: string;
}
export interface ControllerCycle {
  proposed: ControllerProposal[];
  accepted: ControllerProposal[];
  applied: { action: string; projection_id: string; run?: Record<string, unknown> }[];
  verified: boolean;
  converged?: boolean;
  escalated?: boolean;
  stopped?: boolean;
  consecutive_failures?: number;
  reason?: string;
  verification: ReconcileResult;
  cycle: {
    proposed: number; accepted: number; rejected: number;
    converged?: boolean; consecutive_failures?: number; stopped?: boolean;
  };
}
export interface Member {
  workspace_id: string; user: string; role: "viewer" | "editor" | "owner";
}
export interface OntologyLint {
  ok: boolean; warnings: { code: string; subject: string; message: string }[];
  ontology_id: string; version: number;
}

/* ---------------- 执行账本（P0-1 / P0-2） ---------------- */
export type ExecutionStatus =
  | "requested" | "claimed" | "running" | "succeeded" | "failed" | "unknown"
  | "resolved_applied" | "resolved_not_applied";
export interface ConditionResult {
  ok?: boolean;
  property?: string;
  op?: string;
  expected?: unknown;
  actual?: unknown;
  problems?: string[];
  [k: string]: unknown;
}
export interface Verification {
  pre?: ConditionResult | null;
  post?: ConditionResult | null;
  contract_failed?: boolean;
  [k: string]: unknown;
}
export interface ExecutionResolution {
  outcome: "applied" | "not_applied" | string;
  note?: string;
  actor?: string;
  at?: string;
}
export interface InvokeResult {
  ok: boolean;
  /** 唯一成功判定入口：只有 succeeded 是成功。 */
  status: ExecutionStatus | string;
  retryable?: boolean;
  contract_failed?: boolean;
  execution_id?: string;
  output?: unknown;
  changes?: Record<string, unknown>[];
  error?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  verification?: Verification | null;
  idempotent_replay?: boolean;
  resolution?: ExecutionResolution | null;
}
export interface ExecutionRecord {
  id?: string;
  execution_id: string;
  idempotency_key?: string | null;
  workspace_id?: string;
  function?: string;
  target?: string | null;
  status: ExecutionStatus | string;
  ok?: boolean;
  attempts?: number;
  in_flight?: boolean;
  actor?: string;
  args?: Record<string, unknown>;
  result?: Record<string, unknown> | null;
  verification?: Verification | null;
  resolution?: ExecutionResolution | null;
  error?: string | null;
  last_error?: string | null;
  started_at?: string | null;
  created_at?: string | null;
  finished_at?: string | null;
  updated_at?: string | null;
}
export interface ResolveExecutionBody {
  /** 与后端 ExecutionLedger.resolve 对齐：applied / not_applied。 */
  outcome: "applied" | "not_applied";
  note?: string;
  /** 未来接口若使用 decision 字段，一并携带，后端会忽略未知键。 */
  decision?: "applied" | "not_applied";
  evidence?: string;
  reason?: string;
}
export interface ProjectionRun {
  run_id?: string;
  run_status?: string;
  status?: string;
  projection?: string;
  objects_upserted?: number;
  links_asserted?: number;
  objects_deleted?: number;
  watermark?: string;
  started_at?: string;
  finished_at?: string | null;
  checked_at?: string | null;
  reason?: string | null;
  skipped_reason?: string | null;
}
export interface AskResult { question: string; answer: string; concepts: { term: string; concept: string }[]; objects: Obj[]; related: { from: string; relation: string; object: Obj }[] }
export interface AgentStep {
  index?: number; thought?: string;
  action?: { tool?: string; args?: Record<string, unknown> };
  observation?: string;
}
export interface AgentRun {
  goal: string;
  ok: boolean;
  status?: ExecutionStatus | string;
  answer: string;
  steps: AgentStep[];
  run_id?: string;
  trace_id?: string;
  verification?: Verification | null;
  failed_predicates?: string[];
  evidence?: Record<string, unknown> | null;
  budget_used?: Record<string, unknown> | null;
}
export interface QueryResult { query: string; objects: Obj[]; expand: { relation: string; direction: string; objects: Obj[] }[] }

/* ---------------- 工作空间级模型配置（P2-14） ---------------- */
export interface LlmConfig {
  workspace_id?: string;
  provider: string;
  model?: string;
  base_url?: string;
  key_last4?: string | null;
  /** 服务端只返回掩码（前缀与末四位），明文永不回显。 */
  api_key_masked?: string | null;
  has_key?: boolean;
  version?: number;
  updated_at?: string | null;
  updated_by?: string | null;
  source?: "workspace" | "environment" | string;
  configured?: boolean;
}
export interface LlmConfigUpdate {
  provider: string;
  model?: string;
  base_url?: string;
  /** 只写不读；服务端永不回显。 */
  api_key?: string;
}

export class ApiError extends Error {
  status: number;
  type: string;
  constructor(message: string, status = 0, type = "") {
    super(message);
    this.status = status;
    this.type = type;
  }
}

/** 后端英文错误 -> 业务语言 + 下一步 */
export function friendly(msg: string): string {
  const m = String(msg || "");
  if (m.includes("no LLM provider configured"))
    return "还没有配置模型：到「设置 · 模型服务」里填一个 OpenAI 兼容服务，或先打开「演示模式」体验流程。「业务问答」不需要模型，可以直接用。";
  if (m.includes("published ontology is immutable"))
    return "本体已发布，定义已锁定。要改动请先确认是否要新建一个工作空间（当前版本的限制）。";
  if (m.includes("cannot publish from") || m.includes("cannot validate from"))
    return "当前生命周期状态不允许这个操作（很可能已经发布过了）。";
  if (m.includes("no schema published"))
    return "业务世界还没启动：请先到「本体」页点「发布」。";
  if (m.includes("has no ontology"))
    return "这个工作空间还没有本体：请先到「本体」页创建。";
  if (m.includes("ontology validation failed"))
    return "本体检查没通过：" + m.replace("ontology validation failed: ", "");
  if (m.includes("has no previous release"))
    return "当前发布已经是最早版本，没有可回滚的历史发布。";
  if (m.includes("no route")) {
    const hit = /no route(?: for ([^\s"]+))?/.exec(m);
    const path = (hit?.[1] || "").replace(/\?.*$/, "");
    if (path.includes("/ontology/draft"))
      return `后端还没有「AI 快速建模」接口：${path}。请更新并重启 onto-api 后重试。`;
    return path
      ? `后端还没有这个接口：${path}。当前 onto-api 进程可能是旧版本，重启后再试。`
      : "后端还没有这个接口：当前 onto-api 进程可能是旧版本，重启后再试。";
  }
  if (m.includes("already has an ontology"))
    return "这个工作空间已经有一个本体了（当前版本每个空间一个）。";
  if (m.includes("not found")) return m.replace("not found", "没找到");
  return m;
}

const TIMEOUT = 15000;
const LLM_TIMEOUT = 90000;   // 走模型的接口（AI 快速建模）解析一段业务描述可能明显超过 15 秒

async function call<T>(path: string, init: RequestInit = {}, timeoutMs = TIMEOUT): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  const headers = new Headers(init.headers || {});
  headers.set("Accept", "application/json");
  const token = getApiToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  try {
    const res = await fetch(path, { ...init, headers, signal: ctrl.signal });
    const text = await res.text();
    let data: unknown = null;
    if (text) { try { data = JSON.parse(text); } catch { /* 非 JSON */ } }
    if (!res.ok) {
      const d = (data || {}) as { error?: string; type?: string };
      throw new ApiError(friendly(d.error || res.statusText || `HTTP ${res.status}`), res.status, d.type || "");
    }
    return data as T;
  } catch (e) {
    if (e instanceof ApiError) throw e;
    if ((e as Error).name === "AbortError") throw new ApiError(`请求超时：服务超过 ${Math.round(timeoutMs / 1000)} 秒没响应`, 0, "Timeout");
    throw new ApiError("连不上服务：请确认 onto-api 正在运行", 0, "Network");
  } finally {
    clearTimeout(timer);
  }
}

const post = <T,>(path: string, body: unknown, timeoutMs = TIMEOUT) =>
  call<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  }, timeoutMs);

/* ---------------- 身份：令牌只放在 sessionStorage ---------------- */
const TOKEN_KEY = "onto.api_token";

export function getApiToken(): string {
  try {
    return sessionStorage.getItem(TOKEN_KEY) || "";
  } catch {
    return "";
  }
}

export function setApiToken(token: string): void {
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    /* 隐私模式禁用存储时，令牌只在当前内存调用中缺失 */
  }
}

/** 本地回环模式缺省 actor 是后端 admin；前端不写死 user，远程必须由令牌映射 actor。 */
const ws = (wid: string, suffix: string) =>
  `/api/workspaces/${encodeURIComponent(wid)}${suffix}`;

/** as-of 参数必须是 ISO 8601；本地 datetime-local 要先经 lib/time.toUtcIso。 */
export function assertIsoTimestamp(at: string): string {
  if (!/^\d{4}-\d{2}-\d{2}T/.test(at || "") || Number.isNaN(new Date(at).getTime())) {
    throw new ApiError(
      "时间必须是 ISO 8601（例如 2026-10-28T07:31:00.000Z）；界面里的本地时间会先换算成 UTC。",
      0,
      "Validation",
    );
  }
  return at;
}

const windowQuery = (seconds?: number | null): string => {
  if (!seconds || !Number.isFinite(seconds) || seconds <= 0) return "";
  return `window_seconds=${Math.round(seconds)}`;
};

/** LLM 选择：平台默认 / OpenAI 兼容 / 演示模式（脚本化 Echo，无需真模型）。
 *  api_key 不在这里出现：请求作用域永远不带密钥，密钥只写不读地保存在服务端。 */
export type LlmChoice =
  | null
  | { provider: "openai"; base_url?: string; model?: string }
  | { script: string[] };

export interface Health {
  status: string; version: string; llm_configured: boolean;
  backend?: string; runtime_store?: string; llm_provider?: string; console?: string;
  api_version?: number; auth_mode?: "token" | "local" | string; features?: string[];
  scheduler?: SchedulerStatus;
}

export interface SchedulerTask {
  name: string;
  interval_seconds?: number;
  runs?: number;
  failures?: number;
  last_error?: string | null;
  /** 服务端如果给 ISO 时间就按本地时区显示；只给单调时钟数字时不猜测墙钟时间。 */
  last_run_at?: string | number | null;
  next_run_at?: string | number | null;
}

export interface SchedulerStatus {
  running: boolean;
  tasks: SchedulerTask[];
}

export interface Api {
  health(): Promise<Health>;
  scheduler(): Promise<SchedulerStatus>;
  workspaces(): Promise<Workspace[]>;
  createWorkspace(name: string, description: string): Promise<Workspace>;

  ontology(wid: string): Promise<Ontology>;
  createOntology(wid: string, name: string, description: string): Promise<Ontology>;
  addObject(wid: string, body: { name: string; description: string; properties: PropertySpec[] }): Promise<Ontology>;
  addRelation(wid: string, body: { name: string; source: string; target: string; description: string }): Promise<Ontology>;
  addTerm(wid: string, body: { term: string; concept: string; kind: string; aliases: string[] }): Promise<Ontology>;
  /** AI 快速建模：预览时传 text；apply=true 时回传预览得到的 draft 写入草稿本体 */
  draftOntology(wid: string, body: DraftRequest): Promise<DraftResult>;
  validateOntology(wid: string): Promise<{ ok: boolean; problems: string[] }>;
  publishOntology(wid: string): Promise<{ ontology: Ontology; runtime: RuntimeDescribe }>;

  runtime(wid: string): Promise<RuntimeDescribe>;
  datasources(wid: string): Promise<DataSource[]>;
  connectSource(wid: string, body: Record<string, unknown>): Promise<DataSource>;
  projections(wid: string): Promise<Projection[]>;
  createProjection(wid: string, body: Record<string, unknown>): Promise<Projection>;
  runProjection(wid: string, pid: string): Promise<ProjectionRun>;

  capabilities(wid: string): Promise<Capability[]>;
  createCapability(wid: string, body: Record<string, unknown>): Promise<Capability>;
  publishCapability(wid: string, cid: string): Promise<Capability>;
  invoke(wid: string, body: {
    function: string; target: string | null; args: Record<string, unknown>;
    idempotency_key: string;
  }): Promise<InvokeResult>;

  applications(wid: string): Promise<Application[]>;
  application(wid: string, aid: string): Promise<Application>;
  createApplication(wid: string, name: string, description: string): Promise<Application>;
  publishApplication(wid: string, aid: string): Promise<Application>;
  addView(wid: string, aid: string, body: Record<string, unknown>): Promise<Application>;
  addQuery(wid: string, aid: string, body: Record<string, unknown>): Promise<Application>;
  addAgent(wid: string, aid: string, body: Record<string, unknown>): Promise<Application>;
  attachCapability(wid: string, aid: string, capability_id: string): Promise<Application>;
  runQuery(wid: string, aid: string, alias: string): Promise<QueryResult>;
  runAppAgent(wid: string, aid: string, alias: string, body: { goal?: string; max_steps?: number; llm?: LlmChoice }): Promise<AgentRun>;
  objective(wid: string, aid: string): Promise<ObjectiveEvaluation>;
  evaluateObjective(wid: string, aid: string): Promise<ObjectiveEvaluation>;
  setObjective(wid: string, aid: string, body: { metric: string; target: number; band?: number; window?: number }): Promise<Application>;
  todos(wid: string, status?: string): Promise<Todo[]>;
  resolveTodo(wid: string, tid: string): Promise<Todo>;

  executions(wid: string): Promise<ExecutionRecord[]>;
  execution(wid: string, executionId: string): Promise<ExecutionRecord>;
  executionByKey(wid: string, key: string): Promise<ExecutionRecord>;
  resolveExecution(wid: string, executionId: string, body: ResolveExecutionBody): Promise<ExecutionRecord>;

  releases(wid: string): Promise<ReleaseList>;
  cutRelease(wid: string, note: string): Promise<Release>;
  deployRelease(wid: string, rid: string): Promise<{ release: Release; deployment: Deployment; runtime: RuntimeDescribe }>;
  canaryRelease(wid: string, rid: string): Promise<{ release: Release; deployment: Deployment; current: Deployment | null; runtime: RuntimeDescribe }>;
  promoteCanary(wid: string, requireHealthy: boolean): Promise<{ release: Release; deployment: Deployment; canary: Deployment; runtime: RuntimeDescribe }>;
  rollbackRelease(wid: string, releaseId?: string): Promise<{ release: Release; deployment: Deployment; runtime: RuntimeDescribe }>;
  canary(wid: string): Promise<CanaryStatus>;
  checkCanary(wid: string): Promise<CanaryCheck>;

  events(wid: string, limit?: number, type?: string): Promise<DomainEvent[]>;
  deadLetters(wid: string): Promise<DomainEvent[]>;
  dispatchEvents(wid: string): Promise<{
    dispatched: number; failed: number; skipped?: number;
    dead_lettered?: number; unrouted?: number; errors: string[];
  }>;
  metrics(wid: string, windowSeconds?: number): Promise<WorkspaceMetrics>;
  slo(wid: string, windowSeconds?: number): Promise<SloReport>;
  verify(wid: string, objectType?: string, sample?: number): Promise<VerifyResult>;
  reconcile(wid: string): Promise<ReconcileResult>;
  controllerPropose(wid: string): Promise<{ ok: boolean; proposals: ControllerProposal[]; report: ReconcileResult }>;
  controllerAct(wid: string, accept: boolean): Promise<ControllerCycle>;

  lintOntology(wid: string): Promise<OntologyLint>;
  reviseOntology(wid: string): Promise<Ontology>;

  members(wid: string): Promise<Member[]>;
  addMember(wid: string, member: string, role: string): Promise<Member>;
  workspace(wid: string): Promise<Workspace & { members?: Record<string, string> }>;

  find(wid: string, type: string, filters: Record<string, string>): Promise<Obj[]>;
  get(wid: string, ref: string): Promise<Obj | null>;
  related(wid: string, ref: string, relation: string, direction: string): Promise<string[]>;
  changes(wid: string, after: number): Promise<ChangeEvent[]>;
  history(wid: string, ref: string, limit?: number): Promise<ObjectHistory>;
  asOf(wid: string, ref: string, seq: number): Promise<StateSnapshot>;
  asOfAt(wid: string, ref: string, at: string): Promise<StateSnapshot>;
  audit(wid: string, limit: number): Promise<AuditEvent[]>;

  ask(wid: string, question: string): Promise<AskResult>;
  agent(wid: string, goal: string, maxSteps: number, llm: LlmChoice): Promise<AgentRun>;

  /** 平台级模型配置（一次保存，服务端持有密钥；请求不再携带 key） */
  configureLlm(body: {
    provider: "openai"; base_url?: string; model?: string; api_key?: string;
  }): Promise<{ ok: boolean; llm_provider: string }>;
  /** 工作空间级模型配置：密钥只写不读，读取只返回 key_last4。 */
  llmConfig(wid: string): Promise<LlmConfig>;
  configureWorkspaceLlm(wid: string, body: LlmConfigUpdate): Promise<LlmConfig>;
}

export const api: Api = {
  health: () => call("/api/health"),
  scheduler: () => call("/api/scheduler"),
  workspaces: () => call("/api/workspaces"),
  createWorkspace: (name, description) => post("/api/workspaces", { name, description }),

  ontology: wid => call(ws(wid, "/ontology")),
  createOntology: (wid, name, description) => post(ws(wid, "/ontology"), { name, description }),
  addObject: (wid, b) => post(ws(wid, "/ontology/objects"), b),
  addRelation: (wid, b) => post(ws(wid, "/ontology/relations"), b),
  addTerm: (wid, b) => post(ws(wid, "/ontology/vocabulary"), b),
  draftOntology: (wid, b) => post(ws(wid, "/ontology/draft"), b, LLM_TIMEOUT),
  validateOntology: wid => post(ws(wid, "/ontology/validate"), {}),
  publishOntology: wid => post(ws(wid, "/ontology/publish"), {}),

  runtime: wid => call(ws(wid, "/runtime")),
  datasources: wid => call(ws(wid, "/datasources")),
  connectSource: (wid, b) => post(ws(wid, "/datasources"), b),
  projections: wid => call(ws(wid, "/projections")),
  createProjection: (wid, b) => post(ws(wid, "/projections"), b),
  runProjection: (wid, pid) => post(ws(wid, `/projections/${encodeURIComponent(pid)}/run`), {}),

  capabilities: wid => call(ws(wid, "/capabilities")),
  createCapability: (wid, b) => post(ws(wid, "/capabilities"), b),
  publishCapability: (wid, cid) => post(ws(wid, `/capabilities/${encodeURIComponent(cid)}/publish`), {}),
  invoke: (wid, b) => post(ws(wid, "/invoke"), b),
  applications: wid => call(ws(wid, "/applications")),
  application: (wid, aid) => call(ws(wid, `/applications/${encodeURIComponent(aid)}`)),
  createApplication: (wid, name, description) => post(ws(wid, "/applications"), { name, description }),
  publishApplication: (wid, aid) => post(ws(wid, `/applications/${encodeURIComponent(aid)}/publish`), {}),
  addView: (wid, aid, b) => post(ws(wid, `/applications/${encodeURIComponent(aid)}/views`), b),
  addQuery: (wid, aid, b) => post(ws(wid, `/applications/${encodeURIComponent(aid)}/queries`), b),
  addAgent: (wid, aid, b) => post(ws(wid, `/applications/${encodeURIComponent(aid)}/agents`), b),
  attachCapability: (wid, aid, capability_id) => post(ws(wid, `/applications/${encodeURIComponent(aid)}/capabilities`), { capability_id }),
  runQuery: (wid, aid, alias) => post(ws(wid, `/applications/${encodeURIComponent(aid)}/query`), { alias }),
  runAppAgent: (wid, aid, alias, b) =>
    post(ws(wid, `/applications/${encodeURIComponent(aid)}/agents/${encodeURIComponent(alias)}/agent`), b),
  objective: (wid, aid) =>
    call(ws(wid, `/applications/${encodeURIComponent(aid)}/objective`)),
  evaluateObjective: (wid, aid) =>
    post(ws(wid, `/applications/${encodeURIComponent(aid)}/objective/evaluate`), {}),
  setObjective: (wid, aid, b) =>
    post(ws(wid, `/applications/${encodeURIComponent(aid)}/objective`), b),
  todos: (wid, status = "open") => call(ws(wid, `/todos?status=${encodeURIComponent(status)}`)),
  resolveTodo: (wid, tid) =>
    post(ws(wid, `/todos/${encodeURIComponent(tid)}/resolve`), {}),

  executions: wid => call(ws(wid, "/executions")),
  execution: (wid, executionId) =>
    call(ws(wid, `/executions/${encodeURIComponent(executionId)}`)),
  executionByKey: (wid, key) =>
    call(ws(wid, `/executions/by-key/${encodeURIComponent(key)}`)),
  resolveExecution: (wid, executionId, body) =>
    post(ws(wid, `/executions/${encodeURIComponent(executionId)}/resolve`), body),

  find: (wid, type, filters) => {
    const q = new URLSearchParams({ type });
    for (const [k, v] of Object.entries(filters)) q.set(k, v);
    return call(ws(wid, `/state/find?${q.toString()}`));
  },
  get: (wid, ref) => call(ws(wid, `/state/get?ref=${encodeURIComponent(ref)}`)),
  related: (wid, ref, relation, direction) =>
    call(ws(wid, `/state/related?ref=${encodeURIComponent(ref)}&relation=${encodeURIComponent(relation)}&direction=${direction}`)),
  changes: (wid, after) => call(ws(wid, `/changes?after=${after}`)),
  history: (wid, ref, limit = 50) =>
    call(ws(wid, `/state/history?ref=${encodeURIComponent(ref)}&limit=${limit}`)),
  asOf: (wid, ref, seq) =>
    call(ws(wid, `/state/as-of?ref=${encodeURIComponent(ref)}&seq=${seq}`)),
  asOfAt: (wid, ref, at) =>
    call(ws(wid, `/state/as-of?ref=${encodeURIComponent(ref)}&at=${encodeURIComponent(assertIsoTimestamp(at))}`)),
  audit: (wid, limit) => call(ws(wid, `/audit?limit=${limit}`)),

  releases: wid => call(ws(wid, "/releases")),
  cutRelease: (wid, note) => post(ws(wid, "/releases"), { note }),
  deployRelease: (wid, rid) =>
    post(ws(wid, `/releases/${encodeURIComponent(rid)}/deploy`), {}),
  canaryRelease: (wid, rid) =>
    post(ws(wid, `/releases/${encodeURIComponent(rid)}/canary`), {}),
  promoteCanary: (wid, requireHealthy) =>
    post(ws(wid, "/releases/canary/promote"), { require_healthy: requireHealthy }),
  rollbackRelease: (wid, releaseId) =>
    post(ws(wid, "/releases/rollback"), releaseId ? { release_id: releaseId } : {}),
  canary: wid => call(ws(wid, "/canary")),
  checkCanary: wid => post(ws(wid, "/releases/canary/check"), {}),

  events: (wid, limit = 200, type = "") => {
    const q = new URLSearchParams({ limit: String(limit) });
    if (type) q.set("type", type);
    return call(ws(wid, `/events?${q.toString()}`));
  },
  deadLetters: wid => call(ws(wid, "/events/dead-letters")),
  dispatchEvents: wid => post(ws(wid, "/events/dispatch"), {}),
  metrics: (wid, windowSeconds) => {
    const q = windowQuery(windowSeconds);
    return call(ws(wid, `/metrics${q ? `?${q}` : ""}`));
  },
  slo: (wid, windowSeconds) => {
    const q = windowQuery(windowSeconds);
    return call(ws(wid, `/slo${q ? `?${q}` : ""}`));
  },
  verify: (wid, objectType, sample) => {
    const q = new URLSearchParams();
    if (objectType) q.set("object_type", objectType);
    if (sample) q.set("sample", String(sample));
    return call(ws(wid, `/verify${q.toString() ? `?${q.toString()}` : ""}`));
  },
  reconcile: wid => call(ws(wid, "/reconcile")),
  controllerPropose: wid => call(ws(wid, "/controller/propose")),
  controllerAct: (wid, accept) => post(ws(wid, "/controller/act"), { accept }),

  lintOntology: wid => post(ws(wid, "/ontology/lint"), {}),
  reviseOntology: wid => post(ws(wid, "/ontology/revisions"), {}),

  members: wid => call(ws(wid, "/members")),
  addMember: (wid, member, role) => post(ws(wid, "/members"), { member, role }),
  workspace: wid => call(`/api/workspaces/${encodeURIComponent(wid)}`),

  ask: (wid, question) => post(ws(wid, "/ask"), { question }),
  agent: (wid, goal, maxSteps, llm) => post(ws(wid, "/agent"), { goal, max_steps: maxSteps, ...(llm ? { llm } : {}) }),
  configureLlm: body => post("/api/llm", body),
  llmConfig: wid => call(ws(wid, "/llm-config")),
  configureWorkspaceLlm: (wid, body) => call(ws(wid, "/llm-config"), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }),
};

export const assetNames = (d: DataSource): string[] =>
  Array.isArray(d.assets) ? d.assets.map(a => a.name) : Object.keys(d.assets || {});
