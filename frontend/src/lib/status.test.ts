import { describe, expect, it } from "vitest";
import {
  CANARY_RING_LABEL,
  CHECK_FRESH_SECONDS,
  aggregateSloStatus,
  canaryCheckState,
  deliveryStatus,
  executionStatus,
  isSuccess,
  isUnknown,
  statusMeta,
} from "./status";

const NOW = Date.parse("2026-10-28T08:00:00Z");

describe("成功只由 succeeded 决定", () => {
  it("unknown / failed / contract_failed 都不是成功", () => {
    expect(isSuccess("succeeded")).toBe(true);
    for (const s of ["unknown", "failed", "contract_failed", "running", "pending", "no_data"]) {
      expect(isSuccess(s)).toBe(false);
    }
  });

  it("旧 ok=true 不能覆盖 unknown", () => {
    expect(executionStatus({ status: "unknown", ok: true })).toBe("unknown");
    expect(isUnknown(executionStatus({ status: "unknown", ok: true }))).toBe(true);
  });

  it("契约失败优先于普通失败", () => {
    expect(executionStatus({ status: "succeeded", contract_failed: true })).toBe("contract_failed");
    expect(statusMeta("contract_failed").label).toContain("契约");
  });

  it("未知结果有明确中文标签", () => {
    expect(statusMeta("unknown").label).toBe("结果未知");
    expect(statusMeta("unknown").tone).not.toBe("ok");
  });
});

describe("SLO 聚合不把无数据当绿色", () => {
  const rates = (status: string, extra = {}) => ({
    success_rate: { status },
    unknown_rate: { status: "ok" },
    pending_events: { status: "ok" },
    projection_age_seconds: { status: "ok" },
    ...extra,
  });

  it("没有样本 -> no_data", () => {
    expect(aggregateSloStatus({ ok: true, indicators: rates("no_data") })).toBe("no_data");
    expect(aggregateSloStatus(null)).toBe("no_data");
  });

  it("样本不足 -> insufficient", () => {
    expect(aggregateSloStatus({ ok: true, indicators: rates("insufficient") })).toBe("insufficient");
  });

  it("任一越线 -> breach", () => {
    expect(aggregateSloStatus({
      ok: false,
      indicators: rates("ok", { unknown_rate: { status: "breach" } }),
    })).toBe("breach");
  });

  it("全部有样本且达标才是 ok", () => {
    expect(aggregateSloStatus({ ok: true, indicators: rates("ok") })).toBe("ok");
  });

  it("后端 ok=true 但主指标无数据时仍不绿", () => {
    const status = aggregateSloStatus({ ok: true, indicators: rates("no_data") });
    expect(statusMeta(status).tone).not.toBe("ok");
  });
});

describe("outbox 投递状态", () => {
  it("没有订阅者是 unrouted，不是已投递", () => {
    expect(deliveryStatus({ delivery: "no_subscribers", dispatched_at: "2026-10-28T08:00:00Z" })).toBe("unrouted");
    expect(statusMeta("unrouted").label).toBe("未路由");
    expect(statusMeta("unrouted").tone).not.toBe("ok");
  });

  it("有真实消费者确认才是 delivered", () => {
    expect(deliveryStatus({ delivery_status: "delivered" })).toBe("delivered");
  });

  it("尝试过但未成功 -> retrying", () => {
    expect(deliveryStatus({ attempts: 2 })).toBe("retrying");
  });
});

describe("金丝雀检查新鲜度", () => {
  const fresh = new Date(NOW - 10_000).toISOString();
  const stale = new Date(NOW - (CHECK_FRESH_SECONDS + 1) * 1000).toISOString();

  it("文案明确是隔离验证环，不暗示真实流量", () => {
    expect(CANARY_RING_LABEL).toBe("隔离验证环（不接真实流量）");
    expect(CANARY_RING_LABEL).toContain("不接真实流量");
  });

  it("没有检查 -> no_data", () => {
    expect(canaryCheckState(null, NOW).status).toBe("no_data");
  });

  it("超过新鲜窗口 -> stale，即使当时通过", () => {
    const st = canaryCheckState({ checked_at: stale, ok: true, checks: [{ ok: true }] }, NOW);
    expect(st.status).toBe("stale");
    expect(st.passed).toBe(true);
  });

  it("有失败项 -> failed", () => {
    expect(canaryCheckState({ checked_at: fresh, ok: true, checks: [{ ok: false }] }, NOW).status).toBe("failed");
  });

  it("新鲜且全部通过 -> validated", () => {
    expect(canaryCheckState({ checked_at: fresh, ok: true, checks: [{ ok: true }] }, NOW).status).toBe("validated");
  });
});
