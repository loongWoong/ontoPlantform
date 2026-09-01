/** 与后端 REST 契约一一对应的类型与客户端。
 *  错误在这里统一翻译成业务语言 + 下一步指引（design.txt §42 第一原则）。 */

export type Lifecycle = "draft" | "validated" | "published" | "deprecated" | "none";

export interface Workspace { id: string; name: string; description: string; created_at: string; runtime_config?: Record<string, unknown> }
export interface PropertySpec { name: string; type: string; description?: string }
export interface ObjectTypeSpec { name: string; description: string; properties: Record<string, PropertySpec> }
export interface RelationSpec { name: string; source: string; target: string; description?: string; cardinality?: string }
export interface VocabTerm { term: string; concept: string; kind: string; aliases?: string[] }
export interface Ontology {
  name: string; description: string; version: number; lifecycle: Lifecycle;
  objects: Record<string, ObjectTypeSpec>;
  relations: Record<string, RelationSpec>;
  vocabulary: Record<string, VocabTerm>;
}
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
export interface Application {
  id: string; name: string; description: string; lifecycle: Lifecycle;
  views: Record<string, AppView>; queries: Record<string, AppQuery>;
  agents: AppAgent[]; capabilities: string[];
}
export interface Obj { type: string; id: string; properties: Record<string, unknown> }
export interface ChangeEvent { seq: number; ts: string; source: string; function?: string; changes: Record<string, unknown>[] }
export interface AuditEvent { ts: string; actor: string; action: string; target: string; detail?: Record<string, unknown> }
export interface InvokeResult { ok: boolean; output?: unknown; changes?: Record<string, unknown>[]; execution_id?: string; error?: string | null }
export interface AskResult { question: string; answer: string; concepts: { term: string; concept: string }[]; objects: Obj[]; related: { from: string; relation: string; object: Obj }[] }
export interface AgentStep {
  index?: number; thought?: string;
  action?: { tool?: string; args?: Record<string, unknown> };
  observation?: string;
}
export interface AgentRun { goal: string; ok: boolean; answer: string; steps: AgentStep[] }
export interface QueryResult { query: string; objects: Obj[]; expand: { relation: string; direction: string; objects: Obj[] }[] }

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
    return "本体已发布，定义已锁定。要改动请先确认是否要新建一个工作空间（v0.1 边界）。";
  if (m.includes("cannot publish from") || m.includes("cannot validate from"))
    return "当前生命周期状态不允许这个操作（很可能已经发布过了）。";
  if (m.includes("no schema published"))
    return "业务世界还没启动：请先到「本体」页点「发布」。";
  if (m.includes("has no ontology"))
    return "这个工作空间还没有本体：请先到「本体」页创建。";
  if (m.includes("ontology validation failed"))
    return "本体检查没通过：" + m.replace("ontology validation failed: ", "");
  if (m.includes("already has an ontology"))
    return "这个工作空间已经有一个本体了（v0.1 每个空间一个）。";
  if (m.includes("not found")) return m.replace("not found", "没找到");
  return m;
}

const TIMEOUT = 15000;

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT);
  try {
    const res = await fetch(path, { ...init, signal: ctrl.signal });
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
    if ((e as Error).name === "AbortError") throw new ApiError("请求超时：服务超过 15 秒没响应", 0, "Timeout");
    throw new ApiError("连不上服务：请确认 onto-api 正在运行", 0, "Network");
  } finally {
    clearTimeout(timer);
  }
}

const post = <T,>(path: string, body: unknown) =>
  call<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });

const USER = "admin";
const ws = (wid: string, suffix: string) =>
  `/api/workspaces/${encodeURIComponent(wid)}${suffix}${suffix.includes("?") ? "&" : "?"}user=${USER}`;

/** LLM 选择：平台默认 / OpenAI 兼容 / 演示模式（脚本化 Echo，无需真模型） */
export type LlmChoice =
  | null
  | { provider: "openai"; base_url?: string; model?: string; api_key?: string }
  | { script: string[] };

export interface Health {
  status: string; version: string; llm_configured: boolean;
  backend?: string; runtime_store?: string; llm_provider?: string; console?: string;
}

export interface Api {
  health(): Promise<Health>;
  workspaces(): Promise<Workspace[]>;
  createWorkspace(name: string, description: string): Promise<Workspace>;

  ontology(wid: string): Promise<Ontology>;
  createOntology(wid: string, name: string, description: string): Promise<Ontology>;
  addObject(wid: string, body: { name: string; description: string; properties: PropertySpec[] }): Promise<Ontology>;
  addRelation(wid: string, body: { name: string; source: string; target: string; description: string }): Promise<Ontology>;
  addTerm(wid: string, body: { term: string; concept: string; kind: string; aliases: string[] }): Promise<Ontology>;
  validateOntology(wid: string): Promise<{ ok: boolean; problems: string[] }>;
  publishOntology(wid: string): Promise<{ ontology: Ontology; runtime: RuntimeDescribe }>;

  runtime(wid: string): Promise<RuntimeDescribe>;
  datasources(wid: string): Promise<DataSource[]>;
  connectSource(wid: string, body: Record<string, unknown>): Promise<DataSource>;
  projections(wid: string): Promise<Projection[]>;
  createProjection(wid: string, body: Record<string, unknown>): Promise<Projection>;
  runProjection(wid: string, pid: string): Promise<Record<string, number>>;

  capabilities(wid: string): Promise<Capability[]>;
  createCapability(wid: string, body: Record<string, unknown>): Promise<Capability>;
  publishCapability(wid: string, cid: string): Promise<Capability>;
  invoke(wid: string, body: { function: string; target: string | null; args: Record<string, unknown> }): Promise<InvokeResult>;

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

  find(wid: string, type: string, filters: Record<string, string>): Promise<Obj[]>;
  get(wid: string, ref: string): Promise<Obj | null>;
  related(wid: string, ref: string, relation: string, direction: string): Promise<string[]>;
  changes(wid: string, after: number): Promise<ChangeEvent[]>;
  audit(wid: string, limit: number): Promise<AuditEvent[]>;

  ask(wid: string, question: string): Promise<AskResult>;
  agent(wid: string, goal: string, maxSteps: number, llm: LlmChoice): Promise<AgentRun>;
}

export const api: Api = {
  health: () => call("/api/health"),
  workspaces: () => call(`/api/workspaces?user=${USER}`),
  createWorkspace: (name, description) => post("/api/workspaces", { name, description, user: USER }),

  ontology: wid => call(ws(wid, "/ontology")),
  createOntology: (wid, name, description) => post(ws(wid, "/ontology"), { name, description }),
  addObject: (wid, b) => post(ws(wid, "/ontology/objects"), b),
  addRelation: (wid, b) => post(ws(wid, "/ontology/relations"), b),
  addTerm: (wid, b) => post(ws(wid, "/ontology/vocabulary"), b),
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

  find: (wid, type, filters) => {
    const q = new URLSearchParams({ type });
    for (const [k, v] of Object.entries(filters)) q.set(k, v);
    return call(ws(wid, `/state/find?${q.toString()}`));
  },
  get: (wid, ref) => call(ws(wid, `/state/get?ref=${encodeURIComponent(ref)}`)),
  related: (wid, ref, relation, direction) =>
    call(ws(wid, `/state/related?ref=${encodeURIComponent(ref)}&relation=${encodeURIComponent(relation)}&direction=${direction}`)),
  changes: (wid, after) => call(ws(wid, `/changes?after=${after}`)),
  audit: (wid, limit) => call(ws(wid, `/audit?limit=${limit}`)),

  ask: (wid, question) => post(ws(wid, "/ask"), { question }),
  agent: (wid, goal, maxSteps, llm) => post(ws(wid, "/agent"), { goal, max_steps: maxSteps, ...(llm ? { llm } : {}) }),
};

export const assetNames = (d: DataSource): string[] =>
  Array.isArray(d.assets) ? d.assets.map(a => a.name) : Object.keys(d.assets || {});
