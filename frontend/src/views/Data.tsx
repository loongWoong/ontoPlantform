import { useMemo, useState } from "react";
import { ArrowClockwise, PlugsConnected, Table } from "@phosphor-icons/react";
import { api, assetNames, type DataSource, type Projection, type ProjectionRun } from "../api";
import { useAction, usePlatform } from "../store";
import {
  Btn, Card, Collapsible, Drawer, Empty, Field, JsonField, KIND_LABEL,
  labelOf, Loading, PageHead, Select, TableWrap, Tag, TextInput, useLoad,
} from "../ui";
import { StatusBadge } from "../components/StatusBadge";
import { ValueView } from "../components/ValueView";
import { formatLocal } from "../lib/time";

export function DataView() {
  const { wid, toast, confirm, refreshRuntime, go } = usePlatform();
  const [preview, setPreview] = useState<DataSource | null>(null);
  const [connectOpen, setConnectOpen] = useState(false);
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

      <Card title="数据源" sub="连接参数与可用数据表都由创建时声明；预览只展示已声明的静态样例。"
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
                    <td className="nowrap"><Btn size="sm" variant="subtle" onClick={() => setPreview(d)}>预览</Btn></td>
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
                      </td>
                      <td className="nowrap"><Tag kind={p.mode === "authoritative" ? "warn" : "plain"}>{p.mode || "未返回"}</Tag>
                        {!!p.managed_types?.length && <span className="muted small"> · {p.managed_types.length} 个托管类型</span>}
                      </td>
                      <td className="mono nowrap">{p.last_check_at ? formatLocal(p.last_check_at) : "未返回"}</td>
                      <td className="mono nowrap">{p.last_change_at ? formatLocal(p.last_change_at) : "未返回"}</td>
                      <td className="nowrap">
                        <Btn size="sm" busy={syncing}
                             onClick={() => confirm({
                               title: `立即同步「${p.name}」？`,
                               confirmText: "开始同步",
                               body: <p className="muted">会读取数据源并按映射写入业务世界；结果会返回运行编号与计数，失败时以错误为准，不会显示成完成。</p>,
                               run: async () => { await sync(p.id, p.name); },
                             })}>立即同步</Btn>
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
            <h3 className="h4">{n}{rows.length ? ` · ${rows.length} 行` : ""}</h3>
            {rows.length ? (
              <TableWrap auto>
                <table>
                  <thead><tr>{Object.keys(rows[0]).map(k => <th key={k}>{k}</th>)}</tr></thead>
                  <tbody>
                    {rows.slice(0, 5).map((r, i) => (
                      <tr key={i}>{Object.keys(rows[0]).map(k => <td key={k}><ValueView value={r[k]} compact field={k} /></td>)}</tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            ) : <p className="muted">静态样例之外的数据源在同步时才读取，这里只展示已声明的样例。</p>}
          </div>
        );
      })}
      <Collapsible label="这个数据源的连接参数">
        <pre className="obs">{JSON.stringify({ kind: source.kind, config: source.config }, null, 2)}</pre>
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
