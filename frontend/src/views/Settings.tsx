import { useEffect, useState } from "react";
import { api, type AuditEvent } from "../api";
import { useAction, usePlatform } from "../store";
import { LlmPicker, LlmTest, LLM_MODE_LABEL } from "../LlmPicker";
import { Btn, Card, Empty, Loading, TableWrap, Tag, TextInput, useLoad } from "../ui";

const TABS = [
  { k: "llm", l: "模型服务" },
  { k: "workspace", l: "工作空间" },
  { k: "health", l: "系统自检" },
  { k: "audit", l: "操作记录" },
  { k: "about", l: "关于" },
];

export function SettingsView() {
  const { params, llm } = usePlatform();
  const [tab, setTab] = useState<string>(params.tab || "llm");
  // 深链（?tab=llm）在组件已挂载时也要生效：go("settings",{tab}) 不会重新挂载
  useEffect(() => { if (params.tab) setTab(params.tab); }, [params.tab]);
  return (
    <Card title="设置" sub="模型来源、工作空间与运行环境。改这里不会动业务数据。">
      <div className="subtabs" role="tablist" aria-label="设置分区">
        {TABS.map(t => (
          <button key={t.k} type="button" role="tab" aria-selected={tab === t.k} onClick={() => setTab(t.k)}>{t.l}</button>
        ))}
      </div>
      {tab === "llm" && (
        <>
          <LlmPicker />
          <LlmTest />
          <div className="banner info">
            当前来源：{LLM_MODE_LABEL[llm.mode]}
            {llm.mode === "openai" && ` · ${llm.model || "（未填模型名）"}`}。
            服务端也可以用环境变量固定：ONTO_LLM_BASE_URL / ONTO_LLM_MODEL / ONTO_LLM_API_KEY。
          </div>
        </>
      )}
      {tab === "workspace" && <Workspaces />}
      {tab === "health" && <Health />}
      {tab === "audit" && <Audit />}
      {tab === "about" && <About />}
    </Card>
  );
}

function Workspaces() {
  const { wid, setWorkspace, toast } = usePlatform();
  const load = useLoad(() => api.workspaces(), []);
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const { run, running } = useAction(async () => {
    if (!name.trim()) { toast("请填写工作空间名称", "err"); return; }
    const ws = await api.createWorkspace(name.trim(), desc.trim());
    toast(`已创建工作空间「${name.trim()}」`, "ok");
    setName(""); setDesc("");
    load.reload();
    setWorkspace(ws.id);
  }, [name, desc, setWorkspace, toast]);

  return (
    <>
      <TableWrap auto>
        <table>
          <thead><tr><th>名称</th><th>标识</th><th className="wrap">说明</th><th> </th></tr></thead>
          <tbody>
            {(load.data || []).map(w => (
              <tr key={w.id}>
                <td className="nowrap"><b>{w.name}</b></td>
                <td className="nowrap" title={w.id}>{w.id}</td>
                <td className="wrap">{w.description || "—"}</td>
                <td className="nowrap">
                  {w.id === wid ? <Tag kind="ok">当前</Tag>
                    : <Btn size="sm" variant="tint" onClick={() => setWorkspace(w.id)}>进入</Btn>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableWrap>
      <form className="inline-create wrap" onSubmit={e => { e.preventDefault(); void run(); }}>
        <TextInput value={name} placeholder="新工作空间名称，如 公交三公司" aria-label="名称" onChange={e => setName(e.target.value)} />
        <TextInput value={desc} placeholder="一句话说明（可选）" aria-label="说明" onChange={e => setDesc(e.target.value)} />
        <Btn type="submit" size="sm" busy={running}>创建并进入</Btn>
      </form>
      <p className="muted">每个工作空间是一套独立的本体与运行时状态；成员与权限在后续版本提供。</p>
    </>
  );
}

function Health() {
  const { wid, completeness, objectTypes, relationTypes } = usePlatform();
  const load = useLoad(() => api.health(), []);
  if (load.loading && !load.data) return <Loading />;
  const h = load.data;
  if (!h) return <Empty title="后端未响应" hint="确认 python -m onto_platform.interfaces.api 正在运行。" />;

  const yes = (v: boolean) => <Tag kind={v ? "ok" : "warn"}>{v ? "正常" : "需注意"}</Tag>;
  const llmOk = !!h.llm_configured && !!h.llm_provider && h.llm_provider !== "none";
  return (
    <>
      <TableWrap auto>
        <table>
          <thead><tr><th>检查项</th><th>结果</th><th> </th></tr></thead>
          <tbody>
            <tr><td className="nowrap">版本</td><td className="wrap">{h.version}</td><td>{yes(true)}</td></tr>
            <tr><td className="nowrap">定义存储</td><td className="wrap">{h.backend || "memory"}</td><td>{yes(true)}</td></tr>
            <tr><td className="nowrap">运行时状态存储</td><td className="wrap">{h.runtime_store || "memory"}</td><td>{yes(h.runtime_store !== "memory")}</td></tr>
            <tr><td className="nowrap">模型服务</td><td className="wrap">{h.llm_provider || "none"}</td><td>{yes(llmOk)}</td></tr>
            <tr><td className="nowrap">前端控制台</td><td className="wrap">{h.console === "react" ? "React 构建版" : "旧版单文件"}</td><td>{yes(h.console === "react")}</td></tr>
          </tbody>
        </table>
      </TableWrap>

      {completeness && wid && (
        <>
          <h4 className="h4">这个工作空间走到哪一步了</h4>
          <div className="chips">
            <span className="chip on">对象类型 {objectTypes.length}</span>
            <span className="chip on">关系类型 {relationTypes.length}</span>
            <span className="chip">能力 {completeness.capabilities}（已发布 {completeness.published_capabilities}）</span>
            <span className="chip">应用 {completeness.applications}（已发布 {completeness.published_applications}）</span>
            {completeness.suggestions.map(s => <span key={s} className="chip">{s}</span>)}
          </div>
        </>
      )}
    </>
  );
}

function Audit() {
  const { wid } = usePlatform();
  const [limit, setLimit] = useState("30");
  const [fetched, setFetched] = useState("30");
  const load = useLoad(
    () => (wid ? api.audit(wid, Number(fetched) || 30) : Promise.resolve([] as AuditEvent[])),
    [wid, fetched],
  );
  const events = load.data || [];
  return (
    <>
      <form className="inline-create" onSubmit={e => { e.preventDefault(); setFetched(limit); load.reload(); }}>
        <TextInput className="w-xs" type="number" min={10} max={500} value={limit} aria-label="读取条数"
                   onChange={e => setLimit(e.target.value)} />
        <Btn type="submit" size="sm" busy={load.loading}>读取最近操作</Btn>
      </form>
      {!events.length && <Empty title="暂无操作记录" hint="做过定义、同步或执行之后，这里会有痕迹。" />}
      {!!events.length && (
        <TableWrap>
          <table>
            <thead><tr><th>时间</th><th>操作者</th><th>动作</th><th className="wrap">对象</th><th className="wrap">细节</th></tr></thead>
            <tbody>
              {events.map((e, i) => (
                <tr key={i}>
                  <td className="nowrap">{(e.ts || "").replace("T", " ").slice(0, 19)}</td>
                  <td className="nowrap">{e.actor}</td>
                  <td className="nowrap">{e.action}</td>
                  <td className="wrap">{e.target}</td>
                  <td className="wrap" title={JSON.stringify(e.detail || {})}>{JSON.stringify(e.detail || {})}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      )}
    </>
  );
}

function About() {
  const load = useLoad(() => api.health(), []);
  const h = load.data;
  return (
    <>
      <dl className="kv">
        <div className="kv-pair"><dt>平台</dt><dd>OntoPlatform · 业务世界平台 v{h?.version || "0.1.0"}</dd></div>
        <div className="kv-pair"><dt>前端</dt><dd>React + TypeScript + Vite，构建产物由 Python 后端直接托管</dd></div>
        <div className="kv-pair"><dt>启动</dt><dd>python -m onto_platform.interfaces.api（一条命令，运行时不需要 Node）</dd></div>
        <div className="kv-pair"><dt>快捷键</dt><dd>Ctrl/⌘ + K 打开「问一问」 · Esc 关闭抽屉与弹窗</dd></div>
        <div className="kv-pair"><dt>设计依据</dt><dd>design.txt §41 前端信息架构 / §42 前端设计原则</dd></div>
      </dl>
      {!h && <Empty title="后端未响应" hint="确认服务正在运行，然后点上方状态灯重试。" />}
    </>
  );
}
