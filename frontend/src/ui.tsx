/** 展示原语：所有页面只用这些组件，保证尺寸/间距一致、label 常驻、表格永远可横滚。 */
import {
  Children, cloneElement, isValidElement, useEffect, useRef, useState,
  type ReactElement,
  type ReactNode, type SelectHTMLAttributes, type InputHTMLAttributes,
  type TextareaHTMLAttributes, type ButtonHTMLAttributes,
} from "react";
import type { AgentStep, Obj } from "./api";
import { usePlatform } from "./store";

/* ---------------- 数据加载 ---------------- */
export function useLoad<T>(loader: () => Promise<T>, deps: unknown[], label = "加载中") {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    let cancelled = false;
    setLoading(true); setError(null);
    loader()
      .then(d => { if (!cancelled && alive.current) { setData(d); setLoading(false); } })
      .catch(e => { if (!cancelled && alive.current) { setError(e?.message || String(e)); setLoading(false); } });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);
  return { data, error, loading, reload: () => setNonce(n => n + 1), label };
}

/* ---------------- 布局 ---------------- */
export function Card({ title, sub, aside, children, tight }: {
  title?: ReactNode; sub?: ReactNode; aside?: ReactNode; children: ReactNode; tight?: boolean;
}) {
  return (
    <section className="card" style={undefined} data-tight={tight ? "true" : undefined}>
      {(title || aside) && (
        <div className="card-head">
          <div className="card-head-main">
            {title && <h3 className="card-title">{title}</h3>}
            {sub && <p className="card-sub">{sub}</p>}
          </div>
          {aside && <div className="card-aside">{aside}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

export function Section({ title, children, aside }: { title: ReactNode; children: ReactNode; aside?: ReactNode }) {
  return (
    <>
      <div className="h4-row">
        <h4 className="h4">{title}</h4>
        {aside}
      </div>
      {children}
    </>
  );
}

export function Collapsible({ label, children, defaultOpen = false }: {
  label: ReactNode; children: ReactNode; defaultOpen?: boolean;
}) {
  return (
    <details className="collapse" open={defaultOpen}>
      <summary>{label}</summary>
      <div className="collapse-body">{children}</div>
    </details>
  );
}

/* ---------------- 表单 ---------------- */
/** 控件没写 aria-label 时，用字段标签兜底 —— 屏幕阅读器与探针都只认常驻标签。 */
function labelControls(children: ReactNode, label: string): ReactNode {
  if (!label) return children;
  return Children.map(children, (c: ReactNode) => {
    if (!isValidElement(c)) return c;
    const el = c as ReactElement<Record<string, unknown>>;
    const p = el.props || {};
    if (p["aria-label"] || p.id) return c;
    if (el.type === TextInput || el.type === Select || el.type === Area)
      return cloneElement(el, { "aria-label": label } as Record<string, unknown>);
    return c;
  });
}

export function Field({ label, hint, error, children, top }: {
  label: ReactNode; hint?: ReactNode; error?: ReactNode; children: ReactNode; top?: boolean;
}) {
  return (
    <div className={"field" + (top ? " field-top" : "")}>
      <label>{label}</label>
      <div className="field-ctl">{labelControls(children, typeof label === "string" ? label : "")}</div>
      {hint && <span className="field-hint">{hint}</span>}
      {error && <span className="field-error">{error}</span>}
    </div>
  );
}

type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "subtle" | "tint" | "link" | "danger";
  size?: "md" | "sm";
  busy?: boolean;
};
export function Btn({ variant = "primary", size = "md", busy, children, ...rest }: BtnProps) {
  const cls = [
    variant === "subtle" && "subtle",
    variant === "tint" && "tint",
    variant === "link" && "link",
    variant === "danger" && "danger",
    size === "sm" && "sm",
  ].filter(Boolean).join(" ");
  return (
    <button type="button" className={cls} disabled={rest.disabled || busy} {...rest}>
      {busy ? "处理中…" : children}
    </button>
  );
}

export function TextInput(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} />;
}
export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} />;
}
export function Area(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} />;
}

export function Tag({ kind = "", children }: { kind?: "" | "ok" | "warn" | "err" | "plain"; children: ReactNode }) {
  return <span className={"tag " + kind}>{children}</span>;
}

export const LIFECYCLE_LABEL: Record<string, string> = {
  draft: "草稿", validated: "已校验", published: "已发布", deprecated: "已废弃", none: "未创建",
};
export const KIND_LABEL: Record<string, string> = {
  static: "静态样例", sqlite: "数据库", rest: "接口",
  table: "表格", card: "卡片", chart: "图表", map: "地图",
  python: "脚本", workflow: "编排", agent: "智能目标",
};
export const labelOf = (map: Record<string, string>, v: string) => map[v] || v;

export function Empty({ title, hint, action }: { title: string; hint?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty">
      <b>{title}</b>
      {hint && <div>{hint}</div>}
      {action && <div className="empty-act">{action}</div>}
    </div>
  );
}

export function Loading({ text = "加载中" }: { text?: string }) {
  return <div className="loading">{text}…</div>;
}

export function ErrorNote({ text, onRetry }: { text: string; onRetry?: () => void }) {
  return (
    <div className="banner err">
      {text}
      {onRetry && <button type="button" className="link" onClick={onRetry}>重试</button>}
    </div>
  );
}

/* ---------------- 表格 ---------------- */
export function TableWrap({ children, auto }: { children: ReactNode; auto?: boolean }) {
  return <div className={"tablewrap" + (auto ? " auto" : "")}>{children}</div>;
}

/** 对象表格：引用可点，长属性省略 + 悬浮全文；14 列也不会撑破页面 */
export function ObjectTable({ objects, onOpen }: { objects: Obj[]; onOpen?: (ref: string) => void }) {
  const { go } = usePlatform();
  const cols = Array.from(new Set(objects.flatMap(o => Object.keys(o.properties || {}))));
  if (!objects.length) return <Empty title="没有对象" hint="换个过滤条件，或先去「数据」页同步数据。" />;
  const open = (ref: string) => (onOpen ? onOpen(ref) : go("state", { ref }));
  return (
    <TableWrap>
      <table>
        <thead>
          <tr>
            <th>对象</th>
            {cols.map(c => <th key={c}>{c}</th>)}
            <th> </th>
          </tr>
        </thead>
        <tbody>
          {objects.map(o => {
            const ref = `${o.type}/${o.id}`;
            return (
              <tr key={ref}>
                <td className="nowrap"><button type="button" className="link" onClick={() => open(ref)}>{ref}</button></td>
                {cols.map(c => {
                  const v = o.properties[c];
                  const text = v === null || v === undefined ? "—" : String(v);
                  return <td key={c} title={text}>{text}</td>;
                })}
                <td className="nowrap"><button type="button" className="link" onClick={() => open(ref)}>详情</button></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </TableWrap>
  );
}

/* ---------------- 抽屉 ---------------- */
export function Drawer({ title, sub, onClose, children, wide, footer }: {
  title: ReactNode; sub?: ReactNode; onClose: () => void; children: ReactNode; wide?: boolean; footer?: ReactNode;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="scrim" role="dialog" aria-modal="true" onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className={"drawer" + (wide ? " wide" : "")}>
        <div className="drawer-head">
          <h2>{title}</h2>
          {sub && <span className="muted">{sub}</span>}
          <span className="grow" />
          <button type="button" className="subtle sm" onClick={onClose}>关闭</button>
        </div>
        {children}
        {footer && <div className="drawer-foot">{footer}</div>}
      </div>
    </div>
  );
}

/* ---------------- 推理链 ---------------- */
export function Timeline({ steps }: { steps: AgentStep[] }) {
  if (!steps.length) return <p className="muted">没有留下步骤记录。</p>;
  return (
    <ol className="timeline">
      {steps.map((st, i) => (
        <li key={i}>
          <div className="thought">
            <b>{i + 1}.</b> {st.thought || "（这一步没有写想法）"}
          </div>
          <div className="mono muted">
            <Tag kind="plain">{st.action?.tool || "action"}</Tag>{" "}
            {JSON.stringify(st.action?.args ?? {})}
          </div>
          {!!st.observation && (
            <pre className="obs">{typeof st.observation === "string"
              ? st.observation : JSON.stringify(st.observation, null, 2)}</pre>
          )}
        </li>
      ))}
    </ol>
  );
}

/* ---------------- 行编辑器 ---------------- */
export interface Row { a: string; b: string }
export function RowEditor({ rows, onChange, phA, phB, bKind, bOptions }: {
  rows: Row[];
  onChange: (rows: Row[]) => void;
  phA: string; phB: string;
  bKind?: "text" | "select";
  bOptions?: string[];
}) {
  const set = (i: number, patch: Partial<Row>) => onChange(rows.map((r, j) => (i === j ? { ...r, ...patch } : r)));
  return (
    <div className="rows-edit">
      {rows.map((r, i) => (
        <div className="r" key={i}>
          <input value={r.a} placeholder={phA} aria-label={phA}
                 onChange={e => set(i, { a: e.target.value })} />
          {bKind === "select" ? (
            <select value={r.b} aria-label={phB} onChange={e => set(i, { b: e.target.value })}>
              {(bOptions || ["string"]).map(o => <option key={o} value={o}>{o}</option>)}
            </select>
          ) : (
            <input value={r.b} placeholder={phB} aria-label={phB}
                   onChange={e => set(i, { b: e.target.value })} />
          )}
          <button type="button" className="subtle" aria-label="删除这一行"
                  onClick={() => onChange(rows.filter((_, j) => j !== i))}>×</button>
        </div>
      ))}
      <Btn variant="subtle" size="sm" onClick={() => onChange([...rows, { a: "", b: bKind === "select" ? (bOptions?.[0] || "string") : "" }])}>
        添加一行
      </Btn>
    </div>
  );
}

/* ---------------- 过滤行编辑器：属性 / 比较 / 值 ---------------- */
export const FILTER_OPS = [
  { v: "", label: "等于" },
  { v: "gt", label: "大于" },
  { v: "gte", label: "不小于" },
  { v: "lt", label: "小于" },
  { v: "lte", label: "不大于" },
  { v: "contains", label: "包含" },
  { v: "in", label: "属于" },
];
export interface FilterRow { prop: string; op: string; value: string }
export function FilterRowEditor({ rows, onChange, propPh = "属性名，如 balance" }: {
  rows: FilterRow[]; onChange: (r: FilterRow[]) => void; propPh?: string;
}) {
  const set = (i: number, patch: Partial<FilterRow>) =>
    onChange(rows.map((r, j) => (i === j ? { ...r, ...patch } : r)));
  return (
    <div className="rows-edit">
      {rows.map((r, i) => (
        <div className="r filter" key={i}>
          <input value={r.prop} placeholder={propPh} aria-label="属性名" onChange={e => set(i, { prop: e.target.value })} />
          <select value={r.op} aria-label="比较方式" onChange={e => set(i, { op: e.target.value })}>
            {FILTER_OPS.map(o => <option key={o.v} value={o.v}>{o.label}</option>)}
          </select>
          <input value={r.value} placeholder="值，如 10" aria-label="值" onChange={e => set(i, { value: e.target.value })} />
          <button type="button" className="subtle" aria-label="删除这条条件"
                  onClick={() => onChange(rows.filter((_, j) => j !== i))}>×</button>
        </div>
      ))}
      <Btn variant="subtle" size="sm" onClick={() => onChange([...rows, { prop: "", op: "", value: "" }])}>添加条件</Btn>
    </div>
  );
}

/** 把过滤行编译成后端过滤语法（prop / prop__gt / 类型自动识别） */
export function buildFilters(rows: FilterRow[]): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const r of rows) {
    const prop = r.prop.trim();
    if (!prop) continue;
    const key = r.op ? `${prop}__${r.op}` : prop;
    const raw = r.value.trim();
    if (raw === "") continue;
    if (r.op === "in") { out[key] = raw.split(/[,，、]/).map(v => guessScalar(v.trim())).filter(v => v !== ""); continue; }
    out[key] = guessScalar(raw);
  }
  return out;
}

/** 值类型推断：前导零的编号（"00123"）必须保持字符串，否则查不到 */
function guessScalar(raw: string): unknown {
  if (raw === "true") return true;
  if (raw === "false") return false;
  if (raw !== "" && !Number.isNaN(Number(raw)) && /^-?(0|[1-9]\d*)(\.\d+)?$/.test(raw)) return Number(raw);
  return raw;
}

/* ---------------- JSON 输入 ---------------- */
export function JsonField({ label, value, onChange, hint, rows = 8, required }: {
  label: ReactNode; value: string; onChange: (v: string) => void;
  hint?: ReactNode; rows?: number; required?: boolean;
}) {
  const [err, setErr] = useState<string | null>(null);
  const check = (v: string) => {
    if (!v.trim()) { setErr(required ? "不能为空" : null); return; }
    try { JSON.parse(v); setErr(null); } catch (e) { setErr("JSON 语法错误：" + (e as Error).message); }
  };
  return (
    <div className="field field-top">
      <label>{label}</label>
      <div className="field-ctl">
        <textarea className="code" rows={rows} value={value} aria-label={typeof label === "string" ? label : "JSON"}
                  onChange={e => { onChange(e.target.value); check(e.target.value); }}
                  onBlur={() => check(value)} />
        {err && <span className="field-error">{err}</span>}
        {hint && <span className="field-hint">{hint}</span>}
      </div>
    </div>
  );
}

/** 复制按钮：长 JSON / 引用都用它 */
export function CopyBtn({ text }: { text: string }) {
  const { toast } = usePlatform();
  return (
    <Btn variant="subtle" size="sm"
         onClick={() => navigator.clipboard?.writeText(text)
           .then(() => toast("已复制", "ok"))
           .catch(() => toast("浏览器不允许复制，请手动选择", "err"))}>
      复制
    </Btn>
  );
}

