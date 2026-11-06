/** 本体关系图的力导向布局与 2D / 2.5D / 3D 投影。
 *
 * 和 `ontologyGraph.ts` 的分层布局不同，这一层是“图数据库式”的力导向模型：
 *   - 纯函数、无随机数 / 无时钟：初始位置用名称哈希，同一输入永远得到同一张图；
 *   - 先跑确定性的 Louvain 局部移动做社区聚类（类似 Neo4j GDS 的社区发现），
 *     再把聚类中心作为引力中心，避免节点均匀散开；
 *   - 2D 在平面内模拟，2.5D / 3D 在三维空间模拟；投影层统一处理透视、
 *     深度排序、聚类凸包和边标签。
 *
 * 这里不依赖 d3 / three / WebGL；投影结果仍是 SVG 路径，组件只负责交互。
 */

import type { Ontology } from "../api";
import { wrapGraphText } from "./ontologyGraph";

export type ForceGraphMode = "2d" | "25d" | "3d";

export interface ForceGraphNode {
  name: string;
  description: string;
  propertyCount: number;
  relationCount: number;
  connections: number;
  category?: string;
  x: number;
  y: number;
  z: number;
  radius: number;
  cluster: number;
}

export interface ForceGraphEdge {
  name: string;
  source: string;
  target: string;
  description: string;
  cardinality?: string;
  self: boolean;
}

export interface ForceCluster {
  id: number;
  label: string;
  color: number;
  count: number;
  names: string[];
  x: number;
  y: number;
  z: number;
}

export interface ForceGraphLayout {
  mode: ForceGraphMode;
  nodes: ForceGraphNode[];
  edges: ForceGraphEdge[];
  clusters: ForceCluster[];
  objectCount: number;
  relationCount: number;
  danglingRelations: string[];
}

export interface ForceGraphView {
  mode: ForceGraphMode;
  yaw: number;
  pitch: number;
}

export interface ProjectedForceNode extends ForceGraphNode {
  screenX: number;
  screenY: number;
  depth: number;
  perspective: number;
  screenRadius: number;
}

export interface ProjectedForceEdge extends ForceGraphEdge {
  path: string;
  depth: number;
  label: { x: number; y: number; width: number; height: number; lines: string[] };
}

export interface ProjectedForceCluster extends ForceCluster {
  path: string;
  labelX: number;
  labelY: number;
}

export interface ProjectedForceGraph {
  nodes: ProjectedForceNode[];
  edges: ProjectedForceEdge[];
  clusters: ProjectedForceCluster[];
  bounds: { minX: number; minY: number; maxX: number; maxY: number };
}

export interface ForceLayoutOptions {
  mode?: ForceGraphMode;
  /** 重排种子；相同种子始终得到相同布局。 */
  seed?: number;
}

const TWO_PI = Math.PI * 2;
const ITERATIONS = 220;
const REPULSION = 4600;
const SPRING = 0.026;
const CLUSTER_GRAVITY = 0.016;
const CENTER_GRAVITY = 0.0012;
const DAMPING = 0.72;
const MAX_SPEED = 9;
const COLLISION_GAP = 16;
const MIN_RADIUS = 14;
const PERSPECTIVE = 820;

interface Vec3 {
  x: number;
  y: number;
  z: number;
}

function compareText(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

function finite(value: number, fallback = 0): number {
  return Number.isFinite(value) ? value : fallback;
}

/** FNV-1a 的确定性 0..1 哈希，用作无随机数的初始位置。 */
function hashUnit(text: string): number {
  let hash = 2166136261;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0) / 4294967296;
}

function createNode(
  name: string,
  spec: { name: string; description?: string; properties?: Record<string, unknown>; category?: string },
  relationCount: number,
): ForceGraphNode {
  return {
    name,
    description: spec.description || "",
    propertyCount: Object.keys(spec.properties || {}).length,
    relationCount,
    connections: relationCount,
    category: spec.category,
    x: 0,
    y: 0,
    z: 0,
    // 关系越多，节点越大；但有上限，避免少数枢纽吞掉画布。
    radius: MIN_RADIUS + Math.min(12, Math.sqrt(relationCount) * 3.4),
    cluster: 0,
  };
}

/** 确定性的 Louvain 局部移动。返回每个节点的社区编号（按成员最小名称排序）。 */
function detectCommunities(names: string[], adjacency: Map<string, Map<string, number>>): number[] {
  const count = names.length;
  if (!count) return [];
  const indexOf = new Map(names.map((name, index) => [name, index]));
  const degree = names.map((name) => {
    const neighbours = adjacency.get(name);
    if (!neighbours) return 0;
    let total = 0;
    for (const weight of neighbours.values()) total += weight;
    return total;
  });
  const totalDegree = degree.reduce((sum, value) => sum + value, 0);
  if (totalDegree <= 0) return names.map((_, index) => index);

  let communities = names.map((_, index) => index);
  let totals = [...degree];

  for (let pass = 0; pass < 16; pass += 1) {
    let moved = false;
    for (let nodeIndex = 0; nodeIndex < count; nodeIndex += 1) {
      const name = names[nodeIndex]!;
      const current = communities[nodeIndex]!;
      const nodeDegree = degree[nodeIndex]!;
      if (nodeDegree <= 0) continue;

      // 先把节点从当前社区移出，再比较所有邻居社区；这是 Louvain 的局部移动。
      totals[current] = (totals[current] || 0) - nodeDegree;
      const links = new Map<number, number>();
      const neighbours = adjacency.get(name);
      if (neighbours) {
        for (const [neighbour, weight] of neighbours) {
          const target = indexOf.get(neighbour);
          if (target === undefined || target === nodeIndex) continue;
          const community = communities[target]!;
          links.set(community, (links.get(community) || 0) + weight);
        }
      }

      let best = current;
      let bestGain = 0;
      for (const [community, weight] of links) {
        const gain = weight / totalDegree
          - (totals[community] || 0) * nodeDegree / (totalDegree * totalDegree);
        if (
          gain > bestGain + 1e-12
          || (Math.abs(gain - bestGain) <= 1e-12 && community < best)
        ) {
          best = community;
          bestGain = gain;
        }
      }
      if (best !== current) moved = true;
      communities[nodeIndex] = best;
      totals[best] = (totals[best] || 0) + nodeDegree;
    }
    if (!moved) break;
  }

  // 社区编号按成员最小名称重新排序，保证跨运行稳定。
  const firstMember = new Map<number, string>();
  for (let index = 0; index < count; index += 1) {
    const community = communities[index]!;
    const current = firstMember.get(community);
    if (current === undefined || names[index]! < current) firstMember.set(community, names[index]!);
  }
  const sorted = [...firstMember.keys()].sort((a, b) => compareText(firstMember.get(a)!, firstMember.get(b)!));
  const normalized = new Map(sorted.map((community, index) => [community, index]));
  return communities.map((community) => normalized.get(community) ?? 0);
}

function buildAdjacency(
  names: string[],
  edges: ForceGraphEdge[],
): Map<string, Map<string, number>> {
  const adjacency = new Map<string, Map<string, number>>();
  for (const name of names) adjacency.set(name, new Map());
  for (const edge of edges) {
    if (edge.self) continue;
    const from = adjacency.get(edge.source);
    const to = adjacency.get(edge.target);
    if (from) from.set(edge.target, (from.get(edge.target) || 0) + 1);
    if (to) to.set(edge.source, (to.get(edge.source) || 0) + 1);
  }
  return adjacency;
}

function clusterCenter(index: number, count: number, mode: ForceGraphMode, seed: number): Vec3 {
  if (count <= 1) return { x: 0, y: 0, z: 0 };
  if (mode === "2d") {
    const radius = 64 + count * 52;
    const angle = (index / count) * TWO_PI - Math.PI / 2;
    return {
      x: Math.cos(angle) * radius + (hashUnit(`center:${index}:${seed}`) - 0.5) * 24,
      y: Math.sin(angle) * radius * 0.72 + (hashUnit(`center-y:${index}:${seed}`) - 0.5) * 20,
      z: 0,
    };
  }
  const offset = 2 / count;
  const vertical = -1 + offset / 2 + index * offset;
  const radial = Math.sqrt(Math.max(0, 1 - vertical * vertical));
  const spread = 126 + count * 24;
  const angle = index * 2.399963 + seed * 0.71;
  return {
    x: Math.cos(angle) * radial * spread + (hashUnit(`center-x:${index}:${seed}`) - 0.5) * 30,
    y: vertical * spread * 0.78,
    z: Math.sin(angle) * radial * spread + (hashUnit(`center-z:${index}:${seed}`) - 0.5) * 30,
  };
}

function simulate(
  nodes: ForceGraphNode[],
  edges: ForceGraphEdge[],
  mode: ForceGraphMode,
  seed: number,
): void {
  const count = nodes.length;
  if (!count) return;
  const is3d = mode !== "2d";
  const clusterCount = Math.max(1, Math.max(...nodes.map((node) => node.cluster)) + 1);
  const centers = Array.from({ length: clusterCount }, (_, index) =>
    clusterCenter(index, clusterCount, mode, seed));

  const position = nodes.map((node) => {
    const center = centers[node.cluster] || centers[0]!;
    const angle = hashUnit(`angle:${node.name}:${seed}`) * TWO_PI;
    const distance = 12 + hashUnit(`distance:${node.name}:${seed}`) * 46;
    return {
      x: center.x + Math.cos(angle) * distance,
      y: center.y + Math.sin(angle) * distance * (is3d ? 0.82 : 1),
      z: is3d ? center.z + (hashUnit(`depth:${node.name}:${seed}`) - 0.5) * 44 : 0,
    };
  });
  const velocity = nodes.map(() => ({ x: 0, y: 0, z: 0 }));
  const radius = nodes.map((node) => node.radius);
  const targetLength = mode === "2d" ? 92 : 108;

  for (let iteration = 0; iteration < ITERATIONS; iteration += 1) {
    const force = nodes.map(() => ({ x: 0, y: 0, z: 0 }));

    // 1) 节点两两排斥（Coulomb），距离过近时额外加强。
    for (let i = 0; i < count; i += 1) {
      for (let j = i + 1; j < count; j += 1) {
        const dx = position[j]!.x - position[i]!.x;
        const dy = position[j]!.y - position[i]!.y;
        const dz = is3d ? position[j]!.z - position[i]!.z : 0;
        const distanceSq = dx * dx + dy * dy + dz * dz + 16;
        const distance = Math.sqrt(distanceSq);
        const strength = Math.min(4.2, REPULSION / distanceSq);
        const nx = dx / distance;
        const ny = dy / distance;
        const nz = is3d ? dz / distance : 0;
        force[i]!.x -= nx * strength;
        force[i]!.y -= ny * strength;
        force[i]!.z -= nz * strength;
        force[j]!.x += nx * strength;
        force[j]!.y += ny * strength;
        force[j]!.z += nz * strength;
      }
    }

    // 2) 关系弹簧；同一条关系只拉一次，重边通过 weight 自然增强。
    for (const edge of edges) {
      if (edge.self) continue;
      const sourceIndex = nodes.findIndex((node) => node.name === edge.source);
      const targetIndex = nodes.findIndex((node) => node.name === edge.target);
      if (sourceIndex < 0 || targetIndex < 0) continue;
      const dx = position[targetIndex]!.x - position[sourceIndex]!.x;
      const dy = position[targetIndex]!.y - position[sourceIndex]!.y;
      const dz = is3d ? position[targetIndex]!.z - position[sourceIndex]!.z : 0;
      const distance = Math.sqrt(dx * dx + dy * dy + dz * dz) || 0.001;
      const strength = SPRING * (distance - targetLength);
      const nx = dx / distance;
      const ny = dy / distance;
      const nz = is3d ? dz / distance : 0;
      force[sourceIndex]!.x += nx * strength;
      force[sourceIndex]!.y += ny * strength;
      force[sourceIndex]!.z += nz * strength;
      force[targetIndex]!.x -= nx * strength;
      force[targetIndex]!.y -= ny * strength;
      force[targetIndex]!.z -= nz * strength;
    }

    // 3) 聚类引力 + 全局向心引力：让同一社区的节点靠拢，同时不整体漂走。
    for (let index = 0; index < count; index += 1) {
      const node = nodes[index]!;
      const center = centers[node.cluster] || centers[0]!;
      force[index]!.x += (center.x - position[index]!.x) * CLUSTER_GRAVITY;
      force[index]!.y += (center.y - position[index]!.y) * CLUSTER_GRAVITY;
      force[index]!.z += is3d ? (center.z - position[index]!.z) * CLUSTER_GRAVITY : 0;
      force[index]!.x -= position[index]!.x * CENTER_GRAVITY;
      force[index]!.y -= position[index]!.y * CENTER_GRAVITY;
      force[index]!.z -= is3d ? position[index]!.z * CENTER_GRAVITY : 0;
    }

    // 4) 碰撞修正，避免圆形节点互相覆盖。
    for (let i = 0; i < count; i += 1) {
      for (let j = i + 1; j < count; j += 1) {
        const minDistance = radius[i]! + radius[j]! + COLLISION_GAP;
        const dx = position[j]!.x - position[i]!.x;
        const dy = position[j]!.y - position[i]!.y;
        const dz = is3d ? position[j]!.z - position[i]!.z : 0;
        const distance = Math.sqrt(dx * dx + dy * dy + dz * dz) || 0.001;
        if (distance >= minDistance) continue;
        const overlap = (minDistance - distance) * 0.5;
        const nx = dx / distance;
        const ny = dy / distance;
        const nz = is3d ? dz / distance : 0;
        force[i]!.x -= nx * overlap;
        force[i]!.y -= ny * overlap;
        force[i]!.z -= nz * overlap;
        force[j]!.x += nx * overlap;
        force[j]!.y += ny * overlap;
        force[j]!.z += nz * overlap;
      }
    }

    for (let index = 0; index < count; index += 1) {
      const v = velocity[index]!;
      v.x = (v.x + force[index]!.x) * DAMPING;
      v.y = (v.y + force[index]!.y) * DAMPING;
      v.z = is3d ? (v.z + force[index]!.z) * DAMPING : 0;
      const speed = Math.sqrt(v.x * v.x + v.y * v.y + v.z * v.z);
      if (speed > MAX_SPEED) {
        const scale = MAX_SPEED / speed;
        v.x *= scale;
        v.y *= scale;
        v.z *= scale;
      }
      position[index]!.x += v.x;
      position[index]!.y += v.y;
      position[index]!.z = is3d ? position[index]!.z + v.z : 0;
    }
  }

  // 去掉整体平移，让投影以原点为中心。
  let centerX = 0;
  let centerY = 0;
  let centerZ = 0;
  for (const point of position) {
    centerX += point.x;
    centerY += point.y;
    centerZ += point.z;
  }
  centerX /= count;
  centerY /= count;
  centerZ /= count;

  nodes.forEach((node, index) => {
    node.x = finite(position[index]!.x - centerX);
    node.y = finite(position[index]!.y - centerY);
    node.z = finite(is3d ? position[index]!.z - centerZ : 0);
  });

  // 力导向的绝对坐标没有视觉意义；把图的跨度归一到与节点数匹配的区间，
  // 避免 4 个节点的图在 750px 画布里只占中间一小块，也避免大图被拉得过散。
  // 只缩放位置、不缩放节点半径，因此投影层仍按真实尺寸计算圆与标签。
  let minX = Infinity;
  let minY = Infinity;
  let minZ = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  let maxZ = -Infinity;
  for (const node of nodes) {
    minX = Math.min(minX, node.x);
    minY = Math.min(minY, node.y);
    minZ = Math.min(minZ, node.z);
    maxX = Math.max(maxX, node.x);
    maxY = Math.max(maxY, node.y);
    maxZ = Math.max(maxZ, node.z);
  }
  const span = Math.max(maxX - minX, maxY - minY, is3d ? maxZ - minZ : 0);
  const targetSpan = Math.min(1400, Math.max(360, 280 + count * 55));
  if (span > 0.001) {
    const scale = targetSpan / span;
    for (const node of nodes) {
      node.x *= scale;
      node.y *= scale;
      node.z *= scale;
    }
  }
}

/** 从本体生成确定性的力导向布局；无效关系与空字段都按降级处理。 */
export function layoutForceGraph(
  ontology: Ontology,
  options: ForceLayoutOptions = {},
): ForceGraphLayout {
  const mode = options.mode || "2d";
  const seed = Number.isFinite(options.seed) ? Number(options.seed) : 0;
  const objects = ontology.objects || {};
  const relations = ontology.relations || {};
  const names = Object.keys(objects).sort(compareText);
  const valid = new Set(names);
  const edges: ForceGraphEdge[] = [];
  const danglingRelations: string[] = [];
  const relationCount = new Map<string, number>(names.map((name) => [name, 0]));

  for (const [name, relation] of Object.entries(relations)) {
    if (!valid.has(relation.source) || !valid.has(relation.target)) {
      danglingRelations.push(name);
      continue;
    }
    edges.push({
      name,
      source: relation.source,
      target: relation.target,
      description: relation.description || "",
      cardinality: relation.cardinality,
      self: relation.source === relation.target,
    });
    relationCount.set(relation.source, (relationCount.get(relation.source) || 0) + 1);
    relationCount.set(relation.target, (relationCount.get(relation.target) || 0) + 1);
  }

  const nodes = names.map((name) =>
    createNode(name, objects[name] || { name }, relationCount.get(name) || 0));
  const adjacency = buildAdjacency(names, edges);
  const communities = detectCommunities(names, adjacency);
  nodes.forEach((node, index) => {
    node.cluster = communities[index] || 0;
  });

  // 聚类标签与颜色按“成员最小名称”排序；颜色循环只影响视觉，不改变布局。
  const clusterNames = new Map<number, string[]>();
  for (const node of nodes) {
    const list = clusterNames.get(node.cluster) || [];
    list.push(node.name);
    clusterNames.set(node.cluster, list);
  }
  const clusters: ForceCluster[] = [...clusterNames.entries()]
    .sort((a, b) => compareText(a[1].sort(compareText)[0] || "", b[1].sort(compareText)[0] || ""))
    .map(([id, members], index) => ({
      id,
      label: `聚类 ${String(index + 1).padStart(2, "0")}`,
      color: index % 5,
      count: members.length,
      names: members.sort(compareText),
      x: 0,
      y: 0,
      z: 0,
    }));
  const clusterByIndex = new Map(clusters.map((cluster) => [cluster.id, cluster]));
  nodes.forEach((node) => {
    const cluster = clusterByIndex.get(node.cluster);
    if (cluster) node.cluster = cluster.id;
  });

  simulate(nodes, edges, mode, seed);

  for (const cluster of clusters) {
    const members = nodes.filter((node) => node.cluster === cluster.id);
    if (!members.length) continue;
    cluster.x = members.reduce((sum, node) => sum + node.x, 0) / members.length;
    cluster.y = members.reduce((sum, node) => sum + node.y, 0) / members.length;
    cluster.z = members.reduce((sum, node) => sum + node.z, 0) / members.length;
  }

  return {
    mode,
    nodes,
    edges,
    clusters,
    objectCount: names.length,
    relationCount: edges.length,
    danglingRelations,
  };
}

/** 三种模式的默认相机姿态；2.5D 是固定斜视角，3D 可继续旋转。 */
export function defaultForceView(mode: ForceGraphMode): ForceGraphView {
  if (mode === "2d") return { mode, yaw: 0, pitch: 0 };
  // 2.5D 用更接近等轴测的固定斜视角，和 3D 的默认相机拉开差异。
  if (mode === "25d") return { mode, yaw: -0.78, pitch: 0.46 };
  return { mode, yaw: -0.42, pitch: 0.16 };
}

function projectPoint(node: Vec3, view: ForceGraphView): { x: number; y: number; depth: number } {
  if (view.mode === "2d") return { x: node.x, y: node.y, depth: 0 };
  const cosYaw = Math.cos(view.yaw);
  const sinYaw = Math.sin(view.yaw);
  const x1 = node.x * cosYaw - node.z * sinYaw;
  const z1 = node.x * sinYaw + node.z * cosYaw;
  const cosPitch = Math.cos(view.pitch);
  const sinPitch = Math.sin(view.pitch);
  const y1 = node.y * cosPitch - z1 * sinPitch;
  const depth = node.y * sinPitch + z1 * cosPitch;
  const perspective = Math.max(0.6, Math.min(1.5, PERSPECTIVE / Math.max(200, PERSPECTIVE + depth)));
  return { x: x1 * perspective, y: y1 * perspective, depth };
}

function lineUnits(text: string): number {
  let units = 0;
  for (const char of Array.from(text)) {
    units += /[^\x00-\x7f]/u.test(char) ? 1 : 0.56;
  }
  return units;
}

function edgeLabel(name: string): { width: number; height: number; lines: string[] } {
  const lines = wrapGraphText(name, 8);
  const maxUnits = Math.max(...lines.map(lineUnits), 0);
  return {
    lines,
    width: Math.max(52, maxUnits * 11 + 16),
    height: lines.length * 15 + 10,
  };
}

function smoothClosedPath(points: { x: number; y: number }[]): string {
  if (points.length < 3) return "";
  const first = points[0]!;
  const last = points[points.length - 1]!;
  const startX = (last.x + first.x) / 2;
  const startY = (last.y + first.y) / 2;
  let path = `M ${startX.toFixed(2)} ${startY.toFixed(2)}`;
  for (let index = 0; index < points.length; index += 1) {
    const point = points[index]!;
    const next = points[(index + 1) % points.length]!;
    const middleX = (point.x + next.x) / 2;
    const middleY = (point.y + next.y) / 2;
    path += ` Q ${point.x.toFixed(2)} ${point.y.toFixed(2)} ${middleX.toFixed(2)} ${middleY.toFixed(2)}`;
  }
  return `${path} Z`;
}

function convexHull(points: { x: number; y: number }[]): { x: number; y: number }[] {
  if (points.length < 3) return points;
  const sorted = [...points].sort((a, b) => a.x - b.x || a.y - b.y);
  const cross = (o: { x: number; y: number }, a: { x: number; y: number }, b: { x: number; y: number }) =>
    (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
  const lower: { x: number; y: number }[] = [];
  for (const point of sorted) {
    while (lower.length >= 2 && cross(lower[lower.length - 2]!, lower[lower.length - 1]!, point) <= 0) {
      lower.pop();
    }
    lower.push(point);
  }
  const upper: { x: number; y: number }[] = [];
  for (let index = sorted.length - 1; index >= 0; index -= 1) {
    const point = sorted[index]!;
    while (upper.length >= 2 && cross(upper[upper.length - 2]!, upper[upper.length - 1]!, point) <= 0) {
      upper.pop();
    }
    upper.push(point);
  }
  upper.pop();
  lower.pop();
  return lower.concat(upper);
}

function trimPoint(
  from: { x: number; y: number },
  toward: { x: number; y: number },
  distance: number,
): { x: number; y: number } {
  const dx = toward.x - from.x;
  const dy = toward.y - from.y;
  const length = Math.hypot(dx, dy) || 1;
  return { x: from.x + (dx / length) * distance, y: from.y + (dy / length) * distance };
}

/** 把 2D/2.5D/3D 布局投影成 SVG 可直接渲染的路径与标签盒。 */
export function projectForceGraph(layout: ForceGraphLayout, view: ForceGraphView): ProjectedForceGraph {
  const projectedNodes: ProjectedForceNode[] = layout.nodes.map((node) => {
    const point = projectPoint(node, view);
    const perspective = view.mode === "2d"
      ? 1
      : Math.max(0.6, Math.min(1.5, PERSPECTIVE / Math.max(200, PERSPECTIVE + point.depth)));
    return {
      ...node,
      screenX: point.x,
      screenY: point.y,
      depth: point.depth,
      perspective,
      screenRadius: node.radius * perspective,
    };
  });
  const byName = new Map(projectedNodes.map((node) => [node.name, node]));

  const edges: ProjectedForceEdge[] = layout.edges.map((edge, index) => {
    const source = byName.get(edge.source);
    const target = byName.get(edge.target);
    const label = edgeLabel(edge.name);
    if (!source || !target) {
      return { ...edge, path: "", depth: 0, label: { ...label, x: 0, y: 0 } };
    }
    let path = "";
    let mid = { x: (source.screenX + target.screenX) / 2, y: (source.screenY + target.screenY) / 2 };
    if (edge.self) {
      const radius = Math.max(18, source.screenRadius);
      path = `M ${(source.screenX + radius * 0.15).toFixed(2)} ${(source.screenY - radius).toFixed(2)} C ${(source.screenX + radius * 2.1).toFixed(2)} ${(source.screenY - radius * 1.7).toFixed(2)}, ${(source.screenX + radius * 2.1).toFixed(2)} ${(source.screenY + radius * 1.5).toFixed(2)}, ${(source.screenX + radius * 0.1).toFixed(2)} ${(source.screenY + radius).toFixed(2)}`;
      mid = { x: source.screenX + radius * 1.3, y: source.screenY - radius * 0.2 };
    } else {
      const dx = target.screenX - source.screenX;
      const dy = target.screenY - source.screenY;
      const length = Math.hypot(dx, dy) || 1;
      const normalX = -dy / length;
      const normalY = dx / length;
      const direction = hashUnit(`edge:${edge.name}`) > 0.5 ? 1 : -1;
      const curve = direction * (12 + (index % 3) * 7);
      const control = {
        x: (source.screenX + target.screenX) / 2 + normalX * curve,
        y: (source.screenY + target.screenY) / 2 + normalY * curve,
      };
      const start = trimPoint(
        { x: source.screenX, y: source.screenY },
        control,
        source.screenRadius + 5,
      );
      const end = trimPoint(
        { x: target.screenX, y: target.screenY },
        control,
        target.screenRadius + 8,
      );
      path = `M ${start.x.toFixed(2)} ${start.y.toFixed(2)} Q ${control.x.toFixed(2)} ${control.y.toFixed(2)} ${end.x.toFixed(2)} ${end.y.toFixed(2)}`;
      mid = {
        x: 0.25 * start.x + 0.5 * control.x + 0.25 * end.x,
        y: 0.25 * start.y + 0.5 * control.y + 0.25 * end.y,
      };
    }
    return {
      ...edge,
      path,
      depth: (source.depth + target.depth) / 2,
      label: {
        ...label,
        x: mid.x - label.width / 2,
        y: mid.y - label.height / 2,
      },
    };
  });

  const clusters: ProjectedForceCluster[] = layout.clusters.map((cluster) => {
    const members = projectedNodes.filter((node) => node.cluster === cluster.id);
    if (!members.length) return { ...cluster, path: "", labelX: 0, labelY: 0 };
    const points: { x: number; y: number }[] = [];
    for (const node of members) {
      const radius = Math.max(14, node.screenRadius) + 14;
      for (let step = 0; step < 8; step += 1) {
        const angle = (step / 8) * TWO_PI;
        points.push({
          x: node.screenX + Math.cos(angle) * radius,
          y: node.screenY + Math.sin(angle) * radius,
        });
      }
    }
    const hull = convexHull(points);
    const path = smoothClosedPath(hull);
    const top = Math.min(...points.map((point) => point.y));
    const center = {
      x: members.reduce((sum, node) => sum + node.screenX, 0) / members.length,
      y: members.reduce((sum, node) => sum + node.screenY, 0) / members.length,
    };
    return {
      ...cluster,
      path,
      labelX: center.x,
      labelY: top - 7,
    };
  });

  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const node of projectedNodes) {
    // 节点圆、下方名称、以及聚类凸包额外留出的 14 单位。
    minX = Math.min(minX, node.screenX - node.screenRadius - 16);
    minY = Math.min(minY, node.screenY - node.screenRadius - 20);
    maxX = Math.max(maxX, node.screenX + node.screenRadius + 16);
    maxY = Math.max(maxY, node.screenY + node.screenRadius + 46);
  }
  if (!Number.isFinite(minX)) {
    minX = 0;
    minY = 0;
    maxX = 0;
    maxY = 0;
  }

  return {
    nodes: [...projectedNodes].sort((a, b) => b.depth - a.depth),
    edges: [...edges].sort((a, b) => b.depth - a.depth),
    clusters,
    bounds: { minX, minY, maxX, maxY },
  };
}
