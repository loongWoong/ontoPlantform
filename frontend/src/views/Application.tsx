import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowsClockwise, CaretRight, Copy, Lightning, PencilSimple, Play, Plus,
  Sparkle, Trash,
} from "@phosphor-icons/react";
import {
  api, type Application, type AppQuery, type AppView, type Capability, type Obj,
  type ObjectiveEvaluation, type QueryResult, type Verification,
} from "../api";
import { toChoice, useAction, usePlatform } from "../store";
import {
  buildFilters, Btn, Card, Collapsible, Drawer, Empty, ErrorNote, Field, FilterRowEditor,
  JsonField, KIND_LABEL, labelOf, LIFECYCLE_LABEL, Loading, ObjectTable, PageHead,
  RowEditor, Select, Sparkline, TableWrap, Tag, TextInput, useLoad,
  type FilterRow, type Row,
} from "../ui";
import { fmtNum } from "../metrics";
import { StatusBadge } from "../components/StatusBadge";
import { ValueView } from "../components/ValueView";
import { executionStatus } from "../lib/status";
import { formatLocal, formatWindow } from "../lib/time";
import { valuePreview } from "../lib/value";
import { ChartView, MapView } from "./AppCharts";
import { smartUiFrameSrc, useSmartUiBridge } from "../smartui-bridge";

type Stage =
  | { kind: "none" }
  | { kind: "query"; alias: string; data: QueryResult }
  | { kind: "view"; view: AppView; objects: Obj[] }
  | {
    kind: "agent"; alias: string; answer: string; steps: number; ok: boolean;
    status?: string; verification?: Verification | null;
    trace_id?: string; run_id?: string; failed_predicates?: string[];
    /** 本次运行实际用的目标：运行时覆盖优先，否则是应用里配置的目标。 */
    goal?: string;
  };

/** 目标状态的五种说法：未设置 / 无数据 / 样本不足 / 达标 / 越界。 */
export type ObjectiveState = "unset" | "no_data" | "insufficient" | "ok" | "breach";
export const OBJECTIVE_STATE_LABEL: Record<ObjectiveState, string> = {
  unset: "未设置", no_data: "无数据", insufficient: "样本不足", ok: "达标", breach: "越界",
};

function objectiveTagKind(state: ObjectiveState): "ok" | "warn" | "err" | "plain" {
  if (state === "ok") return "ok";
  if (state === "breach") return "err";
  if (state === "insufficient" || state === "no_data") return "warn";
  return "plain";
}

/** 从应用定义 + 最近一次评估推目标状态：没有样本时绝不显示达标。 */
export function objectiveState(app: Application, ev?: ObjectiveEvaluation | null): ObjectiveState {
  if (!app.objective) return "unset";
  if (!ev) return "no_data";
  const samples = ev.samples?.length ?? 0;
  if (ev.available === false || samples === 0) return "insufficient";
  return ev.ok ? "ok" : "breach";
}

export function ApplicationView() {
  const { wid, params, go, toast, confirm, refreshRuntime } = usePlatform();
  const [selected, setSelected] = useState<string | null>(params.app || null);
  useEffect(() => { if (params.app) setSelected(params.app); }, [params.app]);
  const [stage, setStage] = useState<Stage>({ kind: "none" });
  // 发布校验的一次性多问题清单：成功后清空，失败时常驻展示（不只依赖 toast）
  const [publishProblem, setPublishProblem] = useState<string | null>(null);
  // 目标状态：只缓存「当前应用」的最近一次评估（AppDetail 本来就要取，不额外发请求）
  const [objEvals, setObjEvals] = useState<Record<string, ObjectiveEvaluation | null>>({});
  const reportObjective = useCallback((id: string, ev: ObjectiveEvaluation | null) => {
    setObjEvals(prev => (prev[id] === ev ? prev : { ...prev, [id]: ev }));
  }, []);

  const load = useLoad(async () => {
    if (!wid) return { apps: [] as Application[], caps: [] as Capability[] };
    const [list, caps] = await Promise.all([
      api.applications(wid).catch(() => [] as Application[]),
      api.capabilities(wid).catch(() => [] as Capability[]),
    ]);
    const apps = await Promise.all(list.map(a => api.application(wid, a.id).catch(() => a)));
    return { apps, caps };
  }, [wid]);

  const apps = load.data?.apps || [];
  const caps = load.data?.caps || [];
  const current = apps.find(a => a.id === selected) || apps[0] || null;
  // 换应用后旧的发布问题清单不再相关
  useEffect(() => { setPublishProblem(null); }, [current?.id]);

  const { run: deleteCurrent, running: deletingCurrent } = useAction(async () => {
    if (!wid || !current) return;
    await api.deleteApplication(wid, current.id);
    toast(`应用「${current.name}」已删除；对象实例、能力与审计记录都保留不变`, "ok");
    const next = apps.find(a => a.id !== current.id) || null;
    setSelected(next?.id || null);
    setStage({ kind: "none" });
    go("application", next ? { app: next.id } : undefined);
    load.reload();
    void refreshRuntime();
  }, [wid, current]);

  if (load.loading && !load.data) {
    return (
      <>
        <PageHead kicker="Applications" title="应用" desc="读取应用列表…" />
        <Loading />
      </>
    );
  }

  return (
    <>
      <PageHead
        kicker="Applications"
        title="应用"
        desc="应用 = 视图 + 查询 + 智能体 + 已挂载能力，是业务人员真正打开的那个东西。"
        meta={<>
          <span>应用 <b>{apps.length}</b></span>
          <span>已发布 <b>{apps.filter(a => a.lifecycle === "published").length}</b></span>
          {current && <span>当前 <b>{current.name}</b></span>}
        </>}
        actions={<>
          {current && current.lifecycle !== "published" && (
            <>
              <span className="muted small">发布前会校验视图 / 查询 / 能力引用</span>
              <PublishApp app={current} onChanged={load.reload} onProblem={setPublishProblem} />
            </>
          )}
          {current && (
            <Btn variant="subtle" disabled={deletingCurrent}
                 aria-label={`删除应用 ${current.name}`}
                 onClick={() => confirm({
                   title: `删除应用「${current.name}」？`,
                   danger: true,
                   confirmText: "确认删除",
                   body: (
                     <p className="muted">
                       应用及其视图、查询、智能体和挂载关系会被删除；业务对象实例、能力与审计记录都保留。这个操作不可撤销。
                     </p>
                   ),
                   run: async () => { await deleteCurrent(); },
                 })}>
              <Trash size={14} /> 删除应用
            </Btn>
          )}
          <Btn variant="subtle" onClick={load.reload} busy={load.loading}>刷新</Btn>
        </>}
      />

      {publishProblem && (
        <div className="banner err banner-row">
          <div>
            <b>发布没通过：下面这些问题要一次改完</b>
            <ul className="doc-list">
              {splitPublishProblems(publishProblem).map((problem, i) => <li key={i}>{problem}</li>)}
            </ul>
            <p className="muted small">
              发布前的校验覆盖视图、查询、挂载能力与智能体；没过校验前应用不会被发布，运行时也不会变。
            </p>
          </div>
          <Btn size="sm" variant="subtle" onClick={() => setPublishProblem(null)}>知道了</Btn>
        </div>
      )}

      <div className="layout app-layout">
        <Card title="应用清单" tight sub="选中一个来配置与运行。">
          {!apps.length && (
            <div className="aside-body">
              <Empty title="还没有应用" hint="建一个，然后把视图和查询挂上去，业务人员就有入口了。" />
            </div>
          )}
          {!!apps.length && (
            <div className="app-rail" role="tablist" aria-label="应用列表">
              {apps.map(a => (
                <button key={a.id} type="button" role="tab" aria-selected={current?.id === a.id}
                        className="app-item" onClick={() => {
                          setSelected(a.id); setStage({ kind: "none" });
                          go("application", { app: a.id });
                        }}>
                  <span className="app-item-main">
                    <span className="app-item-name">{a.name}</span>
                    <span className="app-item-meta">
                      {Object.keys(a.views || {}).length} 视图 · {Object.keys(a.queries || {}).length} 查询 · {(a.agents || []).length} 智能体
                    </span>
                  </span>
                  <span className="app-item-tags">
                    <span title="目标状态">
                      <Tag kind={objectiveTagKind(objectiveState(a, objEvals[a.id]))}>
                        {OBJECTIVE_STATE_LABEL[objectiveState(a, objEvals[a.id])]}
                      </Tag>
                    </span>
                    <Tag kind={a.lifecycle === "published" ? "ok" : "warn"}>{LIFECYCLE_LABEL[a.lifecycle]}</Tag>
                  </span>
                </button>
              ))}
            </div>
          )}
          <div className="aside-body rule-top">
            <CreateApp onDone={load.reload} />
          </div>
        </Card>

        {current ? (
          <AppDetail key={current.id} app={current} caps={caps} stage={stage} setStage={setStage}
                     onChanged={load.reload} onOpenObject={ref => go("state", { ref })}
                     onObjective={reportObjective}
                     onCloned={id => { setSelected(id); go("application", { app: id }); load.reload(); }} />
        ) : (
          <Card title="应用做什么" sub="把「谁看什么数据、能做什么动作」固定下来，业务人员点一下就有结果。">
            <Empty title="选一个应用" hint="或者在左侧新建一个应用。"
                   action={<Btn size="sm" variant="subtle" onClick={() => go("capability")}>先看看有哪些能力</Btn>} />
          </Card>
        )}
      </div>
    </>
  );
}

function PublishApp({ app, onChanged, onProblem }: {
  app: Application; onChanged: () => void; onProblem: (message: string | null) => void;
}) {
  const { wid, toast, confirm, refreshRuntime } = usePlatform();
  const { run, running } = useAction(async () => {
    if (!wid) return;
    onProblem(null);
    try {
      await api.publishApplication(wid, app.id);
    } catch (e) {
      // 发布校验会一次性返回全部问题：错误原文交给调用方常驻展示，别只留一个 toast
      onProblem((e as Error).message);
      throw e;
    }
    toast(`应用「${app.name}」已发布`, "ok");
    onChanged();
    void refreshRuntime();
  }, [wid, app.id, refreshRuntime, app.name, onProblem]);
  return (
    <Btn size="sm" busy={running} onClick={() => confirm({
      title: `发布应用「${app.name}」？`,
      confirmText: "发布",
      body: (
        <>
          <p className="muted">发布后应用及其视图、查询、智能体对外可用。</p>
          <p className="muted">
            发布前会校验视图 / 查询 / 挂载能力与智能体：引用的对象类型、属性、关系要仍在已发布本体里，
            挂载的能力要已发布。有问题会一次列全，改完再发布。
          </p>
        </>
      ),
      run: async () => { await run(); },
    })}>发布应用</Btn>
  );
}

/** 把发布校验的中文清单拆成条目：friendly() 已逐条翻译并用「；」连接。
 *  认不出的说法原样保留，绝不吞掉后端原文。 */
function splitPublishProblems(message: string): string[] {
  const body = message.includes("：") ? message.slice(message.indexOf("：") + 1) : message;
  const parts = body.split("；").map(s => s.trim()).filter(Boolean);
  return parts.length ? parts : [message];
}

function CreateApp({ onDone }: { onDone: () => void }) {
  const { wid, toast, refreshRuntime } = usePlatform();
  const [name, setName] = useState("");
  const { run, running } = useAction(async () => {
    if (!wid) return;
    if (!name.trim()) { toast("请填写应用名称", "err"); return; }
    await api.createApplication(wid, name.trim(), "");
    toast(`应用「${name.trim()}」已创建`, "ok");
    setName("");
    void refreshRuntime();
    onDone();
  }, [wid, name, refreshRuntime]);
  return (
    <form className="inline-create stack" onSubmit={e => { e.preventDefault(); void run(); }}>
      <Field label="新应用">
        <TextInput value={name} placeholder="如 运营工作台" aria-label="应用名称" onChange={e => setName(e.target.value)} />
      </Field>
      <Btn type="submit" size="sm" busy={running}><Plus size={13} /> 新建应用</Btn>
    </form>
  );
}

type AppTab = "views" | "queries" | "agents" | "caps" | "objective" | "ai";
const APP_TABS: readonly AppTab[] = ["views", "queries", "agents", "caps", "objective", "ai"];

function AppDetail({ app, caps, stage, setStage, onChanged, onOpenObject, onObjective, onCloned }: {
  app: Application; caps: Capability[]; stage: Stage;
  setStage: (s: Stage) => void; onChanged: () => void; onOpenObject: (ref: string) => void;
  onObjective: (id: string, ev: ObjectiveEvaluation | null) => void;
  onCloned: (id: string) => void;
}) {
  const { wid, params, go, toast, confirm } = usePlatform();
  const urlTab = APP_TABS.find(t => t === params.tab);
  const [tab, setTab] = useState<AppTab>(urlTab || "views");
  useEffect(() => { if (urlTab) setTab(urlTab); }, [urlTab]);
  const pick = (k: AppTab) => {
    setTab(k);
    go("application", { app: app.id, tab: k });   // 组成页签进 URL，刷新/分享后仍停在原处
  };
  const views = Object.values(app.views || {});
  const queries = Object.values(app.queries || {});
  const agents = app.agents || [];
  const attached = (app.capabilities || []).map(id => caps.find(c => c.id === id)).filter(Boolean) as Capability[];
  const published = app.lifecycle === "published";
  const [infoOpen, setInfoOpen] = useState(false);
  const [viewEditor, setViewEditor] = useState<{ mode: "create" } | { mode: "edit"; view: AppView } | null>(null);
  const [queryEditor, setQueryEditor] = useState<AppQuery | null>(null);
  // AI视图：应用发布时附加的生成快照。iframe 按 generation id 取数；
  // 版本 / program hash / 发布时间直接取应用里的 ui_artifact。
  const artifact = app.ui_artifact;
  const [aiReloadKey, setAiReloadKey] = useState(0);
  const aiFrameRef = useRef<HTMLIFrameElement>(null);
  const postAiAuthToken = useSmartUiBridge(aiFrameRef, wid, aiReloadKey);

  // 目标评估：应用详情本来就要显示它，列表行复用同一份结果，不额外发请求。
  const objLoad = useLoad(async () => {
    if (!wid || !app.objective) return null;
    return api.objective(wid, app.id).catch(() => null);
  }, [wid, app.id, app.objective]);
  const objEv = objLoad.data ?? null;
  useEffect(() => { onObjective(app.id, objEv); }, [app.id, objEv, onObjective]);

  const { run: cloneApp, running: cloningApp } = useAction(async () => {
    if (!wid) return;
    const created = await api.cloneApplication(wid, app.id, {});
    toast(`已克隆为草稿「${created.name}」：新应用是草稿，发布后旧应用保持原样`, "ok");
    onCloned(created.id);
  }, [wid, app.id, toast, onCloned]);

  type PartKind = "view" | "query" | "agent" | "cap";
  const { run: removePart, running: removingPart } = useAction(
    async (kind: PartKind, value: string, label: string) => {
      if (!wid) return;
      if (kind === "view") await api.removeView(wid, app.id, value);
      else if (kind === "query") await api.removeQuery(wid, app.id, value);
      else if (kind === "agent") await api.removeAgent(wid, app.id, value);
      else await api.detachCapability(wid, app.id, value);
      toast(`已从应用「${app.name}」移除${label}`, "ok");
      onChanged();
    }, [wid, app.id, app.name, onChanged]);

  const askRemovePart = (kind: PartKind, value: string, label: string,
                         warning: string) => confirm({
    title: `移除${label}？`,
    danger: true,
    confirmText: "确认移除",
    body: <p className="muted">{warning}</p>,
    run: async () => { await removePart(kind, value, label); },
  });

  return (
    <Card title={app.name}
          sub="上面改定义，下面看结果；增删视图或查询不会清掉上一次跑出来的数据。"
          aside={<>
            <span className="app-tags">
              <span title="目标状态">
                <Tag kind={objectiveTagKind(objectiveState(app, objEv))}>
                  {OBJECTIVE_STATE_LABEL[objectiveState(app, objEv)]}
                </Tag>
              </span>
              <Tag kind={published ? "ok" : "warn"}>{LIFECYCLE_LABEL[app.lifecycle]}</Tag>
              <Tag kind="plain">{views.length} 视图</Tag>
            </span>
            <Btn size="sm" variant="subtle" aria-label={`编辑应用信息 ${app.name}`}
                 onClick={() => setInfoOpen(true)}>
              <PencilSimple size={12} /> 编辑信息
            </Btn>
            {published && (
              <Btn size="sm" variant="subtle" busy={cloningApp}
                   aria-label={`克隆应用 ${app.name} 为草稿`}
                   onClick={() => confirm({
                     title: `克隆应用「${app.name}」为草稿？`,
                     confirmText: "克隆为草稿",
                     body: (
                       <p className="muted">
                         复制一份应用定义（视图 / 查询 / 智能体 / 挂载能力 / 目标）成新草稿。
                         旧应用保持发布状态继续服务，改动只影响新草稿；发布新应用后再决定是否替换旧的。
                       </p>
                     ),
                     run: async () => { await cloneApp(); },
                   })}>
                <Copy size={12} /> 克隆为草稿
              </Btn>
            )}
          </>}>
      {published && (
        <div className="banner warn">
          已发布应用不可就地改，先克隆为草稿：视图、查询、智能体与挂载能力只能查看和运行；
          要改定义就在头部点「克隆为草稿」，改新草稿并发布后替换旧的。
        </div>
      )}

      <div className="subtabs" role="tablist" tabIndex={0} aria-label="应用组成">
        {([["views", `视图 ${views.length}`], ["queries", `查询 ${queries.length}`],
           ["agents", `智能体 ${agents.length}`], ["caps", `挂载能力 ${attached.length}`],
           ["objective", "目标"], ["ai", "AI视图"]] as [AppTab, string][])
          .map(([k, l]) => (
            <button key={k} type="button" role="tab" aria-selected={tab === k} onClick={() => pick(k)}>{l}</button>
          ))}
      </div>

      {tab === "objective" && (
        <ObjectivePanel app={app} ev={objEv} reload={objLoad.reload} onChanged={onChanged} />
      )}


      {tab === "views" && (
        <>
          {!views.length && <Empty title="还没有视图" hint="视图就是「把某类对象摆成表格、卡片、图表或地图给人看」。" />}
          {!!views.length && (
            <TableWrap auto>
              <table>
                <thead><tr><th>视图</th><th>形式</th><th className="wrap">数据来源</th><th>操作</th></tr></thead>
                <tbody>
                  {views.map(v => (
                    <tr key={v.alias}>
                      <td className="nowrap"><b>{v.title || v.alias}</b></td>
                      <td className="nowrap"><Tag kind="plain">{labelOf(KIND_LABEL, v.kind)}</Tag></td>
                      <td className="wrap">
                        {v.query_alias ? `查询 ${v.query_alias}` : (v.object_type || "–")}
                        {(v.kind === "chart" || v.kind === "map") && (
                          <span className="muted small">
                            {" · "}
                            {v.kind === "chart"
                              ? `x=${v.config?.x || "未配"} / y=${v.config?.y || "未配"}`
                              : `lat=${v.config?.lat || "未配"} / lng=${v.config?.lng || "未配"}`}
                          </span>
                        )}
                      </td>
                      <td className="nowrap">
                        <div className="pill-row">
                          <OpenView app={app} view={v} onResult={setStage} />
                          {!published && (
                            <Btn size="sm" variant="subtle"
                                 aria-label={`编辑视图 ${v.alias}`}
                                 onClick={() => setViewEditor({ mode: "edit", view: v })}>
                              <PencilSimple size={12} /> 编辑
                            </Btn>
                          )}
                          <Btn size="sm" variant="subtle" disabled={removingPart}
                               aria-label={`移除视图 ${v.alias}`}
                               onClick={() => askRemovePart(
                                 "view", v.alias, `视图「${v.title || v.alias}」`,
                                 "只删除这个视图定义，不会删除查询、对象实例或上一次跑出来的数据。")}>
                            <Trash size={12} /> 移除
                          </Btn>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
          )}
          {!published && (
            <div className="inline-create">
              <Btn size="sm" variant="subtle" onClick={() => setViewEditor({ mode: "create" })}>
                <Plus size={13} /> 新建视图（表格 / 卡片 / 图表 / 地图）
              </Btn>
              <span className="muted small">新建与编辑共用同一个表单；图表 / 地图要选渲染配置。</span>
            </div>
          )}
        </>
      )}

      {tab === "queries" && (
        <>
          {!queries.length && <Empty title="还没有查询" hint="查询 = 固定好的过滤条件，业务人员点一下就出结果。" />}
          {!!queries.length && (
            <TableWrap auto>
              <table>
                <thead><tr><th>查询</th><th>对象</th><th className="wrap">条件</th><th>操作</th></tr></thead>
                <tbody>
                  {queries.map(q => (
                    <tr key={q.alias}>
                      <td className="nowrap"><b>{q.title || q.alias}</b></td>
                      <td className="nowrap">{q.object_type}</td>
                      <td className="wrap" title={JSON.stringify(q.filters)}>
                        {Object.keys(q.filters || {}).length ? JSON.stringify(q.filters) : "全部"}
                        {(q.expand || []).length > 0 && (
                          <span className="muted"> · 顺带展开 {q.expand.map(e => e.relation).join("、")}</span>
                        )}
                      </td>
                      <td className="nowrap">
                        <div className="pill-row">
                          <RunQuery app={app} alias={q.alias} onResult={setStage} />
                          {!published && (
                            <Btn size="sm" variant="subtle"
                                 aria-label={`编辑查询 ${q.alias}`}
                                 onClick={() => setQueryEditor(q)}>
                              <PencilSimple size={12} /> 编辑
                            </Btn>
                          )}
                          <Btn size="sm" variant="subtle" disabled={removingPart}
                               aria-label={`移除查询 ${q.alias}`}
                               onClick={() => askRemovePart(
                                 "query", q.alias, `查询「${q.title || q.alias}」`,
                                 "视图仍引用这个查询时不能删除；先移除对应视图，再删除查询。")}>
                            <Trash size={12} /> 移除
                          </Btn>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
          )}
          {!published && <AddQuery app={app} onDone={onChanged} />}
        </>
      )}

      {tab === "agents" && (
        <>
          {!agents.length && <Empty title="还没有智能体" hint="给应用配一个懂这个业务世界的助手。" />}
          {!!agents.length && (
            <TableWrap auto>
              <table>
                <thead><tr><th>智能体</th><th className="wrap">目标</th><th>操作</th></tr></thead>
                <tbody>
                  {agents.map(g => (
                    <tr key={g.alias}>
                      <td className="nowrap"><b>{g.alias}</b></td>
                      <td className="wrap" title={g.goal}>{g.goal}</td>
                      <td className="nowrap">
                        <div className="pill-row">
                          <RunAgent app={app} alias={g.alias} onResult={setStage} />
                          <Btn size="sm" variant="subtle" disabled={removingPart}
                               aria-label={`移除智能体 ${g.alias}`}
                               onClick={() => askRemovePart(
                                 "agent", g.alias, `智能体「${g.alias}」`,
                                 "只从应用里移除这个智能体配置；历史运行记录与能力本身不受影响。")}>
                            <Trash size={12} /> 移除
                          </Btn>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
          )}
          <AddAgent app={app} onDone={onChanged} />
        </>
      )}

      {tab === "caps" && (
        <>
          <div className="chips">
            {!attached.length && <span className="muted">还没有挂载能力。</span>}
            {attached.map(c => (
              <span key={c.id} className="chip">
                <Lightning size={11} /> {c.name} · {c.target_type}.{c.key}
                <button type="button" className="chip-x" disabled={removingPart}
                        aria-label={`从应用移除能力 ${c.name}`} title="取消挂载"
                        onClick={() => askRemovePart(
                          "cap", c.id, `能力「${c.name}」`,
                          "只是取消这个应用与能力的挂载关系；能力本身和其他应用不受影响。已发布的应用需要重新发布后才会对外生效。")}>×</button>
              </span>
            ))}
          </div>
          <AttachCapability app={app} caps={caps} onDone={onChanged} />
        </>
      )}

      {tab === "ai" && (
        artifact && wid ? (
          <>
            <div className="app-ai-meta">
              <span><b>版本</b><span className="mono">v{artifact.version}</span></span>
              <span>
                <b>Program</b>
                <span className="mono" title={artifact.program_hash || ""}>
                  {(artifact.program_hash || "—").replace("sha256:", "").slice(0, 12)}
                </span>
              </span>
              <span><b>发布时间</b><span>{artifact.published_at ? formatLocal(artifact.published_at) : "—"}</span></span>
              <span><b>发布人</b><span>{artifact.published_by || "—"}</span></span>
              <div className="btn-row">
                <Btn size="sm" variant="subtle" onClick={() => setAiReloadKey(k => k + 1)}>
                  <ArrowsClockwise size={13} /> 刷新
                </Btn>
                <Btn size="sm" variant="subtle"
                     onClick={() => go("aiview", { generation: artifact.generation_id })}>
                  <Sparkle size={13} /> 打开生成台
                </Btn>
              </div>
            </div>
            <iframe
              key={`${artifact.generation_id}-${aiReloadKey}`}
              ref={aiFrameRef}
              className="smartui-frame"
              title={`AI视图 ${app.name}`}
              src={`${smartUiFrameSrc(wid, artifact.generation_id)}&r=${aiReloadKey}`}
              onLoad={postAiAuthToken}
              sandbox="allow-scripts allow-forms allow-popups"
            />
          </>
        ) : (
          <>
            <Empty
              title="还没有 AI 生成视图"
              hint="这个应用还没有附加 AI 生成快照。到「AI视图」用业务语言生成并发布时选择这个应用（只有草稿应用可作为目标），发布后这里会挂载隔离 iframe，并显示版本、program hash 与发布时间。"
            />
            <div className="btn-row">
              <Btn size="sm" onClick={() => go("aiview", {})}>
                <Sparkle size={13} /> 去 AI视图 生成并发布
              </Btn>
            </div>
          </>
        )
      )}

      <Stage area={stage} onOpenObject={onOpenObject} onClear={() => setStage({ kind: "none" })} />

      {infoOpen && (
        <AppInfoDrawer app={app} onClose={() => setInfoOpen(false)}
                       onSaved={() => { setInfoOpen(false); onChanged(); }} />
      )}
      {viewEditor && (
        <ViewDrawer app={app} queries={queries}
                    mode={viewEditor.mode}
                    view={viewEditor.mode === "edit" ? viewEditor.view : null}
                    onClose={() => setViewEditor(null)}
                    onSaved={() => { setViewEditor(null); onChanged(); }} />
      )}
      {queryEditor && (
        <QueryDrawer app={app} query={queryEditor}
                     onClose={() => setQueryEditor(null)}
                     onSaved={() => { setQueryEditor(null); onChanged(); }} />
      )}
    </Card>
  );
}

/* ---------- 业务目标：当前值 / 偏差 / 趋势 / 越界待办 ---------- */
const OBJECTIVE_METRICS: [string, string][] = [
  ["success_rate", "成功率"], ["unknown_rate", "未知率"],
  ["pending_events", "待派发事件"], ["projection_age_seconds", "投影新鲜度（秒）"],
];
const TREND_LABEL: Record<string, string> = { up: "上升", down: "下降", flat: "持平" };

function ObjectivePanel({ app, ev, reload, onChanged }: {
  app: Application; ev: ObjectiveEvaluation | null; reload: () => void; onChanged: () => void;
}) {
  const { wid, toast } = usePlatform();
  const [metric, setMetric] = useState(app.objective?.metric || "success_rate");
  const [target, setTarget] = useState(String(app.objective?.target ?? 0.99));
  const [band, setBand] = useState(String(app.objective?.band ?? 0.02));
  const [win, setWin] = useState(String(app.objective?.window ?? 3600));

  const { run, running } = useAction(async () => {
    if (!wid) return;
    const t = Number(target);
    if (!Number.isFinite(t)) { toast("目标值要填数字", "err"); return; }
    await api.setObjective(wid, app.id, {
      metric, target: t,
      band: Number(band) || 0,
      window: Number(win) || 3600,
    });
    toast("业务目标已保存；评估需要单独触发", "ok");
    reload();
    onChanged();
  }, [wid, app.id, metric, target, band, win, reload, onChanged]);

  const evaluate = useAction(async () => {
    if (!wid) return;
    const res = await api.evaluateObjective(wid, app.id);
    const noData = res.available === false || (res.samples || []).length === 0;
    toast(
      noData ? "评估完成：窗口内没有样本，不能判定达标"
        : res.ok ? "评估完成：当前在目标带内" : "评估完成：当前越界，已生成待办",
      noData ? "warn" : res.ok ? "ok" : "warn",
    );
    reload();
    onChanged();
  }, [wid, app.id, reload, onChanged]);

  const { run: resolveTodo, running: resolving } = useAction(async () => {
    if (!wid || !ev?.todo) return;
    await api.resolveTodo(wid, ev.todo.id);
    toast("待办已关闭；这只记录处理动作，不代表指标已经恢复", "warn");
    reload();
  }, [wid, ev?.todo?.id, reload]);

  const objective = ev?.objective || app.objective;
  const sampleCount = ev?.samples?.length ?? 0;
  const available = !!ev && ev.available !== false && sampleCount > 0;
  const state = objectiveState(app, ev);
  const scopeLabel = ev?.scope === "workspace" ? "工作空间"
    : ev?.scope === "application" ? "当前应用" : "接口未返回";
  const spark = useMemo(() => (ev?.samples || []).map(s => s.value), [ev]);

  return (
    <section className="objective-panel">
      <div className="result-head">
        <h3>业务目标</h3>
        {state === "unset"
          ? <Tag kind="plain">未设置</Tag>
          : <StatusBadge status={state} hint={`目标状态：${OBJECTIVE_STATE_LABEL[state]}`} />}
        {ev?.generated_at && <span className="muted small">评估于 {formatLocal(ev.generated_at)}</span>}
        <span className="grow" />
        {!!spark.length && <Sparkline points={spark} />}
      </div>
      {ev && objective ? (
        <>
          <div className="obj-stats">
            <div className="obj-stat">
              <span className="stat-label">当前值</span>
              <span className="stat-value">{available ? fmtNum(ev.current) : "–"}</span>
              <span className="stat-foot">{OBJECTIVE_METRICS.find(([k]) => k === objective.metric)?.[1] || objective.metric}</span>
            </div>
            <div className="obj-stat">
              <span className="stat-label">目标 / 容差</span>
              <span className="stat-value">{fmtNum(objective.target)}<small>±{fmtNum(objective.band)}</small></span>
              <span className="stat-foot">窗口 {formatWindow(ev.window_seconds || objective.window)}</span>
            </div>
            <div className="obj-stat">
              <span className="stat-label">偏差</span>
              <span className={"stat-value" + ((ev.deviation ?? 0) > 0 ? " warn" : "")}>
                {available && ev.deviation !== null ? `${ev.deviation > 0 ? "+" : ""}${fmtNum(ev.deviation)}` : "–"}
              </span>
              <span className="stat-foot">趋势{TREND_LABEL[ev.trend] || ev.trend}（{ev.trend_delta === null ? "–" : `${ev.trend_delta > 0 ? "+" : ""}${fmtNum(ev.trend_delta)}`}）</span>
            </div>
            <div className="obj-stat">
              <span className="stat-label">样本</span>
              <span className="stat-value">{sampleCount}</span>
              <span className="stat-foot">范围 {scopeLabel} · 最少样本 {ev.min_samples ?? "未返回"}</span>
            </div>
          </div>
          {!available && (
            <p className="muted small">
              {ev.reason || "还没有可用样本：先让能力执行或同步数据产生观测，再评估目标。此时不显示达标。"}
            </p>
          )}
          {ev.todo && (
            <div className="banner warn banner-row">
              <div>
                <b>越界待办：{ev.todo.title}</b>
                <p className="muted small">
                  当前 {fmtNum(ev.todo.current)} / 目标 {fmtNum(ev.todo.target)} · 偏差 {fmtNum(ev.todo.deviation)} · 生成于 {formatLocal(ev.todo.created_at)}
                </p>
              </div>
              <Btn size="sm" busy={resolving} onClick={() => void resolveTodo()}>关闭待办</Btn>
            </div>
          )}
          <p className="muted small">
            目标评估是显式动作；GET 只读取最近一次评估。评估时间 {ev.last_evaluation ? formatLocal(ev.last_evaluation) : (ev.generated_at ? formatLocal(ev.generated_at) : "–")}。
          </p>
        </>
      ) : (
        <p className="muted">还没有设定业务目标。设好后这里计算偏差与趋势；评估需要点「立即评估」，越界会生成处理待办。</p>
      )}
      <div className="inline-create wrap">
        <Select value={metric} aria-label="目标指标" onChange={e => setMetric(e.target.value)}>
          {OBJECTIVE_METRICS.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
        </Select>
        <TextInput className="w-xs" type="number" step="0.01" value={target}
                   aria-label="目标值" placeholder="目标值" onChange={e => setTarget(e.target.value)} />
        <TextInput className="w-xs" type="number" step="0.01" value={band}
                   aria-label="容差" placeholder="容差" onChange={e => setBand(e.target.value)} />
        <TextInput className="w-xs" type="number" step="600" value={win}
                   aria-label="评估窗口（秒）" placeholder="窗口秒" onChange={e => setWin(e.target.value)} />
        <Btn size="sm" busy={running} onClick={() => void run()}>保存目标</Btn>
        <Btn size="sm" variant="subtle" busy={evaluate.running} onClick={() => void evaluate.run()}>立即评估</Btn>
      </div>
    </section>
  );
}

/* ---------- 结果区：与定义解耦，永不被定义变更清空 ---------- */
function Stage({ area, onOpenObject, onClear }: { area: Stage; onOpenObject: (r: string) => void; onClear: () => void }) {
  if (area.kind === "none") return null;
  return (
    <section className="stage-result">
      <div className="result-head">
        <h3>
          {area.kind === "query" && `查询「${area.alias}」 · ${area.data.objects.length} 个对象`}
          {area.kind === "view" && `视图「${area.view.title || area.view.alias}」 · ${area.objects.length} 个对象`}
          {area.kind === "agent" && `智能体「${area.alias}」`}
        </h3>
        <span className="grow" />
        <Btn size="sm" variant="subtle" onClick={onClear}>清空结果</Btn>
      </div>

      {area.kind === "query" && (
        <>
          <ObjectTable objects={area.data.objects} onOpen={onOpenObject} />
          {(area.data.expand || []).map(e => (
            <div key={e.relation}>
              <h3 className="h4">顺带展开「{e.relation}」 · {e.objects.length} 个</h3>
              <ObjectTable objects={e.objects} onOpen={onOpenObject} />
            </div>
          ))}
        </>
      )}

      {area.kind === "view" && (
        <>
          {area.view.kind === "card"
            ? <CardGrid objects={area.objects} view={area.view} />
            : area.view.kind === "chart"
              ? <ChartView objects={area.objects} view={area.view} />
              : area.view.kind === "map"
                ? <MapView objects={area.objects} view={area.view} />
                : <ObjectTable objects={area.objects} onOpen={onOpenObject} />}
          {(area.view.kind === "chart" || area.view.kind === "map") && (
            <Collapsible label={`明细数据（${area.objects.length} 个对象）`} defaultOpen={false}>
              <ObjectTable objects={area.objects} onOpen={onOpenObject} />
            </Collapsible>
          )}
        </>
      )}

      {area.kind === "agent" && (
        <>
          {area.goal && (
            <div className="result-head">
              <span className="muted small">本次目标：{area.goal}</span>
              <span className="grow" />
              <StatusBadge status={executionStatus({ status: area.status, ok: area.ok })} />
            </div>
          )}
          <div className="result-head">
            <span className="muted small">{area.steps} 步 · {area.trace_id ? `轨迹 ${area.trace_id}` : "后端未返回轨迹编号"}</span>
            {!area.goal && <StatusBadge status={executionStatus({ status: area.status, ok: area.ok })} />}
            {area.run_id && <span className="muted mono small">运行 {area.run_id}</span>}
          </div>
          <div className="answer final">{area.answer || "（没有返回结论）"}</div>
          {!!area.failed_predicates?.length && (
            <div className="banner warn">
              未满足的完成条件：{area.failed_predicates.join("；")}
            </div>
          )}
          {area.verification && (
            <>
              <h3 className="h4">完成校验</h3>
              <ValueView value={area.verification} />
            </>
          )}
        </>
      )}
    </section>
  );
}

function CardGrid({ objects, view }: { objects: Obj[]; view: AppView }) {
  if (!objects.length) return <Empty title="没有对象" />;
  const cols = view.properties?.length
    ? view.properties
    : Array.from(new Set(objects.flatMap(o => Object.keys(o.properties || {})))).slice(0, 4);
  return (
    <div className="card-grid">
      {objects.map(o => (
        <div className="card" data-tight="true" key={`${o.type}/${o.id}`}>
          <div className="card-title">{valuePreview(o.properties?.name ?? `${o.type}/${o.id}`)}</div>
          <dl className="kv">
            {cols.map(c => (
              <div key={c} className="kv-pair"><dt>{c}</dt><dd><ValueView value={o.properties[c]} compact field={c} /></dd></div>
            ))}
          </dl>
        </div>
      ))}
    </div>
  );
}

/* ---------- 单个动作 ---------- */
function OpenView({ app, view, onResult }: { app: Application; view: AppView; onResult: (s: Stage) => void }) {
  const { wid, toast } = usePlatform();
  const { run, running } = useAction(async () => {
    if (!wid) return;
    if (view.query_alias) {
      const r = await api.runQuery(wid, app.id, view.query_alias);
      onResult({ kind: "view", view, objects: r.objects });
      toast(`视图已加载 ${r.objects.length} 个对象`, "ok");
      return;
    }
    if (!view.object_type) { toast("这个视图没有绑定数据来源", "err"); return; }
    const objects = await api.find(wid, view.object_type, {});
    onResult({ kind: "view", view, objects });
    toast(`视图已加载 ${objects.length} 个对象`, "ok");
  }, [wid, app.id, view]);
  return <Btn size="sm" variant="tint" busy={running} onClick={() => void run()}>打开</Btn>;
}

function RunQuery({ app, alias, onResult }: { app: Application; alias: string; onResult: (s: Stage) => void }) {
  const { wid, toast } = usePlatform();
  const { run, running } = useAction(async () => {
    if (!wid) return;
    const r = await api.runQuery(wid, app.id, alias);
    onResult({ kind: "query", alias, data: r });
    toast(`查到 ${r.objects.length} 个对象`, "ok");
  }, [wid, app.id, alias]);
  return <Btn size="sm" variant="tint" busy={running} onClick={() => void run()}>运行</Btn>;
}

function RunAgent({ app, alias, onResult }: { app: Application; alias: string; onResult: (s: Stage) => void }) {
  const { wid, toast, llm, go, objectTypes } = usePlatform();
  const [open, setOpen] = useState(false);
  const [goal, setGoal] = useState("");
  const [steps, setSteps] = useState("8");
  const { run, running } = useAction(async () => {
    if (!wid) return;
    try {
      const r = await api.runAppAgent(wid, app.id, alias, {
        goal: goal.trim() || undefined,
        max_steps: Number(steps) || 8,
        ...(llm.mode === "demo" ? { llm: toChoice(llm, objectTypes[0]) } : {}),
      });
      const st = executionStatus(r);
      const effectiveGoal = goal.trim() || (app.agents || []).find(g => g.alias === alias)?.goal || "";
      onResult({
        kind: "agent", alias, answer: r.answer, steps: (r.steps || []).length, ok: r.ok,
        status: r.status, verification: r.verification, trace_id: r.trace_id,
        run_id: r.run_id, failed_predicates: r.failed_predicates, goal: effectiveGoal,
      });
      setOpen(false);
      if (st === "succeeded") toast("智能体已完成，并通过完成校验", "ok");
      else if (st === "failed") toast("智能体未完成：任务状态是 failed，结论只作参考", "err");
      else toast(`智能体结果未知或未完成（${st}）：不要按已完成对外承诺`, "warn");
    } catch (e) {
      toast((e as Error).message, "err");
      if (/模型|LLM/.test((e as Error).message)) go("settings", { tab: "llm" });
    }
  }, [wid, app.id, alias, goal, steps, llm, objectTypes]);

  return (
    <>
      <Btn size="sm" variant="tint" onClick={() => setOpen(true)}><Play size={12} weight="fill" /> 运行</Btn>
      {open && (
        <Drawer title={`运行智能体 · ${alias}`} onClose={() => setOpen(false)}
                footer={<>
                  <Btn busy={running} onClick={() => void run()}>开始</Btn>
                  <Btn variant="subtle" onClick={() => setOpen(false)}>取消</Btn>
                </>}>
          <Field label="目标（可覆盖）" hint="留空用应用里配置的目标。">
            <TextInput value={goal} placeholder="例如：找出余额不足的车辆并给出处置建议" onChange={e => setGoal(e.target.value)} />
          </Field>
          <Field label="最多步数" hint="步数越多越聪明，也越慢。">
            <TextInput className="w-xs" type="number" min={1} max={24} value={steps} onChange={e => setSteps(e.target.value)} />
          </Field>
          <p className="muted small">模型来源在「设置 · 模型服务」里配置；没有真实模型时选「演示模式」先看流程。</p>
        </Drawer>
      )}
    </>
  );
}

/* ---------- 应用信息 / 视图 / 查询的抽屉表单 ---------- */

/** 编辑应用信息：名称 + 说明。已发布应用保存会被后端拒绝（提示先克隆）。 */
function AppInfoDrawer({ app, onClose, onSaved }: {
  app: Application; onClose: () => void; onSaved: () => void;
}) {
  const { wid, toast } = usePlatform();
  const [name, setName] = useState(app.name);
  const [description, setDescription] = useState(app.description || "");
  const [error, setError] = useState<string | null>(null);
  const published = app.lifecycle === "published";
  const { run, running } = useAction(async () => {
    if (!wid) return;
    setError(null);
    if (!name.trim()) { setError("应用名称不能为空"); return; }
    try {
      await api.updateApplication(wid, app.id, { name: name.trim(), description: description.trim() });
      toast(`应用信息已更新：${name.trim()}`, "ok");
      onSaved();
    } catch (e) {
      setError((e as Error).message);
      throw e;
    }
  }, [wid, app.id, name, description]);

  return (
    <Drawer title={`编辑信息 · ${app.name}`} sub="只改应用名称与说明；视图、查询、智能体与已发布版本不变。"
            onClose={onClose}
            footer={<div className="btn-row flat">
              <Btn busy={running} onClick={() => void run()}>保存信息</Btn>
              <Btn variant="subtle" disabled={running} onClick={onClose}>取消</Btn>
            </div>}>
      {published && (
        <div className="banner warn">
          已发布应用不可就地改，先克隆为草稿：保存会被后端拒绝（发布版本已锁定）。先克隆成新草稿再改。
        </div>
      )}
      {error && <ErrorNote text={`保存没成功：${error}`} />}
      <Field label="应用名称"><TextInput value={name} disabled={running} onChange={e => setName(e.target.value)} /></Field>
      <Field label="说明" top hint="业务人员打开应用时看到的一句话。">
        <TextInput value={description} disabled={running} onChange={e => setDescription(e.target.value)} />
      </Field>
      {app.created_at && <p className="muted small">创建于 {formatLocal(app.created_at)}{app.updated_at ? ` · 最近更新 ${formatLocal(app.updated_at)}` : ""}</p>}
    </Drawer>
  );
}

const VIEW_KINDS: [string, string][] = [
  ["table", "表格"], ["card", "卡片"], ["chart", "图表"], ["map", "地图"],
];

/** 新建 / 编辑视图共用：形态、数据来源、属性与 chart/map 的渲染配置。
 *  属性列表在打开时用 api.ontology(wid) 现取，不依赖全局 store。 */
function ViewDrawer({ app, queries, mode, view, onClose, onSaved }: {
  app: Application; queries: AppQuery[]; mode: "create" | "edit"; view: AppView | null;
  onClose: () => void; onSaved: () => void;
}) {
  const { wid, toast, objectTypes } = usePlatform();
  const [alias, setAlias] = useState(view?.alias || "");
  const [title, setTitle] = useState(view?.title || "");
  const [kind, setKind] = useState(view?.kind || "table");
  const [source, setSource] = useState<"object" | "query">(view?.query_alias ? "query" : "object");
  const [objectType, setObjectType] = useState(view?.object_type || "");
  const [queryAlias, setQueryAlias] = useState(view?.query_alias || queries[0]?.alias || "");
  const [props, setProps] = useState((view?.properties || []).join("、"));
  const [xField, setXField] = useState(view?.config?.x || "");
  const [yField, setYField] = useState(view?.config?.y || "");
  const [latField, setLatField] = useState(view?.config?.lat || "");
  const [lngField, setLngField] = useState(view?.config?.lng || "");
  const [error, setError] = useState<string | null>(null);

  // 打开抽屉时才取本体：只读当前对象的属性名，不新增全局依赖。
  const onto = useLoad(async () => (wid ? api.ontology(wid).catch(() => null) : null), [wid]);
  const queryType = source === "query" ? (queries.find(q => q.alias === queryAlias)?.object_type || "") : "";
  const typeName = source === "object" ? objectType : queryType;
  const schema = (typeName && onto.data?.objects?.[typeName]?.properties) || {};
  const allProps = Object.keys(schema);
  const numProps = allProps.filter(p => {
    const t = String(schema[p]?.type || "").toLowerCase();
    return t === "number" || t === "integer";
  });
  const needsConfig = kind === "chart" || kind === "map";

  const { run, running } = useAction(async () => {
    if (!wid) return;
    setError(null);
    if (mode === "create" && !alias.trim()) { setError("视图标识要填，例如 vehicles"); return; }
    if (source === "object" && !objectType) { setError("请选择对象类型"); return; }
    if (source === "query" && !queryAlias) { setError("请选择要绑定的查询"); return; }
    let config: Record<string, string> = {};
    if (kind === "chart") {
      if (!xField || !yField) { setError("图表要选两个属性：x 做标签、y 做数值"); return; }
      if (!numProps.includes(yField)) { setError(`图表的 y 只能选数值属性（number / integer）；「${yField}」不是`); return; }
      config = { x: xField, y: yField };
    } else if (kind === "map") {
      if (!latField || !lngField) { setError("地图要选两个数值属性：lat 纬度、lng 经度"); return; }
      if (!numProps.includes(latField)) { setError(`地图的纬度只能选数值属性；「${latField}」不是`); return; }
      if (!numProps.includes(lngField)) { setError(`地图的经度只能选数值属性；「${lngField}」不是`); return; }
      config = { lat: latField, lng: lngField };
    }
    const body = {
      title: title.trim(), kind,
      object_type: source === "object" ? objectType : "",
      query_alias: source === "query" ? queryAlias : "",
      properties: props.split(/[，,、\s]+/).map(s => s.trim()).filter(Boolean),
      config,
    };
    try {
      if (mode === "create") {
        await api.addView(wid, app.id, { alias: alias.trim(), ...body });
        toast(`视图「${title.trim() || alias.trim()}」已添加`, "ok");
      } else {
        await api.updateView(wid, app.id, view?.alias || alias, body);
        toast(`视图「${title.trim() || alias}」已更新`, "ok");
      }
      onSaved();
    } catch (e) {
      setError((e as Error).message);
      throw e;
    }
  }, [wid, app.id, mode, alias, title, kind, source, objectType, queryAlias, props,
      xField, yField, latField, lngField, view?.alias, numProps.join("|")]);

  const propSelect = (label: string, value: string, setter: (v: string) => void, options: string[], hint: string) => (
    <Field label={label} hint={hint}>
      <Select value={value} disabled={running} onChange={e => setter(e.target.value)}>
        <option value="">选择属性</option>
        {value && !options.includes(value) && <option value={value}>{value}（当前值）</option>}
        {options.map(p => <option key={p} value={p}>{p}</option>)}
      </Select>
    </Field>
  );

  return (
    <Drawer title={mode === "create" ? "新建视图" : `编辑视图 · ${view?.title || view?.alias || ""}`}
            sub="决定视图用哪种形式呈现、取哪些数据；图表 / 地图还要选渲染配置。"
            onClose={onClose} wide
            footer={<div className="btn-row flat">
              <Btn busy={running} onClick={() => void run()}>{mode === "create" ? "添加视图" : "保存视图"}</Btn>
              <Btn variant="subtle" disabled={running} onClick={onClose}>取消</Btn>
            </div>}>
      {error && <ErrorNote text={`保存没成功：${error}`} />}
      <Field label="视图标识" hint="应用里的唯一英文短词；建好后不改。">
        <TextInput value={alias} disabled={mode === "edit" || running} onChange={e => setAlias(e.target.value)} />
      </Field>
      <Field label="显示名"><TextInput value={title} disabled={running} onChange={e => setTitle(e.target.value)} /></Field>
      <Field label="形式">
        <Select value={kind} disabled={running} onChange={e => setKind(e.target.value)}>
          {VIEW_KINDS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </Select>
      </Field>
      <Field label="数据来源">
        <Select value={source} disabled={running} onChange={e => setSource(e.target.value as "object" | "query")}>
          <option value="object">按对象类型</option>
          <option value="query" disabled={!queries.length}>按已存查询</option>
        </Select>
      </Field>
      {source === "object" ? (
        <Field label="对象类型">
          <Select value={objectType} disabled={running} onChange={e => setObjectType(e.target.value)}>
            <option value="">选择对象类型</option>
            {objectType && !objectTypes.includes(objectType) && <option value={objectType}>{objectType}（当前值）</option>}
            {objectTypes.map(t => <option key={t} value={t}>{t}</option>)}
          </Select>
        </Field>
      ) : (
        <Field label="绑定的查询">
          <Select value={queryAlias} disabled={running} onChange={e => setQueryAlias(e.target.value)}>
            {queries.map(q => <option key={q.alias} value={q.alias}>{q.title || q.alias}</option>)}
          </Select>
        </Field>
      )}
      <Field label="只显示这些属性（可选）" hint="用逗号或顿号分隔；留空显示全部列。">
        <TextInput value={props} disabled={running} placeholder="例如 plate、status" onChange={e => setProps(e.target.value)} />
      </Field>
      {needsConfig && (
        <section className="view-config">
          <div className="add-block-head">
            <span className="section-title">视图配置</span>
            <span className="muted small">
              {kind === "chart" ? "x 做标签、y 做数值（y 只列数值属性）" : "纬度 lat 与经度 lng（只列数值属性）"}
            </span>
          </div>
          {kind === "chart" ? (
            <>
              {propSelect("x（标签）", xField, setXField, allProps, "按哪个属性分组做横轴。")}
              {propSelect("y（数值）", yField, setYField, numProps, "只列 number / integer 属性。")}
            </>
          ) : (
            <>
              {propSelect("纬度 lat", latField, setLatField, numProps, "只列 number / integer 属性。")}
              {propSelect("经度 lng", lngField, setLngField, numProps, "只列 number / integer 属性。")}
            </>
          )}
          {!typeName && <p className="muted small">先选对象类型（或绑定查询），才能列出可用属性。</p>}
          {!!typeName && !allProps.length && (
            <p className="muted small">没读到对象类型「{typeName}」的属性：确认本体里有这个类型、且已发布或保存草稿。</p>
          )}
        </section>
      )}
    </Drawer>
  );
}

/** 编辑查询：过滤条件用 JSON，展开用「关系 + 方向」行。 */
function QueryDrawer({ app, query, onClose, onSaved }: {
  app: Application; query: AppQuery; onClose: () => void; onSaved: () => void;
}) {
  const { wid, toast, objectTypes } = usePlatform();
  const [title, setTitle] = useState(query.title || "");
  const [objectType, setObjectType] = useState(query.object_type || "");
  const [filters, setFilters] = useState(() => JSON.stringify(query.filters || {}, null, 2));
  const [expands, setExpands] = useState<Row[]>(
    (query.expand || []).map(e => ({ a: e.relation, b: e.direction || "out" })));
  const [error, setError] = useState<string | null>(null);

  const { run, running } = useAction(async () => {
    if (!wid) return;
    setError(null);
    if (!objectType) { setError("请选择对象类型"); return; }
    let filterObj: Record<string, unknown>;
    try { filterObj = filters.trim() ? JSON.parse(filters) : {}; }
    catch (e) { setError(`过滤条件不是合法 JSON：${(e as Error).message}`); return; }
    if (!filterObj || typeof filterObj !== "object" || Array.isArray(filterObj)) {
      setError('过滤条件要是一个 JSON 对象，例如 {"status__eq": "active"}');
      return;
    }
    const expand = expands.filter(r => r.a.trim())
      .map(r => ({ relation: r.a.trim(), direction: r.b || "out" }));
    try {
      await api.updateQuery(wid, app.id, query.alias, {
        title: title.trim(), object_type: objectType, filters: filterObj, expand,
      });
      toast(`查询「${title.trim() || query.alias}」已更新`, "ok");
      onSaved();
    } catch (e) {
      setError((e as Error).message);
      throw e;
    }
  }, [wid, app.id, query.alias, title, objectType, filters, expands]);

  return (
    <Drawer title={`编辑查询 · ${query.title || query.alias}`} sub="查询 = 对象类型 + 过滤条件 + 顺带展开的关系；只改定义，不删除已有结果。"
            onClose={onClose} wide
            footer={<div className="btn-row flat">
              <Btn busy={running} onClick={() => void run()}>保存查询</Btn>
              <Btn variant="subtle" disabled={running} onClick={onClose}>取消</Btn>
            </div>}>
      {error && <ErrorNote text={`保存没成功：${error}`} />}
      <Field label="显示名"><TextInput value={title} disabled={running} onChange={e => setTitle(e.target.value)} /></Field>
      <Field label="对象类型">
        <Select value={objectType} disabled={running} onChange={e => setObjectType(e.target.value)}>
          <option value="">选择对象类型</option>
          {objectType && !objectTypes.includes(objectType) && <option value={objectType}>{objectType}（当前值）</option>}
          {objectTypes.map(t => <option key={t} value={t}>{t}</option>)}
        </Select>
      </Field>
      <JsonField label="过滤条件" value={filters} onChange={setFilters} rows={6}
                 hint='JSON 对象，键是「属性 + 比较」，例如 {"status__eq": "active"}；空对象表示全部。' />
      <Field label="顺带展开" top hint="关系名 + 方向（out 出边 / in 入边）；留空表示不展开。">
        <RowEditor rows={expands} onChange={setExpands} phA="关系名，如 owns" phB="方向"
                   bKind="select" bOptions={["out", "in"]} />
      </Field>
    </Drawer>
  );
}

function AddQuery({ app, onDone }: { app: Application; onDone: () => void }) {
  const { wid, toast, objectTypes } = usePlatform();
  const [alias, setAlias] = useState("");
  const [title, setTitle] = useState("");
  const [objectType, setObjectType] = useState("");
  const [filters, setFilters] = useState<FilterRow[]>([]);
  const [expands, setExpands] = useState("");
  const preview = useMemo(() => JSON.stringify(buildFilters(filters)) || "{}", [filters]);

  const { run, running } = useAction(async () => {
    if (!wid) return;
    if (!alias.trim() || !objectType) { toast("查询标识和对象类型都要填", "err"); return; }
    await api.addQuery(wid, app.id, {
      alias: alias.trim(), title: title.trim(), object_type: objectType,
      filters: buildFilters(filters),
      expand: expands.split(/[，,、]/).map(s => s.trim()).filter(Boolean),
    });
    toast("查询已添加", "ok");
    setAlias(""); setTitle(""); setFilters([]); setExpands("");
    onDone();
  }, [wid, app.id, alias, title, objectType, filters, expands]);

  return (
    <form className="add-block" onSubmit={e => { e.preventDefault(); void run(); }}>
      <div className="add-block-head"><span className="section-title">添加查询</span><span className="muted small">固定过滤条件，一键运行</span></div>
      <div className="inline-create wrap">
        <TextInput value={alias} placeholder="查询标识 low_balance" aria-label="查询标识" onChange={e => setAlias(e.target.value)} />
        <TextInput value={title} placeholder="显示名 余额不足" aria-label="显示名" onChange={e => setTitle(e.target.value)} />
        <Select value={objectType} aria-label="对象类型" onChange={e => setObjectType(e.target.value)}>
          <option value="">选择对象类型</option>
          {(objectTypes || []).map(t => <option key={t} value={t}>{t}</option>)}
        </Select>
        <TextInput value={expands} placeholder="顺带展开关系（可选）passes" aria-label="展开关系" onChange={e => setExpands(e.target.value)} />
        <Btn type="submit" size="sm" busy={running}><Plus size={13} /> 添加查询</Btn>
      </div>
      <Field label="过滤条件" top hint="不用写 JSON：选属性、选比较方式、填值即可。">
        <FilterRowEditor rows={filters.length ? filters : [{ prop: "", op: "", value: "" }]} onChange={setFilters} />
        <span className="field-hint">当前条件：{preview}</span>
      </Field>
    </form>
  );
}

function AddAgent({ app, onDone }: { app: Application; onDone: () => void }) {
  const { wid, toast } = usePlatform();
  const [alias, setAlias] = useState("");
  const [goal, setGoal] = useState("");
  const { run, running } = useAction(async () => {
    if (!wid) return;
    if (!alias.trim() || !goal.trim()) { toast("智能体标识和目标都要填", "err"); return; }
    await api.addAgent(wid, app.id, { alias: alias.trim(), goal: goal.trim() });
    toast("智能体已添加", "ok");
    setAlias(""); setGoal("");
    onDone();
  }, [wid, app.id, alias, goal]);
  return (
    <form className="inline-create wrap add-block-inline" onSubmit={e => { e.preventDefault(); void run(); }}>
      <TextInput value={alias} placeholder="标识 helper" aria-label="智能体标识" onChange={e => setAlias(e.target.value)} />
      <TextInput value={goal} placeholder="目标：检查余额不足的车辆并给出处置建议" aria-label="目标" onChange={e => setGoal(e.target.value)} />
      <Btn type="submit" size="sm" busy={running}><Plus size={13} /> 添加智能体</Btn>
    </form>
  );
}

function AttachCapability({ app, caps, onDone }: { app: Application; caps: Capability[]; onDone: () => void }) {
  const { wid, toast, go } = usePlatform();
  const [cid, setCid] = useState("");
  const options = caps.filter(c => c.lifecycle === "published" && !(app.capabilities || []).includes(c.id));
  const { run, running } = useAction(async () => {
    if (!wid) return;
    if (!cid) { toast("没有可挂载的已发布能力", "err"); return; }
    await api.attachCapability(wid, app.id, cid);
    toast("能力已挂载到应用", "ok");
    setCid("");
    onDone();
  }, [wid, app.id, cid]);
  return (
    <form className="inline-create" onSubmit={e => { e.preventDefault(); void run(); }}>
      <Select value={cid} aria-label="选择能力" onChange={e => setCid(e.target.value)}>
        <option value="">{options.length ? "选择已发布能力" : "暂无已发布能力"}</option>
        {options.map(c => <option key={c.id} value={c.id}>{c.name} · {c.target_type}.{c.key}</option>)}
      </Select>
      <Btn type="submit" size="sm" busy={running}>挂载</Btn>
      {!options.length && (
        <Btn type="button" size="sm" variant="subtle" onClick={() => go("capability")}>
          去发布能力 <CaretRight size={12} />
        </Btn>
      )}
    </form>
  );
}
