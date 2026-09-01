/** 纯逻辑单测：不需要 DOM，`npx vitest run` 直接跑。
 *  重点守护两条曾经坏过的契约：
 *  1) 演示模式脚本的工具名/结束格式必须与后端 agent_runner 一致（否则每步 unknown tool）
 *  2) 过滤行的类型推断（前导零字符串、in 拆数组） */
import { describe, expect, it } from "vitest";
import { friendly } from "./api";
import { buildFilters } from "./ui";
import { DEMO_SCRIPT, toChoice } from "./store";

const BACKEND_TOOLS = new Set([
  "describe_ontology", "describe_object", "find", "related", "get", "invoke_capability",
]);

describe("friendly 错误翻译", () => {
  it("把 LLM 未配置翻译成带下一步指引的中文", () => {
    const msg = friendly("no LLM provider configured; pass one or configure the platform");
    expect(msg).toContain("模型");
    expect(msg).toContain("演示模式");
  });
  it("未知错误原样透传", () => {
    expect(friendly("something odd")).toBe("something odd");
  });
});

describe("buildFilters 类型推断", () => {
  it("数字值转 number，前导零字符串保持字符串", () => {
    const f = buildFilters([
      { prop: "balance", op: "", value: "100" },
      { prop: "code", op: "", value: "00123" },
      { prop: "ok", op: "", value: "true" },
    ]);
    expect(f).toEqual({ balance: 100, code: "00123", ok: true });
  });
  it("in 操作符拆成数组（后端按列表匹配，不再退化成子串）", () => {
    const f = buildFilters([{ prop: "plate", op: "in", value: "鲁A1, 鲁B2" }]);
    expect(f).toEqual({ plate__in: ["鲁A1", "鲁B2"] });
  });
  it("空属性与空值被跳过", () => {
    expect(buildFilters([{ prop: "", op: "", value: "x" }, { prop: "a", op: "", value: "" }])).toEqual({});
  });
});

describe("演示模式脚本与后端 agent_runner 的契约", () => {
  it("每个 action 的工具名都在后端工具表里", () => {
    for (const line of DEMO_SCRIPT) {
      const step = JSON.parse(line);
      if (step.action) expect(BACKEND_TOOLS.has(step.action.tool)).toBe(true);
    }
  });
  it("结束步是顶层 answer 且不带 action（后端以此判定收束）", () => {
    const last = JSON.parse(DEMO_SCRIPT[DEMO_SCRIPT.length - 1]);
    expect(last.answer).toBeTruthy();
    expect(last.action).toBeUndefined();
  });
  it("有已发布类型时，find 步骤替换成真实类型", () => {
    const choice = toChoice({ mode: "demo" }, "TollStation");
    if (!choice || !("script" in choice)) throw new Error("expected script choice");
    const find = choice.script.map(l => JSON.parse(l)).find(s => s.action?.tool === "find");
    expect(find?.action?.args?.object_type).toBe("TollStation");
  });
  it("没有已发布类型时，跳过 find 步骤仍能跑完", () => {
    const choice = toChoice({ mode: "demo" });
    if (!choice || !("script" in choice)) throw new Error("expected script choice");
    const steps = choice.script.map(l => JSON.parse(l));
    expect(steps.some(s => s.action?.tool === "find")).toBe(false);
    expect(steps[steps.length - 1].answer).toBeTruthy();
  });
});
