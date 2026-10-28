/** 状态语义的唯一映射（P0-1 / P2-15）。
 *
 *  规则只有一条：只有 `succeeded` 是成功。`unknown` 既不是成功也不是失败，
 *  `no_data` / `insufficient` 不是绿色，`unrouted` 不是已投递。
 *  页面里不允许再手写「成功」「已投递」「达标」之类的判断。
 */

export type TagTone = "" | "ok" | "warn" | "err" | "plain";

export interface StatusMeta {
  label: string;
  /** 只有真实成功才允许 "ok"（绿色）。 */
  tone: TagTone;
  /** 给 title / 空态用的解释，不替代标签。 */
  hint?: string;
  /** 便捷判断：仅 succeeded 为 true。 */
  success?: boolean;
}

export const STATUS_META: Record<string, StatusMeta> = {
  /* ---- 执行 ---- */
  requested: { label: "已请求", tone: "plain", hint: "请求已记录，尚未认领" },
  claimed: { label: "已认领", tone: "plain", hint: "执行权已原子认领" },
  running: { label: "执行中", tone: "plain", hint: "执行尚未结束，结果未知" },
  succeeded: { label: "成功", tone: "ok", success: true, hint: "唯一可判定为成功的状态" },
  failed: { label: "失败", tone: "err", hint: "确定性失败，世界状态未按预期改变" },
  refused: { label: "已拒绝", tone: "err", hint: "前置条件拒绝执行" },
  unknown: {
    label: "结果未知",
    tone: "warn",
    hint: "效果可能已发生也可能没有；不会自动重试，需人工查证",
  },
  contract_failed: {
    label: "契约未通过",
    tone: "err",
    hint: "运行时返回成功但后置条件不成立，按失败处理",
  },
  retryable: { label: "可重试（未知）", tone: "warn", hint: "仅表示可人工解析，不表示自动重试" },
  resolved_applied: { label: "已查证生效", tone: "plain", hint: "人工/外部证据确认效果已发生" },
  resolved_not_applied: { label: "已查证未生效", tone: "warn", hint: "人工/外部证据确认效果未发生" },

  /* ---- 观测 / SLO ---- */
  ok: { label: "达标", tone: "ok", hint: "有足够样本且未越线" },
  breach: { label: "越线", tone: "err", hint: "有样本且超出目标" },
  no_data: { label: "无数据", tone: "plain", hint: "窗口内没有样本，不能显示为绿色" },
  insufficient: { label: "样本不足", tone: "plain", hint: "有样本但少于最小样本数，不能判定达标" },
  not_applicable: { label: "不适用", tone: "plain", hint: "该指标在当前配置下不参与判定" },
  stale: { label: "检查已过期", tone: "warn", hint: "最近一次检查超出新鲜窗口" },

  /* ---- 投递 ---- */
  pending: { label: "待投递", tone: "plain" },
  retrying: { label: "重试中", tone: "warn" },
  delivered: { label: "已投递", tone: "ok", hint: "有真实消费者确认消费" },
  unrouted: { label: "未路由", tone: "warn", hint: "没有消费者，事件没有投递出去" },
  dead: { label: "死信", tone: "err", hint: "超过最大尝试次数，保留原文等待人工重放" },

  /* ---- 发布 / 部署 ---- */
  active: { label: "生效中", tone: "ok", hint: "deployment/current 指向该发布" },
  superseded: { label: "已被取代", tone: "plain" },
  staged: { label: "已暂存", tone: "plain" },
  validated: { label: "验证通过", tone: "ok" },
  promoted: { label: "已提升", tone: "ok" },
  rejected: { label: "已拒绝", tone: "err" },
  expired: { label: "已过期", tone: "warn" },

  /* ---- 待办 ---- */
  open: { label: "待处理", tone: "warn" },
  resolved: { label: "已处理", tone: "plain" },
  overridden: { label: "人工关闭", tone: "plain" },
};

const FALLBACK: StatusMeta = { label: "未知状态", tone: "plain" };

export function statusMeta(status: string | null | undefined): StatusMeta {
  if (!status) return FALLBACK;
  const key = String(status).trim().toLowerCase();
  return STATUS_META[key] || { label: String(status), tone: "plain" };
}

/** 唯一成功判定：status === "succeeded"。`ok` 布尔不能单独当成功。 */
export function isSuccess(status: string | null | undefined): boolean {
  return status === "succeeded";
}

export function isUnknown(status: string | null | undefined): boolean {
  return status === "unknown";
}

export function toneFor(status: string | null | undefined): TagTone {
  return statusMeta(status).tone;
}

/** contract_failed 优先于普通 failed 显示，两者都不是成功。 */
export function executionStatus(result: {
  status?: string | null;
  ok?: boolean;
  contract_failed?: boolean;
}): string {
  if (result.contract_failed) return "contract_failed";
  if (result.status) return result.status;
  return result.ok ? "succeeded" : "failed";
}

/** 汇总 SLO 显示状态：无样本 / 样本不足绝不显示绿色。 */
export function aggregateSloStatus(slo: {
  ok?: boolean;
  blocked?: string[];
  breached?: string[];
  indicators?: Record<string, { status?: string }>;
} | null | undefined): string {
  if (!slo) return "no_data";
  const indicators = slo.indicators || {};
  const items = Object.values(indicators);
  if (!items.length) return "no_data";
  const primary = indicators.success_rate;
  if (!primary || primary.status === "no_data") return "no_data";
  if (primary.status === "insufficient") return "insufficient";
  if (items.some(i => i.status === "breach")) return "breach";
  if (items.some(i => i.status === "no_data" || i.status === "insufficient")) return "insufficient";
  if (slo.ok && primary.status === "ok") return "ok";
  return "insufficient";
}

/** 事件投递状态：兼容新字段 delivery_status 与旧字段 delivery/dispatched_at。 */
export function deliveryStatus(event: {
  delivery_status?: string | null;
  delivery?: string | null;
  dead_lettered_at?: string | null;
  dispatched_at?: string | null;
  attempts?: number | null;
}): string {
  if (event.delivery_status) return event.delivery_status;
  if (event.dead_lettered_at) return "dead";
  if (event.delivery === "no_subscribers") return "unrouted";
  if (event.delivery === "delivered") return "delivered";
  if (event.dispatched_at) return "delivered";
  if ((event.attempts ?? 0) > 0) return "retrying";
  return "pending";
}

/** 部署状态：pending/failed 时不得显示“已切换”。 */
export function deploymentStatus(status: string | null | undefined): string {
  if (!status) return "unknown";
  return String(status).toLowerCase();
}

/** 金丝雀检查的新鲜窗口：超过这个时间提升按钮必须禁用。 */
export const CHECK_FRESH_SECONDS = 600;

/** 金丝雀是隔离 shadow 验证环，不是真实流量；文案只在这里定义一次。 */
export const CANARY_RING_LABEL = "隔离验证环（不接真实流量）";

export function isCheckFresh(checkedAt: string | null | undefined, now = Date.now()): boolean {
  if (!checkedAt) return false;
  const ts = new Date(checkedAt).getTime();
  if (Number.isNaN(ts)) return false;
  const age = (now - ts) / 1000;
  return age >= 0 && age <= CHECK_FRESH_SECONDS;
}

export function canaryCheckState(check: {
  checked_at?: string | null;
  ok?: boolean;
  checks?: { ok?: boolean }[];
} | null | undefined, now = Date.now()): { status: string; fresh: boolean; passed: boolean } {
  if (!check || !check.checked_at) return { status: "no_data", fresh: false, passed: false };
  const fresh = isCheckFresh(check.checked_at, now);
  const passed = check.ok === true && (check.checks || []).every(c => c.ok !== false);
  return { status: !fresh ? "stale" : passed ? "validated" : "failed", fresh, passed };
}
