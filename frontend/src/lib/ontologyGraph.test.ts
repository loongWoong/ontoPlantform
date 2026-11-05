import { describe, expect, it } from "vitest";
import type { GraphNode, OntologyGraphLayout } from "./ontologyGraph";
import {
  layoutOntologyGraph,
  roundedGraphPath,
  wrapGraphText,
} from "./ontologyGraph";
import type { ObjectTypeSpec, Ontology, RelationSpec } from "../api";

type ObjectOverrides = Partial<ObjectTypeSpec>;
type RelationOverrides = Partial<RelationSpec>;

/** 构造 REST 契约里的 Ontology 样例（不依赖任何后端）。 */
function makeOntology(
  objects: Record<string, ObjectOverrides> = {},
  relations: Record<string, RelationOverrides> = {},
): Ontology {
  const builtObjects: Record<string, ObjectTypeSpec> = {};
  for (const [key, overrides] of Object.entries(objects)) {
    const spec: ObjectTypeSpec = {
      name: overrides.name ?? key,
      description: overrides.description ?? "",
      properties: overrides.properties ?? {},
    };
    if (overrides.identity) spec.identity = overrides.identity;
    if (overrides.category) spec.category = overrides.category;
    if (overrides.state_machine) spec.state_machine = overrides.state_machine;
    builtObjects[key] = spec;
  }
  const builtRelations: Record<string, RelationSpec> = {};
  for (const [key, overrides] of Object.entries(relations)) {
    builtRelations[key] = {
      name: overrides.name ?? key,
      source: overrides.source ?? "",
      target: overrides.target ?? "",
      description: overrides.description,
      cardinality: overrides.cardinality,
      inverse: overrides.inverse,
    };
  }
  return {
    name: "demo",
    description: "",
    version: 1,
    lifecycle: "published",
    objects: builtObjects,
    relations: builtRelations,
    vocabulary: {},
  };
}

function nodeOf(layout: OntologyGraphLayout, name: string): GraphNode {
  const found = layout.nodes.find((node) => node.name === name);
  expect(found, `节点 ${name} 应存在`).toBeDefined();
  return found!;
}

function collectNumbers(value: unknown, collected: number[] = []): number[] {
  if (typeof value === "number") collected.push(value);
  else if (Array.isArray(value)) value.forEach((item) => collectNumbers(item, collected));
  else if (value && typeof value === "object") {
    for (const item of Object.values(value as Record<string, unknown>)) {
      collectNumbers(item, collected);
    }
  }
  return collected;
}

const LONG_CJK_NAME = "这是一个非常非常长的业务对象名称需要换行展示";

describe("wrapGraphText：确定性的按字宽换行", () => {
  it("CJK 每个字算一个单位，短文本不换行", () => {
    expect(wrapGraphText("收费站", 11)).toEqual(["收费站"]);
    expect(wrapGraphText("车辆通过收费站", 3)).toEqual(["车辆通", "过收费", "站"]);
  });

  it("ASCII 约 0.56 单位：单词超宽时按字符折行且信息不丢", () => {
    const lines = wrapGraphText("TollStation", 6);
    expect(lines.length).toBe(2);
    expect(lines.join("")).toBe("TollStation");
    expect(lines.every((line) => line.length > 0)).toBe(true);
  });

  it("显式换行与空串都有稳定输出", () => {
    expect(wrapGraphText("A\nB", 11)).toEqual(["A", "B"]);
    expect(wrapGraphText("", 11)).toEqual([""]);
  });

  it("同样的输入两次结果相同", () => {
    expect(wrapGraphText(LONG_CJK_NAME, 11)).toEqual(wrapGraphText(LONG_CJK_NAME, 11));
  });
});

describe("roundedGraphPath：圆角正交路径", () => {
  it("空数组/单点/两点有稳定输出", () => {
    expect(roundedGraphPath([])).toBe("");
    expect(roundedGraphPath([{ x: 10, y: 20 }])).toBe("M 10 20");
    expect(
      roundedGraphPath([
        { x: 0, y: 0 },
        { x: 100, y: 0 },
      ]),
    ).toBe("M 0 0 L 100 0");
  });

  it("折线以 M 起、L 止，转角用 Q 倒角", () => {
    const path = roundedGraphPath([
      { x: 0, y: 0 },
      { x: 100, y: 0 },
      { x: 100, y: 50 },
      { x: 200, y: 50 },
    ]);
    expect(path.startsWith("M 0 0 ")).toBe(true);
    expect(path.endsWith("L 200 50")).toBe(true);
    expect((path.match(/Q /g) ?? []).length).toBe(2);
    expect(path).not.toContain("NaN");
  });

  it("重复点会被折叠，不影响端点", () => {
    const withDuplicates = roundedGraphPath([
      { x: 0, y: 0 },
      { x: 0, y: 0 },
      { x: 10, y: 10 },
    ]);
    expect(withDuplicates).toBe("M 0 0 L 10 10");
  });

  it("确定性：同样的折线两次结果相同", () => {
    const points = [
      { x: 5, y: 5 },
      { x: 60, y: 5 },
      { x: 60, y: 80 },
    ];
    expect(roundedGraphPath(points)).toBe(roundedGraphPath(points));
  });

  it("同层折线（下行-横移-上行）也按同一规则圆角渲染", () => {
    const path = roundedGraphPath([
      { x: 100, y: 100 },
      { x: 100, y: 128 },
      { x: 264, y: 128 },
      { x: 264, y: 100 },
    ]);
    expect(path.startsWith("M 100 100")).toBe(true);
    expect(path.endsWith("L 264 100")).toBe(true);
    expect((path.match(/Q /g) ?? []).length).toBe(2);
  });
});

describe("空本体", () => {
  it("返回至少 88×88 的空画布，不产生节点/边", () => {
    const layout = layoutOntologyGraph(makeOntology());
    expect(layout.nodes).toEqual([]);
    expect(layout.edges).toEqual([]);
    expect(layout.objectCount).toBe(0);
    expect(layout.relationCount).toBe(0);
    expect(layout.danglingRelations).toEqual([]);
    expect(layout.width).toBeGreaterThanOrEqual(88);
    expect(layout.height).toBeGreaterThanOrEqual(88);
  });
});

describe("链式 3 对象：从左到右分层", () => {
  const ontology = makeOntology(
    { A: {}, B: {}, C: {} },
    {
      ab: { source: "A", target: "B" },
      bc: { source: "B", target: "C" },
    },
  );
  const layout = layoutOntologyGraph(ontology);

  it("节点 x 逐层递增，y 都从 44 起", () => {
    const a = nodeOf(layout, "A");
    const b = nodeOf(layout, "B");
    const c = nodeOf(layout, "C");
    expect(a.x).toBeLessThan(b.x);
    expect(b.x).toBeLessThan(c.x);
    expect(a.x).toBe(44);
    expect(b.x).toBe(44 + 216 + 128);
    expect(c.x).toBe(44 + 2 * (216 + 128));
    for (const node of layout.nodes) {
      expect(node.x).toBeGreaterThanOrEqual(44);
      expect(node.y).toBeGreaterThanOrEqual(44);
      expect(node.y).toBe(44);
      expect(node.height).toBe(76);
    }
  });

  it("前向边：右端口出发、左端口到达、带圆角", () => {
    const a = nodeOf(layout, "A");
    const b = nodeOf(layout, "B");
    const edge = layout.edges.find((item) => item.name === "ab")!;
    expect(edge.self).toBe(false);
    expect(edge.back).toBe(false);
    expect(edge.path.startsWith(`M ${a.x + a.width} ${a.y + a.height / 2}`)).toBe(true);
    expect(edge.path.endsWith(`L ${b.x} ${b.y + b.height / 2}`)).toBe(true);
    // 两端口同高时中段退化为直线，只保留 1 个圆角；否则为 2 个
    expect((edge.path.match(/Q /g) ?? []).length).toBeGreaterThanOrEqual(1);
    expect((edge.path.match(/Q /g) ?? []).length).toBeLessThanOrEqual(2);
  });

  it("统计与计数正确", () => {
    expect(layout.objectCount).toBe(3);
    expect(layout.relationCount).toBe(2);
    expect(nodeOf(layout, "B").relationCount).toBe(2);
    expect(nodeOf(layout, "A").connections).toBe(1);
    expect(layout.width).toBeGreaterThan(44 + 2 * (216 + 96) + 216);
  });
});

describe("悬空关系", () => {
  const layout = layoutOntologyGraph(
    makeOntology({ A: {} }, {
      r1: { source: "Ghost", target: "A" },
      r2: { source: "A", target: "A" },
      r3: { source: "A", target: "Ghost" },
    }),
  );

  it("按声明顺序记入 danglingRelations，只保留合法关系", () => {
    expect(layout.danglingRelations).toEqual(["r1", "r3"]);
    expect(layout.edges.length).toBe(1);
    expect(layout.relationCount).toBe(1);
    expect(layout.edges[0]!.self).toBe(true);
    expect(nodeOf(layout, "A").relationCount).toBe(1);
  });
});

describe("环 A<->B：不死循环且至少一条 back", () => {
  const layout = layoutOntologyGraph(
    makeOntology({ A: {}, B: {} }, {
      ab: { source: "A", target: "B" },
      ba: { source: "B", target: "A" },
    }),
  );

  it("分层仍从左到右，回向边标 back", () => {
    const a = nodeOf(layout, "A");
    const b = nodeOf(layout, "B");
    expect(a.x).toBeLessThan(b.x);
    const ab = layout.edges.find((item) => item.name === "ab")!;
    const ba = layout.edges.find((item) => item.name === "ba")!;
    expect(ab.back).toBe(false);
    expect(ba.back).toBe(true);
  });

  it("回向/跨层向左的边走外侧，至少 4 个折点且不进入卡片内部", () => {
    const a = nodeOf(layout, "A");
    const b = nodeOf(layout, "B");
    const ba = layout.edges.find((item) => item.name === "ba")!;
    // 从源的左端口出发，绕到目标的右端口
    expect(ba.path.startsWith(`M ${b.x} ${b.y + b.height / 2}`)).toBe(true);
    expect(ba.path.endsWith(`L ${a.x + a.width} ${a.y + a.height / 2}`)).toBe(true);
    expect((ba.path.match(/Q /g) ?? []).length).toBeGreaterThanOrEqual(4);
    expect(ba.path).not.toContain("NaN");
  });
});

describe("self 关系", () => {
  const layout = layoutOntologyGraph(
    makeOntology({ A: {} }, {
      loop1: { source: "A", target: "A" },
      loop2: { source: "A", target: "A" },
    }),
  );

  it("路径是右侧贝塞尔小环（C 命令），self=true", () => {
    const a = nodeOf(layout, "A");
    expect(layout.edges.length).toBe(2);
    for (const edge of layout.edges) {
      expect(edge.self).toBe(true);
      expect(edge.path).toContain(" C ");
      expect(edge.path.startsWith(`M ${a.x + a.width} `)).toBe(true);
    }
  });

  it("多个 self 关系按 index 偏移，标签在环右侧", () => {
    const a = nodeOf(layout, "A");
    const [first, second] = layout.edges;
    expect(first!.label.x).toBeGreaterThan(a.x + a.width);
    expect(second!.label.x).toBeGreaterThan(first!.label.x);
    expect(first!.label.width).toBeGreaterThanOrEqual(52);
    expect(first!.label.height).toBe(first!.label.lines.length * 15 + 10);
  });

  it("长名称 self 标签收窄换行，不伸进下一层卡片", () => {
    const longName = "这是一个很长的自关联名称";
    const selfLayout = layoutOntologyGraph(
      makeOntology({ A: {}, B: {} }, {
        [longName]: { name: longName, source: "A", target: "A" },
        ab: { source: "A", target: "B" },
      }),
    );
    const a = nodeOf(selfLayout, "A");
    const b = nodeOf(selfLayout, "B");
    const selfEdge = selfLayout.edges.find((edge) => edge.self)!;
    expect(selfEdge).toBeDefined();
    expect(selfEdge.label.x + selfEdge.label.width).toBeLessThanOrEqual(b.x - 4);
    expect(selfEdge.label.x).toBeGreaterThan(a.x + a.width);
  });

  it("多个 self 关系的标签都留在下一层卡片之前", () => {
    const longName = "这是一个很长的自关联名称";
    const selfLayout = layoutOntologyGraph(
      makeOntology({ A: {}, B: {} }, {
        [longName]: { name: longName, source: "A", target: "A" },
        second: { source: "A", target: "A" },
        ab: { source: "A", target: "B" },
      }),
    );
    const b = nodeOf(selfLayout, "B");
    for (const edge of selfLayout.edges.filter((item) => item.self)) {
      expect(edge.label.x + edge.label.width).toBeLessThanOrEqual(b.x - 4);
    }
  });
});

describe("节点尺寸与层垂直居中", () => {
  it("名称换行越多卡片越高", () => {
    const layout = layoutOntologyGraph(
      makeOntology({ Short: {}, [LONG_CJK_NAME]: {} }),
    );
    const short = nodeOf(layout, "Short");
    const long = nodeOf(layout, LONG_CJK_NAME);
    expect(short.height).toBe(76);
    expect(short.lines.length).toBe(1);
    expect(long.lines.length).toBeGreaterThan(1);
    expect(long.height).toBe(76 + (long.lines.length - 1) * 20);
    expect(long.height).toBeGreaterThan(short.height);
  });

  it("矮层相对最高层垂直居中，且 y>=44", () => {
    const layout = layoutOntologyGraph(
      makeOntology({ [LONG_CJK_NAME]: {}, Short: {} }, {
        edge: { source: LONG_CJK_NAME, target: "Short" },
      }),
    );
    const long = nodeOf(layout, LONG_CJK_NAME);
    const short = nodeOf(layout, "Short");
    expect(long.y).toBe(44);
    expect(short.y).toBe(44 + (long.height - short.height) / 2);
    expect(short.y).toBeGreaterThan(44);
  });

  it("孤立节点都在第 0 层", () => {
    const layout = layoutOntologyGraph(makeOntology({ A: {}, B: {} }));
    expect(nodeOf(layout, "A").x).toBe(44);
    expect(nodeOf(layout, "B").x).toBe(44);
    expect(nodeOf(layout, "A").y).not.toBe(nodeOf(layout, "B").y);
  });
});

describe("节点元数据与边字段", () => {
  const layout = layoutOntologyGraph(
    makeOntology(
      {
        Vehicle: {
          description: "车辆",
          properties: {
            plate: { name: "plate", type: "string" },
            status: { name: "status", type: "string" },
          },
        },
        Station: {},
      },
      {
        passes: {
          source: "Vehicle",
          target: "Station",
          description: "通过",
          cardinality: "many-to-one",
        },
      },
    ),
  );

  it("属性/关系计数、描述来自 ObjectTypeSpec", () => {
    const vehicle = nodeOf(layout, "Vehicle");
    expect(vehicle.propertyCount).toBe(2);
    expect(vehicle.description).toBe("车辆");
    expect(vehicle.relationCount).toBe(1);
    expect(vehicle.connections).toBe(1);
    expect(nodeOf(layout, "Station").relationCount).toBe(1);
  });

  it("边名/方向/描述/cardinality 与标签盒", () => {
    const edge = layout.edges[0]!;
    expect(edge.name).toBe("passes");
    expect(edge.source).toBe("Vehicle");
    expect(edge.target).toBe("Station");
    expect(edge.description).toBe("通过");
    expect(edge.cardinality).toBe("many-to-one");
    expect(edge.self).toBe(false);
    expect(edge.label.lines).toEqual(wrapGraphText("passes", 8));
    expect(edge.label.width).toBeGreaterThanOrEqual(52);
    expect(edge.label.height).toBe(edge.label.lines.length * 15 + 10);
  });
});

describe("同一对对象的多条关系", () => {
  it("前向关系共享路由时标签纵向错开，不重叠", () => {
    const layout = layoutOntologyGraph(
      makeOntology(
        { Vehicle: {}, Station: {} },
        {
          enters: { source: "Vehicle", target: "Station" },
          passes: { source: "Vehicle", target: "Station" },
        },
      ),
    );
    expect(layout.edges).toHaveLength(2);
    const [first, second] = layout.edges;
    expect(first!.path).toBe(second!.path);
    const gap = Math.abs(first!.label.y - second!.label.y);
    expect(gap).toBeGreaterThanOrEqual(
      Math.max(first!.label.height, second!.label.height) + 2,
    );
  });

  it("3 条以上且行数不同时按真实高度依次排开", () => {
    const layout = layoutOntologyGraph(
      makeOntology(
        { A: {}, B: {} },
        {
          one: { source: "A", target: "B" },
          two_lines_relation_name: { source: "A", target: "B" },
          three: { source: "A", target: "B" },
        },
      ),
    );
    const labels = layout.edges
      .map((edge) => edge.label)
      .sort((left, right) => left.y - right.y);
    expect(labels).toHaveLength(3);
    expect(labels[1]!.y).toBeGreaterThanOrEqual(
      labels[0]!.y + labels[0]!.height,
    );
    expect(labels[2]!.y).toBeGreaterThanOrEqual(
      labels[1]!.y + labels[1]!.height,
    );
  });

  it("跨多层的长边走节点下方走廊，标签不压任何卡片", () => {
    const layout = layoutOntologyGraph(
      makeOntology(
        { A: {}, B: {}, C: {} },
        {
          ab: { source: "A", target: "B" },
          bc: { source: "B", target: "C" },
          ac: { source: "A", target: "C" },
        },
      ),
    );
    const long = layout.edges.find((edge) => edge.name === "ac")!;
    const nodeBottom = Math.max(...layout.nodes.map((node) => node.y + node.height));
    expect(long.label.y).toBeGreaterThanOrEqual(nodeBottom);
    for (const node of layout.nodes) {
      const overlapsX =
        long.label.x < node.x + node.width &&
        long.label.x + long.label.width > node.x;
      const overlapsY =
        long.label.y < node.y + node.height &&
        long.label.y + long.label.height > node.y;
      expect(overlapsX && overlapsY).toBe(false);
    }
  });
});

describe("确定性与纯函数", () => {
  const ontology = makeOntology(
    { A: {}, B: {}, C: {}, D: {} },
    {
      ab: { source: "A", target: "B" },
      ac: { source: "A", target: "C" },
      bd: { source: "B", target: "D" },
      cd: { source: "C", target: "D" },
      da: { source: "D", target: "A" },
    },
  );

  it("同一输入两次布局深度相等", () => {
    expect(layoutOntologyGraph(ontology)).toEqual(layoutOntologyGraph(ontology));
  });

  it("不产生 NaN / Infinity", () => {
    const layout = layoutOntologyGraph(ontology);
    const numbers = collectNumbers(layout);
    expect(numbers.length).toBeGreaterThan(0);
    for (const number of numbers) expect(Number.isFinite(number)).toBe(true);
  });

  it("所有边路径都可被 <path d> 渲染（M 起、含端点、无 NaN）", () => {
    const layout = layoutOntologyGraph(ontology);
    expect(layout.edges.length).toBe(5);
    for (const edge of layout.edges) {
      expect(edge.path.startsWith("M ")).toBe(true);
      expect(edge.path.includes("L ")).toBe(true);
      expect(edge.path).not.toMatch(/NaN|Infinity/);
      expect(Number.isFinite(edge.label.x)).toBe(true);
      expect(Number.isFinite(edge.label.y)).toBe(true);
    }
  });
});
