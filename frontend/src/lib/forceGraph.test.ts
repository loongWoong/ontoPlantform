import { describe, expect, it } from "vitest";
import type { ObjectTypeSpec, Ontology, RelationSpec } from "../api";
import {
  defaultForceView,
  layoutForceGraph,
  projectForceGraph,
  type ForceGraphLayout,
  type ForceGraphMode,
} from "./forceGraph";

type ObjectOverrides = Partial<ObjectTypeSpec>;
type RelationOverrides = Partial<RelationSpec>;

function makeOntology(
  objects: Record<string, ObjectOverrides> = {},
  relations: Record<string, RelationOverrides> = {},
): Ontology {
  const builtObjects: Record<string, ObjectTypeSpec> = {};
  for (const [key, overrides] of Object.entries(objects)) {
    builtObjects[key] = {
      name: overrides.name ?? key,
      description: overrides.description ?? "",
      properties: overrides.properties ?? {},
      category: overrides.category,
      identity: overrides.identity,
      state_machine: overrides.state_machine,
    };
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
    name: "force-demo",
    description: "",
    version: 1,
    lifecycle: "published",
    objects: builtObjects,
    relations: builtRelations,
    vocabulary: {},
  };
}

const SAMPLE = makeOntology(
  {
    TollStation: {
      description: "收费站",
      properties: { station_id: { name: "station_id", type: "string" } },
    },
    Lane: { properties: { lane_id: { name: "lane_id", type: "string" } } },
    Vehicle: { description: "车辆" },
    TollTransaction: { description: "交易" },
    Invoice: {},
    Staff: {},
  },
  {
    station_lanes: { source: "TollStation", target: "Lane" },
    lane_transactions: { source: "Lane", target: "TollTransaction" },
    vehicle_transactions: { source: "Vehicle", target: "TollTransaction" },
    station_vehicles: { source: "TollStation", target: "Vehicle" },
    station_staff: { source: "TollStation", target: "Staff" },
    transaction_invoice: { source: "TollTransaction", target: "Invoice" },
  },
);

function nodeOf(layout: ForceGraphLayout, name: string) {
  const found = layout.nodes.find((node) => node.name === name);
  expect(found, `节点 ${name} 应存在`).toBeDefined();
  return found!;
}

function collectNumbers(value: unknown, numbers: number[] = []): number[] {
  if (typeof value === "number") numbers.push(value);
  else if (Array.isArray(value)) value.forEach((item) => collectNumbers(item, numbers));
  else if (value && typeof value === "object") {
    for (const item of Object.values(value as Record<string, unknown>)) collectNumbers(item, numbers);
  }
  return numbers;
}

describe("layoutForceGraph：确定性力导向布局", () => {
  it("空本体返回空节点与空聚类", () => {
    const layout = layoutForceGraph(makeOntology());
    expect(layout.nodes).toEqual([]);
    expect(layout.edges).toEqual([]);
    expect(layout.clusters).toEqual([]);
    expect(layout.objectCount).toBe(0);
  });

  it("对象按名称排序，悬空关系进入 danglingRelations", () => {
    const layout = layoutForceGraph(
      makeOntology({ B: {}, A: {} }, {
        valid: { source: "A", target: "B" },
        invalid: { source: "A", target: "Missing" },
      }),
    );
    expect(layout.nodes.map((node) => node.name)).toEqual(["A", "B"]);
    expect(layout.edges.map((edge) => edge.name)).toEqual(["valid"]);
    expect(layout.danglingRelations).toEqual(["invalid"]);
  });

  it("相同输入与种子得到完全相同的布局", () => {
    for (const mode of ["2d", "25d", "3d"] as ForceGraphMode[]) {
      expect(layoutForceGraph(SAMPLE, { mode, seed: 3 }))
        .toEqual(layoutForceGraph(SAMPLE, { mode, seed: 3 }));
    }
  });

  it("不同种子会给出不同的确定性布局", () => {
    const first = layoutForceGraph(SAMPLE, { mode: "2d", seed: 0 });
    const second = layoutForceGraph(SAMPLE, { mode: "2d", seed: 1 });
    expect(first.nodes.some((node, index) => node.x !== second.nodes[index]!.x)).toBe(true);
  });

  it("所有坐标与半径都是有限数，2D 的 z 恒为 0", () => {
    for (const mode of ["2d", "25d", "3d"] as ForceGraphMode[]) {
      const layout = layoutForceGraph(SAMPLE, { mode });
      for (const number of collectNumbers(layout)) expect(Number.isFinite(number)).toBe(true);
      if (mode === "2d") expect(layout.nodes.every((node) => node.z === 0)).toBe(true);
    }
  });

  it("聚类覆盖全部节点，计数与颜色稳定", () => {
    const layout = layoutForceGraph(SAMPLE, { mode: "3d" });
    expect(layout.clusters.length).toBeGreaterThan(0);
    const seen = layout.clusters.flatMap((cluster) => cluster.names).sort();
    expect(seen).toEqual(layout.nodes.map((node) => node.name).sort());
    expect(layout.clusters.reduce((sum, cluster) => sum + cluster.count, 0)).toBe(layout.nodes.length);
    for (const cluster of layout.clusters) {
      expect(cluster.color).toBeGreaterThanOrEqual(0);
      expect(cluster.color).toBeLessThan(5);
      expect(cluster.label).toMatch(/^聚类 \d{2}$/);
    }
  });

  it("互不相连的两组节点会被分成两个聚类", () => {
    const layout = layoutForceGraph(
      makeOntology({ A: {}, B: {}, C: {}, D: {} }, {
        ab: { source: "A", target: "B" },
        cd: { source: "C", target: "D" },
      }),
      { mode: "2d" },
    );
    expect(layout.clusters).toHaveLength(2);
    expect(layout.clusters.map((cluster) => cluster.names)).toEqual([["A", "B"], ["C", "D"]]);
    expect(nodeOf(layout, "A").cluster).toBe(nodeOf(layout, "B").cluster);
    expect(nodeOf(layout, "A").cluster).not.toBe(nodeOf(layout, "C").cluster);
  });

  it("度数越高节点半径越大，且半径有上限", () => {
    const layout = layoutForceGraph(SAMPLE, { mode: "2d" });
    expect(nodeOf(layout, "TollStation").radius).toBeGreaterThan(nodeOf(layout, "Invoice").radius);
    expect(nodeOf(layout, "TollStation").radius).toBeLessThanOrEqual(26);
  });

  it("自环与多关系不破坏布局", () => {
    const layout = layoutForceGraph(
      makeOntology({ A: {}, B: {} }, {
        self: { source: "A", target: "A" },
        first: { source: "A", target: "B" },
        second: { source: "A", target: "B" },
      }),
      { mode: "2d" },
    );
    expect(layout.edges).toHaveLength(3);
    expect(layout.edges.filter((edge) => edge.self)).toHaveLength(1);
    expect(layout.nodes.every((node) => Number.isFinite(node.x) && Number.isFinite(node.y))).toBe(true);
  });
});

describe("projectForceGraph：2D / 2.5D / 3D 投影", () => {
  it("默认视图姿态稳定且模式正确", () => {
    expect(defaultForceView("2d")).toEqual({ mode: "2d", yaw: 0, pitch: 0 });
    for (const mode of ["25d", "3d"] as const) {
      const view = defaultForceView(mode);
      expect(view.mode).toBe(mode);
      expect(Number.isFinite(view.yaw)).toBe(true);
      expect(Number.isFinite(view.pitch)).toBe(true);
    }
  });

  it("边路径、标签盒与凸包路径都是有效数值", () => {
    for (const mode of ["2d", "25d", "3d"] as ForceGraphMode[]) {
      const projected = projectForceGraph(
        layoutForceGraph(SAMPLE, { mode }),
        defaultForceView(mode),
      );
      const numbers = collectNumbers(projected);
      expect(numbers.every(Number.isFinite)).toBe(true);
      expect(projected.nodes).toHaveLength(6);
      expect(projected.edges.every((edge) => edge.path.startsWith("M "))).toBe(true);
      expect(projected.clusters.every((cluster) => cluster.path.startsWith("M "))).toBe(true);
      expect(projected.bounds.minX).toBeLessThan(projected.bounds.maxX);
      expect(projected.bounds.minY).toBeLessThan(projected.bounds.maxY);
    }
  });

  it("3D 旋转会改变投影坐标，但 2D 不受 yaw/pitch 影响", () => {
    const layout3d = layoutForceGraph(SAMPLE, { mode: "3d" });
    const base = projectForceGraph(layout3d, defaultForceView("3d"));
    const rotated = projectForceGraph(layout3d, { mode: "3d", yaw: 1.1, pitch: -0.4 });
    expect(rotated.nodes[0]!.screenX).not.toBe(base.nodes[0]!.screenX);

    const layout2d = layoutForceGraph(SAMPLE, { mode: "2d" });
    const flat = projectForceGraph(layout2d, defaultForceView("2d"));
    const alsoFlat = projectForceGraph(layout2d, { mode: "2d", yaw: 1.1, pitch: -0.4 });
    expect(alsoFlat.nodes.map((node) => node.screenX)).toEqual(flat.nodes.map((node) => node.screenX));
  });

  it("2.5D 与 3D 默认投影都有透视缩放，节点按深度从远到近排序", () => {
    for (const mode of ["25d", "3d"] as const) {
      const projected = projectForceGraph(
        layoutForceGraph(SAMPLE, { mode }),
        defaultForceView(mode),
      );
      expect(projected.nodes.some((node) => node.perspective !== 1)).toBe(true);
      const depths = projected.nodes.map((node) => node.depth);
      expect([...depths].sort((a, b) => b - a)).toEqual(depths);
    }
  });

  it("空图投影给出零包围盒而不是 Infinity", () => {
    const projected = projectForceGraph(
      layoutForceGraph(makeOntology()),
      defaultForceView("2d"),
    );
    expect(projected.nodes).toEqual([]);
    expect(projected.edges).toEqual([]);
    expect(projected.bounds).toEqual({ minX: 0, minY: 0, maxX: 0, maxY: 0 });
  });
});
