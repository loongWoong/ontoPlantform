import { useMemo, useState } from "react";
import { ArrowRight, ArrowClockwise, Plus } from "@phosphor-icons/react";
import {
  api, type AuditEvent, type DomainEvent, type Ontology, type ReleaseList,
  type RuntimeDescribe, type SloReport, type WorkspaceMetrics,
} from "../api";
import { useAction, usePlatform } from "../store";
import {
  Btn, Card, Collapsible, Empty, ErrorNote, Field, LIFECYCLE_LABEL, Meter,
  PageHead, Skeleton, Stat, Tag, TextInput, useLoad,
} from "../ui";
import { fmtNum, fmtPct, fmtRelative } from "../metrics";
import { StatusBadge } from "../components/StatusBadge";
import { DataQuality } from "../components/DataQuality";
import { aggregateSloStatus, deliveryStatus } from "../lib/status";
import { formatLocal, formatWindow } from "../lib/time";

interface Bundle {
  ont: Ontology | null;
  rt: RuntimeDescribe | null;
  sources: number;
  projections: number;
  caps: number;
  capsPublished: number;
  apps: number;
  objects: number;
  instances: number;
  audit: AuditEvent[];
  events: DomainEvent[];
  metrics: WorkspaceMetrics | null;
  slo: SloReport | null;
  releases: ReleaseList | null;
}

interface NextStep {
  title: string; desc: string; to: string; tab?: string; cta: string; tone?: "amber" | "sunken";
}

/** 工作台：五步主流程 + 真实计数 + 下一步直达，不展示任何假数据。 */
export function Workbench() {
  const { wid, workspaceName, go, toast, reloadWorkspaces, online, refreshRuntime } = usePlatform();
  const [name, setName] = useState("");
  const [refreshing, setRefreshing] = useState(false);

  const { run: createWs, running } = useAction(async () => {
    const trimmed = name.trim();
    if (!trimmed) { toast("请填写工作空间名称", "err"); return; }
    await api.createWorkspace(trimmed, "");
    setName("");
    await reloadWorkspaces();
    toast(`工作空间「${trimmed}」已创建，下一步定义本体`, "ok");
    go("ontology");
  }, [name]);

  const bundle = useLoad<Bundle | null>(async () => {
    if (!wid) return null;
    const [ont, rt, sources, projections, caps, apps, audit, events, metrics, slo, releases] =
      await Promise.all([
        api.ontology(wid).catch(() => null),
        api.runtime(wid).catch(() => null),
        api.datasources(wid).catch(() => []),
        api.projections(wid).catch(() => []),
        api.capabilities(wid).catch(() => []),
        api.applications(wid).catch(() => []),
        api.audit(wid, 6).catch(() => [] as AuditEvent[]),
        api.events(wid, 6).catch(() => [] as DomainEvent[]),
        api.metrics(wid).catch(() => null),
        api.slo(wid).catch(() => null),
        api.releases(wid).catch(() => null),
      ]);
    const runtimeObjects = rt ? (rt.objects || []) : [];
    const instances = (
      await Promise.all(
        runtimeObjects.map(o => api.find(wid, o.name, {}).then(r => r.length).catch(() => 0)),
      )
    ).reduce((sum, n) => sum + n, 0);
    return {
      ont, rt,
      sources: sources.length, projections: projections.length,
      caps: caps.length, capsPublished: caps.filter(c => c.lifecycle === "published").length,
      apps: apps.length,
      objects: runtimeObjects.length,
      instances,
      audit, events, metrics, slo, releases,
    };
  }, [wid]);

  const b = bundle.data;
  const goRefresh = async () => {
    setRefreshing(true);
    bundle.reload();
    refreshRuntime().catch(() => undefined);
    window.setTimeout(() => setRefreshing(false), 600);
  };

  const steps = useMemo(() => [
    {
      n: "01", title: "创建工作空间", done: !!wid,
      desc: wid ? workspaceName : "一套独立的业务世界",
      to: "", label: "",
    },
    {
      n: "02", title: "定义并发布本体", done: !!b?.rt,
      desc: b ? `${Object.keys(b.ont?.objects || {}).length} 对象 · ${Object.keys(b.ont?.relations || {}).length} 关系 · ${LIFECYCLE_LABEL[b.ont?.lifecycle || "none"]}` : "读取中…",
      to: "ontology", label: "去建模",
    },
    {
      n: "03", title: "接入现实数据", done: !!b && b.projections > 0,
      desc: b ? `${b.sources} 个数据源 · ${b.projections} 个映射 · ${fmtNum(b.instances)} 个实例` : "读取中…",
      to: "data", label: "去接入",
    },
    {
      n: "04", title: "沉淀业务能力", done: !!b && b.capsPublished > 0,
      desc: b ? `${b.capsPublished}/${b.caps} 个能力已发布` : "读取中…",
      to: "capability", label: "去创建",
    },
    {
      n: "05", title: "组装业务应用", done: !!b && b.apps > 0,
      desc: b ? `${b.apps} 个应用入口` : "读取中…",
      to: "application", label: "去组装",
    },
  ], [b, wid, workspaceName]);

  const next = useMemo<NextStep[]>(() => {
    if (!b) return [];
    const list: NextStep[] = [];
    const objectTypes = Object.keys(b.ont?.objects || {}).length;
    const relations = Object.keys(b.ont?.relations || {}).length;
    if (!wid) return list;
    if (!objectTypes) list.push({ title: "定义第一个对象类型", desc: "从业务里最常见的名词开始，比如「收费站」「车辆」。", to: "ontology", cta: "去建模" });
    else if (!relations) list.push({ title: "给对象之间补一条关系", desc: "关系决定对象实例能被怎样检索和推理。", to: "ontology", cta: "补关系" });
    if (objectTypes && !b.rt) list.push({ title: "发布本体，让运行时生效", desc: "发布之后对象实例才能进来，能力才能被调用。", to: "ontology", cta: "去发布" });
    if (!b.sources) list.push({ title: "接入第一个数据源", desc: "静态样例、SQLite、REST 接口或已有对象表。", to: "data", cta: "接数据" });
    else if (!b.projections) list.push({ title: "把数据映射成对象", desc: "映射决定一条记录落到哪个对象类型。", to: "data", cta: "配映射" });
    else if (b.instances === 0) list.push({ title: "跑一次同步", desc: "把源数据写进运行时，之后就能查询和联动。", to: "data", cta: "去同步" });
    if (b.rt && !b.capsPublished) list.push({ title: "发布一个业务能力", desc: "业务人员和 AI 只能通过能力改变世界。", to: "capability", cta: "建能力" });
    if (b.capsPublished && !b.apps) list.push({ title: "组装第一个应用", desc: "视图、查询、智能目标挂在应用下，业务才有入口。", to: "application", cta: "组装应用" });
    const pending = b.metrics?.indicators?.events_pending ?? 0;
    if (pending > 0) list.push({ title: `投递 ${fmtNum(pending)} 条积压事件`, desc: "事件是过程记录，积压说明订阅方没有跟上节奏。", to: "operations", cta: "去运行页" });
    if (b.slo && aggregateSloStatus(b.slo) === "breach") list.push({ title: "处理 SLO 违规", desc: "成功率、未知率或新鲜度有一项越线，先查运行时。", to: "operations", cta: "看指标", tone: "amber" });
    if (b.slo && ["no_data", "insufficient"].includes(aggregateSloStatus(b.slo))) list.push({ title: "先积累运行样本", desc: "当前窗口没有足够样本，SLO 还不能判定；跑一次同步或能力调用后再看。", to: "operations", cta: "看数据质量" });
    if (b.releases && !b.releases.deployment) list.push({ title: "把当前结构落成发布", desc: "发布是可变部署指针指向的不可变快照。", to: "operations", cta: "去发布" });
    return list.slice(0, 5);
  }, [b, wid]);

  const release = b?.releases?.deployment?.release_id
    ? b.releases.releases.find(r => r.id === b.releases?.deployment?.release_id)
    : undefined;
  const sloStatus = aggregateSloStatus(b?.slo);
  const slo = b?.slo;
  const pendingEvents = b?.metrics?.indicators?.events_pending ?? 0;
  const sampleTime = b?.metrics?.generated_at || b?.slo?.generated_at;
  const successIndicator = slo?.indicators?.success_rate;
  const unknownIndicator = slo?.indicators?.unknown_rate;

  return (
    <>
      <PageHead
        kicker={wid ? "Workspace" : "Console"}
        title={wid ? workspaceName || "工作台" : "把一个业务世界立起来"}
        desc={wid
          ? "本体、数据、能力、应用在同一条链路上；这里只汇总运行时里真正生效的结构与最近活动。"
          : "从零开始：先建工作空间，再定义本体、接入数据、沉淀能力、组装应用。"}
        meta={wid ? (
          <>
            <span>ID {wid}</span>
            <span>结构 <b>{(() => { const v = b?.ont?.schema_version ?? b?.metrics?.deployment?.schema_version; return v ? `v${v}` : "–"; })()}</b></span>
            <span>发布 <b>{release?.id?.slice(0, 14) || "未发布"}</b></span>
            <span>对象类型 <b>{b?.objects ?? "–"}</b></span>
            {sampleTime && <span title={formatLocal(sampleTime)}>采样 <b>{fmtRelative(sampleTime)}</b></span>}
          </>
        ) : undefined}
        actions={<>
          <Btn variant="subtle" onClick={() => void goRefresh()} busy={refreshing}>
            <ArrowClockwise size={14} /> 刷新
          </Btn>
          {wid && <Btn onClick={() => go("operations")}>运行与发布 <ArrowRight size={14} /></Btn>}
        </>}
      />

      {bundle.error && <ErrorNote text={bundle.error} onRetry={bundle.reload} />}

      {!wid ? (
        <>
          <Card title="五步上线" sub="每一步都对应界面里的一个页面，完成状态来自真实数据。">
            <div className="pipeline">
              {steps.map(s => (
                <div className="pipe-step" key={s.n} data-done={s.done ? "true" : "false"}>
                  <span className="pipe-index">STEP {s.n}</span>
                  <span className="pipe-title">{s.title}</span>
                  <span className="pipe-desc">{s.desc}</span>
                  <span className="pipe-state">{s.done ? "已完成" : "未开始"}</span>
                </div>
              ))}
            </div>
            <form className="panel-form create-form" onSubmit={e => { e.preventDefault(); void createWs(); }}>
              <Field label="新工作空间名称" hint="例如「高速运营」「城市停车」。工作空间之间完全隔离。">
                <TextInput value={name} placeholder="例如：高速运营" autoFocus onChange={e => setName(e.target.value)} />
              </Field>
              <div className="btn-row flat">
                <Btn type="submit" busy={running}>创建并开始建模</Btn>
                {!online && <span className="muted">服务未连接，请先启动 onto-api。</span>}
              </div>
            </form>
          </Card>

          <div className="bento gap-top">
            <div className="col-4"><Card title="定义" sub="对象、关系、术语先对齐语言，再谈数据。"><p className="muted">本体是这套系统的世界观：对象类型决定什么能存在，关系决定它们如何相连，术语把业务口语翻译成对象。</p></Card></div>
            <div className="col-4"><Card title="执行" sub="只有已发布的能力能改变世界。"><p className="muted">能力带目标类型、输入契约与实现，每次调用都写入执行记录与事件流，可追溯、可回放。</p></Card></div>
            <div className="col-4"><Card title="闭环" sub="指标、SLO 与控制器把偏差拉回来。"><p className="muted">运行页把发布、事件、校验、对账和控制器放在一起，出现偏差时先看到证据，再决定动作。</p></Card></div>
          </div>
        </>
      ) : (
        <>
          <div className="bento">
            <div className="col-3"><Stat label="对象类型" value={bundle.loading ? "…" : fmtNum(b?.objects)} unit={b?.rt ? "类" : undefined} foot={b?.rt ? "运行时已生效" : "尚未发布"} tone={b?.rt ? "sunken" : "amber"} /></div>
            <div className="col-3"><Stat label="对象实例" value={bundle.loading ? "…" : fmtNum(b?.instances)} foot={b?.projections ? `${b.projections} 个映射` : "还没有映射"} /></div>
            <div className="col-3"><Stat label="已发布能力" value={bundle.loading ? "…" : `${b?.capsPublished ?? 0}`} unit={`/ ${b?.caps ?? 0}`} foot={b?.capsPublished ? "可以调用" : "能力页创建"} tone={b?.capsPublished ? "green" : "sunken"} /></div>
            <div className="col-3"><Stat label="应用入口" value={bundle.loading ? "…" : fmtNum(b?.apps)} foot={b?.apps ? "业务人员可打开" : "还没有应用"} /></div>
          </div>

          <div className="bento gap-top">
            <div className="col-8">
              <Card title="下一步" sub="按依赖顺序给出，完成前一步之前，后一步通常没有意义。"
                    aside={<Tag kind={next.length ? "warn" : "ok"}>{next.length ? `${next.length} 项待办` : "骨架已通"}</Tag>}>
                {bundle.loading && <Skeleton rows={4} />}
                {!bundle.loading && next.length === 0 && (
                  <Empty title="主流程已经打通" hint="建议去「对象实例」抽取几条真实记录核对，再在「运行」页看指标与发布。"
                         action={<div className="btn-row flat">
                           <Btn size="sm" variant="subtle" onClick={() => go("state")}>看对象实例</Btn>
                           <Btn size="sm" variant="subtle" onClick={() => go("operations")}>看运行指标</Btn>
                         </div>} />
                )}
                {next.map((t, i) => (
                  <div className="todo-item" key={t.title + i}>
                    <span className="mono muted todo-index">{String(i + 1).padStart(2, "0")}</span>
                    <div className="todo-main">
                      <div className="todo-title">{t.title}</div>
                      <div className="todo-detail">{t.desc}</div>
                    </div>
                    <Btn size="sm" variant="subtle" onClick={() => go(t.to, t.tab ? { tab: t.tab } : undefined)}>{t.cta}</Btn>
                  </div>
                ))}
              </Card>
            </div>
            <div className="col-4">
              <Card title="运行概况" tone={sloStatus === "breach" ? "amber" : undefined}
                    sub={sampleTime ? `最近一次采样：${formatLocal(sampleTime)}` : "还没有采样记录"}
                    aside={<Btn size="sm" variant="link" onClick={() => go("operations")}>运行页</Btn>}>
                {bundle.loading && <div className="skeleton"><span /><span /><span /></div>}
                {!bundle.loading && (
                  <>
                    <dl className="kv">
                      <div className="kv-pair"><dt>SLO</dt><dd><StatusBadge status={sloStatus} /></dd></div>
                      <div className="kv-pair"><dt>评估窗口</dt><dd>{formatWindow(slo?.window_seconds)}</dd></div>
                      <div className="kv-pair"><dt>成功率</dt><dd className="mono">{successIndicator?.status === "no_data" ? "–" : fmtPct(successIndicator?.value)} <span className="muted small">{successIndicator?.status === "no_data" ? "无数据" : `样本 ${successIndicator?.samples ?? 0}`}</span></dd></div>
                      <div className="kv-pair"><dt>未知结果</dt><dd className="mono">{unknownIndicator?.status === "no_data" ? "–" : fmtPct(unknownIndicator?.value)} <span className="muted small">{unknownIndicator?.status === "no_data" ? "无数据" : `样本 ${unknownIndicator?.samples ?? 0}`}</span></dd></div>
                      <div className="kv-pair"><dt>事件投递</dt><dd className="mono">待投递 {fmtNum(pendingEvents)} · 未路由 {fmtNum(b?.metrics?.indicators?.events_unrouted)} · 死信 {fmtNum(b?.metrics?.indicators?.events_dead_lettered)}</dd></div>
                      <div className="kv-pair"><dt>当前发布</dt><dd className="mono">{release?.id?.slice(0, 18) || "未发布"} {b?.releases?.deployment?.status && <StatusBadge status={b.releases.deployment.status} />}</dd></div>
                      <div className="kv-pair"><dt>结构指纹</dt><dd className="mono">{release?.fingerprint || "–"}</dd></div>
                    </dl>
                    {slo?.reason && <p className="muted small">{slo.reason}</p>}
                    <div className="meter-wrap">
                      <div className="meter-label">事件积压</div>
                      <Meter value={Math.min(pendingEvents, 20)} max={20} tone={pendingEvents > 0 ? "warn" : undefined} />
                    </div>
                    <DataQuality
                      quality={b?.metrics?.data_quality}
                      windowSeconds={slo?.window_seconds}
                      samples={successIndicator?.samples}
                      minSamples={successIndicator?.min_samples}
                      status={sloStatus}
                      reason={slo?.reason}
                      compact
                    />
                  </>
                )}
              </Card>
            </div>
          </div>

          <Card title="上线路径" sub="五个阶段，完成状态来自最近一次读取的本体、映射、能力与应用计数。">
            <div className="pipeline">
              {steps.map(s => (
                <button type="button" className="pipe-step" key={s.n} data-done={s.done ? "true" : "false"}
                        disabled={!s.to} onClick={() => s.to && go(s.to)}>
                  <span className="pipe-index">STEP {s.n}</span>
                  <span className="pipe-title">{s.title}</span>
                  <span className="pipe-desc">{s.desc}</span>
                  <span className="pipe-state">{s.done ? "已完成" : s.label ? `待完成 · ${s.label}` : "未开始"}</span>
                </button>
              ))}
            </div>
          </Card>

          <div className="bento gap-top">
            <div className="col-7">
              <Card title="业务世界状态" sub="只展示运行时里真正生效的定义。"
                    aside={<Tag kind={b?.ont?.lifecycle === "published" ? "ok" : "warn"}>{LIFECYCLE_LABEL[b?.ont?.lifecycle || "none"]}</Tag>}>
                {bundle.loading && <div className="skeleton"><span /><span /><span /></div>}
                {!bundle.loading && !b?.rt && (
                  <Empty title="业务世界还没启动" hint="到「本体」页把定义发布出去，这里会出现运行时索引。"
                         action={<Btn size="sm" onClick={() => go("ontology")}>去发布本体</Btn>} />
                )}
                {b?.rt && <RuntimeIndex rt={b.rt} ont={b.ont} />}
              </Card>
            </div>
            <div className="col-5">
              <Card title="控制回路" sub="平台把“目标、执行、观测、纠偏”放在同一个闭环里。">
                <LoopDiagram />
              </Card>
            </div>
          </div>

          <div className="bento gap-top">
            <div className="col-6">
              <Card title="最近活动" aside={<Btn size="sm" variant="link" onClick={() => go("settings", { tab: "audit" })}>全部记录</Btn>}>
                {b && b.audit.length === 0 && <Empty title="暂无记录" hint="创建、发布、同步与调用都会留痕。" />}
                {!!b?.audit.length && (
                  <div>
                    {b.audit.map((e, i) => (
                      <div className="audit-row" key={i}>
                        <span className="audit-ts" title={e.ts}>{fmtRelative(e.ts)}</span>
                        <span><Tag kind="plain">{e.action}</Tag> <span className="muted">{e.target}</span></span>
                      </div>
                    ))}
                  </div>
                )}
              </Card>
            </div>
            <div className="col-6">
              <Card title="事件流" sub="outbox 里最近发生的事实；未路由不等于已投递。"
                    aside={<Btn size="sm" variant="link" onClick={() => go("operations", { tab: "events" })}>事件页</Btn>}>
                {b && b.events.length === 0 && <Empty title="还没有事件" hint="发布、同步、调用能力都会产生事件。" />}
                {!!b?.events.length && (
                  <div>
                    {b.events.map(e => (
                      <div className="audit-row" key={e.id}>
                        <span className="audit-ts">{fmtRelative(e.ts)}</span>
                        <span>
                          <span className="mono small">{e.type}</span>
                          <StatusBadge status={deliveryStatus(e)} />
                          {(e.attempts ?? 0) > 0 && <span className="muted small">尝试 {e.attempts} 次</span>}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </Card>
            </div>
          </div>

          <Collapsible label="再建一个工作空间 / 三条链路的分工">
            <p>定义 → 发布：本体经编译器翻译成运行时结构，平台不复制第二份业务状态。</p>
            <p>数据 → 状态：数据源经映射与投影写入内核，需要时再同步。</p>
            <p>语言 → 行动：术语把业务口语翻译成对象，业务人员和 AI 都只能通过能力改变世界，每次改变都留下可追溯的记录。</p>
            <form className="inline-create" onSubmit={e => { e.preventDefault(); void createWs(); }}>
              <TextInput value={name} placeholder="新工作空间名称" aria-label="新工作空间名称"
                         onChange={e => setName(e.target.value)} />
              <Btn type="submit" size="sm" variant="subtle" busy={running}><Plus size={13} /> 创建</Btn>
            </form>
          </Collapsible>
        </>
      )}
    </>
  );
}

function RuntimeIndex({ rt, ont }: { rt: RuntimeDescribe; ont: Ontology | null }) {
  const objects = rt.objects || [];
  const relations = rt.relations || [];
  const functions = rt.functions || [];
  return (
    <>
      <div className="page-meta flush">
        <span>结构版本 <b>v{rt.ontology?.version ?? "–"}</b></span>
        <span>对象 <b>{objects.length}</b></span>
        <span>关系 <b>{relations.length}</b></span>
        <span>动作 <b>{functions.length}</b></span>
      </div>
      <dl className="kv">
        <dt>对象类型</dt>
        <dd>{objects.length ? (
          <span className="tag-list">{objects.map(o => <Tag key={o.name} kind="plain">{o.name}</Tag>)}</span>
        ) : "–"}</dd>
        <dt>关系</dt>
        <dd>{relations.length ? (
          <span className="mono-list">{relations.map((r, i) => (
            <span key={i} className="mono">{r.source} → {r.target}</span>
          ))}</span>
        ) : "–"}</dd>
        <dt>可执行动作</dt>
        <dd>{functions.length ? (
          <span className="tag-list">{functions.map(f => <Tag key={f.name} kind="ok">{f.name}</Tag>)}</span>
        ) : "–"}</dd>
        <dt>术语</dt>
        <dd>{Object.keys(ont?.vocabulary || {}).length ? (
          <span className="mono-list">{Object.entries(ont?.vocabulary || {}).slice(0, 8).map(([k, v]) => (
            <span key={k}><span className="mono">{k}</span> → {String(v)}</span>
          ))}</span>
        ) : "–"}</dd>
      </dl>
      <div className="btn-row flat">
        <span className="muted small">动作与关系来自运行时返回值，不是界面推断。</span>
      </div>
    </>
  );
}

/** 解释性回路图：真实语义，不为装饰。 */
function LoopDiagram() {
  return (
    <div className="loop-diagram">
      <svg viewBox="0 0 520 236" role="img" aria-label="目标、控制器、内核、对象世界、观测与对账组成的控制回路">
        <defs>
          <marker id="loop-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
            <path d="M0 0L8 4L0 8z" fill="#d5d0c4" />
          </marker>
        </defs>
        <rect className="loop-node" x="24" y="44" width="112" height="52" rx="7" />
        <text className="loop-label" x="80" y="66" textAnchor="middle">目标</text>
        <text className="loop-sub" x="80" y="82" textAnchor="middle">objective · 人工触发</text>

        <rect className="loop-node" x="200" y="44" width="120" height="52" rx="7" />
        <text className="loop-label" x="260" y="66" textAnchor="middle">控制器</text>
        <text className="loop-sub" x="260" y="82" textAnchor="middle">propose / act · 只提议</text>

        <rect className="loop-node" x="384" y="44" width="112" height="52" rx="7" />
        <text className="loop-label" x="440" y="66" textAnchor="middle">内核</text>
        <text className="loop-sub" x="440" y="82" textAnchor="middle">runtime</text>

        <rect className="loop-node" x="384" y="150" width="112" height="52" rx="7" />
        <text className="loop-label" x="440" y="172" textAnchor="middle">对象世界</text>
        <text className="loop-sub" x="440" y="188" textAnchor="middle">objects</text>

        <rect className="loop-node" x="200" y="150" width="120" height="52" rx="7" />
        <text className="loop-label" x="260" y="172" textAnchor="middle">观测</text>
        <text className="loop-sub" x="260" y="188" textAnchor="middle">metrics / SLO · 只读</text>

        <rect className="loop-node" x="24" y="150" width="112" height="52" rx="7" />
        <text className="loop-label" x="80" y="172" textAnchor="middle">对账与校验</text>
        <text className="loop-sub" x="80" y="188" textAnchor="middle">verify / reconcile</text>

        <path className="loop-arrow" markerEnd="url(#loop-arrow)" d="M136 70 H194" />
        <path className="loop-arrow" markerEnd="url(#loop-arrow)" d="M320 70 H378" />
        <path className="loop-arrow" markerEnd="url(#loop-arrow)" d="M440 96 V144" />
        <path className="loop-arrow" markerEnd="url(#loop-arrow)" d="M384 176 H326" />
        <path className="loop-arrow" markerEnd="url(#loop-arrow)" d="M200 176 H142" />
        <path className="loop-arrow" markerEnd="url(#loop-arrow)" d="M80 150 V102" />
        <path className="loop-pulse" d="M136 70 H194 M320 70 H378 M440 96 V144 M384 176 H326 M200 176 H142 M80 150 V102" />
      </svg>
      <p className="muted small">
        当前是开环：目标评估、控制器动作、发布提升都需要人工触发；观测只读，不会自动改变业务世界。
      </p>
    </div>
  );
}
