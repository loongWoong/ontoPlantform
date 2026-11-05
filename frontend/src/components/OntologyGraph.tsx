import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import type {
  KeyboardEvent as ReactKeyboardEvent,
  MouseEvent as ReactMouseEvent,
  PointerEvent as ReactPointerEvent,
} from "react";
import {
  ArrowsOut,
  Crosshair,
  CursorClick,
  Minus,
  Plus,
  Scan,
  Tag,
  X,
} from "@phosphor-icons/react";
import type { ObjectTypeSpec, Ontology, RelationSpec } from "../api";
import { layoutOntologyGraph } from "../lib/ontologyGraph";
import type { GraphEdge, GraphNode } from "../lib/ontologyGraph";

/**
 * 本体关系图：把「对象类型 + 关系」画成可点击、可缩放、可展开的 SVG 图。
 *
 * 布局由 `../lib/ontologyGraph` 的纯函数负责（A 的模块），本组件只负责：
 * 摄像机（fit / 滚轮缩放 / 拖拽平移）、选中与关联高亮、只看相关过滤、
 * 关系名称开关、全屏展开（原生 <dialog>）以及选中详情条。
 * 暖色设计规范：只用 frontend/src/styles.css :root 里的 tokens，样式统一
 * 挂在 .onto-graph-* 命名空间下，不污染其他页面。
 */

type Selection =
  | { kind: "object"; name: string }
  | { kind: "relation"; name: string }
  | null;

interface Camera {
  x: number;
  y: number;
  scale: number;
}

interface DragState {
  pointerId: number;
  startX: number;
  startY: number;
  camera: Camera;
  moved: boolean;
}

export interface OntologyGraphProps {
  ontology: Ontology;
  frozen?: boolean;
  onEditObject?: (name: string) => void;
  onEditRelation?: (name: string) => void;
}

const MIN_SCALE = 0.12;
const MAX_SCALE = 2;
const FIT_PADDING_X = 48;
const FIT_PADDING_Y = 96;
const CARDINALITY_LABEL: Record<string, string> = {
  "one-to-one": "一对一",
  "one-to-many": "一对多",
  "many-to-one": "多对一",
  "many-to-many": "多对多",
};

const clampScale = (scale: number) => Math.max(MIN_SCALE, Math.min(MAX_SCALE, scale));

/** 只保留「焦点对象 + 直接邻居 + 与之相连的关系」的临时本体。 */
function focusOntology(ontology: Ontology, focusName: string): Ontology {
  const objects: Record<string, ObjectTypeSpec> = {};
  const relations: Record<string, RelationSpec> = {};
  const keep = new Set<string>([focusName]);
  for (const [name, relation] of Object.entries(ontology.relations || {})) {
    if (!ontology.objects[relation.source] || !ontology.objects[relation.target]) continue;
    if (relation.source !== focusName && relation.target !== focusName) continue;
    relations[name] = relation;
    keep.add(relation.source);
    keep.add(relation.target);
  }
  for (const name of keep) {
    if (ontology.objects[name]) objects[name] = ontology.objects[name];
  }
  return { ...ontology, objects, relations };
}

export function OntologyGraph({
  ontology,
  frozen = false,
  onEditObject,
  onEditRelation,
}: OntologyGraphProps) {
  const [selection, setSelection] = useState<Selection>(null);
  const [focusName, setFocusName] = useState<string | null>(null);
  const [showLabels, setShowLabels] = useState(true);
  const [expanded, setExpanded] = useState(false);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [camera, setCamera] = useState<Camera>({ x: 0, y: 0, scale: 1 });
  const [dragging, setDragging] = useState(false);

  const viewportRef = useRef<HTMLDivElement | null>(null);
  const dialogRef = useRef<HTMLDialogElement | null>(null);
  const expandButtonRef = useRef<HTMLButtonElement | null>(null);
  const dragRef = useRef<DragState | null>(null);
  const movedRef = useRef(false);
  const wasExpandedRef = useRef(false);
  const markerBase = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const arrowMarker = `${markerBase}-arrow`;
  const activeArrowMarker = `${markerBase}-arrow-active`;

  /** 数据来自 REST/草稿，缺字段时降级为空集合，保证图不崩。 */
  const source = useMemo<Ontology>(
    () => ({
      ...ontology,
      objects: ontology.objects || {},
      relations: ontology.relations || {},
      vocabulary: ontology.vocabulary || {},
    }),
    [ontology],
  );

  /* ---- 视口里实际要画的本体（聚焦时是子集） ---- */
  const focusedObject = focusName ? source.objects[focusName] : undefined;
  const visibleOntology = useMemo(
    () => (focusName && focusedObject ? focusOntology(source, focusName) : source),
    [source, focusName, focusedObject],
  );
  const layout = useMemo(() => layoutOntologyGraph(visibleOntology), [visibleOntology]);

  /* ---- 选中对象 / 关系（始终以完整本体为准，详情条不受聚焦影响） ---- */
  const selectedObject =
    selection?.kind === "object" ? source.objects[selection.name] : undefined;
  const selectedRelation =
    selection?.kind === "relation" ? source.relations[selection.name] : undefined;

  /* ---- 数据刷新后清理失效的选中 / 聚焦 ---- */
  useEffect(() => {
    if (selection?.kind === "object" && !source.objects[selection.name]) setSelection(null);
    if (selection?.kind === "relation" && !source.relations[selection.name]) setSelection(null);
  }, [source, selection]);

  useEffect(() => {
    if (!focusName) return;
    if (!source.objects[focusName] || !layout.nodes.some((node) => node.name === focusName)) {
      setFocusName(null);
    }
  }, [focusName, layout, source]);

  /* ---- ResizeObserver：画布尺寸 → fit 计算 ---- */
  useEffect(() => {
    const element = viewportRef.current;
    if (!element) return;
    setSize({ width: element.clientWidth, height: element.clientHeight });
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) return;
      setSize({ width: entry.contentRect.width, height: entry.contentRect.height });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [expanded]);

  const fittedCamera = useMemo<Camera>(() => {
    if (!size.width || !size.height || !layout.nodes.length) {
      return { x: 0, y: 0, scale: 1 };
    }
    const scale = clampScale(
      Math.min(
        1,
        (size.width - FIT_PADDING_X) / Math.max(1, layout.width),
        (size.height - FIT_PADDING_Y) / Math.max(1, layout.height),
      ),
    );
    // 窄屏上若严格 fit，整张图会缩到看不清；与参考实现一致，保留最低可读比例，
    // 超出部分由拖拽/缩放/全屏继续查看。
    const readableScale =
      size.width < 600 && scale < 0.65 ? 0.65 : scale;
    return {
      scale: readableScale,
      x: (size.width - layout.width * readableScale) / 2,
      y: (size.height - layout.height * readableScale) / 2 - 12,
    };
  }, [layout, size]);

  /* 首次挂载与布局变化（聚焦/数据刷新/展开）时自动 fit。 */
  useEffect(() => {
    setCamera(fittedCamera);
  }, [fittedCamera]);

  /* ---- 滚轮缩放（以指针为锚点），passive:false 才能 preventDefault ---- */
  useEffect(() => {
    const element = viewportRef.current;
    if (!element) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const bounds = element.getBoundingClientRect();
      const point = { x: event.clientX - bounds.left, y: event.clientY - bounds.top };
      setCamera((current) => {
        const scale = clampScale(
          current.scale * Math.exp(-Math.max(-80, Math.min(80, event.deltaY)) * 0.008),
        );
        return {
          scale,
          x: point.x - ((point.x - current.x) * scale) / current.scale,
          y: point.y - ((point.y - current.y) * scale) / current.scale,
        };
      });
    };
    element.addEventListener("wheel", onWheel, { passive: false });
    return () => element.removeEventListener("wheel", onWheel);
  }, [expanded]);

  /* ---- 原生 dialog：全屏展开，Esc/关闭后焦点回到展开按钮 ---- */
  useEffect(() => {
    if (!expanded) return;
    dialogRef.current?.showModal?.();
  }, [expanded]);

  /* 收起（X / Esc / 浏览器关闭）后，把焦点还给工具栏的展开按钮。 */
  useEffect(() => {
    if (wasExpandedRef.current && !expanded) expandButtonRef.current?.focus();
    wasExpandedRef.current = expanded;
  }, [expanded]);

  const collapse = useCallback(() => {
    const node = dialogRef.current;
    if (node?.open) node.close();
    setExpanded(false);
    window.requestAnimationFrame(() => expandButtonRef.current?.focus());
  }, []);

  const toggleExpanded = () => {
    if (expanded) collapse();
    else setExpanded(true);
  };

  /* ---- 摄像机操作 ---- */
  const zoomBy = (factor: number) =>
    setCamera((current) => {
      const scale = clampScale(current.scale * factor);
      return {
        scale,
        x: size.width / 2 - ((size.width / 2 - current.x) * scale) / current.scale,
        y: size.height / 2 - ((size.height / 2 - current.y) * scale) / current.scale,
      };
    });

  /* ---- 拖拽平移（pointer capture；从按钮/节点开始时不拖） ---- */
  const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    const target = event.target;
    if (target instanceof Element && target.closest("[role='button'], button")) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    movedRef.current = false;
    dragRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      camera,
      moved: false,
    };
    setDragging(true);
  };

  const onPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const start = dragRef.current;
    if (!start || start.pointerId !== event.pointerId) return;
    const dx = event.clientX - start.startX;
    const dy = event.clientY - start.startY;
    if (!start.moved && Math.hypot(dx, dy) > 4) {
      start.moved = true;
      movedRef.current = true;
    }
    setCamera({ ...start.camera, x: start.camera.x + dx, y: start.camera.y + dy });
  };

  const endDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    const start = dragRef.current;
    if (start && start.pointerId === event.pointerId) {
      dragRef.current = null;
      setDragging(false);
      if (event.currentTarget.hasPointerCapture(event.pointerId)) {
        event.currentTarget.releasePointerCapture(event.pointerId);
      }
    }
  };

  /* 点击画布空白处取消选中；刚拖完的 click 不算。 */
  const onCanvasClick = (event: ReactMouseEvent<HTMLDivElement>) => {
    if (movedRef.current) {
      movedRef.current = false;
      return;
    }
    const target = event.target;
    const isBlank =
      target === event.currentTarget ||
      (target instanceof SVGElement && target.tagName.toLowerCase() === "svg");
    if (isBlank) setSelection(null);
  };

  /* ---- 选中 / 键盘激活 ---- */
  const selectObject = (name: string) => setSelection({ kind: "object", name });
  const selectRelation = (name: string) => setSelection({ kind: "relation", name });

  const onItemKeyDown = (event: ReactKeyboardEvent<SVGGElement>, run: () => void) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      run();
    }
  };

  const toggleFocus = () => {
    if (focusName) setFocusName(null);
    else if (selectedObject) setFocusName(selectedObject.name);
  };

  /* ---- 关联集合（当前布局内计算，聚焦子集也正确） ---- */
  const relatedNodes = useMemo(() => {
    const names = new Set<string>();
    if (selection?.kind === "object") {
      names.add(selection.name);
      for (const edge of layout.edges) {
        if (edge.source === selection.name || edge.target === selection.name) {
          names.add(edge.source);
          names.add(edge.target);
        }
      }
    } else if (selection?.kind === "relation") {
      const edge = layout.edges.find((item) => item.name === selection.name);
      if (edge) {
        names.add(edge.source);
        names.add(edge.target);
      }
    }
    return names;
  }, [layout, selection]);

  const relatedEdges = useMemo(() => {
    const names = new Set<string>();
    if (selection?.kind === "object") {
      for (const edge of layout.edges) {
        if (edge.source === selection.name || edge.target === selection.name) {
          names.add(edge.name);
        }
      }
    } else if (selection?.kind === "relation") {
      if (layout.edges.some((edge) => edge.name === selection.name)) names.add(selection.name);
    }
    return names;
  }, [layout, selection]);

  const selectionInLayout =
    selection === null
      ? false
      : selection.kind === "object"
        ? layout.nodes.some((node) => node.name === selection.name)
        : layout.edges.some((edge) => edge.name === selection.name);

  /* ---- 详情条用的出向 / 入向关系（完整本体） ---- */
  const outgoing = useMemo(
    () =>
      selectedObject
        ? Object.values(source.relations).filter((r) => r.source === selectedObject.name)
        : [],
    [source, selectedObject],
  );
  const incoming = useMemo(
    () =>
      selectedObject
        ? Object.values(source.relations).filter((r) => r.target === selectedObject.name)
        : [],
    [source, selectedObject],
  );
  const selectedProperties = selectedObject ? Object.values(selectedObject.properties || {}) : [];

  /* ---- 渲染：关系 ---- */
  const renderEdge = (edge: GraphEdge) => {
    const active = selection?.kind === "relation" && selection.name === edge.name;
    const related = relatedEdges.has(edge.name);
    const dimmed = selectionInLayout && !active && !related;
    const highlighted = active || related;
    const labelsShown = showLabels || highlighted;
    const classNames = [
      "onto-graph-edge",
      active ? "is-active" : "",
      related ? "is-related" : "",
      dimmed ? "is-dimmed" : "",
    ]
      .filter(Boolean)
      .join(" ");
    return (
      <g
        key={edge.name}
        className={classNames}
        role="button"
        tabIndex={0}
        aria-pressed={active}
        aria-label={`关系 ${edge.name}：${edge.source} 到 ${edge.target}`}
        onClick={() => selectRelation(edge.name)}
        onKeyDown={(event) => onItemKeyDown(event, () => selectRelation(edge.name))}
      >
        <title>{`${edge.name}：${edge.source} → ${edge.target}${edge.description ? ` · ${edge.description}` : ""}`}</title>
        <path className="onto-graph-edge-hit" d={edge.path} />
        <path
          className="onto-graph-edge-line"
          d={edge.path}
          markerEnd={`url(#${highlighted ? activeArrowMarker : arrowMarker})`}
        />
        <g
          className={
            labelsShown ? "onto-graph-edge-label" : "onto-graph-edge-label is-hidden"
          }
          transform={`translate(${edge.label.x} ${edge.label.y})`}
        >
          <rect width={edge.label.width} height={edge.label.height} rx={7} />
          <text textAnchor="middle">
            {edge.label.lines.map((line, index) => (
              <tspan key={index} x={edge.label.width / 2} y={16 + index * 15}>
                {line}
              </tspan>
            ))}
          </text>
        </g>
      </g>
    );
  };

  /* ---- 渲染：节点卡片 ---- */
  const renderNode = (node: GraphNode) => {
    const active = selection?.kind === "object" && selection.name === node.name;
    const related = relatedNodes.has(node.name);
    const dimmed = selectionInLayout && !active && !related;
    const classNames = [
      "onto-graph-node",
      active ? "is-active" : "",
      related ? "is-related" : "",
      dimmed ? "is-dimmed" : "",
    ]
      .filter(Boolean)
      .join(" ");
    const titleText = node.description ? `${node.name}：${node.description}` : node.name;
    // 可见文本（含 SVG <title>）都写进 aria-label，避免 label-in-name 规则下
    // 读屏名称与卡片上真正显示的文字不一致。
    const accessibleName =
      `${titleText}对象类型 · ${node.propertyCount} 属性` +
      `${node.relationCount} 条关系${node.lines.join("")}`;
    return (
      <g
        key={node.name}
        className={classNames}
        transform={`translate(${node.x} ${node.y})`}
        role="button"
        tabIndex={0}
        aria-pressed={active}
        aria-label={accessibleName}
        onClick={() => selectObject(node.name)}
        onKeyDown={(event) => onItemKeyDown(event, () => selectObject(node.name))}
      >
        <title>{titleText}</title>
        <rect className="onto-graph-node-shadow" width={node.width} height={node.height} y={3} rx={12} />
        <rect className="onto-graph-node-card" width={node.width} height={node.height} rx={12} />
        <rect className="onto-graph-node-icon" x={16} y={14} width={22} height={22} rx={6} />
        <path
          className="onto-graph-node-glyph"
          d="M 27 19 L 33 22.5 L 33 29 L 27 32.5 L 21 29 L 21 22.5 Z M 21 22.5 L 27 26 L 33 22.5 M 27 26 L 27 32.5"
        />
        <text className="onto-graph-node-eyebrow" x={46} y={29}>
          对象类型 · {node.propertyCount} 属性
        </text>
        <text className="onto-graph-node-count" x={node.width - 16} y={29} textAnchor="end">
          {node.relationCount} 条关系
        </text>
        <text className="onto-graph-node-name">
          {node.lines.map((line, index) => (
            <tspan key={index} x={16} y={58 + index * 20}>
              {line}
            </tspan>
          ))}
        </text>
      </g>
    );
  };

  /* ---- 选中详情条 ---- */
  const detail = selectedObject ? (
    <div className="onto-graph-detail">
      <div className="onto-graph-detail-head">
        <strong>{selectedObject.name}</strong>
        <span className="onto-graph-detail-desc" title={selectedObject.description || "暂无说明"}>
          {selectedObject.description || "暂无说明"}
        </span>
        {!frozen && onEditObject && (
          <button
            type="button"
            className="onto-graph-edit"
            title="编辑这个对象"
            aria-label={`编辑对象 ${selectedObject.name}`}
            onClick={() => onEditObject(selectedObject.name)}
          >
            编辑对象
          </button>
        )}
      </div>
      <div className="onto-graph-detail-grid">
        <div className="onto-graph-detail-group">
          <span className="onto-graph-detail-label">属性</span>
          <div className="onto-graph-chips">
            {selectedProperties.length ? (
              selectedProperties.map((property) => (
                <span key={property.name} className="onto-graph-chip">
                  {property.name}: {property.type}
                </span>
              ))
            ) : (
              <span className="onto-graph-detail-empty">没有声明属性</span>
            )}
          </div>
        </div>
        <div className="onto-graph-detail-group">
          <span className="onto-graph-detail-label">出向</span>
          <div className="onto-graph-chips">
            {outgoing.length ? (
              outgoing.map((relation) => (
                <span key={relation.name} className="onto-graph-chip">
                  {relation.name} → {relation.target}
                </span>
              ))
            ) : (
              <span className="onto-graph-detail-empty">没有出向关系</span>
            )}
          </div>
        </div>
        <div className="onto-graph-detail-group">
          <span className="onto-graph-detail-label">入向</span>
          <div className="onto-graph-chips">
            {incoming.length ? (
              incoming.map((relation) => (
                <span key={relation.name} className="onto-graph-chip">
                  {relation.source} → {relation.name}
                </span>
              ))
            ) : (
              <span className="onto-graph-detail-empty">没有入向关系</span>
            )}
          </div>
        </div>
      </div>
    </div>
  ) : selectedRelation ? (
    <div className="onto-graph-detail">
      <div className="onto-graph-detail-head">
        <strong>{selectedRelation.name}</strong>
        <span className="onto-graph-detail-meta">
          {selectedRelation.source} → {selectedRelation.target}
        </span>
        {selectedRelation.cardinality && (
          <span className="onto-graph-chip">
            基数 {CARDINALITY_LABEL[selectedRelation.cardinality] || selectedRelation.cardinality}
          </span>
        )}
        {!frozen && onEditRelation && (
          <button
            type="button"
            className="onto-graph-edit"
            title="编辑这条关系"
            aria-label={`编辑关系 ${selectedRelation.name}`}
            onClick={() => onEditRelation(selectedRelation.name)}
          >
            编辑关系
          </button>
        )}
      </div>
      <div className="onto-graph-detail-grid">
        <div className="onto-graph-detail-group">
          <span className="onto-graph-detail-label">含义</span>
          <span className="onto-graph-detail-desc">
            {selectedRelation.description || "暂无说明"}
          </span>
        </div>
      </div>
    </div>
  ) : (
    <div className="onto-graph-detail is-empty">
      <CursorClick size={14} aria-hidden="true" />
      <span>点击对象或关系查看详情；拖动画布平移，滚轮缩放。</span>
    </div>
  );

  /* ---- 图本体：工具栏 / 画布 / 详情 / 页脚，展开与收起共用 ---- */
  const graph = (
    <div className="onto-graph">
      <div className="onto-graph-toolbar">
        <div className="onto-graph-caption">
          <span className="onto-graph-status-dot" aria-hidden="true" />
          <strong>{focusedObject ? focusedObject.name : "关系总览"}</strong>
          <span className="onto-graph-counts">
            {layout.objectCount} 个对象 · {layout.relationCount} 条关系
          </span>
        </div>
        <div className="onto-graph-actions">
          <button
            type="button"
            className={showLabels ? "is-active" : ""}
            aria-pressed={showLabels}
            aria-label="关系名称"
            title={showLabels ? "隐藏关系名称" : "显示关系名称"}
            onClick={() => setShowLabels((value) => !value)}
          >
            <Tag size={14} aria-hidden="true" />
            关系名称
          </button>
          <button
            type="button"
            aria-pressed={Boolean(focusedObject)}
            disabled={!selectedObject && !focusedObject}
            aria-label={focusedObject ? "全部对象（返回完整关系图）" : "只看相关（只看选中对象及相关对象）"}
            title={
              focusedObject
                ? "返回完整关系图"
                : "只看选中对象及直接关联的对象与关系"
            }
            onClick={toggleFocus}
          >
            <Crosshair size={14} aria-hidden="true" />
            {focusedObject ? "全部对象" : "只看相关"}
          </button>
          <button
            type="button"
            ref={expanded ? undefined : expandButtonRef}
            aria-label={expanded ? "收起关系图" : "展开关系图"}
            title={expanded ? "收起关系图" : "全屏展开关系图"}
            onClick={toggleExpanded}
          >
            {expanded ? <X size={15} aria-hidden="true" /> : <ArrowsOut size={15} aria-hidden="true" />}
          </button>
        </div>
      </div>

      <div
        ref={viewportRef}
        className={dragging ? "onto-graph-canvas is-dragging" : "onto-graph-canvas"}
        onClick={onCanvasClick}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onLostPointerCapture={() => {
          dragRef.current = null;
          setDragging(false);
        }}
      >
        {layout.nodes.length === 0 ? (
          <div className="onto-graph-empty" role="status">
            <Crosshair size={22} aria-hidden="true" />
            <b>还没有可画的对象</b>
            <p>先添加对象（例如 Vehicle、TollStation），关系图会自动出现它们的卡片与连线。</p>
          </div>
        ) : (
          <>
            {layout.relationCount === 0 && (
              <p className="onto-graph-hint">
                对象已就位，还没有关系；在右侧「关系」页签里把它们连起来。
              </p>
            )}
            <svg width="100%" height="100%" role="group" aria-label="本体关系图">
              <defs>
                <marker
                  id={arrowMarker}
                  className="onto-graph-arrow-marker"
                  viewBox="0 0 10 10"
                  markerWidth="7"
                  markerHeight="7"
                  refX="9"
                  refY="5"
                  orient="auto-start-reverse"
                >
                  <path
                    d="M 1 1 L 9 5 L 1 9"
                    fill="none"
                    strokeWidth={1.5}
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </marker>
                <marker
                  id={activeArrowMarker}
                  className="onto-graph-arrow-marker is-active"
                  viewBox="0 0 10 10"
                  markerWidth="7"
                  markerHeight="7"
                  refX="9"
                  refY="5"
                  orient="auto-start-reverse"
                >
                  <path
                    d="M 1 1 L 9 5 L 1 9"
                    fill="none"
                    strokeWidth={1.5}
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </marker>
              </defs>
              <g
                className="onto-graph-transform"
                transform={`translate(${camera.x} ${camera.y}) scale(${camera.scale})`}
              >
                {layout.edges.map(renderEdge)}
                {layout.nodes.map(renderNode)}
              </g>
            </svg>
            <div className="onto-graph-navigation" role="group" aria-label="画布缩放">
              <button
                type="button"
                aria-label="缩小关系图"
                title="缩小"
                disabled={camera.scale <= MIN_SCALE}
                onClick={() => zoomBy(1 / 1.2)}
              >
                <Minus size={16} aria-hidden="true" />
              </button>
              <span>{Math.round(camera.scale * 100)}%</span>
              <button
                type="button"
                aria-label="放大关系图"
                title="放大"
                disabled={camera.scale >= MAX_SCALE}
                onClick={() => zoomBy(1.2)}
              >
                <Plus size={16} aria-hidden="true" />
              </button>
              <i aria-hidden="true" />
              <button
                type="button"
                aria-label="适应画布"
                title="适应画布"
                onClick={() => setCamera(fittedCamera)}
              >
                <Scan size={17} aria-hidden="true" />
              </button>
            </div>
          </>
        )}
      </div>

      {detail}

      <div className="onto-graph-footer">
        <span>
          <CursorClick size={13} aria-hidden="true" />
          点击查看 · 拖动画布 · 滚轮缩放
        </span>
        <span>
          <i className="onto-graph-arrow-legend" aria-hidden="true" />
          箭头表示关系方向
        </span>
      </div>
    </div>
  );

  if (!expanded) return graph;

  return (
    <>
      <div className="onto-graph-placeholder" aria-hidden="true">
        关系图已展开
      </div>
      <dialog
        ref={dialogRef}
        className="onto-graph-dialog"
        aria-label="展开的本体关系图"
        onCancel={(event) => {
          event.preventDefault();
          collapse();
        }}
        onClose={() => setExpanded(false)}
      >
        {graph}
      </dialog>
    </>
  );
}
