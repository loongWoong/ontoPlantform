/** 本体关系图的纯布局计算。
 *
 * 输入是平台 REST 契约里的 `Ontology`（对象类型 + 关系 + 词表），输出是可直接
 * 交给 SVG 渲染的节点/边/标签几何。这一层不碰 DOM、不引第三方布局引擎
 * （elkjs/d3 等），也绝不读取随机数或时钟：同一份本体多次布局必须深度相等，
 * 这样组件层可以放心用 useMemo 缓存，也方便契约测试。
 *
 * 分层规则（从左到右）：
 *   1. 只有 objects 的键才是图节点；relations 的 source/target 必须都在
 *      objects 中，否则该关系被丢弃并记入 danglingRelations（保留声明顺序）；
 *   2. 沿 source -> target 做迭代 DFS 判环，指向「正在访问」节点的边标记为
 *      back（自环也属于 back，但 self 优先级更高）；
 *   3. 忽略 back 边做 longest-path 分层：孤立节点在第 0 层；环图不会死循环；
 *   4. 层内按「前驱平均位置, 名称」做 2 轮重心排序，保证结果确定。
 */

import type { Ontology } from "../api";

export interface GraphNode {
  name: string;
  description: string;
  propertyCount: number;
  /** 关联关系数（入向 + 出向；self 关系只计一次，与 connections 同义）。 */
  relationCount: number;
  /** 参考实现沿用字段名：与 relationCount 相同的关联边数，供卡片计数展示。 */
  connections: number;
  lines: string[];
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface GraphEdge {
  name: string;
  source: string;
  target: string;
  description: string;
  cardinality?: string;
  self: boolean;
  back: boolean;
  path: string;
  label: {
    x: number;
    y: number;
    width: number;
    height: number;
    lines: string[];
  };
}

export interface OntologyGraphLayout {
  width: number;
  height: number;
  nodes: GraphNode[];
  edges: GraphEdge[];
  objectCount: number;
  relationCount: number;
  danglingRelations: string[];
}

/** 节点卡片几何常量（契约固定值）。 */
const NODE_WIDTH = 216;
const NODE_BASE_HEIGHT = 76;
const NODE_LINE_HEIGHT = 20;
const PADDING = 44;
const LAYER_GAP = 128;
const ROW_GAP = 36;
const MIN_CANVAS = 88;

/** 节点名按 11 个「字宽单位」换行；边标签按 8 个换行。 */
const NODE_NAME_UNITS = 11;
const EDGE_LABEL_UNITS = 8;
const EDGE_LABEL_MIN_WIDTH = 52;
const EDGE_LABEL_UNIT_PX = 11;
const EDGE_LABEL_LINE_HEIGHT = 15;
const EDGE_LABEL_PADDING = 10;
const CORNER_RADIUS = 12;

const WHITE = 0;
const GRAY = 1;
const BLACK = 2;

interface Point {
  x: number;
  y: number;
}

interface EdgeWork {
  name: string;
  source: string;
  target: string;
  description: string;
  cardinality?: string;
  self: boolean;
  back: boolean;
}

interface EdgeSize {
  lines: string[];
  width: number;
  height: number;
}

/** 字符宽度单位：CJK 等全角字符算 1，ASCII 约 0.56，用于确定性换行。 */
function textUnits(text: string): number {
  let units = 0;
  for (const char of Array.from(text)) {
    units += /[^\x00-\x7f]/u.test(char) ? 1 : 0.56;
  }
  return units;
}

function compareText(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

function centerX(node: Point & { width: number }): number {
  return node.x + node.width / 2;
}

function buildEdgeSize(
  name: string,
  maxUnits = EDGE_LABEL_UNITS,
  minWidth = EDGE_LABEL_MIN_WIDTH,
): EdgeSize {
  const lines = wrapGraphText(name, maxUnits);
  const maxLineUnits = Math.max(...lines.map(textUnits), 0);
  return {
    lines,
    width: Math.max(minWidth, maxLineUnits * EDGE_LABEL_UNIT_PX + 16),
    height: lines.length * EDGE_LABEL_LINE_HEIGHT + EDGE_LABEL_PADDING,
  };
}

/** 按字宽单位换行；CJK 与 ASCII 混排也保持确定。空串返回 `[""]`。 */
export function wrapGraphText(text: string, maxUnits: number): string[] {
  const lines: string[] = [];
  let line = "";
  for (const char of Array.from(text ?? "")) {
    if (line && (char === "\n" || textUnits(line + char) > maxUnits)) {
      lines.push(line);
      line = "";
    }
    if (char !== "\n") line += char;
  }
  if (line) lines.push(line);
  return lines.length ? lines : [""];
}

/**
 * 把折线转成 SVG path：转角用二次贝塞尔（Q）倒角，半径不超过 12；首个点用 M、
 * 末个点用 L 收尾，保证箭头方向由最后一段决定。空数组返回空串，单点只给 M。
 */
export function roundedGraphPath(points: Array<{ x: number; y: number }>): string {
  if (!points.length) return "";
  const clean: Point[] = [];
  for (const point of points) {
    const previous = clean[clean.length - 1];
    if (previous && previous.x === point.x && previous.y === point.y) continue;
    clean.push({ x: point.x, y: point.y });
  }
  const start = clean[0];
  if (!start) return "";
  if (clean.length === 1) return `M ${start.x} ${start.y}`;
  let path = `M ${start.x} ${start.y}`;
  for (let index = 1; index < clean.length - 1; index++) {
    const previous = clean[index - 1]!;
    const corner = clean[index]!;
    const next = clean[index + 1]!;
    const incoming = Math.hypot(corner.x - previous.x, corner.y - previous.y);
    const outgoing = Math.hypot(next.x - corner.x, next.y - corner.y);
    if (!incoming || !outgoing) continue;
    const radius = Math.min(CORNER_RADIUS, incoming / 2, outgoing / 2);
    const before = {
      x: corner.x + ((previous.x - corner.x) * radius) / incoming,
      y: corner.y + ((previous.y - corner.y) * radius) / incoming,
    };
    const after = {
      x: corner.x + ((next.x - corner.x) * radius) / outgoing,
      y: corner.y + ((next.y - corner.y) * radius) / outgoing,
    };
    path += ` L ${before.x} ${before.y} Q ${corner.x} ${corner.y} ${after.x} ${after.y}`;
  }
  const end = clean[clean.length - 1]!;
  return `${path} L ${end.x} ${end.y}`;
}

function emptyLayout(): OntologyGraphLayout {
  return {
    width: MIN_CANVAS,
    height: MIN_CANVAS,
    nodes: [],
    edges: [],
    objectCount: 0,
    relationCount: 0,
    danglingRelations: [],
  };
}

/**
 * 计算一张本体关系图：从左到右分层、层内垂直居中、正交圆角连线。
 *
 * 纯函数：相同输入永远得到深度相等的结果，且不产生 NaN / Infinity。
 * 空本体返回 88×88 的空画布，组件据此显示空态。
 */
export function layoutOntologyGraph(ontology: Ontology): OntologyGraphLayout {
  if (!ontology) return emptyLayout();
  const objects = ontology.objects ?? {};
  const relations = ontology.relations ?? {};
  const objectKeys = Object.keys(objects);
  if (!objectKeys.length) return emptyLayout();

  const objectSet = new Set(objectKeys);

  // ---- 1. 节点 + 有效关系（悬空关系丢弃，保留声明顺序） ----
  const nodeName = new Map<string, string>();
  const nodeDescription = new Map<string, string>();
  const nodePropertyCount = new Map<string, number>();
  const nodeLines = new Map<string, string[]>();
  const nodeHeight = new Map<string, number>();
  const incident = new Map<string, number>();
  for (const key of objectKeys) {
    const spec = objects[key];
    // 平台契约里 objects 以对象类型名称为键；组件也把 name 当作 identity，
    // 因此节点 name 直接取键，保证选中/聚焦过滤与 relations 的 source/target 一致。
    const name = key;
    const lines = wrapGraphText(name, NODE_NAME_UNITS);
    nodeName.set(key, name);
    nodeDescription.set(key, spec?.description ?? "");
    nodePropertyCount.set(
      key,
      spec?.properties ? Object.keys(spec.properties).length : 0,
    );
    nodeLines.set(key, lines);
    nodeHeight.set(key, NODE_BASE_HEIGHT + (lines.length - 1) * NODE_LINE_HEIGHT);
    incident.set(key, 0);
  }

  const edges: EdgeWork[] = [];
  const danglingRelations: string[] = [];
  for (const key of Object.keys(relations)) {
    const relation = relations[key];
    if (!relation) {
      danglingRelations.push(key);
      continue;
    }
    const source = typeof relation.source === "string" ? relation.source : "";
    const target = typeof relation.target === "string" ? relation.target : "";
    if (!objectSet.has(source) || !objectSet.has(target)) {
      danglingRelations.push(key);
      continue;
    }
    const self = source === target;
    edges.push({
      name: key,
      source,
      target,
      description: relation.description ?? "",
      ...(relation.cardinality ? { cardinality: relation.cardinality } : {}),
      self,
      back: false,
    });
    incident.set(source, (incident.get(source) ?? 0) + 1);
    if (!self) incident.set(target, (incident.get(target) ?? 0) + 1);
  }

  const outgoing = new Map<string, number[]>();
  for (let index = 0; index < edges.length; index++) {
    const edge = edges[index]!;
    const list = outgoing.get(edge.source);
    if (list) list.push(index);
    else outgoing.set(edge.source, [index]);
  }

  // ---- 2. 迭代 DFS 判环：指向灰色（正在访问）节点的边标记 back ----
  const color = new Map<string, number>();
  for (const key of objectKeys) color.set(key, WHITE);
  interface Frame {
    node: string;
    edgeIndexes: number[];
    next: number;
  }
  for (const start of objectKeys) {
    if (color.get(start) !== WHITE) continue;
    color.set(start, GRAY);
    const stack: Frame[] = [
      { node: start, edgeIndexes: outgoing.get(start) ?? [], next: 0 },
    ];
    while (stack.length) {
      const frame = stack[stack.length - 1]!;
      if (frame.next >= frame.edgeIndexes.length) {
        color.set(frame.node, BLACK);
        stack.pop();
        continue;
      }
      const edge = edges[frame.edgeIndexes[frame.next++]!]!;
      const targetColor = color.get(edge.target)!;
      if (targetColor === WHITE) {
        color.set(edge.target, GRAY);
        stack.push({
          node: edge.target,
          edgeIndexes: outgoing.get(edge.target) ?? [],
          next: 0,
        });
      } else if (targetColor === GRAY) {
        edge.back = true;
      }
    }
  }

  // ---- 3. 忽略 back 边做 longest-path 分层（Kahn 拓扑序） ----
  const layer = new Map<string, number>();
  const indegree = new Map<string, number>();
  for (const key of objectKeys) {
    layer.set(key, 0);
    indegree.set(key, 0);
  }
  const forwardOut = new Map<string, number[]>();
  for (let index = 0; index < edges.length; index++) {
    const edge = edges[index]!;
    if (edge.back) continue;
    indegree.set(edge.target, (indegree.get(edge.target) ?? 0) + 1);
    const list = forwardOut.get(edge.source);
    if (list) list.push(index);
    else forwardOut.set(edge.source, [index]);
  }
  const remaining = new Set(objectKeys);
  let progressed = true;
  while (progressed) {
    progressed = false;
    for (const key of objectKeys) {
      if (!remaining.has(key) || (indegree.get(key) ?? 0) !== 0) continue;
      remaining.delete(key);
      progressed = true;
      for (const index of forwardOut.get(key) ?? []) {
        const edge = edges[index]!;
        const nextLayer = (layer.get(key) ?? 0) + 1;
        if ((layer.get(edge.target) ?? 0) < nextLayer) {
          layer.set(edge.target, nextLayer);
        }
        indegree.set(edge.target, (indegree.get(edge.target) ?? 0) - 1);
      }
    }
  }

  // ---- 4. 层内重心排序（只针对第 1 层及以上，2 轮） ----
  const maxLayer = Math.max(...objectKeys.map((key) => layer.get(key) ?? 0), 0);
  const groups: string[][] = Array.from({ length: maxLayer + 1 }, () => []);
  for (const key of objectKeys) groups[layer.get(key) ?? 0]!.push(key);

  const predecessors = new Map<string, string[]>();
  for (const edge of edges) {
    if (edge.back || edge.self) continue;
    const list = predecessors.get(edge.target);
    if (list) list.push(edge.source);
    else predecessors.set(edge.target, [edge.source]);
  }
  const position = new Map<string, number>();
  groups.forEach((group) => group.forEach((key, index) => position.set(key, index)));
  for (let round = 0; round < 2; round++) {
    for (let index = 1; index < groups.length; index++) {
      const group = groups[index]!;
      const scored = group.map((key, currentIndex) => {
        const previous = (predecessors.get(key) ?? []).filter(
          (source) => (layer.get(source) ?? 0) === index - 1,
        );
        const barycenter = previous.length
          ? previous.reduce((sum, source) => sum + (position.get(source) ?? 0), 0) /
            previous.length
          : currentIndex;
        return { key, barycenter, currentIndex };
      });
      scored.sort(
        (a, b) =>
          a.barycenter - b.barycenter ||
          compareText(a.key, b.key) ||
          a.currentIndex - b.currentIndex,
      );
      groups[index] = scored.map((entry) => entry.key);
      groups[index]!.forEach((key, indexInLayer) => position.set(key, indexInLayer));
    }
  }

  // ---- 5. 坐标：每层垂直居中于最高层 ----
  const stackHeight = groups.map(
    (group) =>
      group.reduce((sum, key) => sum + (nodeHeight.get(key) ?? NODE_BASE_HEIGHT), 0) +
      ROW_GAP * Math.max(0, group.length - 1),
  );
  const tallest = Math.max(...stackHeight, 0);
  const nodePos = new Map<string, Point>();
  groups.forEach((group, index) => {
    const x = PADDING + index * (NODE_WIDTH + LAYER_GAP);
    let y = PADDING + (tallest - stackHeight[index]!) / 2;
    for (const key of group) {
      nodePos.set(key, { x, y });
      y += (nodeHeight.get(key) ?? NODE_BASE_HEIGHT) + ROW_GAP;
    }
  });

  const nodes: GraphNode[] = [];
  const boxByKey = new Map<string, GraphNode>();
  for (const key of objectKeys) {
    const point = nodePos.get(key)!;
    const count = incident.get(key) ?? 0;
    const node: GraphNode = {
      name: nodeName.get(key)!,
      description: nodeDescription.get(key)!,
      propertyCount: nodePropertyCount.get(key)!,
      relationCount: count,
      connections: count,
      lines: nodeLines.get(key)!,
      x: point.x,
      y: point.y,
      width: NODE_WIDTH,
      height: nodeHeight.get(key)!,
    };
    nodes.push(node);
    boxByKey.set(key, node);
  }

  // ---- 6. 路由 + 标签 ----
  let contentRight = 0;
  let contentBottom = 0;
  const extend = (right: number, bottom: number): void => {
    if (right > contentRight) contentRight = right;
    if (bottom > contentBottom) contentBottom = bottom;
  };
  for (const node of nodes) extend(node.x + node.width, node.y + node.height);

  const globalBottom = nodes.reduce((max, node) => Math.max(max, node.y + node.height), 0);
  const selfCount = new Map<string, number>();
  let corridorBottom = globalBottom + 36;
  const nextCorridor = (size: EdgeSize): number => {
    const laneY = corridorBottom;
    corridorBottom += size.height + 16;
    return laneY;
  };
  let sameIndex = 0;
  const outEdges: GraphEdge[] = [];

  // 同一对 source -> target 可能声明多条关系（例如 enters / passes）。它们共享
  // 一条前向路由，标签必须沿垂直方向错开，否则会完全叠在一起。先按高度累加
  // 整组标签高度，再让每条标签按真实高度排版，避免 2/3 行混排时互相压住。
  const forwardGroups = new Map<string, number[]>();
  for (const edge of edges) {
    if (edge.self) continue;
    const sourceLayer = layer.get(edge.source) ?? 0;
    const targetLayer = layer.get(edge.target) ?? 0;
    if (edge.back || targetLayer !== sourceLayer + 1) continue;
    const key = edge.source + "\u0000" + edge.target;
    const heights = forwardGroups.get(key) ?? [];
    heights.push(buildEdgeSize(edge.name).height);
    forwardGroups.set(key, heights);
  }
  const forwardSeen = new Map<string, number>();

  for (const edge of edges) {
    const source = boxByKey.get(edge.source)!;
    const target = boxByKey.get(edge.target)!;
    const sourceLayer = layer.get(edge.source) ?? 0;
    const targetLayer = layer.get(edge.target) ?? 0;
    const size = buildEdgeSize(edge.name);
    let path: string;
    let label: GraphEdge["label"];
    if (edge.self) {
      const index = selfCount.get(edge.source) ?? 0;
      selfCount.set(edge.source, index + 1);
      const loop = 52 + index * 22;
      // self 标签只能占用环与下一层之间的空隙；长名称按剩余宽度收窄换行，
      // 避免标签伸进下一层的卡片里。
      const available = LAYER_GAP - (loop + 10) - 8;
      const selfUnits = Math.max(
        1,
        Math.floor((available - 16) / EDGE_LABEL_UNIT_PX),
      );
      // 第二条及以后的环离下一层更近，允许标签比常规最小宽度更窄。
      const selfMinWidth = Math.max(8, Math.min(EDGE_LABEL_MIN_WIDTH, available));
      const selfSize = buildEdgeSize(edge.name, selfUnits, selfMinWidth);
      const startY = Math.min(
        source.y + source.height - 4,
        source.y + source.height * 0.3 + index * 7,
      );
      const endY = Math.min(
        source.y + source.height - 4,
        source.y + source.height * 0.7 + index * 7,
      );
      const startPoint = { x: source.x + source.width, y: startY };
      const controlOne = { x: source.x + source.width + loop, y: source.y - 6 };
      const controlTwo = { x: source.x + source.width + loop, y: source.y + source.height + 6 };
      const endPoint = { x: source.x + source.width, y: endY };
      path =
        `M ${startPoint.x} ${startPoint.y} ` +
        `C ${controlOne.x} ${controlOne.y} ${controlTwo.x} ${controlTwo.y} ` +
        `${endPoint.x} ${endPoint.y}`;
      label = {
        ...selfSize,
        x: source.x + source.width + loop + 10,
        y: source.y + source.height / 2 - selfSize.height / 2 + index * (selfSize.height + 6),
      };
      extend(source.x + source.width + loop, source.y + source.height + 6);
    } else if (edge.back || targetLayer <= sourceLayer) {
      const laneY = nextCorridor(size);
      const left = source.x - 28;
      const right = target.x + target.width + 28;
      const points: Point[] = [
        { x: source.x, y: source.y + source.height / 2 },
        { x: left, y: source.y + source.height / 2 },
        { x: left, y: laneY },
        { x: right, y: laneY },
        { x: right, y: target.y + target.height / 2 },
        { x: target.x + target.width, y: target.y + target.height / 2 },
      ];
      path = roundedGraphPath(points);
      const midX = (left + right) / 2;
      label = {
        ...size,
        x: Math.max(8, midX - size.width / 2),
        y: laneY + 8,
      };
      extend(right, laneY + 8 + size.height);
    } else if (targetLayer > sourceLayer + 1) {
      // 跨多层的长边不能沿中间层卡片的高度直穿。统一走节点下方的走廊，
      // 垂直段落在层与层之间的空隙里，标签也放在走廊下方，避免压住节点。
      const laneY = nextCorridor(size);
      const sourceY = source.y + source.height / 2;
      const targetY = target.y + target.height / 2;
      const sourceExitX = source.x + source.width + 16;
      const targetEntryX = target.x - 16;
      const points: Point[] = [
        { x: source.x + source.width, y: sourceY },
        { x: sourceExitX, y: sourceY },
        { x: sourceExitX, y: laneY },
        { x: targetEntryX, y: laneY },
        { x: targetEntryX, y: targetY },
        { x: target.x, y: targetY },
      ];
      path = roundedGraphPath(points);
      label = {
        ...size,
        x: (sourceExitX + targetEntryX) / 2 - size.width / 2,
        y: laneY + 8,
      };
      extend(targetEntryX, laneY + 8 + size.height);
    } else if (targetLayer === sourceLayer) {
      // 正常分层下非 self 边不会同层；保留该分支作为防御，路由同样绕开卡片。
      const index = sameIndex++;
      const laneX = source.x + source.width + 24 + (index % 3) * 12;
      const sourceBottom = source.y + source.height;
      const targetBottom = target.y + target.height;
      const laneSource = sourceBottom + 28;
      const laneTarget = targetBottom + 28;
      const points: Point[] = [
        { x: centerX(source), y: sourceBottom },
        { x: centerX(source), y: laneSource },
        { x: laneX, y: laneSource },
        { x: laneX, y: laneTarget },
        { x: centerX(target), y: laneTarget },
        { x: centerX(target), y: targetBottom },
      ];
      path = roundedGraphPath(points);
      label = {
        ...size,
        x: Math.max(8, (centerX(source) + laneX) / 2 - size.width / 2),
        y: Math.max(8, laneSource - size.height - 4),
      };
      extend(laneX, Math.max(laneSource, laneTarget) + size.height);
    } else {
      const sourceRight = source.x + source.width;
      const targetLeft = target.x;
      const sourceY = source.y + source.height / 2;
      const targetY = target.y + target.height / 2;
      const midX = (sourceRight + targetLeft) / 2;
      const points: Point[] = [
        { x: sourceRight, y: sourceY },
        { x: midX, y: sourceY },
        { x: midX, y: targetY },
        { x: targetLeft, y: targetY },
      ];
      path = roundedGraphPath(points);
      const groupKey = edge.source + "\u0000" + edge.target;
      const order = forwardSeen.get(groupKey) ?? 0;
      forwardSeen.set(groupKey, order + 1);
      const heights = forwardGroups.get(groupKey) ?? [size.height];
      const totalHeight =
        heights.reduce((sum, height) => sum + height, 0) +
        Math.max(0, heights.length - 1) * 4;
      let top = (sourceY + targetY) / 2 - totalHeight / 2;
      for (let index = 0; index < order && index < heights.length; index += 1) {
        top += (heights[index] ?? 0) + 4;
      }
      label = {
        ...size,
        x: midX - size.width / 2,
        y: top,
      };
      extend(midX, label.y + size.height);
    }
    // 若节点名称与键不同（历史数据），按名称回退定位，避免 NaN
    if (!Number.isFinite(label.x) || !Number.isFinite(label.y)) {
      label = { ...size, x: 8, y: 8 };
    }
    extend(label.x + label.width, label.y + label.height);
    const rendered: GraphEdge = {
      name: edge.name,
      source: edge.source,
      target: edge.target,
      description: edge.description,
      self: edge.self,
      back: edge.back,
      path,
      label,
    };
    if (edge.cardinality) rendered.cardinality = edge.cardinality;
    outEdges.push(rendered);
  }

  const width = Math.max(MIN_CANVAS, contentRight + PADDING);
  const height = Math.max(MIN_CANVAS, contentBottom + PADDING);
  return {
    width,
    height,
    nodes,
    edges: outEdges,
    objectCount: objectKeys.length,
    relationCount: edges.length,
    danglingRelations,
  };
}
