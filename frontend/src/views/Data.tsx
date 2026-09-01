import { useMemo, useState } from "react";
import { api, assetNames, type DataSource, type Projection } from "../api";
import { useAction, usePlatform } from "../store";
import {
  Btn, Card, Collapsible, Drawer, Empty, Field, JsonField, KIND_LABEL,
  labelOf, Loading, Select, TableWrap, Tag, TextInput, useLoad,
} from "../ui";

export function DataView() {
  const { wid, toast, confirm, refreshRuntime } = usePlatform();
  const [preview, setPreview] = useState<DataSource | null>(null);
  const [connectOpen, setConnectOpen] = useState(false);

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
    await refreshRuntime();
    toast(`同步「${pname}」完成：新增或更新 ${r.objects_upserted ?? 0} 个对象、${r.links_asserted ?? 0} 条关系`, "ok");
    load.reload();
  }, [wid]);

  if (load.loading && !load.data) return <Loading />;
  const { sources, projections } = load.data || { sources: [], projections: [] };

  return (
    <>
      <Card title="数据源" sub="外部世界的入口。平台不复制数据，同步时才按需读取。"
            aside={<Btn size="sm" onClick={() => setConnectOpen(true)}>连接新数据源</Btn>}>
        {!sources.length && <Empty title="还没有数据源" hint="连接一个静态样例、数据库或接口。"
                                   action={<Btn size="sm" onClick={() => setConnectOpen(true)}>连接第一个</Btn>} />}
        {!!sources.length && (
          <TableWrap>
            <table>
              <thead><tr><th>名称</th><th>来源</th><th className="wrap">可用数据表</th><th> </th></tr></thead>
              <tbody>
                {sources.map(d => (
                  <tr key={d.id}>
                    <td className="nowrap"><b>{d.name}</b></td>
                    <td className="nowrap"><Tag kind="plain">{labelOf(KIND_LABEL, d.kind)}</Tag></td>
                    <td className="wrap" title={assetNames(d).join("、")}>{assetNames(d).join("、") || "—"}</td>
                    <td className="nowrap"><Btn size="sm" variant="subtle" onClick={() => setPreview(d)}>预览</Btn></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
      </Card>

      <Card title="映射与同步" sub="映射解决「字段对应哪个业务属性」，同步把源数据变成对象实例。">
        {!projections.length && <Empty title="还没有映射" hint="先连接数据源，再声明字段怎么对应业务属性。" />}
        {!!projections.length && (
          <TableWrap>
            <table>
              <thead><tr><th>名称</th><th>覆盖范围</th><th> </th></tr></thead>
              <tbody>
                {projections.map(p => (
                  <tr key={p.id}>
                    <td className="nowrap"><b>{p.name}</b></td>
                    <td className="wrap">
                      {(p.object_mappings || []).length} 个对象映射 · {(p.relation_mappings || []).length} 条关系映射
                    </td>
                    <td className="nowrap">
                      <Btn size="sm" busy={syncing}
                           onClick={() => confirm({
                             title: `立即同步「${p.name}」？`,
                             confirmText: "开始同步",
                             body: <p className="muted">会读取数据源并按映射写入业务世界，每次写入都留痕。</p>,
                             run: async () => { await sync(p.id, p.name); },
                           })}>立即同步</Btn>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
        {!!sources.length && <CreateProjection sources={sources} onDone={load.reload} />}
      </Card>

      {preview && <PreviewDrawer source={preview} onClose={() => setPreview(null)} />}
      {connectOpen && <ConnectDrawer onClose={() => setConnectOpen(false)} onDone={() => { setConnectOpen(false); load.reload(); }} />}
    </>
  );
}

function PreviewDrawer({ source, onClose }: { source: DataSource; onClose: () => void }) {
  const names = assetNames(source);
  return (
    <Drawer title={source.name} sub={labelOf(KIND_LABEL, source.kind)} onClose={onClose}>
      {!names.length && <Empty title="这个数据源还没声明可用数据表" />}
      {names.map(n => {
        const asset = Array.isArray(source.assets) ? source.assets.find(a => a.name === n) : source.assets[n];
        const rows = (asset?.config?.rows as Record<string, unknown>[] | undefined) || [];
        return (
          <div key={n}>
            <h4 className="h4">{n}{rows.length ? ` · ${rows.length} 行` : ""}</h4>
            {rows.length ? (
              <TableWrap auto>
                <table>
                  <thead><tr>{Object.keys(rows[0]).map(k => <th key={k}>{k}</th>)}</tr></thead>
                  <tbody>
                    {rows.slice(0, 5).map((r, i) => (
                      <tr key={i}>{Object.keys(rows[0]).map(k => <td key={k} title={String(r[k])}>{String(r[k] ?? "—")}</td>)}</tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            ) : <p className="muted">静态样例之外的数据源在同步时才读取，这里只展示已声明的样例。</p>}
          </div>
        );
      })}
      <Collapsible label="这个数据源的连接参数">
        <pre>{JSON.stringify({ kind: source.kind, config: source.config }, null, 2)}</pre>
      </Collapsible>
      <div className="btn-row">
        <Btn variant="subtle" onClick={onClose}>关闭</Btn>
      </div>
    </Drawer>
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
          <option value="static">静态样例（演示/测试）</option>
          <option value="sqlite">数据库文件</option>
          <option value="rest">接口</option>
        </Select>
      </Field>
      <JsonField label="连接参数" value={config} onChange={setConfig} rows={3}
                 hint={kind === "sqlite" ? '例如 {"path": "data.db"}；rest 用 {"base_url": "https://…"}`'
                    : "静态样例可留空。"} />
      <JsonField label="数据表声明" value={assets} onChange={setAssets} rows={8}
                 hint='每个数据表：{"name": "vehicles", "config": {"rows": [{"vid": "V001", "plate": "鲁A1"}]}}。静态样例用 rows；数据库用 {"query": "SELECT …"}；接口用 {"path": "/x"}。' />
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
        <Btn variant="subtle" onClick={() => setOpen(true)}>新建映射</Btn>
        <span className="muted">映射 = 源字段 ↔ 业务属性的对应表</span>
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
            <span className="muted">骨架会带入所选数据源的表名与本体里的对象、属性名，改字段名即可。</span>
          </div>
        </Drawer>
      )}
    </>
  );
}
