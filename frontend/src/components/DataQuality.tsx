/** 数据质量：样本、窗口、检查时间、未解析执行、积压与死信。
 *  没有样本时明确写“无数据/样本不足”，不用 0 或绿色填充。 */
import { StatusBadge } from "./StatusBadge";
import { formatLocal, formatWindow } from "../lib/time";

export interface DataQualityFacts {
  samples?: number | null;
  min_samples?: number | null;
  status?: string | null;
  reason?: string | null;
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

export function DataQuality({ quality, windowSeconds, samples, minSamples, status, reason, compact = false }: {
  quality?: DataQualityFacts | null;
  windowSeconds?: number | null;
  samples?: number | null;
  minSamples?: number | null;
  status?: string | null;
  reason?: string | null;
  compact?: boolean;
}) {
  const q = quality || {};
  const sampleCount = samples ?? q.samples ?? null;
  const min = minSamples ?? q.min_samples ?? null;
  const effectiveState = status ?? q.status ?? ((sampleCount ?? 0) > 0 ? "ok" : "no_data");
  const hasSamples = (sampleCount ?? 0) > 0;
  const known = (v: number | null | undefined) => v !== null && v !== undefined;
  const missing = <span className="muted">未返回</span>;

  return (
    <div className={"data-quality" + (compact ? " compact" : "")}>
      <dl className="kv tight">
        <div className="kv-pair">
          <dt>评估窗口</dt>
          <dd>{formatWindow(windowSeconds)}</dd>
        </div>
        <div className="kv-pair">
          <dt>样本</dt>
          <dd>
            {hasSamples
              ? <>{sampleCount}{min ? <span className="muted"> / 最少 {min}</span> : null}</>
              : <span className="muted">无数据</span>}
            {" "}
            <StatusBadge status={effectiveState} />
          </dd>
        </div>
        <div className="kv-pair">
          <dt>最近检查</dt>
          <dd className="mono">
            {q.last_check_at ? formatLocal(q.last_check_at) : <span className="muted">还没有检查记录</span>}
          </dd>
        </div>
        <div className="kv-pair">
          <dt>未解析执行</dt>
          <dd>
            {known(q.unknown_executions) ? q.unknown_executions : missing}
            {known(q.unknown_executions) && (q.unknown_executions ?? 0) > 0 && <> <StatusBadge status="unknown" /></>}
          </dd>
        </div>
        <div className="kv-pair">
          <dt>开放待办</dt>
          <dd>{known(q.open_todos) ? q.open_todos : missing}</dd>
        </div>
        <div className="kv-pair">
          <dt>投影运行</dt>
          <dd>
            {known(q.projection_runs) ? `${q.projection_runs} 次` : missing}
            {(known(q.projection_failed) || known(q.projection_skipped)) && (
              <span className="muted"> · 失败 {known(q.projection_failed) ? q.projection_failed : "未返回"} · 跳过 {known(q.projection_skipped) ? q.projection_skipped : "未返回"}</span>
            )}
          </dd>
        </div>
        <div className="kv-pair">
          <dt>投递异常</dt>
          <dd>
            {known(q.events_unrouted) || known(q.events_dead_lettered)
              ? <>未路由 {known(q.events_unrouted) ? q.events_unrouted : "未返回"} · 死信 {known(q.events_dead_lettered) ? q.events_dead_lettered : "未返回"}</>
              : missing}
          </dd>
        </div>
      </dl>
      {(reason || (!hasSamples)) && (
        <p className="muted small">
          {reason || "窗口内没有样本，不能据此判定达标或健康。"}
        </p>
      )}
    </div>
  );
}
