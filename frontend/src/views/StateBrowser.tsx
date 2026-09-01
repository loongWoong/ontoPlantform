import { useEffect, useMemo, useRef, useState } from "react";
import { api, type Obj } from "../api";
import { useAction, usePlatform } from "../store";
import {
  Btn, Card, Collapsible, Drawer, Empty, Field, FilterRowEditor, Loading,
  ObjectTable, Select, TableWrap, Tag, buildFilters, useLoad, type FilterRow,
} from "../ui";
import type { ChangeEvent } from "../api";

export function StateView() {
  const { wid, objectTypes, params, go } = usePlatform();
  const [type, setType] = useState(params.type || objectTypes[0] || "");
  const [filters, setFilters] = useState<FilterRow[]>([]);
  const [query, setQuery] = useState<{ type: string; filters: Record<string, unknown> } | null>(null);
  const [openRef, setOpenRef] = useState<string | null>(params.ref || null);

  useEffect(() => { if (!type && objectTypes[0]) setType(objectTypes[0]); }, [objectTypes, type]);
  // 换工作空间 = 换业务世界：查询条件、结果、打开的抽屉全部归零，
  // 否则会把上一个空间的对象类型/引用带过来（旧版控制台是显式清空的）
  useEffect(() => { setQuery(null); setOpenRef(null); setFilters([]); }, [wid]);
  useEffect(() => { if (params.ref) setOpenRef(params.ref); }, [params.ref]);
  // 深链 #/state?type=X 说了算；没有深链时首次进入自动查一次首选类型，避免空屏
  useEffect(() => {
    if (!objectTypes.length) return;
    if (params.type && objectTypes.includes(params.type)) {
      setType(params.type);
      setQuery({ type: params.type, filters: {} });
      return;
    }
    if (!query && !params.ref) setQuery({ type: objectTypes[0], filters: {} });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [objectTypes, params.type]);

  const results = useLoad(
    () => (wid && query ? api.find(wid, query.type, stringify(query.filters)) : Promise.resolve([] as Obj[])),
    [wid, query],
  );

  return (
    <>
      <Card title="对象实例" sub="这里的数据只存在于运行时里，平台不缓存第二份业务状态。">
        <form className="searchbar" onSubmit={e => { e.preventDefault(); setQuery({ type, filters: buildFilters(filters) }); }}>
          <Field label="对象类型">
            <Select value={type} onChange={e => setType(e.target.value)}>
              {!objectTypes.length && <option value="">还没有已发布的对象类型</option>}
              {objectTypes.map(t => <option key={t} value={t}>{t}</option>)}
            </Select>
          </Field>
          <div className="searchbar-filters">
            <Field label="过滤条件" hint="可以不加；属性 + 比较 + 值。">
              <FilterRowEditor rows={filters.length ? filters : [{ prop: "", op: "", value: "" }]} onChange={setFilters} />
            </Field>
          </div>
          <div className="searchbar-go">
            <Btn type="submit">查询</Btn>
            <Btn type="button" variant="subtle" onClick={() => { setFilters([]); setQuery({ type, filters: {} }); }}>看全部</Btn>
          </div>
        </form>

        {results.loading && <Loading />}
        {!results.loading && !query && <Empty title="选一个对象类型开始查" hint="或先去「数据」页把数据同步进来。" />}
        {!results.loading && query && <ObjectTable objects={results.data || []} onOpen={ref => setOpenRef(ref)} />}
      </Card>

      <ChangesPanel onOpenObject={ref => setOpenRef(ref)} />

      {openRef && (
        <ObjectDrawer ref_={openRef} onClose={() => { setOpenRef(null); if (params.ref) go("state"); }} />
      )}
    </>
  );
}

const stringify = (f: Record<string, unknown>) =>
  Object.fromEntries(Object.entries(f).map(([k, v]) => [k, String(v)]));

function ObjectDrawer({ ref_, onClose }: { ref_: string; onClose: () => void }) {
  const { wid, go } = usePlatform();
  const [tab, setTab] = useState<"props" | "related" | "changes">("props");
  const detail = useLoad(async () => {
    if (!wid) return null;
    const [obj, rt] = await Promise.all([api.get(wid, ref_), api.runtime(wid).catch(() => null)]);
    if (!obj) return { obj: null as Obj | null, related: [] as { relation: string; direction: string; items: string[] }[], changes: [] as ChangeEvent[] };
    const rels = ((rt?.relations) || []).filter(r => r.source === obj.type || r.target === obj.type);
    // 关系导航并发拉取，不再一条条串行等
    const related = await Promise.all(rels.map(async r => {
      const direction = r.source === obj.type ? "out" : "in";
      const items = await api.related(wid, ref_, r.name, direction).catch(() => [] as string[]);
      return { relation: r.name, direction, items };
    }));
    const all = await api.changes(wid, 0).catch(() => [] as ChangeEvent[]);
    const touches = (e: ChangeEvent) => (e.changes || []).some(c =>
      [c.object, c.source, c.target, c.ref].some(v => typeof v === "string" && v === ref_));
    const changes = all.filter(touches).slice(-12);
    return { obj, related, changes };
  }, [wid, ref_]);

  const d = detail.data;
  return (
    <Drawer title={ref_} sub={d?.obj ? `${d.obj.type} 详情` : ""} onClose={onClose}>
      {detail.loading && <Loading />}
      {!detail.loading && !d?.obj && <Empty title="没找到这个对象" hint={ref_} />}
      {d?.obj && (
        <>
          <div className="subtabs" role="tablist">
            {([["props", "属性"], ["related", `关系 ${d.related.length}`], ["changes", `相关记录 ${d.changes.length}`]] as const)
              .map(([k, l]) => (
                <button key={k} type="button" role="tab" aria-selected={tab === k} onClick={() => setTab(k)}>{l}</button>
              ))}
          </div>

          {tab === "props" && (
            <dl className="kv">
              {Object.entries(d.obj.properties || {}).map(([k, v]) => (
                <div key={k} className="kv-pair"><dt>{k}</dt><dd title={String(v)}>{String(v)}</dd></div>
              ))}
            </dl>
          )}

          {tab === "related" && (
            <>
              {!d.related.length && <Empty title="这个对象类型没有定义关系" hint="到「本体」页补充关系。" />}
              {d.related.map(r => (
                <div key={r.relation}>
                  <h4 className="h4">{r.relation} {r.direction === "out" ? "→" : "←"} · {r.items.length}</h4>
                  {r.items.length ? (
                    <div className="chips">
                      {r.items.map(i => (
                        <button key={i} type="button" className="chip on" onClick={() => go("state", { ref: i })}>
                          {i}
                        </button>
                      ))}
                    </div>
                  ) : <p className="muted">暂无关联对象</p>}
                </div>
              ))}
            </>
          )}

          {tab === "changes" && (
            <>
              {!d.changes.length && <Empty title="没有与它相关的变更记录" />}
              {!!d.changes.length && (
                <TableWrap auto>
                  <table>
                    <thead><tr><th>序号</th><th>来源</th><th className="wrap">改动</th></tr></thead>
                    <tbody>
                      {d.changes.slice().reverse().map(e => (
                        <tr key={e.seq}>
                          <td className="nowrap">{e.seq}</td>
                          <td className="nowrap"><Tag kind="plain">{e.source}</Tag></td>
                          <td className="wrap" title={JSON.stringify(e.changes)}>{summarize(e)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </TableWrap>
              )}
            </>
          )}
        </>
      )}
    </Drawer>
  );
}

function summarize(e: ChangeEvent): string {
  return (e.changes || []).map(c => {
    if (c.kind === "set_property") return `${c.object}.${c.property}: ${c.old} → ${c.new}`;
    if (c.kind === "link") return `${c.source} —${c.relation}→ ${c.target}`;
    if (c.kind === "create_object") return `新增 ${c.object}`;
    return JSON.stringify(c);
  }).join("；");
}

function ChangesPanel({ onOpenObject }: { onOpenObject: (ref: string) => void }) {
  const { wid } = usePlatform();
  const [live, setLive] = useState(false);
  const [events, setEvents] = useState<ChangeEvent[]>([]);
  const seq = useRef(0);                 // 游标放 ref：pull 身份稳定，计时器不被反复重建
  const pending = useRef(false);

  const { run: pull, running: pulling } = useAction(async () => {
    if (!wid) return;
    if (pending.current) return;         // 上一轮没回来就跳过这一拍，不排队堆积
    pending.current = true;
    try {
      const batch = await api.changes(wid, seq.current);
      if (batch.length) {
        seq.current = batch[batch.length - 1].seq;
        setEvents(prev => [...prev, ...batch].slice(-200));
      }
    } finally { pending.current = false; }
  }, [wid]);

  // 换空间：游标与列表一起归零，避免把上一个空间的流水带过来
  useEffect(() => { seq.current = 0; setEvents([]); void pull(); }, [wid]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!live) return;
    const t = setInterval(() => void pull(), 2500);
    return () => clearInterval(t);
  }, [live, pull]);

  const objects = useMemo(() => new Set(events.flatMap(e => (e.changes || []).map(c => String(c.object || "")))), [events]);

  return (
    <Card title="变更流水" sub="每一次同步与执行都留痕，序号递增。"
          aside={<>
            <label className="check">
              <input type="checkbox" checked={live} onChange={e => setLive(e.target.checked)} />
              实时跟随
            </label>
            <Btn size="sm" variant="subtle" busy={pulling} onClick={() => void pull()}>刷新</Btn>
          </>}>
      {!events.length && <Empty title="暂无变更" hint="同步数据或执行能力后，这里会出现记录。" />}
      {!!events.length && (
        <Collapsible label={`展开 ${events.length} 条记录 · 涉及 ${objects.size} 个对象`} defaultOpen>
          <TableWrap>
            <table>
              <thead><tr><th>序号</th><th>来源</th><th className="wrap">改动</th></tr></thead>
              <tbody>
                {events.slice().reverse().map(e => (
                  <tr key={e.seq}>
                    <td className="nowrap">{e.seq}</td>
                    <td className="nowrap"><Tag kind={e.source === "invoke" ? "" : "plain"}>{e.source}{e.function ? ` · ${e.function}` : ""}</Tag></td>
                    <td className="wrap" title={summarize(e)}>
                      {(e.changes || []).map((c, i) => {
                        const obj = String(c.object || "");
                        if (c.kind === "set_property")
                          return <span key={i}>{obj}.{String(c.property)}: {String(c.old)} → <b>{String(c.new)}</b>{"　"}</span>;
                        if (c.kind === "link")
                          return <span key={i}>{String(c.source)} —{String(c.relation)}→ {String(c.target)}{"　"}</span>;
                        if (c.kind === "create_object")
                          return <button key={i} type="button" className="link" onClick={() => onOpenObject(obj)}>新增 {obj}</button>;
                        return <span key={i}>{JSON.stringify(c)}</span>;
                      })}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        </Collapsible>
      )}
    </Card>
  );
}
