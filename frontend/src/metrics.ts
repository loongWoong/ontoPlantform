/** 指标与观测的展示词表：后端 indicator 名到业务中文的唯一映射。
 *  页面只消费这里，避免 Operations / Workbench 各写一套翻译。 */
import { formatLocal } from "./lib/time";

export const INDICATOR_LABEL: Record<string, string> = {
  objects_total: "对象实例",
  relations_total: "关系实例",
  schema_version: "结构版本",
  releases_total: "发布次数",
  capabilities_total: "能力",
  executions_total: "动作执行",
  executions_succeeded: "执行成功",
  executions_failed: "执行失败",
  executions_unknown: "结果未知",
  executions_all_time: "历史执行总数",
  events_pending: "待投递事件",
  events_unrouted: "未路由事件",
  events_dead_lettered: "死信事件",
  projections_total: "映射",
  changes_total: "状态变更",
  controller_proposals_total: "控制建议",
  controller_proposals_rejected: "被拒建议",
  schema_fingerprint_mismatches: "指纹不一致",
};

export const SLO_LABEL: Record<string, string> = {
  success_rate: "动作成功率",
  unknown_rate: "结果未知率",
  pending_events: "事件积压",
  projection_age_seconds: "投影新鲜度",
};

export const SLO_TARGET_LABEL: Record<string, string> = {
  success_rate: "成功率下限",
  unknown_rate: "未知率上限",
  freshness_seconds: "新鲜度上限（秒）",
  pending_events: "积压上限",
};

/** 指标单位与语义方向；`inverse` 表示越小越好。 */
export const INDICATOR_META: Record<string, { unit?: string; inverse?: boolean }> = {
  executions_failed: { inverse: true },
  executions_unknown: { inverse: true },
  events_pending: { inverse: true },
  controller_proposals_rejected: { inverse: true },
  schema_fingerprint_mismatches: { inverse: true },
  projection_age_seconds: { unit: "秒", inverse: true },
};

export function fmtNum(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "–";
  if (Math.abs(v) >= 10000) return `${(v / 1000).toFixed(1)}k`;
  if (Number.isInteger(v)) return String(v);
  return v.toFixed(2);
}

export function fmtPct(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "–";
  return `${(v * 100).toFixed(1)}%`;
}

export function fmtSeconds(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "–";
  if (v < 60) return `${Math.round(v)} 秒`;
  if (v < 3600) return `${Math.round(v / 60)} 分`;
  return `${(v / 3600).toFixed(1)} 小时`;
}

export function fmtTime(ts: string | null | undefined): string {
  return formatLocal(ts);
}

export function fmtRelative(ts: string | null | undefined): string {
  if (!ts) return "–";
  const t = new Date(ts).getTime();
  if (Number.isNaN(t)) return formatLocal(ts);
  const diff = Math.round((Date.now() - t) / 1000);
  if (diff < 5) return "刚刚";
  if (diff < 60) return `${diff} 秒前`;
  if (diff < 3600) return `${Math.round(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.round(diff / 3600)} 小时前`;
  return formatLocal(ts);
}
