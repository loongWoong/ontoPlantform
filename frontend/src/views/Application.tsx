import { useEffect, useMemo, useState } from "react";
import {
  CaretRight, Lightning, Play, Plus,
} from "@phosphor-icons/react";
import { api, type Application, type AppView, type Capability, type Obj, type QueryResult, type Verification } from "../api";
import { toChoice, useAction, usePlatform } from "../store";
import {
  buildFilters, Btn, Card, Drawer, Empty, Field, FilterRowEditor,
  KIND_LABEL, labelOf, LIFECYCLE_LABEL, Loading, ObjectTable, PageHead, Select,
  Sparkline, TableWrap, Tag, TextInput, useLoad, type FilterRow,
} from "../ui";
import { fmtNum } from "../metrics";
import { StatusBadge } from "../components/StatusBadge";
import { ValueView } from "../components/ValueView";
import { executionStatus } from "../lib/status";
import { formatLocal, formatWindow } from "../lib/time";
import { valuePreview } from "../lib/value";

type Stage =
  | { kind: "none" }
  | { kind: "query"; alias: string; data: QueryResult }
  | { kind: "view"; view: AppView; objects: Obj[] }
  | {
    kind: "agent"; alias: string; answer: string; steps: number; ok: boolean;
    status?: string; verification?: Verification | null;
    trace_id?: string; run_id?: string; failed_predicates?: string[];
  };

export function ApplicationView() {
  const { wid, params, go } = usePlatform();
  const [selected, setSelected] = useState<string | null>(params.app || null);
  useEffect(() => { if (params.app) setSelected(params.app); }, [params.app]);
  const [stage, setStage] = useState<Stage>({ kind: "none" });

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
            <PublishApp app={current} onChanged={load.reload} />
          )}
          <Btn variant="subtle" onClick={load.reload} busy={load.loading}>刷新</Btn>
        </>}
      />

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
                  <Tag kind={a.lifecycle === "published" ? "ok" : "warn"}>{LIFECYCLE_LABEL[a.lifecycle]}</Tag>
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
                     onChanged={load.reload} onOpenObject={ref => go("state", { ref })} />
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

function PublishApp({ app, onChanged }: { app: Application; onChanged: () => void }) {
  const { wid, toast, confirm, refreshRuntime } = usePlatform();
  const { run, running } = useAction(async () => {
    if (!wid) return;
    await api.publishApplication(wid, app.id);
    toast(`应用「${app.name}」已发布`, "ok");
    onChanged();
    void refreshRuntime();
  }, [wid, app.id, refreshRuntime, app.name]);
  return (
    <Btn size="sm" busy={running} onClick={() => confirm({
      title: `发布应用「${app.name}」？`,
      confirmText: "发布",
      body: <p className="muted">发布后应用及其视图、查询、智能体对外可用。</p>,
      run: async () => { await run(); },
    })}>发布应用</Btn>
  );
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

type AppTab = "views" | "queries" | "agents" | "caps";
const APP_TABS: readonly AppTab[] = ["views", "queries", "agents", "caps"];

function AppDetail({ app, caps, stage, setStage, onChanged, onOpenObject }: {
  app: Application; caps: Capability[]; stage: Stage;
  setStage: (s: Stage) => void; onChanged: () => void; onOpenObject: (ref: string) => void;
}) {
  const { params, go } = usePlatform();
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

  return (
    <Card title={app.name}
          sub="上面改定义，下面看结果；增删视图或查询不会清掉上一次跑出来的数据。"
          aside={<>
            <Tag kind={app.lifecycle === "published" ? "ok" : "warn"}>{LIFECYCLE_LABEL[app.lifecycle]}</Tag>
            <Tag kind="plain">{views.length} 视图</Tag>
          </>}>
      <ObjectivePanel app={app} onChanged={onChanged} />

      <div className="subtabs" role="tablist" tabIndex={0} aria-label="应用组成">
        {([["views", `视图 ${views.length}`], ["queries", `查询 ${queries.length}`],
           ["agents", `智能体 ${agents.length}`], ["caps", `挂载能力 ${attached.length}`]] as [AppTab, string][])
          .map(([k, l]) => (
            <button key={k} type="button" role="tab" aria-selected={tab === k} onClick={() => pick(k)}>{l}</button>
          ))}
      </div>

      {tab === "views" && (
        <>
          {!views.length && <Empty title="还没有视图" hint="视图就是「把某类对象摆成表格或卡片给人看」。" />}
          {!!views.length && (
            <TableWrap auto>
              <table>
                <thead><tr><th>视图</th><th>形式</th><th className="wrap">数据来源</th><th>操作</th></tr></thead>
                <tbody>
                  {views.map(v => (
                    <tr key={v.alias}>
                      <td className="nowrap"><b>{v.title || v.alias}</b></td>
                      <td className="nowrap"><Tag kind="plain">{labelOf(KIND_LABEL, v.kind)}</Tag></td>
                      <td className="wrap">{v.query_alias ? `查询 ${v.query_alias}` : (v.object_type || "–")}</td>
                      <td className="nowrap"><OpenView app={app} view={v} onResult={setStage} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
          )}
          <AddView app={app} queries={queries} onDone={onChanged} />
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
                      <td className="nowrap"><RunQuery app={app} alias={q.alias} onResult={setStage} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
          )}
          <AddQuery app={app} onDone={onChanged} />
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
                      <td className="nowrap"><RunAgent app={app} alias={g.alias} onResult={setStage} /></td>
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
              <span key={c.id} className="chip"><Lightning size={11} /> {c.name} · {c.target_type}.{c.key}</span>
            ))}
          </div>
          <AttachCapability app={app} caps={caps} onDone={onChanged} />
        </>
      )}

      <Stage area={stage} onOpenObject={onOpenObject} onClear={() => setStage({ kind: "none" })} />
    </Card>
  );
}

/* ---------- 业务目标：当前值 / 偏差 / 趋势 / 越界待办 ---------- */
const OBJECTIVE_METRICS: [string, string][] = [
  ["success_rate", "成功率"], ["unknown_rate", "未知率"],
  ["pending_events", "待派发事件"], ["projection_age_seconds", "投影新鲜度（秒）"],
];
const TREND_LABEL: Record<string, string> = { up: "上升", down: "下降", flat: "持平" };

function ObjectivePanel({ app, onChanged }: { app: Application; onChanged: () => void }) {
  const { wid, toast } = usePlatform();
  const [metric, setMetric] = useState(app.objective?.metric || "success_rate");
  const [target, setTarget] = useState(String(app.objective?.target ?? 0.99));
  const [band, setBand] = useState(String(app.objective?.band ?? 0.02));
  const [win, setWin] = useState(String(app.objective?.window ?? 3600));

  const load = useLoad(async () => {
    if (!wid || !app.objective) return null;
    return api.objective(wid, app.id).catch(() => null);
  }, [wid, app.id, app.objective]);
  const ev = load.data;

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
    load.reload();
    onChanged();
  }, [wid, app.id, metric, target, band, win]);

  const evaluate = useAction(async () => {
    if (!wid) return;
    const res = await api.evaluateObjective(wid, app.id);
    const noData = res.available === false || (res.samples || []).length === 0;
    toast(
      noData ? "评估完成：窗口内没有样本，不能判定达标"
        : res.ok ? "评估完成：当前在目标带内" : "评估完成：当前越界，已生成待办",
      noData ? "warn" : res.ok ? "ok" : "warn",
    );
    load.reload();
    onChanged();
  }, [wid, app.id]);

  const { run: resolveTodo, running: resolving } = useAction(async () => {
    if (!wid || !ev?.todo) return;
    await api.resolveTodo(wid, ev.todo.id);
    toast("待办已关闭；这只记录处理动作，不代表指标已经恢复", "warn");
    load.reload();
  }, [wid, ev?.todo?.id]);

  const objective = ev?.objective || app.objective;
  const sampleCount = ev?.samples?.length ?? 0;
  const available = !!ev && ev.available !== false && sampleCount > 0;
  const objStatus = !ev ? "no_data" : !available ? "insufficient" : ev.ok ? "ok" : "breach";
  const scopeLabel = ev?.scope === "workspace" ? "工作空间"
    : ev?.scope === "application" ? "当前应用" : "接口未返回";
  const spark = useMemo(() => (ev?.samples || []).map(s => s.value), [ev]);

  return (
    <section className="objective-panel">
      <div className="result-head">
        <h3>业务目标</h3>
        <StatusBadge status={objStatus} />
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
            : <ObjectTable objects={area.objects} onOpen={onOpenObject} />}
          {(area.view.kind === "chart" || area.view.kind === "map") && (
            <div className="banner info">
              当前版本先把{labelOf(KIND_LABEL, area.view.kind)}视图用表格呈现数据，图形渲染在后续版本。
            </div>
          )}
        </>
      )}

      {area.kind === "agent" && (
        <>
          <div className="result-head">
            <span className="muted small">{area.steps} 步 · {area.trace_id ? `轨迹 ${area.trace_id}` : "后端未返回轨迹编号"}</span>
            <StatusBadge status={executionStatus({ status: area.status, ok: area.ok })} />
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
      onResult({
        kind: "agent", alias, answer: r.answer, steps: (r.steps || []).length, ok: r.ok,
        status: r.status, verification: r.verification, trace_id: r.trace_id,
        run_id: r.run_id, failed_predicates: r.failed_predicates,
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

function AddView({ app, queries, onDone }: {
  app: Application; queries: { alias: string }[]; onDone: () => void;
}) {
  const { wid, toast, objectTypes } = usePlatform();
  const [alias, setAlias] = useState("");
  const [title, setTitle] = useState("");
  const [kind, setKind] = useState("table");
  const [source, setSource] = useState<"object" | "query">("object");
  const [objectType, setObjectType] = useState("");
  const [queryAlias, setQueryAlias] = useState(queries[0]?.alias || "");
  const [props, setProps] = useState("");

  const { run, running } = useAction(async () => {
    if (!wid) return;
    if (!alias.trim()) { toast("请填写视图标识", "err"); return; }
    if (source === "object" && !objectType) { toast("请选择对象类型", "err"); return; }
    await api.addView(wid, app.id, {
      alias: alias.trim(), title: title.trim(), kind,
      object_type: source === "object" ? objectType : "",
      query_alias: source === "query" ? queryAlias : "",
      properties: props.split(/[，,、]/).map(s => s.trim()).filter(Boolean),
    });
    toast("视图已添加", "ok");
    setAlias(""); setTitle(""); setProps("");
    onDone();
  }, [wid, app.id, alias, title, kind, source, objectType, queryAlias, props]);

  return (
    <form className="add-block" onSubmit={e => { e.preventDefault(); void run(); }}>
      <div className="add-block-head"><span className="section-title">添加视图</span><span className="muted small">表格 / 卡片 / 图表 / 地图</span></div>
      <div className="inline-create wrap">
        <TextInput value={alias} placeholder="视图标识 vehicles" aria-label="视图标识" onChange={e => setAlias(e.target.value)} />
        <TextInput value={title} placeholder="显示名 车辆列表" aria-label="显示名" onChange={e => setTitle(e.target.value)} />
        <Select value={kind} aria-label="形式" onChange={e => setKind(e.target.value)}>
          <option value="table">表格</option><option value="card">卡片</option>
          <option value="chart">图表</option><option value="map">地图</option>
        </Select>
        <Select value={source} aria-label="数据来源" onChange={e => setSource(e.target.value as "object" | "query")}>
          <option value="object">按对象类型</option>
          <option value="query" disabled={!queries.length}>按已存查询</option>
        </Select>
        {source === "object" ? (
          <Select value={objectType} aria-label="对象类型" onChange={e => setObjectType(e.target.value)}>
            <option value="">选择对象类型</option>
            {(objectTypes || []).map(t => <option key={t} value={t}>{t}</option>)}
          </Select>
        ) : (
          <Select value={queryAlias} aria-label="绑定的查询" onChange={e => setQueryAlias(e.target.value)}>
            {queries.map(q => <option key={q.alias} value={q.alias}>{q.alias}</option>)}
          </Select>
        )}
        <TextInput value={props} placeholder="只显示这些属性（可选）" aria-label="属性列表" onChange={e => setProps(e.target.value)} />
        <Btn type="submit" size="sm" busy={running}><Plus size={13} /> 添加视图</Btn>
      </div>
    </form>
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
