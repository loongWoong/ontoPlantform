/** 展示值判断的纯函数；ValueView 与单元测试共用，避免再出现 String(v) 得到
 *  `[object Object]`（P2-13）。 */

export type ValueKind = "null" | "string" | "number" | "boolean" | "array" | "object" | "unknown";

export function valueKind(value: unknown): ValueKind {
  if (value === null || value === undefined) return "null";
  if (Array.isArray(value)) return "array";
  switch (typeof value) {
    case "string": return "string";
    case "number": return "number";
    case "boolean": return "boolean";
    case "object": return "object";
    default: return "unknown";
  }
}

/** 时间字段：后端约定 `*_at` 与 `ts`。 */
export function isTimeField(field: string | null | undefined): boolean {
  if (!field) return false;
  const name = field.trim().toLowerCase();
  return name === "ts" || name === "time" || name === "timestamp" || name.endsWith("_at");
}

/** 一行摘要：对象/数组用 JSON，绝不用 String(v)。 */
export function valuePreview(value: unknown, limit = 96): string {
  const kind = valueKind(value);
  if (kind === "null") return "–";
  if (kind === "string") {
    const text = value as string;
    return text.length > limit ? `${text.slice(0, limit)}…` : text;
  }
  if (kind === "number" || kind === "boolean") return String(value);
  try {
    const json = JSON.stringify(value);
    if (json === undefined) return String(value);
    return json.length > limit ? `${json.slice(0, limit)}…` : json;
  } catch {
    return "[无法序列化的值]";
  }
}

export function valueSummary(value: unknown): string {
  const kind = valueKind(value);
  if (kind === "array") return `数组 · ${(value as unknown[]).length} 项`;
  if (kind === "object") return `对象 · ${Object.keys(value as Record<string, unknown>).length} 个键`;
  if (kind === "string") {
    const text = value as string;
    return `文本 · ${text.length} 字`;
  }
  return kind;
}

export function valueJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2) ?? String(value);
  } catch {
    return "[无法序列化的值]";
  }
}

/** 长文本 / 结构值是否值得折叠。 */
export function isExpandable(value: unknown, limit = 96): boolean {
  const kind = valueKind(value);
  if (kind === "array" || kind === "object") return true;
  if (kind === "string") return (value as string).length > limit;
  return false;
}
