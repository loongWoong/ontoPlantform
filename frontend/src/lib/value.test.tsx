import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { ValueView } from "../components/ValueView";
import { isExpandable, isTimeField, valueKind, valuePreview, valueSummary } from "./value";

describe("结构化值渲染", () => {
  it("对象和数组不会被 String() 成 [object Object]", () => {
    expect(valueKind({ a: 1 })).toBe("object");
    expect(valueKind([1, 2])).toBe("array");
    expect(valuePreview({ a: 1 })).toBe('{"a":1}');
    expect(valuePreview([1, "x"])).toBe('[1,"x"]');
    expect(valuePreview({ a: 1 })).not.toContain("[object Object]");
    expect(isExpandable({ a: 1 })).toBe(true);
    expect(valueSummary({ a: 1, b: 2 })).toBe("对象 · 2 个键");
    expect(valueSummary([1, 2, 3])).toBe("数组 · 3 项");
  });

  it("时间字段按本地时间显示，对象展开成 JSON", () => {
    expect(isTimeField("created_at")).toBe(true);
    expect(isTimeField("ts")).toBe(true);
    expect(isTimeField("balance")).toBe(false);

    const html = renderToStaticMarkup(
      <ValueView value={{ nested: { at: "2026-10-28T07:41:21Z" }, list: [1, 2] }} />,
    );
    expect(html).toContain("<details");
    expect(html).toContain("对象 · 2 个键");
    expect(html).toContain("&quot;nested&quot;");
    expect(html).not.toContain("[object Object]");
  });

  it("数组在表格 compact 模式只显示一行摘要", () => {
    const html = renderToStaticMarkup(<ValueView value={["a", "b"]} compact />);
    expect(html).not.toContain("<details");
    expect(html).toContain("[&quot;a&quot;,&quot;b&quot;]");
  });

  it("空值显示破折号而非 null 文案", () => {
    const html = renderToStaticMarkup(<ValueView value={null} />);
    expect(html).toContain("–");
  });
});
