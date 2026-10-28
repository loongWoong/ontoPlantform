/** 时间显示与输入的唯一入口（P2-13）。
 *
 *  后端只接受 / 只返回 ISO 8601。界面显示时统一按浏览器时区转换，
 *  并把时区标出来；`<input type="datetime-local">` 的本地时间必须显式
 *  转成 UTC ISO 再发给后端，不能把 "2026-10-28T15:31" 直接当 UTC。
 */

const LOCAL_INPUT = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?$/;

export function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

interface ZoneParts {
  year: number; month: number; day: number;
  hour: number; minute: number; second: number;
}

function partsInZone(date: Date, timeZone: string): ZoneParts {
  const fmt = new Intl.DateTimeFormat("en-US", {
    timeZone,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
  const values: Record<string, number> = {};
  for (const part of fmt.formatToParts(date)) {
    if (part.type !== "literal") values[part.type] = Number(part.value);
  }
  return {
    year: values.year,
    month: values.month,
    day: values.day,
    hour: values.hour,
    minute: values.minute,
    second: values.second,
  };
}

/** 指定时区在某一时刻相对 UTC 的偏移（分钟，东八区为 480）。 */
export function zoneOffsetMinutes(date: Date, timeZone: string): number {
  const p = partsInZone(date, timeZone);
  const asUtc = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second);
  return Math.round((asUtc - date.getTime()) / 60000);
}

export function isValidIso(value: string | null | undefined): boolean {
  if (!value) return false;
  if (!/^\d{4}-\d{2}-\d{2}T/.test(value)) return false;
  return !Number.isNaN(new Date(value).getTime());
}

/** datetime-local 的本地时间 -> 带时区语义的 UTC ISO 字符串。
 *  传 timeZone 只是为了测试与固定时区场景，默认用浏览器时区。 */
export function toUtcIso(localValue: string, timeZone: string = browserTimeZone()): string {
  const raw = (localValue || "").trim();
  const match = LOCAL_INPUT.exec(raw);
  if (!match) {
    throw new Error("时间格式必须是 YYYY-MM-DDTHH:mm 或 YYYY-MM-DDTHH:mm:ss");
  }
  const [, ys, mos, ds, hs, mis, ss] = match;
  const year = Number(ys);
  const month = Number(mos);
  const day = Number(ds);
  const hour = Number(hs);
  const minute = Number(mis);
  const second = Number(ss || "0");
  if (month < 1 || month > 12 || day < 1 || day > 31 || hour > 23 || minute > 59 || second > 59) {
    throw new Error("时间超出有效范围");
  }
  const naive = Date.UTC(year, month - 1, day, hour, minute, second);
  const check = new Date(naive);
  // 拒绝 2 月 30 日这类会被 Date.UTC 静默进位的日期
  if (check.getUTCFullYear() !== year || check.getUTCMonth() !== month - 1 || check.getUTCDate() !== day) {
    throw new Error("日期不存在");
  }
  const firstOffset = zoneOffsetMinutes(check, timeZone);
  let utc = naive - firstOffset * 60000;
  const secondOffset = zoneOffsetMinutes(new Date(utc), timeZone);
  if (secondOffset !== firstOffset) utc = naive - secondOffset * 60000;
  return new Date(utc).toISOString();
}

function pad(n: number, width = 2): string {
  return String(Math.abs(n)).padStart(width, "0");
}

function offsetLabel(offsetMinutes: number): string {
  const sign = offsetMinutes >= 0 ? "+" : "-";
  const abs = Math.abs(offsetMinutes);
  return `UTC${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`;
}

/** UTC ISO -> 本地时间 + 时区。不可解析时原样返回，避免吞掉后端证据。 */
export function formatLocal(
  iso: string | null | undefined,
  timeZone: string = browserTimeZone(),
): string {
  if (!iso) return "–";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return String(iso);
  try {
    const p = partsInZone(date, timeZone);
    const offset = zoneOffsetMinutes(date, timeZone);
    return `${p.year}-${pad(p.month)}-${pad(p.day)} ${pad(p.hour)}:${pad(p.minute)}:${pad(p.second)}（${offsetLabel(offset)}）`;
  } catch {
    return date.toISOString();
  }
}

/** 表格里的短格式：MM-DD HH:mm（仍按本地时区）。 */
export function formatLocalShort(
  iso: string | null | undefined,
  timeZone: string = browserTimeZone(),
): string {
  if (!iso) return "–";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return String(iso);
  try {
    const p = partsInZone(date, timeZone);
    return `${pad(p.month)}-${pad(p.day)} ${pad(p.hour)}:${pad(p.minute)}`;
  } catch {
    return date.toISOString();
  }
}

export function nowIso(): string {
  return new Date().toISOString();
}

/** 评估窗口的人类可读写法。 */
export function formatWindow(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "–";
  if (seconds < 60) return `${Math.round(seconds)} 秒`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟`;
  if (seconds < 86400) return `${Number((seconds / 3600).toFixed(1))} 小时`;
  return `${Number((seconds / 86400).toFixed(1))} 天`;
}
