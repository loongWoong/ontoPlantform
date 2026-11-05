import { describe, expect, it } from "vitest";
import {
  cqStatusKind, cqStatusLabel, expectedLabel, joinRefs, parseJsonObject,
  parseJsonRows, progress, queryLabel, roundStatusLabel, splitRefs,
  stepStatusKind,
} from "./workflow";
import type { WorkflowStep } from "../api";

const step = (status: WorkflowStep["status"]): WorkflowStep => ({
  key: status, no: 1, title: status, status, detail: "", action: "",
});

describe("workflow view model", () => {
  it("computes progress from eight steps", () => {
    const steps = [
      step("done"), step("done"), step("current"),
      ...Array.from({ length: 5 }, () => step("todo")),
    ];
    expect(progress(steps)).toEqual({ done: 2, total: 8, percent: 25 });
  });

  it("maps statuses to tag kinds and labels", () => {
    expect(stepStatusKind("done")).toBe("ok");
    expect(stepStatusKind("warn")).toBe("warn");
    expect(cqStatusLabel("passed")).toBe("通过");
    expect(cqStatusKind("failed")).toBe("err");
    expect(roundStatusLabel("accepted")).toBe("已发布");
  });

  it("splits model refs on commas and whitespace", () => {
    expect(splitRefs("Vehicle, Vehicle.plate   passes，TollStation")).toEqual([
      "Vehicle", "Vehicle.plate", "passes", "TollStation",
    ]);
    expect(joinRefs(["Vehicle", "passes"])).toBe("Vehicle, passes");
  });

  it("summarises expectations and queries", () => {
    expect(expectedLabel({ kind: "row_count", value: 3 })).toBe("= 3 行");
    expect(expectedLabel({ kind: "row_count", operator: "gte", value: 2 }))
      .toBe("≥ 2 行");
    expect(expectedLabel({ kind: "rows_contain", rows: [{}, {}] }))
      .toBe("包含 2 行");
    expect(queryLabel({ object_type: "Vehicle", filters: { plate: "A" } }))
      .toBe("Vehicle · plate=A");
    expect(queryLabel({ relation: "passes", direction: "out", ref: "Vehicle/V1" }))
      .toBe("关系 passes · out · Vehicle/V1");
  });

  it("parses JSON objects and rows, rejecting wrong shapes", () => {
    expect(parseJsonObject('{"plate":"A"}')).toEqual({ plate: "A" });
    expect(parseJsonObject("")).toEqual({});
    expect(parseJsonRows('[{"plate":"A"}]')).toEqual([{ plate: "A" }]);
    expect(() => parseJsonObject("[1]")).toThrow(/JSON 对象/);
    expect(() => parseJsonRows('{"plate":"A"}')).toThrow(/JSON 数组/);
  });
});
