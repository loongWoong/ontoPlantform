/** 与后端 REST 契约一一对应的类型与客户端。
 *  错误在这里统一翻译成业务语言 + 下一步指引（design.txt §42 第一原则）。 */

export type Lifecycle = "draft" | "validated" | "published" | "deprecated" | "none";

export interface Workspace { id: string; name: string; description: string; created_at: string; runtime_config?: Record<string, unknown>; deletable?: boolean; blockers?: string[] }
/** 属性的 facet 全部可选：编辑器按原样回传，缺省时后端用默认值，绝不丢 required/enum。 */
export interface PropertySpec {
  name: string; type: string; description?: string;
  required?: boolean; enum?: unknown[]; minimum?: number | null; maximum?: number | null;
  unique?: boolean; unit?: string; precision?: number | null; default?: unknown; temporal?: string;
}
export interface ObjectTypeSpec {
  name: string; description: string; properties: Record<string, PropertySpec>;
  /** 业务主键（去重与引用同一实体的判据）；空数组表示按 id_field 派生。 */
  identity?: string[];
  /** object / event / value 三类元语义。 */
  category?: string;
  /** 可选的生命周期声明；编辑器不改它，但整对象 upsert 时必须原样回传，否则后端会清空。 */
  state_machine?: Record<string, unknown> | null;
}
export interface RelationSpec {
  name: string; source: string; target: string; description?: string; cardinality?: string;
  /** 反向关系名；空串表示没有声明。 */
  inverse?: string;
}
export interface VocabTerm { term: string; concept: string; kind: string; aliases?: string[] }
export interface Ontology {
  id?: string; workspace_id?: string;
  name: string; description: string; version: number; schema_version?: number; lifecycle: Lifecycle;
  /** 修订来源：草稿由哪个已发布版本复制而来；为空表示不是修订草稿。 */
  parent_version?: number | null;
  /** closed（缺省即假）或 open（缺省未知）；影响问答与校验的推理口径。 */
  profile?: string;
  schema_fingerprint?: string;
  created_at?: string; updated_at?: string;
  objects: Record<string, ObjectTypeSpec>;
  relations: Record<string, RelationSpec>;
  vocabulary: Record<string, VocabTerm>;
}
/** PATCH /ontology 的请求：至少给一个字段，只允许在草稿上改。 */
export interface OntologyMetaUpdate { name?: string; description?: string; profile?: string }
/** POST /ontology/discard 的响应：丢弃当前修订草稿，恢复 parent 版本。 */
export interface DiscardDraftResult {
  discarded: number | string;
  restored: Ontology;
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
/** PATCH /datasources 提交的数据表声明格式（与连接数据源时一致，后端只收数组）。 */
export interface DataAssetInput { name: string; config?: Record<string, unknown>; description?: string }
export interface DataSource {
  id: string; name: string; kind: string;
  config: Record<string, unknown>;
  assets: Record<string, DataAsset> | DataAsset[];
  workspace_id?: string; created_at?: string;
}
/** POST /datasources/{sid}/test 的结果：只说明这次连通性检查。 */
export interface SourceTestResult { ok: boolean; kind: string; tested_at?: string }
/** POST /datasources/{sid}/preview 的结果：total 是全量行数，rows 只是被截到 limit 的那一页。 */
export interface PreviewResult {
  asset: string;
  /** 全量行数（不是 rows.length）；旧进程不返回时按 rows.length 兜底。 */
  total?: number;
  /** 有序字段名；旧进程不返回时按 rows[0] 的键兜底。 */
  columns?: string[];
  rows: Record<string, unknown>[];
}
export interface ObjectMapping { asset: string; object_type: string; id_field: string; filter?: Record<string, unknown>; fields: { source_field: string; property: string }[] }
export interface RelationMapping { asset: string; relation: string; source_field: string; target_field: string }
export interface Projection {
  id: string; name: string; datasource_id: string;
  object_mappings: ObjectMapping[]; relation_mappings: RelationMapping[];
  mode?: "additive" | "authoritative" | string;
  /** 每次同步每个数据表最多写多少行；0 表示不限制。 */
  row_limit?: number;
  lifecycle?: string;
  managed_types?: string[];
  sync_interval_seconds?: number;
  last_check_at?: string | null;
  last_change_at?: string | null;
  created_at?: string;
  updated_at?: string;
}
export interface CapabilityInput { name: string; type: string; description?: string }
export interface Capability {
  id: string; key: string; name: string; target_type: string; description: string;
  inputs: CapabilityInput[]; implementation: { kind: string; [k: string]: unknown };
  /** 执行契约：pre/postconditions、effects、idempotent、compensate；空对象表示没声明。 */
  contract?: Record<string, unknown>;
  lifecycle: Lifecycle;
  workspace_id?: string; created_at?: string; updated_at?: string;
}
/** 改能力的请求体：只传要改的字段。 */
export interface CapabilityUpdate {
  key?: string; name?: string; target_type?: string; description?: string;
  inputs?: CapabilityInput[]; implementation?: Record<string, unknown>;
  /** 传 {} 表示清空契约。 */
  contract?: Record<string, unknown>;
}
/** 发布能力同时切一个新发布版本（与能力的发布语义一致）。 */
export interface CapabilityPublishResult {
  capability: Capability;
  runtime?: RuntimeDescribe;
  release?: Record<string, unknown>;
  deployment?: Record<string, unknown>;
}
/** 平台智能视图桥：真实工作空间 → 本体编译器 → iframe 运行时。 */
export interface SmartUiMeta {
  ready: boolean;
  reason?: string;
  guidance?: string[];
  focus?: {
    objectType: string;
    label: string;
    identityKey: string | null;
    value: string;
  } | null;
  workspace: { id: string; name: string; description: string };
  defaultQuestion: string;
  roles: { id: string; name: string; glyph: string }[];
  objectTypes: string[];
  counts: Record<string, number>;
  capabilities: { key: string; name: string; target: string }[];
  /** 真正进入生成程序动作面板的能力（无参数、非 agent、已发布）。 */
  actions: { key: string; name: string; target: string }[];
  bridge: {
    focusObject: string | null;
    focusLabel?: string | null;
    focusValue?: string | null;
    identityKey?: string | null;
    identityLabel?: string | null;
    relatedObjects?: { type: string; relation: string }[];
    vehicleObject: string | null;
    stationObject: string | null;
    laneObject: string | null;
    engineVersion: string;
  };
  /** ONTO_SMART_UI_STUDIO 开关；关闭时生成台接口返回 4xx。 */
  studio?: { enabled: boolean };
  /** Smart-UI contribution registry snapshot; generation is still platform-adjudicated. */
  plugins?: SmartUiPluginManifest[];
  /** Plugin combination pinned into generation artifacts/drafts. */
  pluginLock?: { id: string; kind: string; version: string; apiVersion: string }[];
  /** Registry lock (includes manifests without a runtime implementation). */
  pluginRegistryLock?: { id: string; kind: string; version: string }[];
  /** Declarative workspace packs; they carry no executable code. */
  packs?: SmartUiWorkspacePack[];
  /** Pack bound to this workspace, if any. */
  activePack?: SmartUiWorkspacePack | null;
  /** Rule packs available for the no-model generation mode. */
  rulePacks?: SmartUiRulePackSummary[];
  boundRulePackId?: string;
  /** UI slot manifest from the UI contribution plus registered renderers. */
  ui?: SmartUiUIManifest;
  /** View descriptors registered by view plugins (perspective -> renderer). */
  engineDescriptors?: SmartUiEngineDescriptor[];
  /** 这个工作空间实际会用的模型（服务端按 workspace > platform > none 解析）。
   *  生成台以它为准判断“模型 / 确定性回退”，不看全局 llmReady。 */
  model?: SmartUiModelStatus;
}

export interface SmartUiUIManifest {
  slots: { slot: string; id: string; order: number; label: string; builtin: boolean }[];
  renderers: {
    viewType: string;
    component: string;
    pluginId?: string;
    order?: number;
    format?: string;
  }[];
  remoteScripts: boolean;
}

export interface SmartUiEngineDescriptor {
  perspective: string;
  viewType: string;
  component: string;
  pluginId: string;
  order: number;
}

export interface SmartUiPluginManifest {
  id: string;
  kind:
    | "context"
    | "generator"
    | "prompt"
    | "adjudicator"
    | "view"
    | "renderer"
    | "publisher"
    | "ui";
  version: string;
  apiVersion: string;
  apiVersions?: string[];
  summary: string;
  order: number;
  enabled: boolean;
  implemented: boolean;
  builtin: boolean;
  permissions?: string[];
  viewType?: string;
  component?: string;
}

export interface SmartUiWorkspacePack {
  id: string;
  name: string;
  version: string;
  description: string;
  promptPack: string;
  ontologyProfile: string;
  perspectives: string[];
  defaultQuestion: string;
  permissions?: string[];
  viewPresets?: { perspective: string; options: Record<string, unknown> }[];
  /** Optional sub-binding to a rule pack. */
  rulePack?: string;
  builtin: boolean;
}

export interface SmartUiConfig {
  workspaceId: string;
  packId: string;
  pack: SmartUiWorkspacePack | null;
  rulePackId?: string;
}

/** One rule a pack matched; the trace explains why a view was chosen. */
export interface UiRuleTraceMatch {
  ruleId: string;
  priority: number;
  reason: string;
  effects: string[];
  source?: string;
}

export interface UiRuleCandidate {
  perspective: string;
  weight: number;
  title: string;
  options: Record<string, unknown>;
  reasons: string[];
}

export interface UiRuleClarifyOption {
  id: string;
  label: string;
}

export interface UiRuleClarify {
  id: string;
  question: string;
  reason: string;
  options: UiRuleClarifyOption[];
}

/** Structured trace of the pure rule engine (no model needed). */
export interface UiRuleTrace {
  pack?: { id?: string; version?: string; hash?: string };
  goal?: string;
  object?: string;
  focus?: { key: string; value: string } | null;
  confidence?: number;
  maxViews?: number;
  candidates?: UiRuleCandidate[];
  matched?: UiRuleTraceMatch[];
  skipped?: { ruleId: string; priority: number; reason: string }[];
  pruned?: { kind: string; key: string; reason: string }[];
  clarify?: UiRuleClarify[];
  annotations?: string[];
  facts?: Record<string, unknown>;
}

export interface SmartUiRulePackSummary {
  id: string;
  name: string;
  version: string;
  status: "builtin" | "draft" | "published" | "archived" | string;
  description: string;
  ontologyProfile: string;
  hash: string;
  ruleCount: number;
  source: string;
  builtin: boolean;
  created_at: string | null;
  updated_at: string | null;
  published_at: string | null;
  created_by: string | null;
  published_by: string | null;
}

export interface SmartUiRulePack extends SmartUiRulePackSummary {
  workspace_id: string | null;
  rules: Record<string, unknown>[];
  budget: { maxViews?: number; [key: string]: unknown };
  parent_id: string | null;
}

export interface SmartUiRulePackList {
  items: SmartUiRulePackSummary[];
  total: number;
  defaultRulePackId: string;
  boundRulePackId: string;
}

export interface SmartUiRuleValidation {
  ok: boolean;
  issues: { code: string; ruleId: string; message: string }[];
  hash: string;
  pack: SmartUiRulePack | null;
}

export interface SmartUiModelStatus {
  configured: boolean;
  source: "workspace" | "platform" | "none";
  provider: string;
  /** 实际会调用的模型名；source=none 时为空。 */
  model: string;
  /** 只给主机（不含路径与密钥），用于界面展示端点来源。 */
  base_url_host: string;
  has_key: boolean;
  /** true 表示这次生成会真的调用模型；false 时走确定性回退。 */
  will_use_model: boolean;
}

/** 可选“数据范围”：让生成聚焦到某个具体对象实例，而不是平台默认焦点。 */
export interface SmartUiFocusOption {
  objectType: string;
  /** 用来定位实例的对象属性名（例如车辆的车牌 plate）。 */
  key: string;
  /** 实例值（例如 鲁B67890）。 */
  value: string;
  /** 给用户看的显示名，可能比 value 更可读。 */
  label: string;
}

/** GET /smart-ui/entities：可搜索的焦点实例选项。 */
export interface SmartUiEntityResult {
  items: SmartUiFocusOption[];
  /** 过滤后的总数（items 只返回 limit 条）。 */
  total: number;
  objectTypes: string[];
}

/** 请求体里的焦点选择（与后端 _focus_request 对齐）。 */
export interface SmartUiFocusRequest {
  objectType: string;
  key: string;
  value: string;
}

/** 生成记录里保存的焦点（后端存储形状：object / key / value）。 */
export interface UiGenerationFocus {
  object: string;
  key: string;
  value: string;
}

/** POST /llm-config/check：真实调用模型端点的诊断结果（HTTP 仍为 200）。 */
export interface LlmCheckResult {
  ok: boolean;
  /** 诊断分类：auth / not_found / timeout / rate_limit / transport / not_configured … */
  errorType: string | null;
  /** 模型端点返回的 HTTP 状态；未发出请求时为 null。 */
  status: number | null;
  latencyMs: number;
  message: string;
  model: string;
  source: "workspace" | "platform" | "none" | string;
  responsePreview?: string;
}

/** AI 生成台：一条生成记录（轮询到 succeeded/failed/cancelled/interrupted 为止）。 */
export type UiGenerationStatus =
  | "queued" | "running" | "succeeded" | "failed" | "cancelled" | "interrupted";
export type UiGenerationMode = "llm" | "rule" | "fallback" | "deterministic";

export interface UiGenerationStep {
  key: "context" | "propose" | "adjudicate" | "compile" | "ready" | string;
  label: string;
  status: "pending" | "running" | "ok" | "warn" | "err";
  detail: string;
  startedAt?: string;
  finishedAt?: string;
  durationMs: number | null;
}

export interface UiGenerationCheck {
  key: string;
  label: string;
  status: "ok" | "warn" | "err";
  detail: string;
}

/** 模型调用返回的 token 用量；不同 provider 的字段名不同，前端做兼容。 */
export interface UiGenerationUsage {
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
  input_tokens?: number;
  output_tokens?: number;
}

/** 平台按部署配置的费率估算的成本；未配置费率时后端不返回 cost。 */
export interface UiGenerationCost {
  amount: number;
  currency: string;
  inputRatePer1k: number;
  outputRatePer1k: number;
}

export interface UiGenerationSummary {
  id: string;
  workspace_id: string;
  question: string;
  instruction: string;
  status: UiGenerationStatus;
  mode: UiGenerationMode;
  /** 用户请求的模式：auto / llm / rule / deterministic；mode 是平台最终的裁决结果。 */
  requested_mode?: string | null;
  /** 本次生成使用的规则包；规则模式/回退都记录，便于复现。 */
  rule_pack_id?: string | null;
  rule_pack_hash?: string | null;
  /** 真实模型调用的 token 用量；确定性回退时为 null。 */
  usage?: UiGenerationUsage | null;
  /** 按部署配置费率估算的成本；未配置费率时为 null（前端显示“未配置费率”）。 */
  cost?: UiGenerationCost | null;
  /** 本次生成指定的数据范围；未指定时为 null（平台默认焦点）。 */
  focus?: UiGenerationFocus | null;
  parent_id: string | null;
  version: number;
  created_at: string;
  updated_at: string;
  finished_at: string | null;
  actor: string;
  roles: string[];
  object: string | null;
  goal: string | null;
  view_count: number;
  program_hash: string | null;
  model: string | null;
  provider: string | null;
  error: string | null;
  draft_id: string | null;
  published_application_id: string | null;
}

export interface UiGeneration extends UiGenerationSummary {
  steps: UiGenerationStep[];
  validation: {
    ok: boolean;
    checks: UiGenerationCheck[];
    pruned?: { kind: string; key: string; reason: string }[];
    fallbackReason?: string;
    error?: string;
  };
  /**
   * 宽字段袋：模型/引擎版本/哈希，失败路径还会写 rawPreview（模型原始输出，≤2000 字符）。
   */
  trace: Record<string, string | number | boolean | null | undefined>;
  metrics: {
    totalMs?: number;
    compileMs?: number;
    usage?: UiGenerationUsage | null;
    cost?: UiGenerationCost | null;
  };
  proposal: Record<string, unknown> | null;
  blueprint: {
    name?: string; displayName?: string; description?: string;
    context?: { object?: string; goal?: string; focus?: { key: string; value: string } };
    views?: { perspective: string; title?: string }[];
    actions?: string[];
  } | null;
  context: Record<string, unknown>;
  budget: Record<string, number>;
  /** 规则引擎决策轨迹（匹配/跳过/剪除/候选/澄清）。 */
  ruleTrace?: UiRuleTrace | null;
  /** 规则建议补充的澄清问题；前端可一键填入问题框。 */
  clarify?: UiRuleClarify[];
}

/** GET /smart-ui/generations/{gid} 里的一条“相对上一版”变化。 */
export interface UiGenerationDiffChange {
  /** object / goal / focus / view / action / displayName，后端可能扩展。 */
  kind: string;
  key: string;
  before: unknown;
  after: unknown;
}

/** 相邻两版蓝图的结构 diff（只比结构，不比像素）。 */
export interface UiGenerationDiff {
  /** 父版本的蓝图名。 */
  from: string | null;
  /** 当前版本的蓝图名。 */
  to: string | null;
  changed: boolean;
  changes: UiGenerationDiffChange[];
}

/** 生成详情：在 summary 之外带 steps/blueprint，以及版本链和相对上一版 diff。 */
export interface UiGenerationDetail extends UiGeneration {
  /** 祖先记录，最新在前（第 0 项就是当前记录）。 */
  lineage?: UiGenerationSummary[];
  /** 有父版本且两版都有蓝图时才有；首个版本没有。 */
  diff?: UiGenerationDiff;
}

/** GET /smart-ui/generations 的过滤/分页参数（与后端 list_generations 对齐）。 */
export interface SmartUiGenerationListQuery {
  limit?: number;
  offset?: number;
  /** 匹配 question / instruction / display_name / name / id。 */
  q?: string;
  /** 精确匹配生成状态（queued/running/succeeded/failed/cancelled/interrupted）。 */
  status?: string;
  /** 精确匹配蓝图里的对象类型。 */
  object?: string;
  /** 精确匹配生成模式（llm/fallback/deterministic）。 */
  mode?: UiGenerationMode;
}

/** GET /smart-ui/generations 的分页结果。 */
export interface SmartUiGenerationListResult {
  items: UiGenerationSummary[];
  /** 过滤后的总条数。 */
  total: number;
  offset: number;
  limit: number;
  /** 未过滤的总条数。 */
  all: number;
}

export interface UiDraft {
  id: string;
  workspace_id: string;
  generation_id: string;
  name: string;
  version: number;
  program_hash: string | null;
  status: "draft" | "published";
  created_at: string;
  created_by: string;
  updated_at: string;
  published_at: string | null;
  application_id: string | null;
}

export interface UiGenerationViewResult {
  compiled: Record<string, unknown>;
  /** 运行时 bundle，交给 iframe 自己取数，控制台不解释。 */
  bundle: Record<string, unknown>;
  generation: UiGenerationSummary;
}
/** 视图的渲染配置：chart 用 {x,y}，map 用 {lat,lng}；值是对象属性名。 */
export type ViewConfig = Record<string, string>;
export interface AppView {
  alias: string; title: string; kind: string; object_type: string;
  properties: string[]; query_alias: string;
  config?: ViewConfig;
}
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
  /** Studio 发布时附带的生成快照；只描述呈现，不改 views/queries。
   *  现在会持久化蓝图与程序本身，生成记录清理后应用仍可渲染。 */
  ui_artifact?: {
    generation_id: string; draft_id: string; version: number;
    program_hash?: string | null; published_at?: string; published_by?: string;
    name?: string;
    /** 发布时裁决通过的蓝图与编译产物快照。 */
    blueprint?: Record<string, unknown> | null;
    program?: Record<string, unknown> | null;
    question?: string;
    roles?: string[];
    ontology_version?: string | null;
    engine_version?: string | null;
  };
  created_at?: string; updated_at?: string;
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
  /** 发布快照引用的能力里，已经不存在（或不再是 published）的那些 id。
   *  非空表示这份发布装不起来，必须先重新切发布；界面只据此禁用部署，不猜原因。 */
  missing_capability_ids?: string[];
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
    return "本体已发布，定义已锁定，不能直接改。点右上角「修订」复制出新草稿，改完再发布；如果这次修订写错了，可以在草稿上用「丢弃草稿」回到上一版。";
  if (m.includes("cannot publish from") || m.includes("cannot validate from"))
    return "当前生命周期状态不允许这个操作（很可能已经发布过了）。";
  if (m.includes("no schema published"))
    return "业务世界还没启动：请先到「本体」页点「发布」。";
  if (m.includes("has no ontology"))
    return "这个工作空间还没有本体：请先到「本体」页创建。";
  if (m.includes("ontology validation failed"))
    return "本体检查没通过：" + m.replace("ontology validation failed: ", "");
  // ---- 应用发布：后端把所有问题一次性返回，逐条翻成中文；认不出的原文原样保留 ----
  if (m.includes("application validation failed")) {
    const detail = m.replace("application validation failed: ", "");
    const problems = detail
      .split(/;\s*(?=(?:view|query|attached|application|duplicate|the workspace)\b)/)
      .filter(Boolean)
      .map(translateApplicationProblem);
    return `应用发布没通过（${problems.length} 条）：${problems.join("；")}`;
  }
  if (m.includes("cannot publish application") || m.includes("application cannot be published"))
    return `应用不能被发布：${m}`;
  // ---- 应用编辑：已发布不可就地改，只能克隆成草稿 ----
  if (/published applications? (are|is) immutable/i.test(m))
    return "已发布应用不可就地改：先「克隆为草稿」（应用头部），改新草稿、发布后替换旧的；替换前旧应用保持原样。";
  if (/application name must not be empty/i.test(m))
    return "应用名称不能为空：给应用起一个业务上认得出的名字。";
  if (/^(view|query) not found/i.test(m))
    return "应用里找不到这个视图或查询：可能已被别人删掉或改名了，刷新页面再试。";
  if (/nothing to update/i.test(m))
    return "这次没有要保存的改动：至少改一个字段再保存。";
  // 逐条编辑（PATCH）返回的是单条校验错误，说法与发布时的 problems 不同，单独翻译
  if (/(chart|map) views? needs? an object_type/i.test(m))
    return "图表 / 地图视图要先绑定对象类型：有了对象类型才知道有哪些属性可以用来画。到视图「编辑」里选上再保存。";
  if (/unknown view kind/i.test(m))
    return `视图形式不合法：${m}。只能选表格 / 卡片 / 图表 / 地图。`;
  const badProp = /^view property '([^']*)' is not defined on object type '([^']*)'/i.exec(m);
  if (badProp)
    return `视图里引用的属性「${badProp[1]}」在对象类型「${badProp[2]}」上不存在：换一个属性，或先到本体里加上它。`;
  if (/expand relation .{0,60}is not defined in the ontology/i.test(m))
    return "查询要展开的关系在本体里不存在：先到「本体」页加上这个关系，或去掉这条展开。";
  if (/object_type is required/i.test(m))
    return "要先选对象类型：视图 / 查询都靠它决定读哪类对象。";
  if (m.includes("object type") && m.includes("is not defined in the ontology") && !m.includes("target object type")) {
    const hit = /object type '([^']*)' is not defined in the ontology/.exec(m);
    return hit
      ? `对象类型「${hit[1]}」在本体里不存在：先到「本体」页加上它，或改选一个已存在的对象类型。`
      : `对象类型不在本体里。${m}`;
  }
  if (/only a draft application can be edited/i.test(m) || /application .{0,60}is not a draft/i.test(m))
    return "这个应用不是草稿：已发布的应用不能直接编辑。用应用头部的「克隆为草稿」复制出新草稿再改。";
  // ---- 视图配置：chart 要 x/y，map 要 lat/lng ----
  if (/chart (view )?needs (a )?config(\.x|\.y)?/i.test(m) || /config\.x.{0,40}config\.y/i.test(m))
    return "图表视图缺配置：需要选两个属性，x 做标签、y 做数值（y 必须是数值属性）。到视图「编辑」里补上配置再保存。";
  if (/map (view )?needs (a )?config(\.lat|\.lng)?/i.test(m) || /config\.lat.{0,40}config\.lng/i.test(m))
    return "地图视图缺配置：需要选两个属性，lat 是纬度、lng 是经度（都必须是数值属性）。到视图「编辑」里补上配置再保存。";
  if (/config\.(x|y|lat|lng).{0,90}(unknown|not (a |an )?(property|attribute)|no such|does not exist)/i.test(m))
    return `视图配置里的属性不在作用对象上：${m}。到视图「编辑」里换一个当前对象类型里存在的属性。`;
  if (/config\.(y|lat|lng).{0,90}(must be|needs to be|is not) (a )?(number|numeric|integer)/i.test(m)
      || /(numeric|number|integer) (property|attribute).{0,40}config\.(y|lat|lng)/i.test(m))
    return "视图配置里的数值字段选错了：图表的 y、地图的 lat/lng 只能选 number 或 integer 属性。";
  // ---- 视图/查询的单个校验错误：与发布时的说法同一套翻译 ----
  if (/^(view|query) '/i.test(m)) {
    const translated = translateApplicationProblem(m);
    if (translated !== m.trim()) return `应用检查没通过：${translated}`;
  }
  if (/query_alias/i.test(m))
    return "视图绑定的 query_alias 对不上：先建这个查询，或把视图改成按对象类型取数。";
  if (/expand direction/i.test(m))
    return "查询展开方向只能是 out（出边）或 in（入边）。";
  if (/(unknown|not found|does not exist) (view|query) '/i.test(m))
    return "应用里找不到这个视图或查询：可能已被删除或改名，刷新后再试。";
  // ---- 能力：只有草稿能改；函数名冲突要说清是「改」还是「克隆」 ----
  if (/only a draft capability can be edited/i.test(m))
    return "只有草稿能力能改：已发布（或已校验）的能力不能直接编辑。用行末「克隆为草稿」复制出新草稿，改好再发布，旧版本继续可用。";
  if (/capability .{0,90}(is not a draft|is already published|is published)/i.test(m))
    return "这个能力不是草稿：已发布的能力不能直接编辑。用「克隆为草稿」复制出新草稿再改，旧版本会继续保留。";
  if (m.includes("clone key must not be empty"))
    return "新标识不能为空：填一个英文短词，例如原标识后加 _v2。";
  if (m.includes("clone name must not be empty"))
    return "新名称不能为空：给克隆出来的副本起一个业务名字。";
  if (m.includes("already exists (capability")) {
    // 后端两种说法：改能力是「choose a different key or target_type」，克隆是「choose a different key」
    const hit = /capability function ([^;]+) already exists \(capability '([^']*)'\)/.exec(m);
    const fn = (hit?.[1] || "").replace(/'/g, "").trim();
    if (m.includes("or target_type"))
      return `函数名冲突${fn ? `（${fn}）` : ""}：同一个作用对象下已经有同名能力。换一个标识（例如加 _v2），或换一个作用对象；也可以改用「克隆为草稿」保留旧版本、生成新标识。`;
    return `克隆用的新标识冲突${fn ? `（${fn}）` : ""}：同一作用对象下已经有这个函数名。换一个标识再试（默认是「原标识_v2」）；克隆失败不会影响原能力和运行时。`;
  }
  if (/clone/i.test(m) && /(conflict|is taken|duplicate)/i.test(m))
    return "克隆用的新标识冲突：同一作用对象下已经有这个标识。换一个（默认是「原标识_v2」）再试；克隆失败不会影响原能力。";
  if (/(capability (key|function)|function) .{0,90}(already exists|conflicts? with|is taken)/i.test(m))
    return "函数名冲突：同一个作用对象下已经有同名能力。换一个标识（例如在原标识后加 _v2），或者用「克隆为草稿」自动生成新标识。";
  if (m.includes("target object type") && m.includes("is not defined in the ontology")) {
    const hit = /target object type '([^']*)' is not defined in the ontology/.exec(m);
    return hit
      ? `作用对象「${hit[1]}」在本体里不存在：先到「本体」页加上这个对象类型，或换一个作用对象。`
      : `作用对象不在本体里。${m}`;
  }
  if (m.includes("capability validation failed")) {
    const detail = m.replace("capability validation failed: ", "")
      .replace(/is not an identifier/g, "只能是字母、数字、下划线")
      .replace(/python implementation needs 'source' or 'entrypoint'/g, "脚本实现需要有 source")
      .replace(/agent implementation needs a 'goal'/g, "智能目标需要一句 goal")
      .replace(/implementation kind '([^']*)' not in/g, "实现方式「$1」不合法，允许的是")
      .replace(/contract\.(\w+) must be a list/g, "契约 $1 要是数组")
      .replace(/contract\.idempotent must be a boolean/g, "契约 idempotent 要是布尔值")
      .replace(/contract\.compensate\.kind '([^']*)' not in/g, "契约补偿方式「$1」不合法，允许的是");
    return `能力定义没通过检查：${detail}`;
  }
  if (m.includes("cannot drop properties")) {
    // object type 'Vehicle' cannot drop properties ['plate']: still referenced by identity fields ['plate']; remove those references first
    const hit = /cannot drop properties (\[[^\]]*\]): still referenced by (.+?); remove those references first/.exec(m);
    if (!hit) return `不能删属性：仍被其他定义引用。${m}`;
    const dropped = hit[1].replace(/[[\]']/g, "");
    const refs = hit[2]
      .replace(/identity fields/gi, "身份键")
      .replace(/state machine field/gi, "状态机字段")
      .replace(/vocabulary terms/gi, "业务术语")
      .replace(/projections/gi, "投影")
      .replace(/applications/gi, "应用");
    return `不能删属性 ${dropped}：仍被${refs}引用。先去「术语 / 投影 / 应用 / 身份键」里去掉这些引用，再删属性。`;
  }
  if (m.includes("only a draft ontology can be edited"))
    return "只有草稿能改本体信息：当前版本不是草稿（多半已发布）。点右上角「修订」复制出新草稿，再改。";
  if (m.includes("only a draft ontology can be discarded"))
    return "只有修订草稿能丢弃：当前版本不是草稿，没有可恢复的上一版。";
  if (m.includes("cannot discard this ontology draft: it was not created by a revision"))
    return "这版草稿不是由「修订」复制来的，没有可恢复的上一发布版本；直接改完发布即可，或先删掉工作空间重来。";
  if (m.includes("cannot discard this ontology draft: parent version"))
    return "这版草稿对应的上一发布版本已经不在库里的，无法恢复；请先确认历史版本再操作。";
  if (/cannot (drop|remove) assets/i.test(m) || /assets? .*still (used|referenced) by/i.test(m)) {
    // cannot remove datasource assets ['vehicles']: still used by projections ['车辆映射']; update or delete those projections first
    const hit = /cannot remove datasource assets (\[[^\]]*\]): still used by projections (.+?);/.exec(m);
    if (hit) {
      const dropped = hit[1].replace(/[[\]']/g, "");
      const used = hit[2].replace(/[[\]']/g, "");
      return `不能删数据表 ${dropped}：仍被映射 ${used} 引用。先改这些映射（去掉对这张表的依赖）或删掉映射，再回来删数据表。`;
    }
    return `不能删除数据表：它仍被映射引用着，先改映射或删掉映射，再删数据表。${m}`;
  }
  if (/datasource kind .{0,40}(immutable|cannot be changed|can not be changed|is fixed)/i.test(m))
    return "数据源类型不能改：改类型等于换一个数据源。请新建一个数据源，再把映射逐个改过去（现有数据源可以继续用）。";
  if (m.includes("datasource connection failed"))
    return `连不上数据源：${m.replace("datasource connection failed: ", "")}。这次改动没有保存，旧配置原样保留。`;
  if (m.includes("invalid datasource config for kind")) {
    const hit = /invalid datasource config for kind '([^']*)': (.+)/.exec(m);
    return hit
      ? `数据源的连接参数不完整或格式不对（来源类型 ${hit[1]}）：${hit[2]}`
      : `数据源的连接参数不完整或格式不对。${m}`;
  }
  if (/unknown asset .* in datasource/i.test(m))
    return "这个数据表不在数据源的声明里：先到数据源「编辑」里补上它的声明，再预览或同步。";
  if (m.includes("failed to fetch asset"))
    return `读取数据表失败：${m.replace("failed to fetch asset ", "").replace(/: /, " —— ")}。检查这张表的配置（查询语句、接口路径或样例数据）后重试。`;
  if (m.includes("projection validation failed")) {
    const detail = m.replace("projection validation failed: ", "")
      .replace(/unknown asset/g, "引用了未声明的数据表")
      .replace(/missing id_field/g, "缺少 id_field")
      .replace(/projection has no mappings/g, "至少要有一条对象或关系映射");
    return `映射检查没通过：${detail}`;
  }
  if (m.includes("projection mode must be"))
    return "写入模式只能是 additive 或 authoritative。";
  if (m.includes("projection row_limit must be an integer"))
    return "每次同步的行上限必须是不小于 0 的整数（0 表示不限制）。";
  if (m.includes("object mapping targets unknown object type"))
    return `映射指向了本体里没有的对象类型：${m.replace("object mapping targets unknown object type ", "")}。先在本体里加上它，或改映射里的 object_type。`;
  if (m.includes("relation mapping targets unknown relation"))
    return `映射指向了本体里没有的关系：${m.replace("relation mapping targets unknown relation ", "")}。先在本体里加上它，或改映射里的 relation。`;
  if (m.includes("does not match the declared identity field")) {
    const hit = /object mapping '([^']*)': id_field '([^']*)' does not match the declared identity field '([^']*)'/.exec(m);
    return hit
      ? `对象「${hit[1]}」的业务键对不上：映射写的是 id_field「${hit[2]}」，本体声明的身份键是「${hit[3]}」。两边改一致再保存。`
      : `映射的业务键与本体的身份键不一致。${m}`;
  }
  if (m.includes("is used by ")) {
    const hit = /^(.+?) is used by (.+?);/.exec(m);
    return hit
      ? `不能删除：${hit[1]} 仍被 ${hit[2]} 引用。请先删除或改写这些引用，再回来删除。`
      : `不能删除：仍被其他定义引用。${m}`;
  }
  if (m.includes("has no published ontology revision"))
    return "这个工作空间没有可用的已发布本体版本；请先发布本体，再重试删除。";
  if (m.includes("the workspace ontology is not published; publish it first"))
    return "当前本体还没发布：请先到「本体」页点「发布」，再重试这个操作。";
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

/** 一条应用发布校验问题的中文说法。认不出的原样返回：宁可留英文，也不篡改后端语义。 */
function translateApplicationProblem(raw: string): string {
  const s = raw.trim();
  let hit: RegExpExecArray | null;
  if (/^application needs at least one view or query/.test(s))
    return "至少需要一个视图或一个查询：空应用不能发布。";
  if ((hit = /^view '([^']*)': unknown kind '([^']*)'/.exec(s)))
    return `视图「${hit[1]}」形式不合法（${hit[2]}）：只能是表格 / 卡片 / 图表 / 地图。`;
  if ((hit = /^view '([^']*)': object_type is required/.exec(s)))
    return `视图「${hit[1]}」没绑定对象类型。`;
  if ((hit = /^view '([^']*)': unknown object type '([^']*)'/.exec(s)))
    return `视图「${hit[1]}」指向不存在的对象类型「${hit[2]}」：本体里没有它，或这次发布用的是别的本体版本。`;
  if ((hit = /^view '([^']*)': unknown property '([^']*)' on '([^']*)'/.exec(s)))
    return `视图「${hit[1]}」在对象类型「${hit[3]}」上引用了不存在的属性「${hit[2]}」。`;
  if ((hit = /^view '([^']*)': query_alias '([^']*)' does not name a query/.exec(s)))
    return `视图「${hit[1]}」绑定的查询「${hit[2]}」不在这个应用里：先加这个查询，或把视图改成按对象类型取数。`;
  if ((hit = /^view '([^']*)': (chart|map) needs config\.(x|y|lat|lng)/.exec(s))) {
    const need = hit[2] === "chart" ? "x 做标签、y 做数值" : "lat 是纬度、lng 是经度";
    return `${hit[2] === "chart" ? "图表" : "地图"}视图「${hit[1]}」缺渲染配置 ${hit[3]}：需要 ${need}，到视图「编辑」里补上再发布。`;
  }
  if ((hit = /^view '([^']*)': config\.(x|y|lat|lng) '([^']*)' (?:is not|isn't|must be) an? (?:numeric|number) property/.exec(s)))
    return `视图「${hit[1]}」的配置 ${hit[2]} = 「${hit[3]}」不是数值属性：图表的 y、地图的 lat/lng 只能选 number/integer 属性。`;
  if ((hit = /^view '([^']*)': config\.(x|y|lat|lng) '([^']*)' (?:is not|isn't) an? property/.exec(s)))
    return `视图「${hit[1]}」的配置 ${hit[2]} = 「${hit[3]}」不在作用对象上：换一个当前对象类型里存在的属性。`;
  if ((hit = /^query '([^']*)': object_type is required/.exec(s)))
    return `查询「${hit[1]}」没选对象类型。`;
  if ((hit = /^query '([^']*)': unknown object type '([^']*)'/.exec(s)))
    return `查询「${hit[1]}」的目标对象类型「${hit[2]}」不存在。`;
  if ((hit = /^query '([^']*)': unknown filter property '([^']*)' on '([^']*)'/.exec(s)))
    return `查询「${hit[1]}」的过滤条件用了「${hit[3]}」上不存在的属性「${hit[2]}」。`;
  if ((hit = /^query '([^']*)': unknown relation '([^']*)' in expand/.exec(s)))
    return `查询「${hit[1]}」要展开的关系「${hit[2]}」不存在。`;
  if ((hit = /^query '([^']*)': expand direction '([^']*)' must be/.exec(s)))
    return `查询「${hit[1]}」的展开方向「${hit[2]}」不合法：只能是 out（出边）或 in（入边）。`;
  if ((hit = /^attached capability '([^']*)' does not exist/.exec(s)))
    return `应用挂载的能力（id ${hit[1]}）已经不存在：到「挂载能力」里取消它，或重新挂一个已发布能力。`;
  if ((hit = /^attached capability '([^']*)' is not published \(lifecycle '([^']*)'\)/.exec(s)))
    return `应用挂载的能力（id ${hit[1]}）还是${hit[2] === "draft" ? "草稿" : hit[2]}状态：先把它发布，或从应用里取消挂载。`;
  if ((hit = /^duplicate application agent alias '([^']*)'/.exec(s)))
    return `智能体标识「${hit[1]}」重复了：每个智能体的标识要唯一。`;
  if (/^application agent needs a non-empty alias/.test(s))
    return "有智能体没填标识：每个智能体都要有一个英文短名。";
  if ((hit = /^application agent '?([^':]*)'?: goal must not be empty/.exec(s)))
    return `智能体「${hit[1]}」的目标是空的：补一句它要完成什么。`;
  if (/the workspace has no published ontology revision/i.test(s))
    return "工作空间还没有已发布的本体：先发布本体，再发布应用（视图和查询都按已发布本体校验）。";
  return s;
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

const del = <T,>(path: string) => call<T>(path, { method: "DELETE" });

const patch = <T,>(path: string, body: unknown) =>
  call<T>(path, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });

const put = <T,>(path: string, body: unknown) =>
  call<T>(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });

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

export interface DeleteResult {
  deleted: boolean;
  id: string;
  name?: string;
  function?: string;
  detached_from?: string[];
  removed_runs?: number;
  runtime_objects_retained?: boolean;
}

/* ---------------- 第一步完整性：清空工作空间 / 对象实例删除与更正 ---------------- */

/** POST /reset：清空工作空间里的业务内容，工作空间本身、成员、模型配置与审计保留。 */
export interface ResetWorkspaceResult {
  workspace_id: string;
  name: string;
  reset: boolean;
  removed_documents: number;
  /** 按文档种类统计的删除数量；后端未返回或形状不同时界面只显示总数。 */
  removed_kinds?: Record<string, number> | string[];
}

/** DELETE /state/objects?ref=Type/Id：missing 是本次请求里本来就不存在的引用。 */
export interface DeleteObjectsResult {
  deleted: string[];
  missing: string[];
  changes: ChangeEvent[];
}

/** PATCH /state/objects?ref=Type/Id：changed 是本次实际被改动的属性名。 */
export interface UpdateObjectResult {
  ref: string;
  object: Obj;
  changed: string[];
}

export interface Api {
  health(): Promise<Health>;
  scheduler(): Promise<SchedulerStatus>;
  workspaces(): Promise<Workspace[]>;
  createWorkspace(name: string, description: string): Promise<Workspace>;
  deleteWorkspace(wid: string): Promise<{ id: string; name: string; deleted: boolean; removed_documents: number }>;
  /** 清空工作空间的业务内容（保留空间、成员、模型配置与审计）；confirmName 必须精确等于空间名。 */
  resetWorkspace(wid: string, confirmName: string): Promise<ResetWorkspaceResult>;

  ontology(wid: string): Promise<Ontology>;
  createOntology(wid: string, name: string, description: string): Promise<Ontology>;
  /** 整对象 upsert：不传的字段会被后端按缺省值覆盖（identity 变空、category 变 object），
   *  所以编辑器必须回传完整 payload。删属性=少提交一项。 */
  addObject(wid: string, body: {
    name: string; description: string; properties: PropertySpec[];
    identity?: string[]; category?: string; state_machine?: Record<string, unknown> | null;
  }): Promise<Ontology>;
  addRelation(wid: string, body: {
    name: string; source: string; target: string; description: string;
    cardinality?: string; inverse?: string;
  }): Promise<Ontology>;
  addTerm(wid: string, body: { term: string; concept: string; kind: string; aliases: string[] }): Promise<Ontology>;
  removeObject(wid: string, name: string): Promise<Ontology>;
  removeRelation(wid: string, name: string): Promise<Ontology>;
  removeTerm(wid: string, term: string): Promise<Ontology>;
  /** 只改本体元信息（名称/说明/口径）；仅在草稿上允许。 */
  updateOntologyMeta(wid: string, body: OntologyMetaUpdate): Promise<Ontology>;
  /** 丢弃当前修订草稿并恢复 parent 版本；不动运行时与发布指针。 */
  discardOntologyDraft(wid: string): Promise<DiscardDraftResult>;
  /** AI 快速建模：预览时传 text；apply=true 时回传预览得到的 draft 写入草稿本体 */
  draftOntology(wid: string, body: DraftRequest): Promise<DraftResult>;
  validateOntology(wid: string): Promise<{ ok: boolean; problems: string[] }>;
  publishOntology(wid: string): Promise<{ ontology: Ontology; runtime: RuntimeDescribe }>;

  runtime(wid: string): Promise<RuntimeDescribe>;
  datasources(wid: string): Promise<DataSource[]>;
  connectSource(wid: string, body: Record<string, unknown>): Promise<DataSource>;
  /** 改名称/连接参数/数据表声明；kind 不可改，后端默认先测连接再落库。 */
  updateSource(wid: string, sid: string, body: {
    name?: string; config?: Record<string, unknown>; assets?: DataAssetInput[]; test_connection?: boolean;
  }): Promise<DataSource>;
  /** 只做连通性检查，不改配置。 */
  testSource(wid: string, sid: string): Promise<SourceTestResult>;
  /** 真实读取一个数据表的前 limit 行（默认 20，后端 clamp 1..100）；不落库。 */
  previewSource(wid: string, sid: string, asset: string, limit?: number): Promise<PreviewResult>;
  deleteSource(wid: string, sid: string): Promise<DeleteResult>;
  projections(wid: string): Promise<Projection[]>;
  createProjection(wid: string, body: Record<string, unknown>): Promise<Projection>;
  /** 改名称/映射/写入模式/行上限；数据源绑定不可改。 */
  updateProjection(wid: string, pid: string, body: {
    name?: string; object_mappings?: ObjectMapping[]; relation_mappings?: RelationMapping[];
    mode?: string; row_limit?: number;
  }): Promise<Projection>;
  deleteProjection(wid: string, pid: string): Promise<DeleteResult>;
  runProjection(wid: string, pid: string): Promise<ProjectionRun>;

  capabilities(wid: string): Promise<Capability[]>;
  smartUiMeta(wid: string): Promise<SmartUiMeta>;
  /** 读取工作空间绑定的声明式 Pack。 */
  smartUiConfig(wid: string): Promise<SmartUiConfig>;
  /** 绑定/清空工作空间默认 Pack；pack_id 为空字符串表示清空。 */
  smartUiSetPack(wid: string, packId: string): Promise<SmartUiConfig>;
  /** 规则包列表（含内置默认包与当前绑定）。 */
  smartUiRulePacks(wid: string): Promise<SmartUiRulePackList>;
  /** 单个规则包完整内容（规则/预算）。 */
  smartUiRulePack(wid: string, id: string): Promise<SmartUiRulePack>;
  smartUiCreateRulePack(wid: string, body: {
    name?: string; description?: string; baseId?: string;
    rules?: Record<string, unknown>[]; budget?: Record<string, unknown>;
    ontologyProfile?: string;
  }): Promise<SmartUiRulePack>;
  smartUiUpdateRulePack(wid: string, id: string, body: Record<string, unknown>): Promise<SmartUiRulePack>;
  smartUiValidateRulePack(wid: string, body: Record<string, unknown>): Promise<SmartUiRuleValidation>;
  smartUiDryRunRulePack(wid: string, id: string, body?: {
    question?: string; roles?: string[]; focus?: SmartUiFocusRequest;
    pack?: Record<string, unknown>;
  }): Promise<{
    ok: boolean;
    issues: { code: string; ruleId: string; message: string }[];
    hash: string;
    pack?: SmartUiRulePack | null;
    blueprint?: Record<string, unknown> | null;
    ruleTrace?: UiRuleTrace | null;
    clarify?: UiRuleClarify[];
    requestedPerspectives?: string[];
    finalPerspectives?: string[];
    prunedPerspectives?: { kind: string; key: string; reason: string }[];
  }>;
  smartUiPublishRulePack(wid: string, id: string, note?: string): Promise<SmartUiRulePack>;
  smartUiRollbackRulePack(wid: string, id: string): Promise<SmartUiRulePack>;
  smartUiArchiveRulePack(wid: string, id: string): Promise<SmartUiRulePack>;
  smartUiCloneRulePack(wid: string, id: string, name?: string): Promise<SmartUiRulePack>;
  smartUiBindRulePack(wid: string, id: string): Promise<{ workspace_id: string; rulePackId: string }>;
  /** 可搜索的焦点实例：object 缺省时返回所有对象类型里的实例。 */
  smartUiEntities(wid: string, params?: {
    object?: string; q?: string; limit?: number;
  }): Promise<SmartUiEntityResult>;
  /** 202 + 后台生成；用 smartUiGeneration 轮询。focus 指定“这次只看某个实例”。 */
  smartUiGenerate(wid: string, body: {
    question: string; roles: string[]; mode?: "auto" | "llm" | "rule" | "deterministic";
    focus?: SmartUiFocusRequest; pack_id?: string; rule_pack_id?: string;
  }): Promise<UiGeneration>;
  /** 分页 + 过滤（q/status/object）的生成列表；返回 total/offset/limit/all。 */
  smartUiGenerations(wid: string, params?: SmartUiGenerationListQuery): Promise<SmartUiGenerationListResult>;
  /** 删除未发布的生成记录（连带未发布草稿）；运行中/已被发布草稿引用时后端 400。 */
  smartUiDeleteGeneration(wid: string, gid: string): Promise<{ deleted: string; drafts: number }>;
  /** 生成详情：比列表多 steps/blueprint/lineage/diff。 */
  smartUiGeneration(wid: string, gid: string): Promise<UiGenerationDetail>;
  /** 取消排队中/生成中的任务；已结束的记录原样返回。 */
  smartUiCancel(wid: string, gid: string): Promise<UiGeneration>;
  /** 按已存蓝图重编译 + 当前数据快照；iframe 用这个地址取最新数据。 */
  smartUiGenerationView(wid: string, gid: string): Promise<UiGenerationViewResult>;
  /** 回放已成功版本并报告插件锁漂移；不重新调用模型。 */
  smartUiReplayGeneration(wid: string, gid: string): Promise<UiGenerationViewResult & {
    replay: {
      ok: boolean; pinned: { id: string; kind: string; version: string }[];
      current: { id: string; kind: string; version: string }[];
      missing: string[]; changed: string[]; added: string[];
    };
  }>;
  /** 基于已成功版本继续修改；focus 省略时后端继承父记录的焦点。 */
  smartUiRefine(wid: string, gid: string, instruction: string,
                focus?: SmartUiFocusRequest): Promise<UiGenerationDetail>;
  smartUiAccept(wid: string, gid: string, name?: string): Promise<UiDraft>;
  smartUiPublish(wid: string, gid: string, body?: {
    name?: string; application_id?: string;
  }): Promise<{ draft: UiDraft; application: Application; artifact: Record<string, unknown> }>;
  smartUiDrafts(wid: string): Promise<{ items: UiDraft[]; total: number }>;
  /** 重命名草稿（published 草稿也允许改名，但删除不允许）。 */
  smartUiRenameDraft(wid: string, did: string, body: { name: string }): Promise<UiDraft>;
  /** 删除草稿；published 草稿属于应用契约，后端会 400。 */
  smartUiDeleteDraft(wid: string, did: string): Promise<DeleteResult>;
  createCapability(wid: string, body: Record<string, unknown>): Promise<Capability>;
  /** 改能力定义；后端只允许改草稿（已发布请用 cloneCapability 复制出新草稿）。 */
  updateCapability(wid: string, cid: string, body: CapabilityUpdate): Promise<Capability>;
  /** 复制成新草稿：不发布、不碰运行时，旧能力原样保留。key 省略时后端用 `${key}_v2`。 */
  cloneCapability(wid: string, cid: string, body: { key?: string; name?: string }): Promise<Capability>;
  publishCapability(wid: string, cid: string): Promise<CapabilityPublishResult>;
  deleteCapability(wid: string, cid: string): Promise<DeleteResult>;
  invoke(wid: string, body: {
    function: string; target: string | null; args: Record<string, unknown>;
    idempotency_key: string;
  }): Promise<InvokeResult>;

  applications(wid: string): Promise<Application[]>;
  application(wid: string, aid: string): Promise<Application>;
  createApplication(wid: string, name: string, description: string): Promise<Application>;
  /** 改应用名/说明；已发布应用会被后端拒绝（先克隆为草稿）。 */
  updateApplication(wid: string, aid: string, body: { name?: string; description?: string }): Promise<Application>;
  /** 复制成新草稿：不发布、不碰运行时，旧应用原样保留。name 省略时后端用 `源名称 副本`。 */
  cloneApplication(wid: string, aid: string, body?: { name?: string }): Promise<Application>;
  publishApplication(wid: string, aid: string): Promise<Application>;
  addView(wid: string, aid: string, body: Record<string, unknown>): Promise<Application>;
  /** 改视图定义；alias 不可改。chart/map 的 config 见 ViewConfig。 */
  updateView(wid: string, aid: string, alias: string, body: Record<string, unknown>): Promise<Application>;
  addQuery(wid: string, aid: string, body: Record<string, unknown>): Promise<Application>;
  /** 改查询定义；alias 不可改。 */
  updateQuery(wid: string, aid: string, alias: string, body: Record<string, unknown>): Promise<Application>;
  addAgent(wid: string, aid: string, body: Record<string, unknown>): Promise<Application>;
  attachCapability(wid: string, aid: string, capability_id: string): Promise<Application>;
  deleteApplication(wid: string, aid: string): Promise<DeleteResult>;
  removeView(wid: string, aid: string, alias: string): Promise<Application>;
  removeQuery(wid: string, aid: string, alias: string): Promise<Application>;
  removeAgent(wid: string, aid: string, alias: string): Promise<Application>;
  detachCapability(wid: string, aid: string, capability_id: string): Promise<Application>;
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
  /** 删除对象实例；关联关系由后端级联处理，不可撤销。 */
  deleteObject(wid: string, ref: string): Promise<DeleteObjectsResult>;
  /** 更正对象属性；identity 属性由后端拒绝，返回实际改动的属性名。 */
  updateObject(wid: string, ref: string, properties: Record<string, unknown>): Promise<UpdateObjectResult>;
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
  /** 真实调用一次模型端点做诊断（HTTP 200，ok=false 时看 errorType/message）。 */
  llmCheck(wid: string): Promise<LlmCheckResult>;
  configureWorkspaceLlm(wid: string, body: LlmConfigUpdate): Promise<LlmConfig>;
}

export const api: Api = {
  health: () => call("/api/health"),
  scheduler: () => call("/api/scheduler"),
  workspaces: () => call("/api/workspaces"),
  createWorkspace: (name, description) => post("/api/workspaces", { name, description }),
  deleteWorkspace: wid => post(ws(wid, "/delete"), { confirm: true }),
  resetWorkspace: (wid, confirmName) =>
    post(ws(wid, "/reset"), { confirm: true, confirm_name: confirmName }),

  ontology: wid => call(ws(wid, "/ontology")),
  createOntology: (wid, name, description) => post(ws(wid, "/ontology"), { name, description }),
  addObject: (wid, b) => post(ws(wid, "/ontology/objects"), b),
  addRelation: (wid, b) => post(ws(wid, "/ontology/relations"), b),
  addTerm: (wid, b) => post(ws(wid, "/ontology/vocabulary"), b),
  removeObject: (wid, name) =>
    del(ws(wid, `/ontology/objects/${encodeURIComponent(name)}`)),
  removeRelation: (wid, name) =>
    del(ws(wid, `/ontology/relations/${encodeURIComponent(name)}`)),
  removeTerm: (wid, term) =>
    del(ws(wid, `/ontology/vocabulary/${encodeURIComponent(term)}`)),
  updateOntologyMeta: (wid, b) => patch(ws(wid, "/ontology"), b),
  discardOntologyDraft: wid => post(ws(wid, "/ontology/discard"), {}),
  draftOntology: (wid, b) => post(ws(wid, "/ontology/draft"), b, LLM_TIMEOUT),
  validateOntology: wid => post(ws(wid, "/ontology/validate"), {}),
  publishOntology: wid => post(ws(wid, "/ontology/publish"), {}),

  runtime: wid => call(ws(wid, "/runtime")),
  datasources: wid => call(ws(wid, "/datasources")),
  connectSource: (wid, b) => post(ws(wid, "/datasources"), b),
  updateSource: (wid, sid, b) =>
    patch(ws(wid, `/datasources/${encodeURIComponent(sid)}`), b),
  testSource: (wid, sid) =>
    post(ws(wid, `/datasources/${encodeURIComponent(sid)}/test`), {}),
  previewSource: (wid, sid, asset, limit = 20) =>
    post(ws(wid, `/datasources/${encodeURIComponent(sid)}/preview`), { asset, limit }),
  deleteSource: (wid, sid) =>
    del(ws(wid, `/datasources/${encodeURIComponent(sid)}`)),
  projections: wid => call(ws(wid, "/projections")),
  createProjection: (wid, b) => post(ws(wid, "/projections"), b),
  updateProjection: (wid, pid, b) =>
    patch(ws(wid, `/projections/${encodeURIComponent(pid)}`), b),
  deleteProjection: (wid, pid) =>
    del(ws(wid, `/projections/${encodeURIComponent(pid)}`)),
  runProjection: (wid, pid) => post(ws(wid, `/projections/${encodeURIComponent(pid)}/run`), {}),

  capabilities: wid => call(ws(wid, "/capabilities")),
  smartUiMeta: wid => call(ws(wid, "/smart-ui/meta")),
  smartUiConfig: wid => call(ws(wid, "/smart-ui/config")),
  smartUiSetPack: (wid, packId) =>
    post(ws(wid, "/smart-ui/config"), { pack_id: packId }),
  smartUiRulePacks: wid => call(ws(wid, "/smart-ui/rule-packs")),
  smartUiRulePack: (wid, id) =>
    call(ws(wid, `/smart-ui/rule-packs/${encodeURIComponent(id)}`)),
  smartUiCreateRulePack: (wid, body) =>
    post(ws(wid, "/smart-ui/rule-packs"), body),
  smartUiUpdateRulePack: (wid, id, body) =>
    put(ws(wid, `/smart-ui/rule-packs/${encodeURIComponent(id)}`), body),
  smartUiValidateRulePack: (wid, body) =>
    post(ws(wid, "/smart-ui/rule-packs/validate"), body),
  smartUiDryRunRulePack: (wid, id, body = {}) =>
    post(ws(wid, `/smart-ui/rule-packs/${encodeURIComponent(id)}/dry-run`), body),
  smartUiPublishRulePack: (wid, id, note = "") =>
    post(ws(wid, `/smart-ui/rule-packs/${encodeURIComponent(id)}/publish`), { note }),
  smartUiRollbackRulePack: (wid, id) =>
    post(ws(wid, `/smart-ui/rule-packs/${encodeURIComponent(id)}/rollback`), {}),
  smartUiArchiveRulePack: (wid, id) =>
    post(ws(wid, `/smart-ui/rule-packs/${encodeURIComponent(id)}/archive`), {}),
  smartUiCloneRulePack: (wid, id, name = "") =>
    post(ws(wid, `/smart-ui/rule-packs/${encodeURIComponent(id)}/clone`), { name }),
  smartUiBindRulePack: (wid, id) =>
    post(ws(wid, "/smart-ui/rule-packs/bind"), { rule_pack_id: id }),
  smartUiEntities: (wid, params = {}) => {
    const search = new URLSearchParams();
    if (params.object) search.set("object", params.object);
    if (params.q) search.set("q", params.q);
    if (params.limit) search.set("limit", String(params.limit));
    const qs = search.toString();
    return call(ws(wid, `/smart-ui/entities${qs ? `?${qs}` : ""}`));
  },
  smartUiGenerate: (wid, b) => post(ws(wid, "/smart-ui/generations"), b),
  smartUiGenerations: (wid, params = {}) => {
    const search = new URLSearchParams();
    const limit = Math.max(1, Math.min(100, Number(params.limit) || 30));
    search.set("limit", String(limit));
    if (params.offset) search.set("offset", String(Math.max(0, params.offset)));
    if (params.q) search.set("q", params.q);
    if (params.status) search.set("status", params.status);
    if (params.object) search.set("object", params.object);
    if (params.mode) search.set("mode", params.mode);
    return call(ws(wid, `/smart-ui/generations?${search.toString()}`));
  },
  smartUiDeleteGeneration: (wid, gid) =>
    del(ws(wid, `/smart-ui/generations/${encodeURIComponent(gid)}`)),
  smartUiGeneration: (wid, gid) =>
    call(ws(wid, `/smart-ui/generations/${encodeURIComponent(gid)}`)),
  smartUiCancel: (wid, gid) =>
    post(ws(wid, `/smart-ui/generations/${encodeURIComponent(gid)}/cancel`), {}),
  smartUiGenerationView: (wid, gid) =>
    call(ws(wid, `/smart-ui/generations/${encodeURIComponent(gid)}/view`)),
  smartUiReplayGeneration: (wid, gid) =>
    call(ws(wid, `/smart-ui/generations/${encodeURIComponent(gid)}/replay`)),
  smartUiRefine: (wid, gid, instruction, focus) =>
    post(ws(wid, `/smart-ui/generations/${encodeURIComponent(gid)}/refine`), {
      instruction,
      ...(focus ? { focus } : {}),
    }),
  smartUiAccept: (wid, gid, name) =>
    post(ws(wid, `/smart-ui/generations/${encodeURIComponent(gid)}/accept`), { name: name ?? "" }),
  smartUiPublish: (wid, gid, body) =>
    post(ws(wid, `/smart-ui/generations/${encodeURIComponent(gid)}/publish`), body ?? {}),
  smartUiDrafts: wid => call(ws(wid, "/smart-ui/drafts")),
  smartUiRenameDraft: (wid, did, body) =>
    put(ws(wid, `/smart-ui/drafts/${encodeURIComponent(did)}`), body),
  smartUiDeleteDraft: (wid, did) =>
    del(ws(wid, `/smart-ui/drafts/${encodeURIComponent(did)}`)),
  createCapability: (wid, b) => post(ws(wid, "/capabilities"), b),
  updateCapability: (wid, cid, b) =>
    patch(ws(wid, `/capabilities/${encodeURIComponent(cid)}`), b),
  cloneCapability: (wid, cid, b) =>
    post(ws(wid, `/capabilities/${encodeURIComponent(cid)}/clone`), b),
  publishCapability: (wid, cid) => post(ws(wid, `/capabilities/${encodeURIComponent(cid)}/publish`), {}),
  deleteCapability: (wid, cid) =>
    del(ws(wid, `/capabilities/${encodeURIComponent(cid)}`)),
  invoke: (wid, b) => post(ws(wid, "/invoke"), b),
  applications: wid => call(ws(wid, "/applications")),
  application: (wid, aid) => call(ws(wid, `/applications/${encodeURIComponent(aid)}`)),
  createApplication: (wid, name, description) => post(ws(wid, "/applications"), { name, description }),
  updateApplication: (wid, aid, b) =>
    patch(ws(wid, `/applications/${encodeURIComponent(aid)}`), b),
  cloneApplication: (wid, aid, b) =>
    post(ws(wid, `/applications/${encodeURIComponent(aid)}/clone`), b || {}),
  publishApplication: (wid, aid) => post(ws(wid, `/applications/${encodeURIComponent(aid)}/publish`), {}),
  addView: (wid, aid, b) => post(ws(wid, `/applications/${encodeURIComponent(aid)}/views`), b),
  updateView: (wid, aid, alias, b) =>
    patch(ws(wid, `/applications/${encodeURIComponent(aid)}/views/${encodeURIComponent(alias)}`), b),
  addQuery: (wid, aid, b) => post(ws(wid, `/applications/${encodeURIComponent(aid)}/queries`), b),
  updateQuery: (wid, aid, alias, b) =>
    patch(ws(wid, `/applications/${encodeURIComponent(aid)}/queries/${encodeURIComponent(alias)}`), b),
  addAgent: (wid, aid, b) => post(ws(wid, `/applications/${encodeURIComponent(aid)}/agents`), b),
  attachCapability: (wid, aid, capability_id) => post(ws(wid, `/applications/${encodeURIComponent(aid)}/capabilities`), { capability_id }),
  deleteApplication: (wid, aid) =>
    del(ws(wid, `/applications/${encodeURIComponent(aid)}`)),
  removeView: (wid, aid, alias) =>
    del(ws(wid, `/applications/${encodeURIComponent(aid)}/views/${encodeURIComponent(alias)}`)),
  removeQuery: (wid, aid, alias) =>
    del(ws(wid, `/applications/${encodeURIComponent(aid)}/queries/${encodeURIComponent(alias)}`)),
  removeAgent: (wid, aid, alias) =>
    del(ws(wid, `/applications/${encodeURIComponent(aid)}/agents/${encodeURIComponent(alias)}`)),
  detachCapability: (wid, aid, capability_id) =>
    del(ws(wid, `/applications/${encodeURIComponent(aid)}/capabilities/${encodeURIComponent(capability_id)}`)),
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
  deleteObject: (wid, ref) =>
    del(ws(wid, `/state/objects?ref=${encodeURIComponent(ref)}`)),
  updateObject: (wid, ref, properties) =>
    patch(ws(wid, `/state/objects?ref=${encodeURIComponent(ref)}`), { properties }),
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
  llmCheck: wid => post(ws(wid, "/llm-config/check"), {}),
  configureWorkspaceLlm: (wid, body) => call(ws(wid, "/llm-config"), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }),
};

export const assetNames = (d: DataSource): string[] =>
  Array.isArray(d.assets) ? d.assets.map(a => a.name) : Object.keys(d.assets || {});
