/// <reference types="vite/client" />
import { describe, expect, it } from "vitest";

/** 静态文案审计：这些说法会把未知、无数据或隔离环境说成已成功，必须消失。 */
const SOURCES = import.meta.glob(
  ["./views/*.tsx", "./components/*.tsx", "./lib/status.ts", "./store.tsx", "./App.tsx"],
  { query: "?raw", import: "default", eager: true },
) as Record<string, string>;

const ALL = Object.values(SOURCES).join("\n");

describe("文案审计", () => {
  it("删除了把未观察状态说成成功或实时的旧文案", () => {
    for (const forbidden of [
      "每一步都留痕",
      "失败不会留下半写状态",
      "实时快照",
      "再试一次",
      "外部模型",
      "user=admin",
    ]) {
      expect(ALL, `不应再出现「${forbidden}」`).not.toContain(forbidden);
    }
  });

  it("金丝雀只以隔离验证环出现，且文案唯一", () => {
    expect(SOURCES["./lib/status.ts"]).toContain('CANARY_RING_LABEL = "隔离验证环（不接真实流量）"');
    expect(SOURCES["./views/Operations.tsx"]).toContain("CANARY_RING_LABEL");
    expect(ALL).not.toContain("金丝雀流量");
  });

  it("未知结果保留人工查证入口，而不是自动重试", () => {
    expect(SOURCES["./views/Capability.tsx"]).toContain("结果未知，已停止自动重试");
    expect(SOURCES["./views/Capability.tsx"]).toContain("查证已生效");
    expect(SOURCES["./views/Capability.tsx"]).toContain("查证未生效");
    expect(SOURCES["./views/Capability.tsx"]).toContain("用同一幂等键重试");
  });
});
