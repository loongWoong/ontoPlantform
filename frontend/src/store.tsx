/** 全局状态：工作空间、连接状态、模型选择、Toast、确认框、AI 抽屉。
 *  所有写操作都经 useAction，统一 in-flight 禁用，杜绝重复提交。 */
import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
  type ReactNode, type DependencyList,
} from "react";
import { api, ApiError, type Application, type Capability, type LlmChoice, type Workspace } from "./api";

export const LS = {
  ws: "onto.workspace",
  nav: "onto.nav",            // 上次所在页签
  collapsed: "onto.nav-collapsed", // 侧栏折叠状态（与页签分开，一个键只干一件事）
  llm: "onto.llm",
};

export type LlmSetting =
  | { mode: "platform" }
  | { mode: "openai"; base_url: string; model: string; api_key: string }
  | { mode: "demo" };

export const DEFAULT_LLM: LlmSetting = { mode: "platform" };

/** 演示脚本：先用真实工具看清世界，再给出结论；不依赖任何密钥。 */
export const DEMO_SCRIPT = [
  JSON.stringify({ think: "先看清这个业务世界里有哪些对象类型与能力", action: { tool: "describe_ontology", args: {} } }),
  JSON.stringify({ think: "挑第一个对象类型看一眼真实数据，避免凭空作答", action: { tool: "find", args: { object_type: "__FIRST_TYPE__", filter: {} } } }),
  JSON.stringify({ think: "信息够了，给出结论", answer: "（演示模式）我已经按本体看清了对象类型与真实数据；接入真实模型后，这里会是带推理链的业务结论。" }),
];

export function toChoice(s: LlmSetting, firstType?: string): LlmChoice {
  if (s.mode === "openai")
    return {
      provider: "openai",
      ...(s.base_url ? { base_url: s.base_url } : {}),
      ...(s.model ? { model: s.model } : {}),
      ...(s.api_key ? { api_key: s.api_key } : {}),
    };
  if (s.mode === "demo")
    return {
      // 演示模式：脚本化的「观察 → 行动 → 结论」。工具名与结束格式必须与后端
      // agent_runner.TOOLS_SPEC 完全一致（describe_ontology / find / get / related /
      // invoke_capability，结束是顶层 {"think", "answer"}），否则每步都 unknown tool。
      // 没有已发布对象类型时，跳过「看真实数据」那一步，脚本仍能跑完
      script: firstType
        ? DEMO_SCRIPT.map(l => l.replace('__FIRST_TYPE__', firstType))
        : DEMO_SCRIPT.filter(l => !l.includes('__FIRST_TYPE__')),
    };
  return null;
}

export type ToastKind = "" | "ok" | "warn" | "err";
interface Toast { id: number; text: string; kind: ToastKind }
export interface Completeness {
  object_types: number; relation_types: number;
  capabilities: number; published_capabilities: number;
  applications: number; published_applications: number;
  suggestions: string[];
}

export interface ConfirmState {
  title: string; body: ReactNode;
  confirmText?: string; danger?: boolean;
  run: () => Promise<void>;
}

interface Ctx {
  wid: string | null;
  workspaces: Workspace[];
  workspaceName: string;
  setWorkspace: (id: string) => void;
  reloadWorkspaces: () => Promise<void>;

  online: boolean;
  version: string;
  llmReady: boolean;
  llm: LlmSetting;
  setLlm: (s: LlmSetting) => void;

  toast: (text: string, kind?: ToastKind) => void;
  confirm: (s: Omit<ConfirmState, "run"> & { run: () => Promise<void> }) => void;

  aiOpen: boolean;
  setAiOpen: (v: boolean) => void;
  aiContext: string;
  setAiContext: (v: string) => void;

  nav: string;
  go: (tab: string, params?: Record<string, string>) => void;
  params: Record<string, string>;

  objectTypes: string[];
  relationTypes: string[];
  completeness: Completeness | null;
  refreshRuntime: () => Promise<void>;
}

const PlatformCtx = createContext<Ctx | null>(null);
export const usePlatform = (): Ctx => {
  const c = useContext(PlatformCtx);
  if (!c) throw new Error("usePlatform 必须在 PlatformProvider 内使用");
  return c;
};

/**
 * 路由形如  #/w/<workspaceId>/<page>?k=v  —— 工作空间进 URL，链接才真的可分享
 * （design.txt §42：界面状态必须可复现）。老的 #/<page> 形式继续兼容，
 * 此时工作空间回落到 localStorage 里记住的那个。
 */
function readHash(): { nav: string; params: Record<string, string>; urlWid: string | null } {
  const raw = location.hash.replace(/^#\/?/, "");
  const [path, qs] = raw.split("?");
  const params: Record<string, string> = {};
  if (qs) new URLSearchParams(qs).forEach((v, k) => { params[k] = v; });
  const parts = path.split("/").filter(Boolean);
  const fallback = localStorage.getItem(LS.nav) || "workbench";
  if (parts[0] === "w" && parts[1]) {
    return { urlWid: decodeURIComponent(parts[1]), nav: parts[2] || fallback, params };
  }
  return { urlWid: null, nav: parts[0] || fallback, params };
}

const hashFor = (wid: string | null, nav: string, params?: Record<string, string>) => {
  const qs = params && Object.keys(params).length ? "?" + new URLSearchParams(params).toString() : "";
  return wid ? `#/w/${encodeURIComponent(wid)}/${nav}${qs}` : `#/${nav}${qs}`;
};

export function PlatformProvider({ children }: { children: ReactNode }) {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [wid, setWid] = useState<string | null>(null);
  const [online, setOnline] = useState(false);
  const [version, setVersion] = useState("0.1.0");
  const [llmReady, setLlmReady] = useState(false);
  const [llm, setLlmState] = useState<LlmSetting>(() => {
    try { return JSON.parse(localStorage.getItem(LS.llm) || "") as LlmSetting; }
    catch { return DEFAULT_LLM; }
  });
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [confirmState, setConfirmState] = useState<ConfirmState | null>(null);
  const [aiOpen, setAiOpen] = useState(false);
  const [aiContext, setAiContext] = useState("");
  const [route, setRoute] = useState(readHash);
  const [objectTypes, setObjectTypes] = useState<string[]>([]);
  const [relationTypes, setRelationTypes] = useState<string[]>([]);
  const [completeness, setCompleteness] = useState<Completeness | null>(null);
  const seq = useRef(0);

  const setLlm = useCallback((s: LlmSetting) => {
    setLlmState(s);
    try { localStorage.setItem(LS.llm, JSON.stringify(s)); } catch { /* 隐私模式忽略 */ }
  }, []);

  const toast = useCallback((text: string, kind: ToastKind = "") => {
    const id = ++seq.current;
    // 最多同时 3 条：新的挤掉最旧的，避免堆叠盖住屏幕
    setToasts(prev => [...prev.slice(-2), { id, text, kind }]);
    setTimeout(() => setToasts(prev => prev.filter(t => t.id !== id)), kind === "err" ? 9000 : 4000);
  }, []);

  const ping = useCallback(async () => {
    try {
      const h = await api.health();
      setOnline(true); setVersion(h.version); setLlmReady(!!h.llm_configured);
    } catch { setOnline(false); }
  }, []);

  const reloadWorkspaces = useCallback(async () => {
    const list = await api.workspaces();
    setWorkspaces(list);
    setWid(prev => {
      // URL 里的空间优先：别人发来的链接不该被本地记忆劫持
      const fromUrl = readHash().urlWid;
      const saved = localStorage.getItem(LS.ws);
      const next = (fromUrl && list.some(w => w.id === fromUrl)) ? fromUrl
        : (prev && list.some(w => w.id === prev)) ? prev
        : list.some(w => w.id === saved) ? saved : (list[0]?.id ?? null);
      if (next) localStorage.setItem(LS.ws, next);
      return next;
    });
  }, []);

  const refreshRuntime = useCallback(async () => {
    if (!wid) { setObjectTypes([]); setRelationTypes([]); setCompleteness(null); return; }
    let rels: string[] = [];
    try {
      const rt = await api.runtime(wid);
      setObjectTypes((rt.objects || []).map(o => o.name));
      rels = Array.from(new Set((rt.relations || []).map(r => r.name)));
      setRelationTypes(rels);
    } catch { setObjectTypes([]); setRelationTypes([]); }
    // 完成度在前端算：本体 + 能力 + 应用三个真实计数，绝不显示假数字
    try {
      const [onto, caps, apps] = await Promise.all([
        api.ontology(wid).catch(() => null),
        api.capabilities(wid).catch(() => [] as Capability[]),
        api.applications(wid).catch(() => [] as Application[]),
      ]);
      const capList = caps || [];
      const appList = apps || [];
      const objectTypes = Object.keys(onto?.objects || {}).length;
      const relationCount = Object.keys(onto?.relations || {}).length;
      const publishedCaps = capList.filter(c => c.lifecycle === "published").length;
      const publishedApps = appList.filter(a => a.lifecycle === "published").length;
      const suggestions: string[] = [];
      if (!objectTypes) suggestions.push("先到「本体」定义第一个对象类型");
      else if (!relationCount) suggestions.push("对象之间还没有关系，去「本体」补一条关系");
      else if (!publishedCaps) suggestions.push("去「能力」发布第一个业务动作");
      else if (!publishedApps) suggestions.push("去「应用」把视图和查询挂上，业务人员才有入口");
      else suggestions.push("骨架已通，去「对象实例」核对真实数据");
      setCompleteness({
        object_types: objectTypes, relation_types: relationCount,
        capabilities: capList.length, published_capabilities: publishedCaps,
        applications: appList.length, published_applications: publishedApps,
        suggestions,
      });
    } catch { setCompleteness(null); }
  }, [wid]);

  useEffect(() => {
    ping();
    const t = setInterval(ping, 20000);
    const onHash = () => setRoute(readHash());
    window.addEventListener("hashchange", onHash);
    (async () => {
      try { await reloadWorkspaces(); }
      catch (e) { toast((e as ApiError).message, "err"); /* 有意不 rethrow：调用方都是 void run()，
                       错误已经通过 toast 呈现；ConfirmDialog 依赖「抛错时不关闭」的行为 */ }
    })();
    return () => { clearInterval(t); window.removeEventListener("hashchange", onHash); };
  }, [ping, reloadWorkspaces, toast]);

  useEffect(() => { refreshRuntime(); }, [refreshRuntime]);

  const setWorkspace = useCallback((id: string) => {
    setWid(id);
    localStorage.setItem(LS.ws, id);
    // 留在当前页，只换空间；但 ref/type/app 是上一个世界的作用域参数，
    // 带过去会指向不存在的对象 —— 只保留与空间无关的 tab
    const { nav, params } = readHash();
    const safe = Object.fromEntries(Object.entries(params).filter(([k]) => k === "tab"));
    const hash = hashFor(id, nav, safe);
    if (location.hash !== hash) location.hash = hash;
  }, []);

  const go = useCallback((tab: string, params?: Record<string, string>) => {
    localStorage.setItem(LS.nav, tab);
    const hash = hashFor(wid, tab, params);
    if (location.hash === hash) setRoute(readHash());
    else location.hash = hash;
  }, [wid]);

  const confirm = useCallback((s: ConfirmState) => setConfirmState(s), []);

  const value = useMemo<Ctx>(() => ({
    wid,
    workspaces,
    workspaceName: workspaces.find(w => w.id === wid)?.name || "",
    setWorkspace,
    reloadWorkspaces,
    online, version, llmReady, llm, setLlm,
    toast, confirm,
    aiOpen, setAiOpen, aiContext, setAiContext,
    nav: route.nav, params: route.params, go,
    objectTypes, relationTypes, completeness, refreshRuntime,
  }), [wid, workspaces, setWorkspace, reloadWorkspaces, online, version, llmReady, llm, setLlm,
       toast, confirm, aiOpen, aiContext, go, route,
       objectTypes, relationTypes, completeness, refreshRuntime]);

  return (
    <PlatformCtx.Provider value={value}>
      {children}
      <ToastStack toasts={toasts} dismiss={id => setToasts(p => p.filter(t => t.id !== id))} />
      {confirmState && (
        <ConfirmDialog state={confirmState} close={() => setConfirmState(null)} />
      )}
    </PlatformCtx.Provider>
  );
}

function ToastStack({ toasts, dismiss }: { toasts: Toast[]; dismiss: (id: number) => void }) {
  if (!toasts.length) return null;
  return (
    <div className="toasts" role="status" aria-live="polite">
      {toasts.map(t => (
        <div key={t.id} className={"toast " + t.kind}>
          <span className="txt">{t.text}</span>
          <button type="button" aria-label="关闭提示" onClick={() => dismiss(t.id)}>×</button>
        </div>
      ))}
    </div>
  );
}

/** 统一的"写操作"包装：进行中禁用 + 统一错误提示。返回 running 供按钮禁用。 */
export function useAction(fn: (...a: never[]) => Promise<unknown>, deps: DependencyList = []) {
  const { toast } = usePlatform();
  const [running, setRunning] = useState(false);
  const busy = useRef(false);
  const run = useCallback(async (...a: unknown[]) => {
    if (busy.current) return;                 // 连点直接忽略，杜绝重复提交
    busy.current = true; setRunning(true);
    try { return await (fn as (...x: unknown[]) => Promise<unknown>)(...a); }
    catch (e) { toast((e as ApiError).message, "err"); /* 有意不 rethrow：调用方都是 void run()，
                       错误已经通过 toast 呈现；ConfirmDialog 依赖「抛错时不关闭」的行为 */ }
    finally { busy.current = false; setRunning(false); }
  }, deps);                       // eslint-disable-line react-hooks/exhaustive-deps
  return { run, running };
}

function ConfirmDialog({ state, close }: { state: ConfirmState; close: () => void }) {
  const { run, running } = useAction(async () => {
    await state.run();
    close();          // 只有成功才关闭；抛错时 useAction 已 toast，弹窗保持打开
  }, [state]);
  return (
    <div className="scrim" role="dialog" aria-modal="true" aria-label={state.title} onClick={e => { if (e.target === e.currentTarget) close(); }}>
      <div className="modal">
        <div className="modal-head"><h2>{state.title}</h2></div>
        <div>{state.body}</div>
        <div className="btn-row">
          <button type="button" className={state.danger ? "danger" : ""} disabled={running} onClick={() => void run()}>
            {running ? "执行中…" : state.confirmText || "确认"}
          </button>
          <button type="button" className="subtle" disabled={running} onClick={close}>取消</button>
        </div>
      </div>
    </div>
  );
}
