import { useMemo, useState } from "react";
import { ArrowClockwise, PencilSimple, Plugs, PlugsConnected, Table, Trash } from "@phosphor-icons/react";
import {
  api, assetNames, type DataAsset, type DataSource, type PreviewResult,
  type Projection, type ProjectionRun, type SourceTestResult,
} from "../api";
import { useAction, usePlatform } from "../store";
import {
  Btn, Card, Collapsible, Drawer, Empty, ErrorNote, Field, JsonField, KIND_LABEL,
  labelOf, Loading, PageHead, Select, TableWrap, Tag, TextInput, useLoad,
} from "../ui";
import { StatusBadge } from "../components/StatusBadge";
import { ValueView } from "../components/ValueView";
import { formatLocal } from "../lib/time";

export function DataView() {
  const { wid, toast, confirm, refreshRuntime, go } = usePlatform();
  const [preview, setPreview] = useState<DataSource | null>(null);
  const [connectOpen, setConnectOpen] = useState(false);
  const [editSource, setEditSource] = useState<DataSource | null>(null);
  const [editProjection, setEditProjection] = useState<Projection | null>(null);
  const [lastRun, setLastRun] = useState<{ name: string; run: ProjectionRun } | null>(null);

  const load = useLoad(async () => {
    if (!wid) return { sources: [] as DataSource[], projections: [] as Projection[] };
    const [sources, projections] = await Promise.all([
      api.datasources(wid).catch(() => [] as DataSource[]),
      api.projections(wid).catch(() => [] as Projection[]),
    ]);
    return { sources, projections };
  }, [wid]);

  const { run: sync, running: syncing } = useAction(async (pid: string, pname: string) => {
    if (!wid) return;
    const r = await api.runProjection(wid, pid);
    setLastRun({ name: pname, run: r });
    await refreshRuntime();
    const st = r.run_status || r.status || "unknown";
    const at = r.checked_at || r.finished_at;
    if (st === "succeeded") {
      toast(`同步「${pname}」成功：运行 ${r.run_id || "未返回"}，写入 ${r.objects_upserted ?? 0} 个对象、${r.links_asserted ?? 0} 条关系${at ? `，检查于 ${formatLocal(at)}` : ""}`, "ok");
    } else if (st === "skipped") {
      toast(`同步「${pname}」跳过：运行 ${r.run_id || "未返回"}${r.skipped_reason || r.reason ? `，原因 ${r.skipped_reason || r.reason}` : "，源数据没有变化"}${at ? `，检查于 ${formatLocal(at)}` : ""}`, "warn");
    } else {
      toast(`同步「${pname}」未成功（${st}）：以运行编号 ${r.run_id || "未返回"} 的记录为准${at ? `，记录于 ${formatLocal(at)}` : ""}`, st === "failed" ? "err" : "warn");
    }
    load.reload();
  }, [wid]);

  const { run: deleteSource, running: deletingSource } = useAction(async (d: DataSource) => {
    if (!wid) return;
    await api.deleteSource(wid, d.id);
    toast(`数据源「${d.name}」已删除；已同步到运行时的对象实例保留不变`, "ok");
    load.reload();
  }, [wid]);

  /* ---- 连通性检查：只测不改，结果含来源类型与测试时间 ---- */
  const { run: testSource, running: testingSource } = useAction(async (d: DataSource) => {
    if (!wid) return;
    const res: SourceTestResult = await api.testSource(wid, d.id);
    const when = res.tested_at ? `，测试于 ${formatLocal(res.tested_at)}` : "";
    toast(`连接正常：${d.name}（${labelOf(KIND_LABEL, res.kind || d.kind)}）${when}`, "ok");
  }, [wid]);

  const { run: deleteProjection, running: deletingProjection } = useAction(async (p: Projection) => {
    if (!wid) return;
    const result = await api.deleteProjection(wid, p.id);
    setLastRun(null);
    toast(`映射「${p.name}」已删除，同时移除 ${result.removed_runs ?? 0} 条同步记录；运行时对象实例保留`, "ok");
    load.reload();
  }, [wid]);

  const { sources, projections } = load.data || { sources: [], projections: [] };

  return (
    <>
      <PageHead
        kicker="Data"
        title="数据"
        desc="外部世界的入口。平台不复制数据，只有映射和显式同步；同步写入的是运行时对象，不是第二份业务库。"
        meta={<>
          <span>数据源 <b>{sources.length}</b></span>
          <span>映射 <b>{projections.length}</b></span>
          <span>同步方式 <b>按需触发</b></span>
        </>}
        actions={<>
          <Btn variant="subtle" onClick={load.reload} busy={load.loading}>
            <ArrowClockwise size={14} /> 刷新
          </Btn>
          <Btn onClick={() => setConnectOpen(true)}>
            <PlugsConnected size={14} /> 连接数据源
          </Btn>
        </>}
      />

      {load.loading && <Loading />}

      <Card title="数据源" sub="连接参数与可用数据表可再编辑；「测试连接」和预览都会真的连一次数据源，不是读界面缓存。"
            aside={<Tag kind="plain">{sources.length} 个</Tag>}>
        {!load.loading && !sources.length && (
          <Empty title="还没有数据源" hint="连接一个静态样例、SQLite 数据库或 REST 接口。"
                 action={<Btn size="sm" onClick={() => setConnectOpen(true)}>连接第一个</Btn>} />
        )}
        {!!sources.length && (
          <TableWrap>
            <table>
              <thead><tr><th>名称</th><th>来源</th><th className="wrap">可用数据表</th><th>操作</th></tr></thead>
              <tbody>
                {sources.map(d => (
                  <tr key={d.id}>
                    <td className="nowrap"><b>{d.name}</b></td>
                    <td className="nowrap"><Tag kind="plain">{labelOf(KIND_LABEL, d.kind)}</Tag></td>
                    <td className="wrap" title={assetNames(d).join("、")}>{assetNames(d).join("、") || "–"}</td>
                    <td className="nowrap">
                      <div className="pill-row">
                        <Btn size="sm" variant="subtle" onClick={() => setPreview(d)}>预览</Btn>
                        <Btn size="sm" variant="subtle"
                             aria-label={`编辑数据源 ${d.name}`} title="改名称、连接参数与数据表声明"
                             onClick={() => setEditSource(d)}>
                          <PencilSimple size={12} /> 编辑
                        </Btn>
                        <Btn size="sm" variant="subtle" disabled={testingSource}
                             aria-label={`测试连接 ${d.name}`} title="只做连通性检查，不改配置"
                             onClick={() => void testSource(d)}>
                          <Plugs size={12} /> 测试连接
                        </Btn>
                        <Btn size="sm" variant="subtle" disabled={deletingSource}
                             aria-label={`删除数据源 ${d.name}`}
                             onClick={() => confirm({
                               title: `删除数据源「${d.name}」？`,
                               danger: true,
                               confirmText: "确认删除",
                               body: (
                                 <p className="muted">
                                   只有没有映射引用它时才能删除；已同步到运行时的对象实例不会被删除。若仍有映射，请先删除对应映射。
                                 </p>
                               ),
                               run: async () => { await deleteSource(d); },
                             })}>
                          <Trash size={12} /> 删除
                        </Btn>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
      </Card>

      <Card title="映射与同步" sub="映射解决「源字段对应哪个业务属性」，同步把它变成对象实例并写入运行时。"
            aside={<Tag kind={projections.length ? "ok" : "plain"}>{projections.length} 个映射</Tag>}>
        {!load.loading && !projections.length && (
          <Empty title="还没有映射" hint="先连接数据源，再声明字段怎么对应业务属性；也可以先生成骨架再改。"
                 action={sources.length ? undefined : <Btn size="sm" variant="subtle" onClick={() => setConnectOpen(true)}>先连接数据源</Btn>} />
        )}
        {!!projections.length && (
          <>
            {lastRun && (
              <div className={"banner " + ((lastRun.run.run_status || lastRun.run.status) === "succeeded" ? "info" : "warn")}>
                最近一次同步「{lastRun.name}」：<StatusBadge status={lastRun.run.run_status || lastRun.run.status || "unknown"} />
                <span className="mono small"> 运行 {lastRun.run.run_id || "–"} · 检查于 {lastRun.run.checked_at ? formatLocal(lastRun.run.checked_at) : "–"} · 完成于 {lastRun.run.finished_at ? formatLocal(lastRun.run.finished_at) : "–"} · 写入 {lastRun.run.objects_upserted ?? 0} 对象 / {lastRun.run.links_asserted ?? 0} 关系</span>
                {lastRun.run.skipped_reason && <p className="muted small">跳过原因：{lastRun.run.skipped_reason}</p>}
              </div>
            )}
            <TableWrap>
              <table>
                <thead><tr><th>名称</th><th>覆盖范围</th><th>写入模式</th><th>最近检查</th><th>最近变更</th><th>操作</th></tr></thead>
                <tbody>
                  {projections.map(p => (
                    <tr key={p.id}>
                      <td className="nowrap"><b>{p.name}</b></td>
                      <td className="wrap">
                        {(p.object_mappings || []).length} 个对象映射 · {(p.relation_mappings || []).length} 条关系映射
                        {!!p.row_limit && <span className="muted small"> · 每次最多 {p.row_limit} 行</span>}
                      </td>
                      <td className="nowrap"><Tag kind={p.mode === "authoritative" ? "warn" : "plain"}>{p.mode || "未返回"}</Tag>
                        {!!p.managed_types?.length && <span className="muted small"> · {p.managed_types.length} 个托管类型</span>}
                      </td>
                      <td className="mono nowrap">{p.last_check_at ? formatLocal(p.last_check_at) : "未返回"}</td>
                      <td className="mono nowrap">{p.last_change_at ? formatLocal(p.last_change_at) : "未返回"}</td>
                      <td className="nowrap">
                        <div className="pill-row">
                          <Btn size="sm" variant="subtle" disabled={deletingProjection}
                               aria-label={`编辑映射 ${p.name}`} title="改名称、映射内容、写入模式与行上限"
                               onClick={() => setEditProjection(p)}>
                            <PencilSimple size={12} /> 编辑
                          </Btn>
                          <Btn size="sm" busy={syncing}
                               onClick={() => confirm({
                                 title: `立即同步「${p.name}」？`,
                                 confirmText: "开始同步",
                                 body: <p className="muted">会读取数据源并按映射写入业务世界；结果会返回运行编号与计数，失败时以错误为准，不会显示成完成。</p>,
                                 run: async () => { await sync(p.id, p.name); },
                               })}>立即同步</Btn>
                          <Btn size="sm" variant="subtle" disabled={deletingProjection}
                               aria-label={`删除映射 ${p.name}`}
                               onClick={() => confirm({
                                 title: `删除映射「${p.name}」？`,
                                 danger: true,
                                 confirmText: "确认删除",
                                 body: (
                                   <p className="muted">
                                     映射配置与它的同步运行记录会被删除；之前同步写入运行时的对象实例会保留。这个操作不会删除数据源。
                                   </p>
                                 ),
                                 run: async () => { await deleteProjection(p); },
                               })}>
                            <Trash size={12} /> 删除
                          </Btn>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
          </>
        )}
        {!!sources.length && <CreateProjection sources={sources} onDone={load.reload} />}
      </Card>

      <div className="bento gap-top">
        <div className="col-6">
          <Card title="数据不会凭空出现" sub="同步是显式动作；成功与否以运行状态和计数为准，不靠界面推断。">
            <p className="muted">源数据先按映射编译成对象写入指令，再交给运行时；身份冲突、属性类型不匹配会在写入前被拒绝。</p>
            <div className="btn-row">
              <Btn size="sm" variant="subtle" onClick={() => go("state")}>去对象实例核对</Btn>
              <Btn size="sm" variant="subtle" onClick={() => go("operations", { tab: "health" })}>跑结构校验</Btn>
            </div>
          </Card>
        </div>
        <div className="col-6">
          <Card title="数据接入清单" sub="建议顺序：先最小一条，再扩大范围。">
            <ul className="doc-list">
              <li>数据源能连通，且声明了正确的表名 / 查询 / 接口路径。</li>
              <li>主键字段唯一且稳定，前导零编号要按字符串处理。</li>
              <li>映射的每个源字段都能落到已存在的属性上。</li>
              <li>同步后用对象实例页抽查 3 条，再跑一次对账。</li>
            </ul>
          </Card>
        </div>
      </div>

      {preview && <PreviewDrawer source={preview} onClose={() => setPreview(null)} />}
      {editSource && wid && (
        <SourceEditDrawer wid={wid} source={editSource}
                          onClose={() => setEditSource(null)}
                          onSaved={() => { setEditSource(null); load.reload(); }} />
      )}
      {editProjection && wid && (
        <ProjectionEditDrawer wid={wid} projection={editProjection} sources={sources}
                              onClose={() => setEditProjection(null)}
                              onSaved={() => { setEditProjection(null); load.reload(); }} />
      )}
      {connectOpen && <ConnectDrawer onClose={() => setConnectOpen(false)} onDone={() => { setConnectOpen(false); load.reload(); }} />}
    </>
  );
}

const PREVIEW_LIMIT = 20;

function PreviewDrawer({ source, onClose }: { source: DataSource; onClose: () => void }) {
  const { wid, toast } = usePlatform();
  const names = assetNames(source);
  const { run: testConn, running: testing } = useAction(async () => {
    if (!wid) return;
    const res = await api.testSource(wid, source.id);
    const when = res.tested_at ? `，测试于 ${formatLocal(res.tested_at)}` : "";
    toast(`连接正常：${source.name}（${labelOf(KIND_LABEL, res.kind || source.kind)}）${when}`, "ok");
  }, [wid, source.id, source.name, source.kind]);

  return (
    <Drawer title={source.name} sub={`${labelOf(KIND_LABEL, source.kind)} · 实时读取预览`} onClose={onClose} wide
            footer={<div className="btn-row flat">
              <Btn variant="subtle" busy={testing} onClick={() => void testConn()}>
                <Plugs size={12} /> 测试连接
              </Btn>
              <Btn variant="subtle" onClick={onClose}>关闭</Btn>
            </div>}>
      {!names.length && (
        <Empty title="这个数据源还没声明可用数据表"
               hint="到数据源行的「编辑」里补 assets 声明，再回来预览。" />
      )}
      {names.map(n => <AssetPreview key={n} source={source} asset={n} />)}
      <Collapsible label="这个数据源的连接参数">
        <pre className="obs">{JSON.stringify({ kind: source.kind, config: source.config }, null, 2)}</pre>
      </Collapsible>
    </Drawer>
  );
}

/** 每个数据表单独取一次真实预览：加载 / 失败 / 空表 / 有行四种状态都显式呈现。
 *  静态、sqlite、rest 走同一条链路，界面不按 kind 分支。 */
function AssetPreview({ source, asset }: { source: DataSource; asset: string }) {
  const { wid } = usePlatform();
  const load = useLoad(() => (
    wid ? api.previewSource(wid, source.id, asset, PREVIEW_LIMIT) : Promise.resolve(null)
  ), [wid, source.id, asset]);
  const res: PreviewResult | null = load.data;
  const columns = res?.columns?.length ? res.columns : (res?.rows?.[0] ? Object.keys(res.rows[0]) : []);
  const total = typeof res?.total === "number" ? res.total : (res?.rows?.length ?? 0);
  const shown = res?.rows?.length ?? 0;
  return (
    <div>
      <h3 className="h4">{asset}{res ? ` · 共 ${total} 行` : ""}</h3>
      {load.loading && <Loading />}
      {load.error && <ErrorNote text={load.error} onRetry={load.reload} />}
      {!!res && !res.rows.length && <p className="muted">这个数据表读出来是空的（共 {total} 行）。</p>}
      {!!res?.rows.length && (
        <>
          <TableWrap auto>
            <table>
              <thead><tr>{columns.map(k => <th key={k}>{k}</th>)}</tr></thead>
              <tbody>
                {res.rows.map((r, i) => (
                  <tr key={i}>{columns.map(k => <td key={k}><ValueView value={r[k]} compact field={k} /></td>)}</tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
          <p className="muted small">
            这是实时读取的结果：共 {total} 行，仅显示前 {shown} 行（单次预览最多 {PREVIEW_LIMIT} 行）。
            预览不写运行时，也不改源数据；写错要改配置，用数据源行的「编辑」。
          </p>
        </>
      )}
    </div>
  );
}

function ConnectDrawer({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const { wid, toast } = usePlatform();
  const [name, setName] = useState("");
  const [kind, setKind] = useState("static");
  const [config, setConfig] = useState("");
  const [assets, setAssets] = useState("");

  const { run, running } = useAction(async () => {
    if (!wid) return;
    if (!name.trim()) { toast("请填写数据源名称", "err"); return; }
    let cfg: Record<string, unknown> = {};
    let ast: unknown[] = [];
    try { cfg = config.trim() ? JSON.parse(config) : {}; } catch { toast("连接参数不是合法 JSON", "err"); return; }
    try { ast = assets.trim() ? JSON.parse(assets) : []; } catch { toast("数据表声明不是合法 JSON", "err"); return; }
    if (!Array.isArray(ast)) { toast("数据表声明要是 JSON 数组", "err"); return; }
    await api.connectSource(wid, { name: name.trim(), kind, config: cfg, assets: ast });
    toast(`数据源「${name.trim()}」已连接`, "ok");
    onDone();
  }, [wid, name, kind, config, assets]);

  return (
    <Drawer title="连接新数据源" onClose={onClose}
            footer={<>
              <Btn busy={running} onClick={() => void run()}>连接</Btn>
              <Btn variant="subtle" onClick={onClose}>取消</Btn>
            </>}>
      <Field label="名称" hint="用业务能看懂的名字，例如「车辆主数据」。">
        <TextInput value={name} placeholder="车辆主数据" onChange={e => setName(e.target.value)} />
      </Field>
      <Field label="来源类型">
        <Select value={kind} onChange={e => setKind(e.target.value)}>
          <option value="static">静态样例（演示 / 测试）</option>
          <option value="sqlite">数据库文件</option>
          <option value="rest">接口</option>
        </Select>
      </Field>
      <JsonField label="连接参数" value={config} onChange={setConfig} rows={3}
                 hint={kind === "sqlite" ? '例如 {"path": "data.db"}；接口用 {"base_url": "https://…"}' : "静态样例可留空。"} />
      <JsonField label="数据表声明" value={assets} onChange={setAssets} rows={8}
                 hint='每个数据表：{"name": "vehicles", "config": {"rows": [{"vid": "V001"}]}}。数据库用 {"query": "SELECT …"}；接口用 {"path": "/x"}。' />
      <Collapsible label="插入一份可直接跑的静态样例">
        <Btn size="sm" variant="subtle" onClick={() => {
          setAssets(JSON.stringify([
            { name: "vehicles", config: { rows: [{ vid: "V001", plate: "鲁A-1001", balance: 50 }, { vid: "V002", plate: "鲁A-1002", balance: 3 }] } },
            { name: "stations", config: { rows: [{ sid: "ST001", code: "济南西", status: "congested" }] } },
          ], null, 2));
        }}>插入样例</Btn>
      </Collapsible>
    </Drawer>
  );
}

function CreateProjection({ sources, onDone }: { sources: DataSource[]; onDone: () => void }) {
  const { wid, toast } = usePlatform();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [dsId, setDsId] = useState(sources[0]?.id || "");
  const [mappings, setMappings] = useState("");
  const obj = useLoad(() => (wid ? api.ontology(wid).catch(() => null) : Promise.resolve(null)), [wid]);

  const skeleton = useMemo(() => {
    const ds = sources.find(s => s.id === dsId);
    const assets = ds ? assetNames(ds) : [];
    const objTypes = Object.keys(obj.data?.objects || {});
    const rels = Object.keys(obj.data?.relations || {});
    const tmpl: Record<string, unknown> = { object_mappings: [], relation_mappings: [] };
    if (assets[0]) {
      const t = objTypes[0] || "对象类型";
      const firstProp = Object.keys(obj.data?.objects?.[t]?.properties || {})[0] || "属性名";
      tmpl.object_mappings = [{
        asset: assets[0], object_type: t, id_field: "源数据里当主键的字段",
        filter: {},
        fields: [{ source_field: "源字段名", property: firstProp }],
      }];
    }
    if (assets[1] && rels[0]) {
      tmpl.relation_mappings = [{
        asset: assets[1], relation: rels[0],
        source_field: "源对象主键字段", target_field: "目标对象主键字段",
      }];
    }
    return JSON.stringify(tmpl, null, 2);
  }, [sources, dsId, obj.data]);

  const { run, running } = useAction(async () => {
    if (!wid) return;
    if (!name.trim() || !dsId) { toast("名称和数据源都要选", "err"); return; }
    let parsed: Record<string, unknown>;
    try { parsed = mappings.trim() ? JSON.parse(mappings) : {}; } catch { toast("映射不是合法 JSON", "err"); return; }
    const object_mappings = Array.isArray(parsed) ? parsed : (parsed.object_mappings || []);
    const relation_mappings = Array.isArray(parsed) ? [] : (parsed.relation_mappings || []);
    await api.createProjection(wid, { name: name.trim(), datasource_id: dsId, object_mappings, relation_mappings });
    toast(`映射「${name.trim()}」已创建，点「立即同步」写入业务世界`, "ok");
    setName(""); setMappings(""); setOpen(false);
    onDone();
  }, [wid, name, dsId, mappings]);

  return (
    <>
      <div className="btn-row">
        <Btn variant="subtle" onClick={() => setOpen(true)}>
          <Table size={14} /> 新建映射
        </Btn>
        <span className="muted small">映射 = 源字段 ↔ 业务属性的对应表</span>
      </div>
      {open && (
        <Drawer title="新建映射" onClose={() => setOpen(false)} wide
                footer={<>
                  <Btn busy={running} onClick={() => void run()}>创建映射</Btn>
                  <Btn variant="subtle" onClick={() => setOpen(false)}>取消</Btn>
                </>}>
          <Field label="名称"><TextInput value={name} placeholder="车辆与通行映射" onChange={e => setName(e.target.value)} /></Field>
          <Field label="数据源">
            <Select value={dsId} onChange={e => setDsId(e.target.value)}>
              {sources.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
            </Select>
          </Field>
          <JsonField label="映射内容" value={mappings} onChange={setMappings} rows={14} required
                     hint='对象映射：{"asset","object_type","id_field","fields":[{"source_field","property"}]}；关系映射：{"asset","relation","source_field","target_field"}。' />
          <div className="btn-row">
            <Btn size="sm" variant="subtle" onClick={() => setMappings(skeleton)}>按当前选择生成骨架</Btn>
            <span className="muted small">骨架会带入所选数据源的表名与本体里的对象、属性名，改字段名即可。</span>
          </div>
        </Drawer>
      )}
    </>
  );
}

/* ------------------------------------------------------------------ 编辑抽屉 */

const assetList = (d: DataSource): DataAsset[] =>
  Array.isArray(d.assets) ? d.assets : Object.values(d.assets || {});

/** 保存失败时既 toast（useAction）又在抽屉里留一条服务端原因；抽屉不关，改完可以再存。 */
function useSave(task: () => Promise<string>, deps: unknown[]) {
  const { toast } = usePlatform();
  const [error, setError] = useState<string | null>(null);
  const { run, running } = useAction(async () => {
    setError(null);
    try {
      const message = await task();
      toast(message, "ok");
    } catch (e) {
      setError((e as Error).message);
      throw e;
    }
  }, deps);
  return { run, running, error };
}

/** 数据源编辑：名称 / 连接参数 / 数据表声明。kind 只读——换类型要新建数据源。
 *  后端默认 test_connection=true：先测连接，通了才落库，失败时旧配置原样保留。 */
function SourceEditDrawer({ wid, source, onClose, onSaved }: {
  wid: string; source: DataSource; onClose: () => void; onSaved: () => void;
}) {
  const [name, setName] = useState(source.name);
  const [config, setConfig] = useState(() => JSON.stringify(source.config || {}, null, 2));
  const [assets, setAssets] = useState(() => JSON.stringify(assetList(source), null, 2));

  // 实时提示这次会删掉哪些数据表声明（删不掉时服务端会说明是哪个映射在用）。
  const removed = (() => {
    let next: unknown;
    try { next = JSON.parse(assets); } catch { return []; }
    if (!Array.isArray(next)) return [];
    const kept = new Set(
      next.map(raw => (raw && typeof raw === "object" ? String((raw as Record<string, unknown>).name || "") : ""))
        .filter(Boolean));
    return assetNames(source).filter(n => !kept.has(n));
  })();

  const { run, running, error } = useSave(async () => {
    const trimmed = name.trim();
    if (!trimmed) throw new Error("数据源名称不能为空");
    let cfg: unknown;
    try { cfg = config.trim() ? JSON.parse(config) : {}; }
    catch (e) { throw new Error(`连接参数不是合法 JSON：${(e as Error).message}`); }
    if (!cfg || typeof cfg !== "object" || Array.isArray(cfg)) {
      throw new Error('连接参数必须是一个 JSON 对象，例如 {"path": "data.db"}');
    }
    let rawAssets: unknown;
    try { rawAssets = assets.trim() ? JSON.parse(assets) : []; }
    catch (e) { throw new Error(`数据表声明不是合法 JSON：${(e as Error).message}`); }
    if (!Array.isArray(rawAssets)) {
      throw new Error('数据表声明必须是数组，例如 [{"name":"vehicles","config":{"query":"SELECT …"}}]');
    }
    const seen = new Set<string>();
    const clean = rawAssets.map((raw, i) => {
      if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
        throw new Error(`第 ${i + 1} 个数据表不是对象`);
      }
      const item = raw as Record<string, unknown>;
      const assetName = typeof item.name === "string" ? item.name.trim() : "";
      if (!assetName) throw new Error(`第 ${i + 1} 个数据表缺少 name`);
      if (seen.has(assetName)) throw new Error(`数据表「${assetName}」重复了`);
      seen.add(assetName);
      const assetConfig = item.config;
      if (assetConfig !== undefined && assetConfig !== null
          && (typeof assetConfig !== "object" || Array.isArray(assetConfig))) {
        throw new Error(`数据表「${assetName}」的 config 必须是对象`);
      }
      return {
        ...item,
        name: assetName,
        config: (assetConfig as Record<string, unknown>) || {},
      };
    });
    await api.updateSource(wid, source.id, { name: trimmed, config: cfg as Record<string, unknown>, assets: clean });
    onSaved();
    return `数据源「${trimmed}」已更新（${clean.length} 个数据表）`;
  }, [wid, source, name, config, assets]);

  return (
    <Drawer title={`编辑数据源 · ${source.name}`} sub="保存前会先测一次连接；连不上就保留旧配置，不会被半截覆盖。"
            onClose={onClose}
            footer={<div className="btn-row flat">
              <Btn busy={running} onClick={() => void run()}>保存更正</Btn>
              <Btn variant="subtle" disabled={running} onClick={onClose}>取消</Btn>
            </div>}>
      {error && <ErrorNote text={`保存被拒绝：${error}`} />}
      <Field label="名称" hint="用业务能看懂的名字，例如「车辆主数据」。">
        <TextInput value={name} placeholder="车辆主数据" onChange={e => setName(e.target.value)} disabled={running} />
      </Field>
      <Field label="来源类型（不可改）" hint="改类型等于换一个数据源；需要时新建一个数据源，再把映射改过去。">
        <TextInput value={labelOf(KIND_LABEL, source.kind)} disabled />
      </Field>
      <JsonField label="连接参数" value={config} onChange={setConfig} rows={4}
                 hint={source.kind === "sqlite"
                   ? '例如 {"path": "data.db"}，保存时会真的打开一次文件。'
                   : source.kind === "rest"
                     ? '例如 {"base_url": "https://…", "headers": {}, "timeout": 30}，保存时会真的请求一次。'
                     : "静态样例可留空 {}。"} />
      <JsonField label="数据表声明" value={assets} onChange={setAssets} rows={12}
                 hint='数组形式，与「连接数据源」时一致：{"name": "vehicles", "config": {"rows": [...]}}。数据库用 {"query": "SELECT …"}；接口用 {"path": "/x"}。' />
      {removed.length > 0 && (
        <p className="muted small">
          这次会删掉数据表声明：{removed.join("、")}。仍被映射引用时服务端会拒绝，并指出是哪个映射在用；
          删掉声明不会删除已同步到运行时的对象实例。
        </p>
      )}
    </Drawer>
  );
}

/** 映射编辑：名称 / 写入模式 / 行上限 / 完整映射 JSON。数据源绑定不可改。 */
function ProjectionEditDrawer({ wid, projection, sources, onClose, onSaved }: {
  wid: string; projection: Projection; sources: DataSource[];
  onClose: () => void; onSaved: () => void;
}) {
  const sourceName = sources.find(s => s.id === projection.datasource_id)?.name || projection.datasource_id;
  const [name, setName] = useState(projection.name);
  const [mode, setMode] = useState(projection.mode || "additive");
  const [rowLimit, setRowLimit] = useState(String(projection.row_limit ?? 0));
  const [mappings, setMappings] = useState(() => JSON.stringify({
    object_mappings: projection.object_mappings || [],
    relation_mappings: projection.relation_mappings || [],
  }, null, 2));

  const { run, running, error } = useSave(async () => {
    const trimmed = name.trim();
    if (!trimmed) throw new Error("映射名称不能为空");
    const limitText = rowLimit.trim() || "0";
    if (!/^\d+$/.test(limitText)) throw new Error("行上限必须是不小于 0 的整数；0 表示不限制");
    const limit = Number(limitText);
    let parsed: unknown;
    try { parsed = mappings.trim() ? JSON.parse(mappings) : {}; }
    catch (e) { throw new Error(`映射内容不是合法 JSON：${(e as Error).message}`); }
    const record = (parsed && typeof parsed === "object" && !Array.isArray(parsed))
      ? parsed as Record<string, unknown> : null;
    const objectMappings = Array.isArray(parsed) ? parsed : record?.object_mappings;
    const relationMappings = Array.isArray(parsed) ? [] : (record?.relation_mappings ?? []);
    if (!Array.isArray(objectMappings)) throw new Error("object_mappings 必须是数组");
    if (!Array.isArray(relationMappings)) throw new Error("relation_mappings 必须是数组");
    await api.updateProjection(wid, projection.id, {
      name: trimmed,
      mode,
      row_limit: limit,
      object_mappings: objectMappings as Projection["object_mappings"],
      relation_mappings: relationMappings as Projection["relation_mappings"],
    });
    onSaved();
    return `映射「${trimmed}」已更新；下一次同步按新映射写入`;
  }, [wid, projection, name, mode, rowLimit, mappings]);

  return (
    <Drawer title={`编辑映射 · ${projection.name}`} sub="数据源绑定不可改；改映射不会回滚已同步的对象。"
            onClose={onClose} wide
            footer={<div className="btn-row flat">
              <Btn busy={running} onClick={() => void run()}>保存更正</Btn>
              <Btn variant="subtle" disabled={running} onClick={onClose}>取消</Btn>
            </div>}>
      {error && <ErrorNote text={`保存被拒绝：${error}`} />}
      <Field label="名称">
        <TextInput value={name} placeholder="车辆与通行映射" onChange={e => setName(e.target.value)} disabled={running} />
      </Field>
      <Field label="数据源（不可改）" hint="换数据源请新建映射；这里只改怎么映射。">
        <TextInput value={sourceName} disabled />
      </Field>
      <Field label="写入模式" hint="additive 只拥有自己写入的对象；authoritative 还会把映射类型下、源里没有对应行的运行时对象当成孤儿。不确定就留 additive。">
        <Select value={mode} onChange={e => setMode(e.target.value)} disabled={running}>
          {mode !== "additive" && mode !== "authoritative" && <option value={mode}>{mode}（当前值）</option>}
          <option value="additive">additive（只增不改，安全）</option>
          <option value="authoritative">authoritative（以源为准，谨慎）</option>
        </Select>
      </Field>
      <Field label="每次同步行上限" hint="0 表示不限制；超过上限的同步会报告被截断，不会静默只写一半。">
        <TextInput value={rowLimit} inputMode="numeric" placeholder="0"
                   onChange={e => setRowLimit(e.target.value)} disabled={running} />
      </Field>
      <JsonField label="映射内容（对象 + 关系）" value={mappings} onChange={setMappings} rows={16} required
                 hint='一个 JSON：{"object_mappings":[{"asset","object_type","id_field","fields":[{"source_field","property"}]}],"relation_mappings":[{"asset","relation","source_field","target_field"}]}。与新建映射的格式一致。' />
      <div className={"banner " + (mode === "authoritative" ? "warn" : "info")}>
        改映射不会回滚已经同步进运行时的对象；下一次「立即同步」才按新映射写入。
        {mode === "authoritative" && " 当前是 authoritative：源里查不到的映射对象会被当作孤儿处理，确认之后再同步。"}
      </div>
    </Drawer>
  );
}
