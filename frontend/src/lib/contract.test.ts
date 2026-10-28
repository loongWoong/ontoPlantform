import { describe, expect, it } from "vitest";
import { CONTRACT_EXAMPLE, contractExampleText, parseContract } from "./contract";

describe("能力执行契约示例", () => {
  it("形状与计划一致", () => {
    expect(CONTRACT_EXAMPLE).toEqual({
      preconditions: [{ property: "status", op: "eq", value: "active" }],
      postconditions: [{ property: "balance", op: "eq_prev_plus", value: { args: "amount" } }],
      effects: ["balance"],
      idempotent: true,
      compensate: "",
    });
    expect(Object.keys(CONTRACT_EXAMPLE)).toEqual([
      "preconditions", "postconditions", "effects", "idempotent", "compensate",
    ]);
  });

  it("示例 JSON 可以原样解析", () => {
    const parsed = parseContract(contractExampleText());
    expect(parsed.error).toBeUndefined();
    expect(parsed.value).toEqual(CONTRACT_EXAMPLE);
  });

  it("不认识的顶层键与错误类型会被拒绝", () => {
    expect(parseContract('{"required":["amount"]}').error).toContain("不认识的契约顶层键");
    expect(parseContract('{"preconditions":{}}').error).toContain("preconditions");
    expect(parseContract("[]").error).toContain("JSON 对象");
    expect(parseContract("{oops").error).toContain("合法 JSON");
  });

  it("空契约允许，表示不声明执行约束", () => {
    expect(parseContract("  ")).toEqual({});
  });
});
