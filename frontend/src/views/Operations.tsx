import { useEffect, useMemo, useState } from "react";
import {
  ArrowClockwise, ArrowCounterClockwise, CheckCircle, CloudArrowUp,
  PaperPlaneTilt, ShieldCheck, WarningCircle,
} from "@phosphor-icons/react";
import {
  api, type CanaryCheck, type CanaryStatus, type ControllerCycle, type ControllerProposal,
  type DomainEvent, type ReconcileResult, type ReleaseList, type SchedulerTask, type SloReport,
  type VerifyResult, type WorkspaceMetrics,
} from "../api";
import { useAction, usePlatform } from "../store";
import {
  Btn, Card, Drawer, Empty, ErrorNote, Field, Loading, PageHead,
  Select, Skeleton, Stat, Tag, TextInput, useLoad,
} from "../ui";
import {
  fmtNum, fmtPct, fmtRelative, fmtSeconds, fmtTime, INDICATOR_LABEL,
  INDICATOR_META, SLO_LABEL,
} from "../metrics";
import { StatusBadge } from "../components/StatusBadge";
import { DataQuality } from "../components/DataQuality";
import { ValueView } from "../components/ValueView";
import {
  CANARY_RING_LABEL, aggregateSloStatus, canaryCheckState, deliveryStatus, statusMeta,
} from "../lib/status";
import { formatLocal, formatWindow } from "../lib/time";

const TABS = [
  { id: "overview", label: "概览" },
  { id: "releases", label: "发布与部署" },
  { id: "events", label: "事件与投递" },
  { id: "health", label: "校验与纠偏" },
];

/** 运行页：观测、发布、事件与新治理闭环都在这里完成。 */
export function OperationsView() {
  const { wid, go, params, toast, confirm, online, objectTypes } = usePlatform();
  const tab = params.tab || "overview";
  const [windowSeconds, setWindowSeconds] = useState(3600);

  const rel = useLoad<ReleaseList | null>(
    () => (wid ? api.releases(wid) : Promise.resolve(null)), [wid]);
  const obs = useLoad<{ metrics: WorkspaceMetrics; slo: SloReport } | null>(
    () => (wid ? Promise.all([api.metrics(wid, windowSeconds), api.slo(wid, windowSeconds)])
      .then(([metrics, slo]) => ({ metrics, slo })) : Promise.resolve(null)), [wid, windowSeconds]);
  const canaryInfo = useLoad<CanaryStatus | null>(
    () => (wid ? api.canary(wid) : Promise.resolve(null)), [wid]);
  const jobs = useLoad(() => api.scheduler(), []);

  const [note, setNote] = useState("");
  const [requireHealthy, setRequireHealthy] = useState(true);
  const [lastCheck, setLastCheck] = useState<CanaryCheck | null>(null);
  useEffect(() => { setLastCheck(null); }, [wid]);

  const r = rel.data;
  const current = r?.deployment?.release_id
    ? r.releases.find(x => x.id === r.deployment?.release_id) : undefined;
  const canaryRel = r?.canary && !r.canary.promoted && r.canary.release_id
    ? r.releases.find(x => x.id === r.canary?.release_id) : undefined;
  const currentIdx = r?.deployment
    ? r.releases.findIndex(x => x.id === r.deployment?.release_id) : -1;
  const canRollback = currentIdx > 0;
  const m = obs.data?.metrics;
  const slo = obs.data?.slo;
  const indicators = m?.indicators || {};
  const sloStatus = aggregateSloStatus(slo);
  const canaryData = canaryInfo.data;
  const canaryEvidence: CanaryCheck | null = lastCheck
    ?? canaryData?.check
    ?? (canaryData?.checked_at ? canaryData : null)
    ?? r?.canary_check
    ?? (r?.canary?.checked_at ? {
      ok: (r.canary.checks || []).length > 0 && (r.canary.checks || []).every(c => c.ok),
      checked_at: r.canary.checked_at,
      checks: r.canary.checks,
      release_id: r.canary.release_id,
      state_mode: r.canary.state_mode,
    } : null);
  const canaryState = canaryCheckState(canaryEvidence);
  const canaryFresh = canaryState.fresh;
  const canaryPassed = canaryState.passed;
  const canaryStateMode = canaryData?.state_mode ?? canaryData?.check?.state_mode
    ?? canaryEvidence?.state_mode ?? r?.canary?.state_mode ?? null;
  const canaryEvidenceMatches = !canaryEvidence?.release_id || canaryEvidence.release_id === canaryRel?.id;
  const canPromote = !!canaryRel && canaryFresh && canaryPassed && canaryEvidenceMatches;

  const cut = useAction(async () => {
    if (!wid) return;
    await api.cutRelease(wid, note.trim());
    setNote("");
    toast("已切出不可变发布快照", "ok");
    rel.reload(); obs.reload();
  }, [wid, note]);

  const deploy = useAction(async (rid: string) => {
    if (!wid) return;
    await api.deployRelease(wid, rid);
    toast(`部署请求已接受：${rid.slice(0, 14)}；状态为 pending 时仍在等待生效，不是流量已切换`, "warn");
    rel.reload(); obs.reload();
  }, [wid]);

  const canaryDeploy = useAction(async (rid: string) => {
    if (!wid) return;
    await api.canaryRelease(wid, rid);
    toast(`已把 ${rid.slice(0, 14)} 部署到隔离验证环（不接真实流量）`, "ok");
    rel.reload(); canaryInfo.reload();
  }, [wid]);

  const runCheck = useAction(async () => {
    if (!wid) return;
    const res = await api.checkCanary(wid);
    setLastCheck({ ...res, checked_at: res.checked_at || new Date().toISOString() });
    toast(res.ok ? "隔离验证环检查通过" : "隔离验证环检查未通过，先看明细", res.ok ? "ok" : "warn");
    rel.reload(); canaryInfo.reload();
  }, [wid]);

  const promote = useAction(async () => {
    if (!wid) return;
    if (!canPromote) {
      toast("金丝雀检查已过期或未通过：先运行验证，再提升", "warn");
      return;
    }
    await api.promoteCanary(wid, requireHealthy);
    toast("提升请求已完成；以当前部署状态为准，pending 时不代表流量已切换", "warn");
    rel.reload(); obs.reload(); canaryInfo.reload();
  }, [wid, requireHealthy, canPromote]);

  const rollback = useAction(async () => {
    if (!wid) return;
    const res = await api.rollbackRelease(wid);
    toast(`已回滚到 ${res.release.id.slice(0, 14)}`, "ok");
    rel.reload(); obs.reload(); canaryInfo.reload();
  }, [wid]);

  if (!wid) return <Empty title="先选择一个工作空间" hint="运行页只展示当前工作空间的数据。" />;

  const selectTab = (id: string) => go("operations", { tab: id });

  return (
    <>
      <PageHead
        kicker="Operations"
        title="运行"
        desc="发布是不可变快照，部署是可移动指针；指标、SLO、校验、对账与控制器组成一条可解释的纠偏回路。"
        meta={<>
          <span>SLO <b>{statusMeta(sloStatus).label}</b></span>
          <span>当前发布 <b>{current?.id.slice(0, 16) || "未部署"}</b></span>
          <span>结构版本 <b>{m?.deployment?.schema_version ?? current?.schema_version ? `v${m?.deployment?.schema_version ?? current?.schema_version}` : "–"}</b></span>
          <span>窗口 <b>{formatWindow(slo?.window_seconds ?? windowSeconds)}</b></span>
          <span>采样于 <b>{obs.data ? fmtTime(m?.generated_at) : "–"}</b></span>
        </>}
        actions={<>
          <Btn variant="subtle" busy={rel.loading || obs.loading}
               onClick={() => { rel.reload(); obs.reload(); canaryInfo.reload(); jobs.reload(); }}>
            <ArrowClockwise size={14} /> 重新计算
          </Btn>
          <Btn onClick={() => selectTab("releases")}><CloudArrowUp size={14} /> 发布</Btn>
        </>}
      />

      {(rel.error || obs.error) && (
        <ErrorNote text={rel.error || obs.error || ""} onRetry={() => { rel.reload(); obs.reload(); }} />
      )}
      {!online && <div className="banner warn">服务当前不可达，运行数据可能不是最新的。</div>}

      <div className="subtabs" role="tablist" tabIndex={0} aria-label="运行页分组">
        {TABS.map(t => (
          <button key={t.id} type="button" role="tab" aria-selected={tab === t.id}
                  onClick={() => selectTab(t.id)}>{t.label}</button>
        ))}
      </div>

      {tab === "overview" && (
        <>
          <div className="bento">
            <div className="col-3">
              <Stat label="SLO 状态" value={slo ? statusMeta(sloStatus).label : "–"}
                    foot={slo ? `${Object.keys(slo.indicators).length} 项指标 · ${formatWindow(slo.window_seconds ?? windowSeconds)}` : "还没有样本"}
                    tone={sloStatus === "ok" ? "green" : sloStatus === "breach" ? "red" : "sunken"} />
            </div>
            <div className="col-3">
              <Stat label="动作成功率" value={slo ? fmtPct(slo.indicators.success_rate?.value) : "–"}
                    foot={slo
                      ? (slo.indicators.success_rate?.status === "no_data"
                        ? "窗口内没有样本"
                        : `下限 ${fmtPct(slo.indicators.success_rate?.target)} · ${slo.indicators.success_rate?.samples ?? 0} 样本`)
                      : "–"}
                    tone={slo?.indicators.success_rate?.status === "ok" && sloStatus === "ok" ? "green"
                      : slo?.indicators.success_rate?.status === "breach" ? "amber" : "sunken"} />
            </div>
            <div className="col-3">
              <Stat label="结果未知" value={slo ? fmtPct(slo.indicators.unknown_rate?.value) : "–"}
                    foot={slo
                      ? (slo.indicators.unknown_rate?.status === "no_data"
                        ? "窗口内没有样本"
                        : `上限 ${fmtPct(slo.indicators.unknown_rate?.target)} · ${slo.indicators.unknown_rate?.samples ?? 0} 样本`)
                      : "–"}
                    tone={slo?.indicators.unknown_rate?.status === "breach" ? "amber" : "sunken"} />
            </div>
            <div className="col-3">
              <Stat label="待投递事件" value={fmtNum(indicators.events_pending)}
                    foot={(indicators.executions_total ?? 0) > 0
                      ? `窗口内执行 ${fmtNum(indicators.executions_total)} 次 · 失败 ${fmtNum(indicators.executions_failed)} 次`
                      : "窗口内还没有执行记录"}
                    tone={((indicators.events_unrouted ?? 0) + (indicators.events_dead_lettered ?? 0)) > 0 ? "amber"
                      : (indicators.events_pending ?? 0) > 0 ? "amber" : undefined} />
            </div>
          </div>

          <div className="bento gap-top">
            <div className="col-8">
              <Card title="运行时指标" sub="来自后端 /metrics 的最近一次采样，不由界面推算；切换窗口不会补造样本。"
                    aside={<>
                      <Select value={String(windowSeconds)} aria-label="评估窗口"
                              onChange={e => setWindowSeconds(Number(e.target.value))}>
                        <option value="300">近 5 分钟</option>
                        <option value="3600">近 1 小时</option>
                        <option value="21600">近 6 小时</option>
                        <option value="86400">近 24 小时</option>
                      </Select>
                      <Tag kind="plain">采样 {m?.generated_at ? fmtTime(m.generated_at) : "–"}</Tag>
                      <Tag kind="plain">{Object.keys(indicators).length} 项</Tag>
                    </>}>
                {obs.loading && <Skeleton rows={6} />}
                {!obs.loading && !m && <Empty title="还没有指标" hint="发布本体、跑一次同步后就会产生数据。" />}
                {m && <IndicatorGrid indicators={indicators} />}
                {m && (
                  <DataQuality
                    quality={m.data_quality}
                    windowSeconds={m.window_seconds ?? windowSeconds}
                    samples={slo?.indicators.success_rate?.samples}
                    minSamples={slo?.indicators.success_rate?.min_samples}
                    status={sloStatus}
                    reason={slo?.reason}
                  />
                )}
              </Card>
            </div>
            <div className="col-4">
              <Card title="服务目标 SLO" sub="目标越线时，先看证据再动作；没有样本时显示灰色。"
                    aside={<StatusBadge status={sloStatus} />}>
                {obs.loading && <Skeleton rows={4} />}
                {!obs.loading && !slo && <Empty title="还没有 SLO 数据" />}
                {slo && <SloList slo={slo} />}
                {slo && <p className="muted small">{slo.reason || `窗口 ${formatWindow(slo.window_seconds ?? windowSeconds)}，样本不足时不判定达标。`}</p>}
              </Card>
            </div>
          </div>

          <Card title="后台作业"
                sub="进程级调度器摘要，不是工作空间级别的实时状态；下次运行时间由服务端返回，本页不自行推算。"
                aside={<>
                  <Tag kind={jobs.data?.running ? "plain" : "warn"}>{jobs.data ? (jobs.data.running ? "运行中" : "未运行") : "未知"}</Tag>
                  <Btn size="sm" variant="link" onClick={() => jobs.reload()}>刷新</Btn>
                </>}>
            {jobs.loading && <Skeleton rows={3} />}
            {jobs.error && <div className="banner warn">作业状态接口不可用：{jobs.error}</div>}
            {!jobs.loading && !jobs.error && !jobs.data?.tasks?.length && (
              <Empty title="没有已注册的后台作业" hint="调度器可能在 serve() 中启动，或当前进程未启用后台作业。" />
            )}
            {!!jobs.data?.tasks?.length && (
              <div className="tablewrap auto">
                <table className="table">
                  <thead>
                    <tr><th>作业</th><th>间隔</th><th>运行 / 失败</th><th>上次运行</th><th>下次计划</th><th>最近错误</th></tr>
                  </thead>
                  <tbody>
                    {jobs.data.tasks.map(t => <JobRow key={t.name} task={t} />)}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          <div className="bento gap-top">
            <div className="col-6">
              <Card title="发布与部署" aside={<Btn size="sm" variant="link" onClick={() => selectTab("releases")}>管理</Btn>}>
                {rel.loading && <Skeleton rows={3} />}
                {!rel.loading && !r?.releases.length && (
                  <Empty title="还没有切过发布" hint="发布把当前已编译结构固化成不可变快照。"
                         action={<Btn size="sm" onClick={() => selectTab("releases")}>去切发布</Btn>} />
                )}
                {!!r?.releases.length && (
                  <>
                    <dl className="kv">
                      <div className="kv-pair"><dt>当前部署</dt><dd className="mono">{current?.id || "未部署"} {r.deployment?.status && <StatusBadge status={r.deployment.status} />}</dd></div>
                      <div className="kv-pair"><dt>结构指纹</dt><dd className="mono">{current?.fingerprint || "–"}</dd></div>
                      <div className="kv-pair"><dt>能力集合</dt><dd className="mono">{current?.capability_ids.length ?? 0} 个</dd></div>
                      <div className="kv-pair"><dt>{CANARY_RING_LABEL}</dt><dd>{canaryRel ? <Tag kind="warn">{canaryRel.id.slice(0, 16)}</Tag> : "未启用"} <StatusBadge status={canaryEvidence ? canaryState.status : "no_data"} /></dd></div>
                      <div className="kv-pair"><dt>历史发布</dt><dd className="mono">{r.releases.length} 个</dd></div>
                    </dl>
                    {(r.deployment?.status === "pending" || r.deployment?.status === "failed") && (
                      <div className="banner warn">
                        部署状态为 {r.deployment.status === "pending" ? "待生效" : "失败"}：当前流量仍由旧发布服务，不要按新结构对外承诺。
                      </div>
                    )}
                    <div className="btn-row">
                      {canaryRel && (
                        <Btn size="sm" busy={promote.running} disabled={!canPromote}
                             title={canPromote ? undefined : "没有新鲜的检查结果：先到「发布与部署」运行验证"}
                             onClick={() => void promote.run()}>提升金丝雀</Btn>
                      )}
                      <Btn size="sm" variant="subtle" busy={rollback.running} disabled={!canRollback}
                           title={canRollback ? undefined : "当前部署已经是最早的发布，没有可回滚的历史版本"}
                           onClick={() => confirm({
                             title: "回滚到上一个发布？",
                             body: <p className="muted">只移动部署指针，不重新编译；当前结构会立刻回到上一快照。</p>,
                             confirmText: "回滚", danger: true,
                             run: async () => { await rollback.run(); },
                           })}>
                        <ArrowCounterClockwise size={14} /> 回滚
                      </Btn>
                    </div>
                  </>
                )}
              </Card>
            </div>
            <div className="col-6">
              <Card title="最近事件" aside={<Btn size="sm" variant="link" onClick={() => selectTab("events")}>全部事件</Btn>}>
                <RecentEvents />
              </Card>
            </div>
          </div>

          <Card title="健康检查入口" sub="校验抓结构错误，对账抓投影漂移，控制器把可执行的修复建议列出来。">
            <dl className="kv">
              <div className="kv-pair"><dt>结构校验</dt><dd>检查对象属性、身份重复、必填缺失。</dd></div>
              <div className="kv-pair"><dt>投影对账</dt><dd>对比映射源与运行时，找出缺失实例。</dd></div>
              <div className="kv-pair"><dt>控制器</dt><dd>只提建议，不自动执行；接受后才触发投影重跑。</dd></div>
            </dl>
            <div className="btn-row">
              <Btn size="sm" variant="subtle" onClick={() => selectTab("health")}>
                <ShieldCheck size={14} /> 打开校验与纠偏
              </Btn>
            </div>
          </Card>
        </>
      )}

      {tab === "releases" && (
        <>
          <Card title="当前部署" sub="部署指针可移动，发布快照不可变。">
            {rel.loading && <Skeleton rows={3} />}
            {!rel.loading && r && (
              <>
                <dl className="kv">
                  <div className="kv-pair"><dt>当前环</dt><dd className="mono">{r.deployment?.release_id || "未部署"} {r.deployment?.status && <StatusBadge status={r.deployment.status} />}</dd></div>
                  <div className="kv-pair"><dt>结构版本</dt><dd className="mono">{r.deployment?.schema_version ? `v${r.deployment.schema_version}` : "–"}</dd></div>
                  <div className="kv-pair"><dt>最近部署时间</dt><dd className="mono">{fmtTime(r.deployment?.deployed_at)}</dd></div>
                  <div className="kv-pair"><dt>{CANARY_RING_LABEL}</dt><dd className="mono">{!r.canary ? "未启用" : r.canary.promoted ? `已提升 ${r.canary.release_id}` : r.canary.release_id}</dd></div>
                </dl>
                {(r.deployment?.status === "pending" || r.deployment?.status === "failed") && (
                  <div className="banner warn">
                    部署状态为 {r.deployment.status === "pending" ? "待生效" : "失败"}：当前流量仍由旧发布服务，不要按新结构对外承诺。
                  </div>
                )}
                <div className="pill-row">
                  <Btn size="sm" variant="subtle" busy={rollback.running} disabled={!canRollback}
                       onClick={() => confirm({
                         title: "回滚到上一个发布？",
                         body: <p className="muted">部署指针回到上一快照，运行时结构立即生效。</p>,
                         confirmText: "回滚", danger: true,
                         run: async () => { await rollback.run(); },
                       })}>回滚</Btn>
                </div>
              </>
            )}
          </Card>

          <Card title={CANARY_RING_LABEL}
                sub="金丝雀是隔离的 shadow 验证环，不是真实流量；检查通过且足够新时才允许提升。"
                aside={<StatusBadge status={canaryEvidence ? canaryState.status : "no_data"} />}>
            {canaryInfo.loading && <Skeleton rows={3} />}
            {canaryInfo.error && (
              <div className="banner warn">
                隔离验证环状态接口不可用：{canaryInfo.error}。仍可点「运行验证」，结果会直接显示在本卡片。
              </div>
            )}
            <dl className="kv">
              <div className="kv-pair"><dt>环状态</dt><dd className="mono">{canaryStateMode || "isolated（隔离运行）"}</dd></div>
              <div className="kv-pair"><dt>候选发布</dt><dd className="mono">{canaryRel?.id || r?.canary?.release_id || "没有候选"}</dd></div>
              <div className="kv-pair"><dt>最近检查</dt><dd className="mono">{canaryEvidence?.checked_at ? formatLocal(canaryEvidence.checked_at) : "从未检查"}</dd></div>
              <div className="kv-pair"><dt>检查结果</dt><dd><StatusBadge status={canaryEvidence ? canaryState.status : "no_data"} /> {canaryEvidence && <span className="muted small">{canaryFresh ? "在 10 分钟新鲜窗口内" : "已超过 10 分钟，需要重新验证"}</span>} {canaryEvidence?.release_id && <span className="muted mono small"> · 检查发布 {canaryEvidence.release_id}</span>}</dd></div>
              <div className="kv-pair"><dt>隔离启动时间</dt><dd className="mono">{fmtTime(r?.canary?.deployed_at)}</dd></div>
            </dl>
            {canaryEvidence?.release_id && canaryRel && canaryEvidence.release_id !== canaryRel.id && (
              <div className="banner warn">
                最近一次检查属于发布 {canaryEvidence.release_id}，不是当前候选 {canaryRel.id}；请先对新候选运行验证，不能拿旧结果提升。
              </div>
            )}
            {!!canaryEvidence?.checks?.length && (
              <ul className="check-list">
                {canaryEvidence.checks.map((c, i) => (
                  <li key={c.name + i}>
                    <Tag kind={c.ok ? "ok" : "err"}>{c.ok ? "通过" : "未通过"}</Tag>
                    <span className="mono">{c.name}</span>
                    <ValueView value={c.detail} compact />
                  </li>
                ))}
              </ul>
            )}
            <div className="btn-row">
              <Btn size="sm" busy={runCheck.running} onClick={() => void runCheck.run()}>
                <ShieldCheck size={14} /> 运行验证
              </Btn>
              <label className="check-line">
                <input type="checkbox" checked={requireHealthy}
                       onChange={e => setRequireHealthy(e.target.checked)} />
                提升前要求健康检查通过
              </label>
              <Btn size="sm" busy={promote.running} disabled={!canPromote}
                   title={canPromote ? undefined : "需要 10 分钟内且全部通过的验证结果"}
                   onClick={() => confirm({
                     title: "提升金丝雀为当前部署？",
                     body: <p className="muted">会把当前环指针移到该隔离验证过的快照{requireHealthy ? "，并要求健康检查通过" : ""}。</p>,
                     confirmText: "提升",
                     run: async () => { await promote.run(); },
                   })}>提升金丝雀</Btn>
            </div>
            <p className="muted small">提升只移动部署指针；验证过期或未通过时按钮保持禁用。</p>
          </Card>

          <Card title="切一个发布" sub="把当前已编译并通过内核接受的 schema 固化为不可变快照；可以写一句变更说明。">
            <div className="field-inline">
              <Field label="变更说明">
                <TextInput value={note} placeholder="例如：新增收费站对象与放行能力"
                           onChange={e => setNote(e.target.value)} />
              </Field>
              <div className="btn-row flat">
                <Btn busy={cut.running} onClick={() => confirm({
                  title: "切出新的发布快照？",
                  body: <p className="muted">当前已发布结构会成为一个不可变发布；不会自动部署。</p>,
                  confirmText: "切发布",
                  run: async () => { await cut.run(); },
                })}><CloudArrowUp size={14} /> 切发布</Btn>
              </div>
            </div>
            <p className="field-hint">前提：本体已发布、能力已编译，运行时里存在 schema payload。</p>
          </Card>

          <Card title="发布历史" sub="部署与金丝雀只移动指针，回滚不重新编译。" aside={<Tag kind="plain">{r?.releases.length ?? 0} 个</Tag>}>
            {rel.loading && <Loading />}
            {!rel.loading && !r?.releases.length && <Empty title="还没有发布" hint="先在上方切一个发布。" />}
            {!!r?.releases.length && (
              <div className="tablewrap auto">
                <table>
                  <thead>
                    <tr><th>发布</th><th>结构</th><th>指纹</th><th>能力</th><th>说明</th><th>时间</th><th>状态</th><th>操作</th></tr>
                  </thead>
                  <tbody>
                    {r.releases.slice().reverse().map(relRow => {
                      const isCurrent = relRow.id === r.deployment?.release_id;
                      const isCanary = relRow.id === r.canary?.release_id && !r.canary?.promoted;
                      return (
                        <tr key={relRow.id}>
                          <td className="mono nowrap">{relRow.id}</td>
                          <td className="mono">{relRow.schema_version ? `v${relRow.schema_version}` : "–"}</td>
                          <td className="mono">{relRow.fingerprint}</td>
                          <td className="mono">{relRow.capability_ids.length}</td>
                          <td className="wrap">{relRow.note || "–"}</td>
                          <td className="mono nowrap">{fmtTime(relRow.created_at)}</td>
                          <td className="nowrap">
                            {isCurrent ? <Tag kind="ok">当前</Tag> : isCanary ? <Tag kind="warn">隔离验证环</Tag> : <span className="muted">历史</span>}
                          </td>
                          <td className="nowrap">
                            {!isCurrent && (
                              <button type="button" className="link" onClick={() => confirm({
                                title: "部署这个发布？",
                                body: <p className="muted">内核接受后才会移动部署指针；失败会保持现状。</p>,
                                confirmText: "部署",
                                run: async () => { await deploy.run(relRow.id); },
                              })}>部署</button>
                            )}
                            {!isCanary && (
                              <button type="button" className="link act-gap"
                                      onClick={() => confirm({
                                        title: "部署到隔离验证环？",
                                        body: <p className="muted">会安装隔离 shadow 运行时（不接真实流量），当前环不动。</p>,
                                        confirmText: "部署到隔离环",
                                        run: async () => { await canaryDeploy.run(relRow.id); },
                                      })}>隔离验证</button>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </>
      )}

      {tab === "events" && <EventsTab />}

      {tab === "health" && <HealthTab objectTypes={objectTypes} />}
    </>
  );
}

function IndicatorGrid({ indicators }: { indicators: Record<string, number | null> }) {
  const keys = Object.keys(INDICATOR_LABEL).filter(k => k in indicators);
  return (
    <div className="ind-grid">
      {keys.map(k => {
        const raw = indicators[k];
        const meta = INDICATOR_META[k];
        const bad = meta?.inverse && (raw ?? 0) > 0;
        return (
          <div className="ind-cell" key={k}>
            <span className="ind-label">{INDICATOR_LABEL[k]}</span>
            <span className={"ind-value" + (bad ? " bad" : "")}>{fmtNum(raw)}{meta?.unit ? <small>{meta.unit}</small> : null}</span>
          </div>
        );
      })}
    </div>
  );
}

function SloList({ slo }: { slo: SloReport }) {
  const order = ["success_rate", "unknown_rate", "pending_events", "projection_age_seconds"];
  return (
    <div className="slo-list">
      {order.filter(k => slo.indicators[k]).map(k => {
        const it = slo.indicators[k];
        const status = it.status || ((it.samples ?? 0) > 0 ? (it.ok ? "ok" : "breach") : "no_data");
        const isRate = k === "success_rate" || k === "unknown_rate";
        const value = status === "no_data"
          ? "–"
          : isRate ? fmtPct(it.value) : k === "projection_age_seconds" ? fmtSeconds(it.value) : fmtNum(it.value);
        const target = isRate ? fmtPct(it.target) : k === "projection_age_seconds" ? fmtSeconds(it.target) : fmtNum(it.target);
        return (
          <div className="slo-row" key={k}>
            <span className="slo-name">{SLO_LABEL[k] || k}</span>
            <span className="slo-val">{value}</span>
            <span className="slo-target">
              目标 {target} · {it.samples ?? 0} 样本
              {it.min_samples ? ` / 最少 ${it.min_samples}` : ""}
            </span>
            <StatusBadge status={status} />
          </div>
        );
      })}
    </div>
  );
}

function jobClock(value: string | number | null | undefined): string {
  if (typeof value === "string" && value) {
    try { return formatLocal(value); } catch { return value; }
  }
  return "服务端未返回";
}

function JobRow({ task }: { task: SchedulerTask }) {
  return (
    <tr>
      <td className="mono">{task.name}</td>
      <td>{task.interval_seconds ? fmtSeconds(task.interval_seconds) : "–"}</td>
      <td>{fmtNum(task.runs)} / {fmtNum(task.failures)}</td>
      <td className="mono small">{jobClock(task.last_run_at)}</td>
      <td className="mono small">{jobClock(task.next_run_at)}</td>
      <td>{task.last_error
        ? <ValueView value={task.last_error} compact limit={40} />
        : <span className="muted">无</span>}</td>
    </tr>
  );
}

function RecentEvents() {
  const { wid, go } = usePlatform();
  const ev = useLoad<DomainEvent[]>(
    () => (wid ? api.events(wid, 5) : Promise.resolve([])), [wid]);
  if (ev.loading) return <Skeleton rows={3} />;
  if (!ev.data?.length) return <Empty title="还没有事件" hint="发布、同步、调用能力都会写事件。" />;
  return (
    <div>
      {ev.data.map(e => (
        <div className="event-row" key={e.id}>
          <span className="event-ts">{fmtRelative(e.ts)}</span>
          <span className="event-type">{e.type}</span>
          <span><StatusBadge status={deliveryStatus(e)} /></span>
        </div>
      ))}
      <div className="btn-row">
        <Btn size="sm" variant="subtle" onClick={() => go("operations", { tab: "events" })}>查看全部</Btn>
      </div>
    </div>
  );
}

function EventsTab() {
  const { wid, toast, confirm } = usePlatform();
  const [type, setType] = useState("");
  const [limit, setLimit] = useState(100);
  const [detail, setDetail] = useState<DomainEvent | null>(null);
  const [last, setLast] = useState<{
    dispatched: number; failed: number; skipped?: number;
    dead_lettered?: number; unrouted?: number; errors: string[];
  } | null>(null);

  const ev = useLoad<DomainEvent[]>(
    () => (wid ? api.events(wid, limit, type.trim()) : Promise.resolve([])), [wid, limit, type]);
  const dead = useLoad<DomainEvent[]>(
    () => (wid ? api.deadLetters(wid) : Promise.resolve([])), [wid]);
  const dispatch = useAction(async () => {
    if (!wid) return;
    const res = await api.dispatchEvents(wid);
    setLast(res);
    const skipped = res.skipped ?? 0;
    toast(
      `投递完成：真实投递 ${res.dispatched}，失败 ${res.failed}，未路由 ${skipped}`,
      res.failed ? "warn" : skipped ? "warn" : "ok",
    );
    ev.reload(); dead.reload();
  }, [wid]);

  const pending = useMemo(
    () => (ev.data || []).filter(e => ["pending", "retrying"].includes(deliveryStatus(e))).length,
    [ev.data]);
  const unrouted = useMemo(
    () => (ev.data || []).filter(e => deliveryStatus(e) === "unrouted").length, [ev.data]);
  const deadCount = useMemo(
    () => (ev.data || []).filter(e => deliveryStatus(e) === "dead").length, [ev.data]);

  return (
    <>
      <Card title="事件流" sub="outbox 记录已经发生的事实；没有订阅者时标为未路由，不等于已投递。"
            aside={<>
              <Tag kind="plain">{ev.data?.length ?? 0} 条</Tag>
              {pending > 0 && <Tag kind="warn">{pending} 条待投递</Tag>}
              {unrouted > 0 && <Tag kind="warn">{unrouted} 条未路由</Tag>}
              {deadCount > 0 && <Tag kind="err">{deadCount} 条死信</Tag>}
              <Btn size="sm" busy={dispatch.running} onClick={() => confirm({
                title: "投递所有待处理事件？",
                body: <p className="muted">按订阅者逐个投递；没有订阅者的事件会记为未路由，失败的事件会保留并记录最后一次错误。</p>,
                confirmText: "投递",
                run: async () => { await dispatch.run(); },
              })}>
                <PaperPlaneTilt size={14} /> 投递待处理
              </Btn>
            </>}>
        <div className="field-inline">
          <Field label="事件类型过滤">
            <TextInput value={type} placeholder="例如 ontology.published（留空看全部）"
                       onChange={e => setType(e.target.value)} />
          </Field>
          <Field label="条数">
            <Select value={String(limit)} onChange={e => setLimit(Number(e.target.value))}>
              {[50, 100, 200, 500].map(n => <option key={n} value={n}>{n} 条</option>)}
            </Select>
          </Field>
          <div className="btn-row flat">
            <Btn size="sm" variant="subtle" onClick={ev.reload} busy={ev.loading}>刷新</Btn>
          </div>
        </div>

        {last && (
          <div className={"banner " + ((last.failed || (last.skipped ?? 0)) ? "warn" : "info")}>
            本轮投递：真实投递 {last.dispatched}，失败 {last.failed}，未路由 {last.skipped ?? 0}
            {last.dead_lettered ? `，新增死信 ${last.dead_lettered}` : ""}
            {!!last.errors.length && <ul className="doc-list">{last.errors.slice(0, 4).map((x, i) => <li key={i}>{x}</li>)}</ul>}
          </div>
        )}
        {ev.error && <ErrorNote text={ev.error} onRetry={ev.reload} />}
        {ev.loading && <Loading />}
        {!ev.loading && !ev.data?.length && <Empty title="没有匹配的事件" hint="清空类型过滤，或先去发布本体 / 同步数据。" />}
        {!!ev.data?.length && (
          <div className="tablewrap auto">
            <table>
              <thead><tr><th>时间</th><th>类型</th><th>聚合</th><th>执行</th><th>投递</th><th>消费者</th><th>尝试</th><th className="wrap">最后错误</th><th>操作</th></tr></thead>
              <tbody>
                {ev.data.slice().reverse().map(e => (
                  <tr key={e.id}>
                    <td className="mono nowrap">{fmtTime(e.ts)}</td>
                    <td className="mono">{e.type}</td>
                    <td className="wrap">{e.aggregate.kind}/{e.aggregate.id}</td>
                    <td className="mono nowrap">{e.execution_id || "–"}</td>
                    <td className="nowrap"><StatusBadge status={deliveryStatus(e)} hint={e.last_error || undefined} /></td>
                    <td className="mono">{e.consumer || (deliveryStatus(e) === "unrouted" ? "未配置消费者" : "–")}</td>
                    <td className="mono">{e.attempts ?? 0}</td>
                    <td className="wrap">{e.last_error || "–"}</td>
                    <td className="nowrap"><button type="button" className="link" onClick={() => setDetail(e)}>详情</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="死信" sub="超过最大尝试次数或明确失败后保留原文，等待人工处理；不会假装已投递。"
            aside={<Tag kind={dead.data?.length ? "err" : "plain"}>{dead.data?.length ?? 0} 条</Tag>}>
        {dead.loading && <Loading />}
        {dead.error && <ErrorNote text={dead.error} onRetry={dead.reload} />}
        {!dead.loading && !dead.data?.length && <Empty title="没有死信" hint="投递失败且放弃重试的事件才会进入这里。" />}
        {!!dead.data?.length && (
          <div className="tablewrap auto">
            <table>
              <thead><tr><th>时间</th><th>类型</th><th>尝试</th><th className="wrap">最后错误</th><th>操作</th></tr></thead>
              <tbody>
                {dead.data.map(e => (
                  <tr key={e.id}>
                    <td className="mono nowrap">{fmtTime(e.ts)}</td>
                    <td className="mono">{e.type}</td>
                    <td className="mono">{e.attempts}</td>
                    <td className="wrap">{e.last_error || "–"}</td>
                    <td className="nowrap"><button type="button" className="link" onClick={() => setDetail(e)}>详情</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {detail && (
        <Drawer title="事件详情" sub={detail.type} onClose={() => setDetail(null)}>
          <dl className="kv">
            <div className="kv-pair"><dt>事件 ID</dt><dd className="mono">{detail.id}</dd></div>
            <div className="kv-pair"><dt>发生时间</dt><dd className="mono">{fmtTime(detail.ts)}</dd></div>
            <div className="kv-pair"><dt>操作者</dt><dd>{detail.actor}</dd></div>
            <div className="kv-pair"><dt>聚合</dt><dd className="mono">{detail.aggregate.kind}/{detail.aggregate.id} @ {detail.aggregate.version ?? "–"}</dd></div>
            <div className="kv-pair"><dt>执行</dt><dd className="mono">{detail.execution_id || "–"}</dd></div>
            <div className="kv-pair"><dt>投递状态</dt><dd><StatusBadge status={deliveryStatus(detail)} /> {detail.dispatched_at && <span className="mono small">{fmtTime(detail.dispatched_at)}</span>}</dd></div>
            <div className="kv-pair"><dt>消费者</dt><dd className="mono">{detail.consumer || (deliveryStatus(detail) === "unrouted" ? "无订阅者" : "服务端未返回")}</dd></div>
            <div className="kv-pair"><dt>下次重试</dt><dd className="mono">{detail.next_attempt_at ? fmtTime(detail.next_attempt_at) : "–"}</dd></div>
            <div className="kv-pair"><dt>死信时间</dt><dd className="mono">{detail.dead_lettered_at ? fmtTime(detail.dead_lettered_at) : "–"}</dd></div>
            <div className="kv-pair"><dt>尝试次数</dt><dd className="mono">{detail.attempts}</dd></div>
            <div className="kv-pair"><dt>最后错误</dt><dd className="mono">{detail.last_error || "–"}</dd></div>
          </dl>
          <div className="h4-row"><h3 className="h4">payload</h3></div>
          <ValueView value={detail.payload} />
        </Drawer>
      )}
    </>
  );
}

function HealthTab({ objectTypes }: { objectTypes: string[] }) {
  const { wid, toast, confirm } = usePlatform();
  const [objectType, setObjectType] = useState("");
  const [sample, setSample] = useState("");
  const [verify, setVerify] = useState<VerifyResult | null>(null);
  const [reconcile, setReconcile] = useState<ReconcileResult | null>(null);
  const [proposals, setProposals] = useState<ControllerProposal[] | null>(null);
  const [cycle, setCycle] = useState<ControllerCycle | null>(null);

  const runVerify = useAction(async () => {
    if (!wid) return;
    const n = sample.trim() ? Number(sample) : undefined;
    if (n !== undefined && (!Number.isFinite(n) || n <= 0)) { toast("样本数需要是正整数", "err"); return; }
    const res = await api.verify(wid, objectType || undefined, n);
    setVerify(res);
    toast(res.ok ? `校验通过，检查 ${res.checked} 个对象` : `发现 ${res.problems.length} 个问题`, res.ok ? "ok" : "warn");
  }, [wid, objectType, sample]);

  const runReconcile = useAction(async () => {
    if (!wid) return;
    const res = await api.reconcile(wid);
    setReconcile(res);
    toast(res.ok ? "投影与源数据一致" : `发现 ${res.findings.length} 条漂移`, res.ok ? "ok" : "warn");
  }, [wid]);

  const runPropose = useAction(async () => {
    if (!wid) return;
    const res = await api.controllerPropose(wid);
    setProposals(res.proposals);
    if (!res.ok || !res.proposals.length) toast("当前没有需要控制的偏差", "ok");
  }, [wid]);

  const runAct = useAction(async () => {
    if (!wid) return;
    const res = await api.controllerAct(wid, true);
    setCycle(res);
    setProposals(res.accepted);
    const converged = !!(res.converged ?? res.verified);
    toast(
      `接受 ${res.cycle.accepted} 条建议，应用 ${res.applied.length} 条；重新观察后${converged ? "已收敛" : "仍有偏差"}`,
      converged ? "ok" : "warn",
    );
  }, [wid]);

  return (
    <>
      <div className="layout cols-2">
        <Card title="结构校验" sub="检查属性类型、身份重复与必填缺失；可以限定对象类型和样本量。">
          <div className="field-inline">
            <Field label="对象类型">
              <Select value={objectType} onChange={e => setObjectType(e.target.value)}>
                <option value="">全部类型</option>
                {objectTypes.map(t => <option key={t} value={t}>{t}</option>)}
              </Select>
            </Field>
            <Field label="样本量" hint="留空表示全量">
              <TextInput value={sample} placeholder="例如 500" inputMode="numeric"
                         onChange={e => setSample(e.target.value)} />
            </Field>
          </div>
          <div className="btn-row">
            <Btn busy={runVerify.running} onClick={() => void runVerify.run()}>
              <CheckCircle size={14} /> 开始校验
            </Btn>
            <span className="muted small">只读操作，不会修改对象。</span>
          </div>
          {verify && (
            <div className="stage-result">
              <div className="result-head">
                <h3>结果</h3>
                <Tag kind={verify.ok ? "ok" : "err"}>{verify.ok ? "通过" : "有问题"}</Tag>
                <span className="muted small">检查 {verify.checked} 个对象{verify.sampled ? ` · 样本 ${verify.sample_size}` : " · 全量"}</span>
              </div>
              {verify.problems.length === 0 ? (
                <p className="muted">没有发现结构问题。</p>
              ) : (
                <div className="tablewrap auto">
                  <table>
                    <thead><tr><th>类型</th><th>对象</th><th>问题</th><th>细节</th></tr></thead>
                    <tbody>
                      {verify.problems.map((p, i) => (
                        <tr key={i}>
                          <td className="mono nowrap">{p.object_type}</td>
                          <td className="mono">{p.id || "–"}</td>
                          <td className="nowrap"><Tag kind="err">{p.kind}</Tag></td>
                          <td className="wrap">{p.property || p.duplicate_of || p.error || (p.identity ? p.identity.join(", ") : "–")}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </Card>

        <Card title="投影对账" sub="按投影归属对比源与运行时：additive 只核对本投影拥有的对象，authoritative 才把多余对象报为 orphan。只给证据，不修复。">
          <p className="muted">建议在同步之后跑一次：如果运行时缺对象，通常是映射条件过滤掉了，或投影还没跑完。</p>
          <div className="btn-row">
            <Btn variant="subtle" busy={runReconcile.running} onClick={() => void runReconcile.run()}>
              <WarningCircle size={14} /> 开始对账
            </Btn>
          </div>
          {reconcile && (
            <div className="stage-result">
              <div className="result-head">
                <h3>对账结果</h3>
                <Tag kind={reconcile.ok ? "ok" : "warn"}>{reconcile.ok ? "一致" : `${reconcile.findings.length} 条漂移`}</Tag>
              </div>
              <dl className="kv">
                <div className="kv-pair"><dt>模式</dt><dd className="mono">{reconcile.mode || Array.from(new Set(reconcile.findings.map(f => f.ownership).filter(Boolean))).join(" / ") || "接口未返回"}</dd></div>
                <div className="kv-pair"><dt>范围</dt><dd className="mono">{reconcile.scope || "接口未返回"}</dd></div>
                <div className="kv-pair"><dt>检查时间</dt><dd className="mono">{reconcile.checked_at ? fmtTime(reconcile.checked_at) : "接口未返回；这是本次请求的结果"}</dd></div>
              </dl>
              {reconcile.ok && <p className="muted">源侧与运行时侧没有发现缺失实例。</p>}
              {!reconcile.ok && (
                <>
                  {reconcile.findings.map((f, i) => (
                    <div className="banner warn" key={i}>
                      <b>{f.kind}</b>
                      {f.ownership && <span className="mono"> · 归属 {f.ownership}</span>}
                      {f.projection_id && <span className="mono"> · {f.projection_id}</span>}
                      {f.object_type && <span className="mono"> · {f.object_type}</span>}
                      {!!f.missing_in_runtime?.length && <div>运行时缺失：{f.missing_in_runtime.join("、")}</div>}
                      {!!f.missing_in_source?.length && <div>源侧缺失：{f.missing_in_source.join("、")}</div>}
                    </div>
                  ))}
                  <p className="muted small">可在「数据」页重跑对应投影，然后再对账一次。</p>
                </>
              )}
            </div>
          )}
        </Card>
      </div>

      <Card title="控制器" sub="先提建议，再接受执行。建议可解释，执行有边界，执行后立刻用对账验证。">
        <div className="btn-row">
          <Btn variant="subtle" busy={runPropose.running} onClick={() => void runPropose.run()}>
            只提建议
          </Btn>
          <Btn busy={runAct.running} disabled={!proposals?.length || !!cycle?.stopped} onClick={() => confirm({
            title: "接受建议并执行？",
            body: <p className="muted">会对可执行的建议调用对应投影重跑，并在执行后重新对账；没有重新观察就不算收敛。</p>,
            confirmText: "接受并执行",
            run: async () => { await runAct.run(); },
          })}>
            <PaperPlaneTilt size={14} /> 接受并执行
          </Btn>
        </div>

        {proposals && (
          <div className="stage-result">
            <div className="result-head">
              <h3>控制建议</h3>
              <Tag kind={proposals.length ? "warn" : "ok"}>{proposals.length ? `${proposals.length} 条` : "无偏差"}</Tag>
            </div>
            {proposals.length === 0 && <p className="muted">当前没有需要执行的修复建议。</p>}
            {proposals.map((p, i) => (
              <div className="todo-item" key={i}>
                <div className="todo-main">
                  <div className="todo-title mono">{p.action}{p.projection_id ? ` · ${p.projection_id}` : ""}</div>
                  <div className="todo-detail">{p.reason || "没有给出原因"}</div>
                </div>
              </div>
            ))}
          </div>
        )}

        {cycle && (
          <div className="stage-result">
            <div className="result-head">
              <h3>执行结果</h3>
              <Tag kind={(cycle.converged ?? cycle.verified) ? "ok" : "warn"}>{(cycle.converged ?? cycle.verified) ? "重新观察后收敛" : "重新观察后仍有偏差"}</Tag>
              {cycle.stopped && <Tag kind="err">自动修复已停止</Tag>}
            </div>
            <dl className="kv">
              <div className="kv-pair"><dt>提议 / 接受</dt><dd className="mono">{cycle.cycle.proposed} / {cycle.cycle.accepted}</dd></div>
              <div className="kv-pair"><dt>被拒</dt><dd className="mono">{cycle.cycle.rejected}</dd></div>
              <div className="kv-pair"><dt>已应用</dt><dd className="mono">{cycle.applied.length} 条</dd></div>
              <div className="kv-pair"><dt>连续未收敛</dt><dd className="mono">{cycle.consecutive_failures ?? cycle.cycle.consecutive_failures ?? "未返回"}{cycle.stopped ? " · 已停止" : ""}</dd></div>
            </dl>
            {(cycle.stopped || cycle.escalated) && (
              <div className="banner warn">
                {cycle.reason || "自动修复没有收敛，已升级为人工处理；请到待办中查看并处理，控制器不会继续重复提议。"}
              </div>
            )}
            {!!cycle.applied.length && (
              <ul className="doc-list">
                {cycle.applied.map((a, i) => (
                  <li key={i}><span className="mono">{a.action} {a.projection_id}</span>{a.run ? ` · ${JSON.stringify(a.run)}` : ""}</li>
                ))}
              </ul>
            )}
            {!cycle.verification.ok && (
              <div className="banner warn">
                验证仍有 {cycle.verification.findings.length} 条漂移，可回到「数据」页检查映射条件或数据源连通性。
              </div>
            )}
          </div>
        )}
      </Card>

      <Card title="这些检查在做什么" sub="控制论里的观测与纠偏，在界面里是三个可独立执行的动作。">
        <p><b>校验</b>看的是结构：属性类型是否匹配、身份是否重复、必填是否缺失。</p>
        <p><b>对账</b>看的是过程：映射源里有的实例，运行时里是否都有。</p>
        <p><b>控制器</b>把观测到的偏差转成可执行建议，只在被接受后才动作；动作后再对账一次，形成闭环。</p>
      </Card>
    </>
  );
}
