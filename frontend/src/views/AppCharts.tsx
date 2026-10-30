/** 应用视图的真实图形渲染：图表（SVG 条形图）与地图（经纬度散点）。
 *
 *  只用视图返回的真实对象，不做任何假装的数据：缺配置、缺数值、缺坐标时
 *  明确说明还差什么，而不是画一张空图。最多画前 50 条（图）/ 200 点（地图），
 *  超出时在标题里说明只画了多少，明细表由调用方在下方另行提供。
 */
import type { AppView, Obj } from "../api";
import { Empty } from "../ui";
import { fmtNum } from "../metrics";
import { valuePreview } from "../lib/value";

const CHART_W = 720;
const CHART_H = 280;
const CHART_PAD = { left: 56, right: 16, top: 16, bottom: 44 };
const CHART_MAX_BARS = 50;

const MAP_W = 720;
const MAP_H = 420;
const MAP_PAD = 40;
const MAP_MAX_POINTS = 200;

/** 数值（number 或数字字符串）；其他类型返回 null，不猜。 */
function numeric(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string" && value.trim() !== "") {
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

function labelOfObject(o: Obj, field: string): string {
  const raw = o.properties?.[field];
  if (raw === undefined || raw === null || raw === "") return o.id;
  return valuePreview(raw, 28);
}

/** 刻度：把 [lo, hi] 分成约 4 段，返回整齐的刻度值。 */
function ticks(lo: number, hi: number, count = 4): number[] {
  if (hi === lo) return [lo];
  const step = (hi - lo) / count;
  const out: number[] = [];
  for (let i = 0; i <= count; i++) out.push(lo + step * i);
  return out;
}

export function ChartView({ objects, view }: { objects: Obj[]; view: AppView }) {
  const xField = view.config?.x || "";
  const yField = view.config?.y || "";
  if (!xField || !yField) {
    return (
      <Empty title="图表视图还没配好"
             hint={`视图配置里要选两个属性：x 做标签、y 做数值。当前 x=${xField || "未选"}、y=${yField || "未选"}。到视图「编辑」里补上再打开。`} />
    );
  }
  const series = objects
    .map(o => ({ id: `${o.type}/${o.id}`, label: labelOfObject(o, xField), value: numeric(o.properties?.[yField]) }))
    .filter((p): p is { id: string; label: string; value: number } => p.value !== null);
  if (!objects.length) {
    return <Empty title="没有可画的对象" hint="查询条件没有匹配到对象，或能力还没产生这类数据。" />;
  }
  if (!series.length) {
    return (
      <Empty title={`属性「${yField}」上没有数值`}
             hint={`这 ${objects.length} 个对象的「${yField}」都不是数字，画不出条形图。到视图「编辑」里换一个数值属性（number / integer）。`} />
    );
  }
  const shown = series.slice(0, CHART_MAX_BARS);
  const values = shown.map(p => p.value);
  const lo = Math.min(0, ...values);
  const hi = Math.max(0, ...values);
  const span = hi - lo || 1;
  const innerW = CHART_W - CHART_PAD.left - CHART_PAD.right;
  const innerH = CHART_H - CHART_PAD.top - CHART_PAD.bottom;
  const yOf = (v: number) => CHART_PAD.top + innerH * ((hi - v) / span);
  const baseline = yOf(0);
  const slot = innerW / Math.max(shown.length, 1);
  const barW = Math.max(2, Math.min(48, slot * 0.68));
  const every = Math.max(1, Math.ceil(shown.length / 12));

  return (
    <div className="chart-wrap">
      <div className="chart-head">
        <span><b>条形图</b> · x = <code>{xField}</code> · y = <code>{yField}</code></span>
        <span className="muted small">
          {series.length} 条{series.length > shown.length ? `（只画前 ${shown.length} 条）` : ""} · 数值范围 {fmtNum(Math.min(...values))} ~ {fmtNum(Math.max(...values))}
        </span>
      </div>
      <svg className="chart-svg" viewBox={`0 0 ${CHART_W} ${CHART_H}`} role="img"
           aria-label={`按 ${xField} 分组的 ${yField} 条形图，共 ${shown.length} 条`}>
        {ticks(lo, hi).map(t => (
          <g key={`t${t}`}>
            <line x1={CHART_PAD.left} x2={CHART_W - CHART_PAD.right} y1={yOf(t)} y2={yOf(t)}
                  className="chart-grid" />
            <text x={CHART_PAD.left - 8} y={yOf(t) + 3.5} textAnchor="end" className="chart-tick">{fmtNum(t)}</text>
          </g>
        ))}
        <line x1={CHART_PAD.left} x2={CHART_W - CHART_PAD.right} y1={baseline} y2={baseline} className="chart-axis" />
        {shown.map((p, i) => {
          const x = CHART_PAD.left + slot * i + (slot - barW) / 2;
          const y = Math.min(yOf(p.value), baseline);
          const h = Math.max(1.5, Math.abs(yOf(p.value) - baseline));
          const negative = p.value < 0;
          return (
            <g key={p.id}>
              <rect x={x} y={y} width={barW} height={h}
                    className={"chart-bar" + (negative ? " neg" : "")} rx={1.5}>
                <title>{`${p.label}：${fmtNum(p.value)}`}</title>
              </rect>
              {p.value !== 0 && (
                <text x={x + barW / 2} y={negative ? y + h + 11 : y - 4} textAnchor="middle" className="chart-value">
                  {fmtNum(p.value)}
                </text>
              )}
              {i % every === 0 && (
                <text x={x + barW / 2} y={CHART_H - CHART_PAD.bottom + 16} textAnchor="middle" className="chart-label">
                  {p.label}
                </text>
              )}
            </g>
          );
        })}
        <text x={CHART_PAD.left} y={CHART_H - 8} className="chart-caption">横轴：{xField} · 纵轴：{yField}（0 线已画出，负值在基线下）</text>
      </svg>
    </div>
  );
}

export function MapView({ objects, view }: { objects: Obj[]; view: AppView }) {
  const latField = view.config?.lat || "";
  const lngField = view.config?.lng || "";
  if (!latField || !lngField) {
    return (
      <Empty title="地图视图还没配好"
             hint={`视图配置里要选两个数值属性：lat 是纬度、lng 是经度。当前 lat=${latField || "未选"}、lng=${lngField || "未选"}。到视图「编辑」里补上再打开。`} />
    );
  }
  const points = objects
    .map(o => ({
      id: `${o.type}/${o.id}`,
      label: o.id,
      lat: numeric(o.properties?.[latField]),
      lng: numeric(o.properties?.[lngField]),
    }))
    .filter((p): p is { id: string; label: string; lat: number; lng: number } => p.lat !== null && p.lng !== null);
  if (!objects.length) {
    return <Empty title="没有可画的对象" hint="查询条件没有匹配到对象，或能力还没产生这类数据。" />;
  }
  if (!points.length) {
    return (
      <Empty title={`属性「${latField}」/「${lngField}」上没有坐标`}
             hint={`这 ${objects.length} 个对象没有可用的经纬度（都必须是数值属性）。到视图「编辑」里换两个数值属性，或先给对象补上坐标。`} />
    );
  }
  const shown = points.slice(0, MAP_MAX_POINTS);
  const lats = shown.map(p => p.lat);
  const lngs = shown.map(p => p.lng);
  const latLo = Math.min(...lats), latHi = Math.max(...lats);
  const lngLo = Math.min(...lngs), lngHi = Math.max(...lngs);
  // 所有点都在同一处时画在中心，避免除零
  const latSpan = latHi - latLo || 1;
  const lngSpan = lngHi - lngLo || 1;
  const innerW = MAP_W - MAP_PAD * 2;
  const innerH = MAP_H - MAP_PAD * 2;
  const xOf = (lng: number) => MAP_PAD + innerW * ((lng - lngLo) / lngSpan);
  const yOf = (lat: number) => MAP_PAD + innerH * ((latHi - lat) / latSpan);   // 纬度上北下南
  const showLabels = shown.length <= 30;

  return (
    <div className="map-wrap">
      <div className="chart-head">
        <span><b>地图散点</b> · lat = <code>{latField}</code> · lng = <code>{lngField}</code></span>
        <span className="muted small">
          {points.length} 个点{points.length > shown.length ? `（只画前 ${shown.length} 个）` : ""}
          {" · "}纬度 {fmtNum(latLo)}~{fmtNum(latHi)} · 经度 {fmtNum(lngLo)}~{fmtNum(lngHi)}
        </span>
      </div>
      <svg className="map-svg" viewBox={`0 0 ${MAP_W} ${MAP_H}`} role="img"
           aria-label={`按经纬度投影的 ${shown.length} 个对象散点，经度 ${fmtNum(lngLo)} 到 ${fmtNum(lngHi)}，纬度 ${fmtNum(latLo)} 到 ${fmtNum(latHi)}`}>
        <rect x={MAP_PAD} y={MAP_PAD} width={innerW} height={innerH} className="map-frame" />
        {[0, 0.25, 0.5, 0.75, 1].map(f => (
          <g key={`g${f}`}>
            <line x1={MAP_PAD + innerW * f} x2={MAP_PAD + innerW * f} y1={MAP_PAD} y2={MAP_PAD + innerH} className="map-grid" />
            <line x1={MAP_PAD} x2={MAP_PAD + innerW} y1={MAP_PAD + innerH * f} y2={MAP_PAD + innerH * f} className="map-grid" />
          </g>
        ))}
        <text x={MAP_PAD} y={MAP_PAD - 14} className="map-tick">纬度 {fmtNum(latHi)}</text>
        <text x={MAP_PAD} y={MAP_PAD + innerH + 20} className="map-tick">纬度 {fmtNum(latLo)}</text>
        <text x={MAP_W - MAP_PAD} y={MAP_PAD + innerH + 20} textAnchor="end" className="map-tick">
          经度 {fmtNum(lngLo)} ~ {fmtNum(lngHi)}
        </text>
        {shown.map((p, i) => {
          const x = xOf(p.lng);
          const y = yOf(p.lat);
          return (
            <g key={p.id}>
              <circle cx={x} cy={y} r={5} className="map-dot">
                <title>{`${p.label}：纬度 ${fmtNum(p.lat)}，经度 ${fmtNum(p.lng)}`}</title>
              </circle>
              {showLabels && (
                <text x={x + 8} y={y + 3.5} className="map-label">{p.label}</text>
              )}
              {!showLabels && i === 0 && (
                <text x={x + 8} y={y + 3.5} className="map-label">{p.label} 等 {shown.length} 个点</text>
              )}
            </g>
          );
        })}
        <text x={MAP_PAD} y={MAP_H - 8} className="chart-caption">
          按真实经纬度归一化投影：上北下南、左西右东；点最多画 {MAP_MAX_POINTS} 个，其余看下方明细表。
        </text>
      </svg>
    </div>
  );
}
