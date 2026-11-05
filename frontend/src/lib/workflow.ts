import type {
  WorkflowCqExpected, WorkflowCqQuery, WorkflowStep,
} from "../api";

/** A small, dependency-free view-model layer so the panel stays readable and
 *  the display rules are unit-testable without rendering React. */

export function splitRefs(text: string): string[] {
  return text.split(/[,，\s]+/).map(part => part.trim()).filter(Boolean);
}

export function joinRefs(values: string[] | undefined): string {
  return (values || []).join(", ");
}

export function progress(steps: WorkflowStep[]): { done: number; total: number; percent: number } {
  const total = steps.length || 8;
  const done = steps.filter(step => step.status === "done").length;
  return { done, total, percent: total ? Math.round((done / total) * 100) : 0 };
}

export function stepStatusKind(status: string): "" | "ok" | "warn" | "err" | "plain" {
  if (status === "done") return "ok";
  if (status === "warn") return "warn";
  if (status === "current") return "plain";
  return "";
}

export function roundStatusLabel(status: string): string {
  return { active: "进行中", in_review: "待发布", accepted: "已发布" }[status] || status;
}

export function cqStatusLabel(status: string): string {
  return {
    open: "未验证", ready: "待验证", passed: "通过", failed: "未通过",
    error: "执行错误", manual: "人工确认",
  }[status] || status;
}

export function cqStatusKind(status: string): "" | "ok" | "warn" | "err" | "plain" {
  if (status === "passed") return "ok";
  if (status === "failed") return "err";
  if (status === "error") return "err";
  if (status === "manual") return "plain";
  return "";
}

export function expectedLabel(expected: WorkflowCqExpected | undefined): string {
  if (!expected) return "未设置";
  switch (expected.kind) {
    case "row_count": return `${expected.operator === "gte" ? "≥" : expected.operator === "lte" ? "≤" : "="} ${String(expected.value ?? 1)} 行`;
    case "rows_contain": return `包含 ${expected.rows?.length || 0} 行`;
    case "golden_rows": return `黄金结果 ${expected.rows?.length || 0} 行`;
    case "empty": return "结果为空";
    case "boolean": return expected.value ? "为真" : "为假";
    case "manual": return "人工判断";
    default: return expected.kind;
  }
}

export function queryLabel(query: WorkflowCqQuery | undefined): string {
  if (!query) return "未设置";
  if (query.relation) {
    return `关系 ${query.relation} · ${query.direction || "out"}${query.ref ? ` · ${query.ref}` : ""}`;
  }
  const filters = Object.entries(query.filters || {});
  const filterText = filters.length
    ? " · " + filters.map(([key, value]) => `${key}=${String(value)}`).join(" & ")
    : "";
  return `${query.object_type || "未选对象"}${filterText}`;
}

export function parseJsonObject(text: string): Record<string, unknown> {
  if (!text.trim()) return {};
  const value: unknown = JSON.parse(text);
  if (!value || Array.isArray(value) || typeof value !== "object") {
    throw new Error("需要 JSON 对象，例如 {\"plate\":\"沪A12345\"}");
  }
  return value as Record<string, unknown>;
}

export function parseJsonRows(text: string): Record<string, unknown>[] {
  if (!text.trim()) return [];
  const value: unknown = JSON.parse(text);
  if (!Array.isArray(value) || value.some(row => !row || typeof row !== "object" || Array.isArray(row))) {
    throw new Error("需要 JSON 数组，例如 [{\"plate\":\"沪A12345\"}]");
  }
  return value as Record<string, unknown>[];
}
