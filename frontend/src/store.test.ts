import { describe, expect, it } from "vitest";
import { persistedLlm, sanitizeLlmSetting, type LlmSetting } from "./store";
import { newIdempotencyKey } from "./lib/idempotency";

describe("模型密钥不进入浏览器存储", () => {
  it("持久化内容只有模式、地址、模型名", () => {
    const setting = {
      mode: "openai",
      base_url: "https://example.invalid/v1",
      model: "gpt-4o-mini",
      api_key: "sk-secret-should-not-persist",
    } as unknown as LlmSetting;
    const raw = persistedLlm(setting);
    expect(raw).not.toContain("sk-secret-should-not-persist");
    expect(raw).not.toContain("api_key");
    expect(JSON.parse(raw)).toEqual({
      mode: "openai",
      base_url: "https://example.invalid/v1",
      model: "gpt-4o-mini",
    });
  });

  it("读取旧 localStorage 时主动剥掉历史 api_key", () => {
    const clean = sanitizeLlmSetting({
      mode: "openai", base_url: "x", model: "m", api_key: "sk-old",
    });
    expect("api_key" in clean).toBe(false);
    expect(JSON.stringify(clean)).not.toContain("sk-old");
  });

  it("演示模式与平台模式不落任何密钥字段", () => {
    expect(persistedLlm({ mode: "demo" })).toBe('{"mode":"demo"}');
    expect(persistedLlm({ mode: "platform" })).toBe('{"mode":"platform"}');
  });
});

describe("幂等键", () => {
  it("每次用户动作生成新的 UUID v4", () => {
    const a = newIdempotencyKey();
    const b = newIdempotencyKey();
    expect(a).not.toBe(b);
    expect(a).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  });
});
