/** 对象 / 数组 / 时间 / 空值的统一渲染（P2-13）。
 *  表格里用 compact 一行摘要；详情里展开 JSON，保留键顺序。 */
import { formatLocal } from "../lib/time";
import {
  isExpandable, isTimeField, valueJson, valueKind, valuePreview, valueSummary,
} from "../lib/value";

export interface ValueViewProps {
  value: unknown;
  /** 字段名：用于识别 `*_at` / `ts` 并按本地时间显示。 */
  field?: string;
  /** 表格单元格：单行摘要，不展开。 */
  compact?: boolean;
  /** 字符串截断长度。 */
  limit?: number;
}

export function ValueView({ value, field, compact = false, limit = 96 }: ValueViewProps) {
  const kind = valueKind(value);

  if (kind === "null") return <span className="muted">–</span>;

  if (kind === "string" && isTimeField(field)) {
    const raw = value as string;
    return (
      <span className="mono value-time" title={raw}>
        {formatLocal(raw)}
        {!compact && <span className="muted small"> · ISO {raw}</span>}
      </span>
    );
  }

  if (kind === "string") {
    const text = value as string;
    if (compact || !isExpandable(text, limit)) {
      return (
        <span className="value-text" title={text}>
          {valuePreview(text, compact ? limit : text.length + 1)}
        </span>
      );
    }
    return (
      <details className="value-obj">
        <summary>{valuePreview(text, limit)}</summary>
        <pre className="obs value-json">{text}</pre>
      </details>
    );
  }

  if (kind === "number" || kind === "boolean") {
    return <span className="mono">{String(value)}</span>;
  }

  if (compact) {
    return (
      <span className="mono value-preview" title={valueJson(value)}>
        {valuePreview(value, limit)}
      </span>
    );
  }

  return (
    <details className="value-obj">
      <summary>{valueSummary(value)}</summary>
      <pre className="obs value-json">{valueJson(value)}</pre>
    </details>
  );
}
