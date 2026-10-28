import { describe, expect, it } from "vitest";
import { formatLocal, formatWindow, toUtcIso, zoneOffsetMinutes } from "./time";

describe("时间显示与时区转换", () => {
  it("UTC ISO 按指定时区显示并标出偏移", () => {
    expect(formatLocal("2026-10-28T07:41:21Z", "Asia/Shanghai"))
      .toBe("2026-10-28 15:41:21（UTC+08:00）");
    expect(formatLocal("2026-10-28T07:41:21Z", "UTC"))
      .toBe("2026-10-28 07:41:21（UTC+00:00）");
  });

  it("夏令时期间偏移正确（纽约 2026-07 为 UTC-04:00）", () => {
    expect(zoneOffsetMinutes(new Date("2026-07-01T12:00:00Z"), "America/New_York")).toBe(-240);
    expect(formatLocal("2026-07-01T12:00:00Z", "America/New_York"))
      .toBe("2026-07-01 08:00:00（UTC-04:00）");
  });

  it("datetime-local 的本地时间转成 UTC，而不是当成 UTC", () => {
    expect(toUtcIso("2026-10-28T15:41", "Asia/Shanghai")).toBe("2026-10-28T07:41:00.000Z");
    expect(toUtcIso("2026-10-28T15:41:21", "Asia/Shanghai")).toBe("2026-10-28T07:41:21.000Z");
    expect(toUtcIso("2026-01-15T09:05", "America/New_York")).toBe("2026-01-15T14:05:00.000Z");
  });

  it("非法时间明确报错，不静默滚动", () => {
    expect(() => toUtcIso("2026-02-30T10:00", "UTC")).toThrow(/日期不存在/);
    expect(() => toUtcIso("2026-10-28", "UTC")).toThrow(/格式/);
  });

  it("窗口写法可读", () => {
    expect(formatWindow(300)).toBe("5 分钟");
    expect(formatWindow(3600)).toBe("1 小时");
    expect(formatWindow(86400)).toBe("1 天");
    expect(formatWindow(null)).toBe("–");
  });
});
