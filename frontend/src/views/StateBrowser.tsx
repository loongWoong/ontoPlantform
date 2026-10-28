import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowClockwise, ArrowRight, MagnifyingGlass } from "@phosphor-icons/react";
import { api, type Obj, type StateSnapshot } from "../api";
import { useAction, usePlatform } from "../store";
import {
  Btn, Card, Collapsible, Drawer, Empty, ErrorNote, Field, FilterRowEditor,
  Loading, ObjectTable, PageHead, Select, TableWrap, Tag, TextInput,
  buildFilters, useLoad, type FilterRow,
} from "../ui";
import type { ChangeEvent } from "../api";
import { ValueView } from "../components/ValueView";
import { formatLocal, toUtcIso } from "../lib/time";
import { valuePreview } from "../lib/value";

export function StateView() {
  const { wid, objectTypes, params, go } = usePlatform();
  const [type, setType] = useState(params.type || objectTypes[0] || "");
  const [filters, setFilters] = useState<FilterRow[]>([]);
  const [query, setQuery] = useState<{ type: string; filters: Record<string, unknown> } | null>(null);
  const [openRef, setOpenRef] = useState<string | null>(params.ref || null);

  useEffect(() => { if (!type && objectTypes[0]) setType(objectTypes[0]); }, [objectTypes, type]);
  useEffect(() => { setQuery(null); setOpenRef(null); setFilters([]); }, [wid]);
  useEffect(() => { if (params.ref) setOpenRef(params.ref); }, [params.ref]);
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
      <PageHead
        kicker="Objects"
        title="对象实例"
        desc="这里的数据只存在于运行时里，平台不缓存第二份业务状态；查询与动作都走同一条校验后的链路。"
        meta={<>
          <span>对象类型 <b>{objectTypes.length}</b></span>
          {query && <span>当前 <b>{query.type}</b></span>}
          {query && <span>结果 <b>{results.loading ? "…" : results.data?.length ?? 0}</b></span>}
        </>}
        actions={<>
          <Btn variant="subtle" busy={results.loading}
               onClick={() => { if (query) setQuery({ ...query }); else results.reload(); }}>
            <ArrowClockwise size={14} /> 刷新
          </Btn>
          <Btn variant="subtle" onClick={() => go("data")}>同步数据</Btn>
        </>}
      />

      <div className="layout main-stage">
        <div>
          <Card title="查询对象" sub="选类型、加条件，结果直接来自运行时。">
            {!objectTypes.length ? (
              <Empty title="运行时还没有对象类型" hint="先发布本体，再把数据同步进来。"
                     action={<Btn size="sm" onClick={() => go("ontology")}>去发布本体</Btn>} />
            ) : (
              <form className="searchbar" onSubmit={e => { e.preventDefault(); setQuery({ type, filters: buildFilters(filters) }); }}>
                <Field label="对象类型">
                  <Select value={type} onChange={e => setType(e.target.value)}>
                    {objectTypes.map(t => <option key={t} value={t}>{t}</option>)}
                  </Select>
                </Field>
                <div className="searchbar-filters">
                  <Field label="过滤条件" hint="可以不加：属性 + 比较方式 + 值。">
                    <FilterRowEditor rows={filters.length ? filters : [{ prop: "", op: "", value: "" }]} onChange={setFilters} />
                  </Field>
                </div>
                <div className="searchbar-go">
                  <Btn type="submit"><MagnifyingGlass size={14} /> 查询</Btn>
                  <Btn type="button" variant="subtle" onClick={() => { setFilters([]); setQuery({ type, filters: {} }); }}>看全部</Btn>
                </div>
              </form>
            )}

            {results.loading && <Loading />}
            {!results.loading && !query && objectTypes.length > 0 && (
              <Empty title="选一个对象类型开始查" hint="或先去「数据」页把数据同步进来。" />
            )}
            {!results.loading && query && !results.data?.length && (
              <Empty title="这个条件下没有对象" hint="换一组条件，或先同步数据。" />
            )}
            {!results.loading && query && !!results.data?.length && (
              <ObjectTable objects={results.data} onOpen={ref => setOpenRef(ref)} />
            )}
            {results.error && <ErrorNote text={results.error} onRetry={results.reload} />}
          </Card>

          <ChangesPanel onOpenObject={ref => setOpenRef(ref)} />
        </div>

        <div>
          <Card title="浏览提示" sub="运行时是唯一事实来源。">
            <ul className="doc-list">
              <li>点对象 id 打开详情：属性、关系、变更记录、历史回读。</li>
              <li>历史回读按变更序号或时间点，回放窗口内的运行时状态。</li>
              <li>关系导航会随本体里的关系定义变化，新增关系后重新发布生效。</li>
            </ul>
            <div className="btn-row">
              {objectTypes[0] && (
                <Btn size="sm" variant="subtle" onClick={() => go("state", { type: objectTypes[0] })}>
                  重新查询 {objectTypes[0]} <ArrowRight size={12} />
                </Btn>
              )}
            </div>
          </Card>
          <Card title="结果为空时" sub="按顺序排查，通常三步内能定位。">
            <ol className="doc-list">
              <li>确认本体已发布，且类型名一致。</li>
              <li>确认同步跑过，数据源映射没有过滤掉全部行。</li>
              <li>在「运行 · 校验与纠偏」里跑一次对账，看运行时是否缺实例。</li>
            </ol>
          </Card>
        </div>
      </div>

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
  const [tab, setTab] = useState<"props" | "related" | "changes" | "asof">("props");
  const detail = useLoad(async () => {
    if (!wid) return null;
    const [obj, rt] = await Promise.all([api.get(wid, ref_), api.runtime(wid).catch(() => null)]);
    if (!obj) return { obj: null as Obj | null, related: [] as { relation: string; direction: string; items: string[] }[], changes: [] as ChangeEvent[] };
    const rels = ((rt?.relations) || []).filter(r => r.source === obj.type || r.target === obj.type);
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
    <Drawer title={ref_} sub={d?.obj ? `${d.obj.type} · 对象详情` : ""} onClose={onClose} wide>
      {detail.loading && <Loading />}
      {!detail.loading && !d?.obj && <Empty title="没找到这个对象" hint={`引用 ${ref_}`} />}
      {d?.obj && (
        <>
          <div className="subtabs" role="tablist" tabIndex={0}>
            {([["props", "属性"], ["related", `关系 ${d.related.length}`], ["changes", `相关记录 ${d.changes.length}`], ["asof", "历史回读"]] as const)
              .map(([k, l]) => (
                <button key={k} type="button" role="tab" aria-selected={tab === k} onClick={() => setTab(k)}>{l}</button>
              ))}
          </div>

          {tab === "props" && (
            <dl className="kv">
              <div className="kv-pair"><dt>类型</dt><dd className="mono">{d.obj.type}</dd></div>
              {Object.entries(d.obj.properties || {}).map(([k, v]) => (
                <div key={k} className="kv-pair"><dt>{k}</dt><dd><ValueView value={v} field={k} /></dd></div>
              ))}
            </dl>
          )}

          {tab === "related" && (
            <>
              {!d.related.length && <Empty title="这个对象类型没有定义关系" hint="到「本体」页补充关系。" />}
              {d.related.map(r => (
                <div key={r.relation}>
                  <h3 className="h4">{r.relation} {r.direction === "out" ? "→" : "←"} · {r.items.length}</h3>
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
                          <td className="nowrap mono">{e.seq}</td>
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
          {tab === "asof" && wid && <AsOfPanel wid={wid} ref_={ref_} />}
        </>
      )}
    </Drawer>
  );
}

function AsOfPanel({ wid, ref_ }: { wid: string; ref_: string }) {
  const history = useLoad(
    () => api.history(wid, ref_, 1).catch(() => null),
    [wid, ref_],
  );
  const head = history.data?.head_seq ?? 0;
  const oldest = history.data?.oldest_seq ?? 0;
  const [seq, setSeq] = useState("");
  const [at, setAt] = useState("");
  const [snap, setSnap] = useState<StateSnapshot | null>(null);
  const [fetchedAt, setFetchedAt] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => { setSnap(null); setFetchedAt(null); setError(""); setSeq(""); setAt(""); }, [wid, ref_]);

  const load = async (value: string) => {
    const parsed = Number(value);
    if (!Number.isInteger(parsed) || parsed <= 0) {
      setError("请输入一个正整数变更序号");
      return;
    }
    setBusy(true);
    setError("");
    try {
      setSnap(await api.asOf(wid, ref_, parsed));
      setFetchedAt(new Date().toISOString());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const loadAt = async (value: string) => {
    if (!value) { setError("请选择一个时间点"); return; }
    let iso: string;
    try {
      iso = toUtcIso(value);
    } catch (e) {
      setError(e instanceof Error ? e.message : "时间格式无法识别，请重新选择");
      return;
    }
    setBusy(true);
    setError("");
    try {
      setSnap(await api.asOfAt(wid, ref_, iso));
      setFetchedAt(new Date().toISOString());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <p className="muted">把运行时状态倒放到某个变更序号，或某个时间点之前的样子；只保留窗口内的历史。查询的是该时刻的快照，不是实时值；下面显示读取瞬间的时间。</p>
      <form className="searchbar" onSubmit={e => { e.preventDefault(); void load(seq || String(head)); }}>
        <Field label="变更序号" hint={head ? `可回读范围 ${oldest} – ${head}（当前）` : "暂无变更记录"}>
          <TextInput value={seq} onChange={e => setSeq(e.target.value)} placeholder={head ? String(head) : ""} inputMode="numeric" />
        </Field>
        <div className="searchbar-go">
          <Btn type="submit" busy={busy}>回读</Btn>
          <Btn type="button" variant="subtle" disabled={!head} onClick={() => { setSeq(String(head)); void load(String(head)); }}>回到最新</Btn>
        </div>
      </form>
      <form className="searchbar" onSubmit={e => { e.preventDefault(); void loadAt(at); }}>
        <Field label="按时间回读" hint="输入的是浏览器本地时间；提交前会转成 UTC，只查窗口内历史。">
          <TextInput type="datetime-local" value={at} onChange={e => setAt(e.target.value)} />
        </Field>
        <div className="searchbar-go">
          <Btn type="submit" variant="tint" busy={busy}>按时间回读</Btn>
        </div>
      </form>
      {error && <p className="error">{error}</p>}
      {snap && !snap.exists && <Empty title={`seq ${snap.as_of_seq} 时该对象还不存在`} hint={`当前 head 为 ${snap.head_seq}`} />}
      {snap && snap.exists && (
        <>
          <p className="muted">
            as-of seq {snap.as_of_seq} · 回放 {snap.changes_replayed} 条变更 · head {snap.head_seq}
            {fetchedAt && ` · 读取于 ${formatLocal(fetchedAt)}`}
          </p>
          <dl className="kv">
            {Object.entries(snap.properties || {}).map(([k, v]) => (
              <div key={k} className="kv-pair"><dt>{k}</dt><dd><ValueView value={v} field={k} /></dd></div>
            ))}
          </dl>
        </>
      )}
    </div>
  );
}

function summarize(e: ChangeEvent): string {
  return (e.changes || []).map(c => {
    if (c.kind === "set_property") return `${c.object}.${c.property}: ${valuePreview(c.old, 30)} → ${valuePreview(c.new, 30)}`;
    if (c.kind === "link") return `${c.source} –${c.relation}→ ${c.target}`;
    if (c.kind === "create_object") return `新增 ${c.object}`;
    return valuePreview(c, 60);
  }).join("；");
}

function ChangesPanel({ onOpenObject }: { onOpenObject: (ref: string) => void }) {
  const { wid } = usePlatform();
  const [live, setLive] = useState(false);
  const [events, setEvents] = useState<ChangeEvent[]>([]);
  const seq = useRef(0);
  const pending = useRef(false);

  const { run: pull, running: pulling } = useAction(async () => {
    if (!wid) return;
    if (pending.current) return;
    pending.current = true;
    try {
      const batch = await api.changes(wid, seq.current);
      if (batch.length) {
        seq.current = batch[batch.length - 1].seq;
        setEvents(prev => [...prev, ...batch].slice(-200));
      }
    } finally { pending.current = false; }
  }, [wid]);

  useEffect(() => { seq.current = 0; setEvents([]); void pull(); }, [wid]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!live) return;
    const t = setInterval(() => void pull(), 5000);
    return () => clearInterval(t);
  }, [live, pull]);

  const objects = useMemo(() => new Set(events.flatMap(e => (e.changes || []).map(c => String(c.object || "")))), [events]);

  return (
    <Card title="变更流水" sub="变更记录按序号追加；界面只做轮询，不是实时推送。"
          aside={<>
            <label className="check-line">
              <input type="checkbox" checked={live} onChange={e => setLive(e.target.checked)} />
              自动刷新（5 秒）
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
                    <td className="nowrap mono">{e.seq}</td>
                    <td className="nowrap"><Tag kind={e.source === "invoke" ? "ok" : "plain"}>{e.source}{e.function ? ` · ${e.function}` : ""}</Tag></td>
                    <td className="wrap" title={summarize(e)}>
                      {(e.changes || []).map((c, i) => {
                        const obj = String(c.object || "");
                        if (c.kind === "set_property")
                          return <span key={i}>{obj}.{String(c.property)}: <ValueView value={c.old} compact /> → <b><ValueView value={c.new} compact /></b>{"　"}</span>;
                        if (c.kind === "link")
                          return <span key={i}>{String(c.source)} –{String(c.relation)}→ {String(c.target)}{"　"}</span>;
                        if (c.kind === "create_object")
                          return <button key={i} type="button" className="link" onClick={() => onOpenObject(obj)}>新增 {obj}</button>;
                        return <span key={i}><ValueView value={c} compact /></span>;
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
