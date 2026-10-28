import { useState } from "react";
import { Plus } from "@phosphor-icons/react";
import { api, type AuditEvent, type Member } from "../api";
import { useAction, usePlatform } from "../store";
import { LlmPicker, LlmTest, LLM_MODE_LABEL } from "../LlmPicker";
import { Btn, Card, Empty, Loading, PageHead, Select, TableWrap, Tag,
  TextInput, useLoad } from "../ui";
import { fmtTime } from "../metrics";
import { ValueView } from "../components/ValueView";

const TABS = [
  { k: "llm", l: "模型服务" },
  { k: "workspace", l: "工作空间与成员" },
  { k: "health", l: "系统自检" },
  { k: "audit", l: "操作记录" },
  { k: "about", l: "关于" },
];

const ROLE_LABEL: Record<string, string> = { owner: "所有者", editor: "编辑者", viewer: "只读者" };

export function SettingsView() {
  const { params, llm, go } = usePlatform();
  const tab = params.tab || "llm";
  return (
    <>
      <PageHead
        kicker="Settings"
        title="设置"
        desc="模型来源、工作空间、成员与运行环境。改这里不会动业务数据；模型不可用时可以用演示模式继续体验。"
        meta={<>
          <span>模型来源 <b>{LLM_MODE_LABEL[llm.mode]}</b></span>
          {llm.mode === "openai" && <span>模型 <b>{llm.model || "未填"}</b></span>}
          <span>分区 <b>{TABS.find(t => t.k === tab)?.l || "模型服务"}</b></span>
        </>}
      />
      <Card tight>
        <div className="subtabs" role="tablist" tabIndex={0} aria-label="设置分区">
          {TABS.map(t => (
            <button key={t.k} type="button" role="tab" aria-selected={tab === t.k}
                    onClick={() => go("settings", { tab: t.k })}>{t.l}</button>
          ))}
        </div>
        <div className="card-pad">
          {tab === "llm" && (
            <>
              <LlmPicker />
              <LlmTest />
              <div className="banner info">
                当前来源：{LLM_MODE_LABEL[llm.mode]}
                {llm.mode === "openai" && ` · ${llm.model || "（未填模型名）"}`}。
                模型配置按工作空间保存；密钥只写不读，保存后只显示掩码。服务端也可以用环境变量固定：ONTO_LLM_BASE_URL / ONTO_LLM_MODEL / ONTO_LLM_API_KEY。
              </div>
              <p className="muted small">浏览器只记住模型名与模式，不保存 API Key；密钥不会写入 localStorage。</p>
            </>
          )}
          {tab === "workspace" && <><Identity /><Workspaces /><Members /></>}
          {tab === "health" && <Health />}
          {tab === "audit" && <Audit />}
          {tab === "about" && <About />}
        </div>
      </Card>
    </>
  );
}

function Identity() {
  const { authMode, token, setToken, toast } = usePlatform();
  const [draft, setDraft] = useState("");
  return (
    <Card title="身份与访问" sub="本地回环请求缺省使用 admin actor；启用服务端令牌后，调用者必须提交令牌。"
          aside={<Tag kind={authMode === "token" ? "ok" : "plain"}>{authMode === "token" ? "已提交访问令牌" : "本地操作者"}</Tag>}>
      <dl className="kv">
        <div className="kv-pair"><dt>当前模式</dt><dd>{authMode === "token" ? "Bearer 令牌（仅本次标签页）" : "本地操作者（回环地址，缺省 admin）"}</dd></div>
        <div className="kv-pair"><dt>令牌状态</dt><dd className="mono">{token ? `已设置（${token.length} 字符，不回显）` : "未设置"}</dd></div>
      </dl>
      <div className="inline-create wrap">
        <TextInput type="password" value={draft} placeholder="粘贴访问令牌（可留空清除）"
                   aria-label="访问令牌" autoComplete="off"
                   onChange={e => setDraft(e.target.value)} />
        <Btn size="sm" onClick={() => { setToken(draft); setDraft(""); toast(draft.trim() ? "访问令牌已写入当前标签页会话" : "访问令牌已清除", "ok"); }}>保存令牌</Btn>
        <Btn size="sm" variant="subtle" disabled={!token} onClick={() => { setToken(""); setDraft(""); toast("访问令牌已清除", "ok"); }}>清除</Btn>
      </div>
      <p className="muted small">令牌只存在 sessionStorage，关闭标签页即失效；不会写入 localStorage，也不会写进模型配置。</p>
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
    <Card title="工作空间" sub="每个工作空间是一套独立的本体与运行时状态；切换不会影响其他空间。">
      <TableWrap auto>
        <table>
          <thead><tr><th>名称</th><th>标识</th><th className="wrap">说明</th><th>操作</th></tr></thead>
          <tbody>
            {(load.data || []).map(w => (
              <tr key={w.id}>
                <td className="nowrap"><b>{w.name}</b></td>
                <td className="nowrap" title={w.id}>{w.id}</td>
                <td className="wrap">{w.description || "–"}</td>
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
    </Card>
  );
}

function Members() {
  const { wid, toast } = usePlatform();
  const load = useLoad(() => (wid ? api.members(wid) : Promise.resolve([] as Member[])), [wid]);
  const [user, setUser] = useState("");
  const [role, setRole] = useState("editor");

  const { run, running } = useAction(async () => {
    if (!wid) return;
    if (!user.trim()) { toast("请填写成员标识，例如邮箱或工号", "err"); return; }
    await api.addMember(wid, user.trim(), role);
    toast(`已把「${user.trim()}」加入工作空间（${ROLE_LABEL[role]}）`, "ok");
    setUser("");
    load.reload();
  }, [wid, user, role]);

  return (
    <Card title="成员与角色" sub="当前版本记录成员与角色，作为后续权限校验的边界；不会改变已有数据的可见性。"
          aside={<Tag kind="plain">{load.data?.length ?? 0} 人</Tag>}>
      {load.loading && <Loading />}
      {!load.loading && !load.data?.length && (
        <Empty title="还没有登记成员" hint="先登记一个负责人，再按需加编辑者与只读者。" />
      )}
      {!!load.data?.length && (
        <TableWrap auto>
          <table>
            <thead><tr><th>成员</th><th>角色</th><th>操作</th></tr></thead>
            <tbody>
              {load.data.map(m => (
                <tr key={m.user}>
                  <td className="nowrap mono">{m.user}</td>
                  <td className="nowrap"><Tag kind={m.role === "owner" ? "ok" : "plain"}>{ROLE_LABEL[m.role] || m.role}</Tag></td>
                  <td className="nowrap muted small">
                    {m.role === "owner" ? "记录为所有者" : "权限由服务端策略决定"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      )}
      <form className="inline-create wrap" onSubmit={e => { e.preventDefault(); void run(); }}>
        <TextInput value={user} placeholder="成员标识，如 alice@example.com" aria-label="成员标识"
                   onChange={e => setUser(e.target.value)} />
        <Select value={role} onChange={e => setRole(e.target.value)} aria-label="角色">
          <option value="owner">所有者</option>
          <option value="editor">编辑者</option>
          <option value="viewer">只读者</option>
        </Select>
        <Btn type="submit" size="sm" busy={running}><Plus size={13} /> 添加成员</Btn>
      </form>
      <p className="muted small">
        角色决定可执行动作，不决定读范围：工作空间内成员可见全部对象；v0.3 不做对象级 ACL。所有者管理成员与工作空间，编辑者可以改本体、能力与应用，只读者只能查看与提问。
        当前本地回环模式缺省 actor 是 admin，界面上的成员角色只在显式传入 user 或令牌模式下生效；服务端始终按工作空间角色校验写操作。对象级 ACL 不在 v0.3 范围。
      </p>
    </Card>
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
          <thead><tr><th>检查项</th><th>结果</th><th>操作</th></tr></thead>
          <tbody>
            <tr><td className="nowrap">版本</td><td className="wrap">{h.version}</td><td>{yes(true)}</td></tr>
            <tr><td className="nowrap">接口版本</td><td className="wrap">{h.api_version ? `v${h.api_version}` : "未知（请重启 onto-api）"}</td><td>{yes((h.api_version || 0) >= 2)}</td></tr>
            <tr><td className="nowrap">接口能力</td><td className="wrap">{h.features?.length ?? 0} 项</td><td>{yes(!!h.features?.length)}</td></tr>
            <tr><td className="nowrap">定义存储</td><td className="wrap">{h.backend || "memory"}</td><td>{yes(true)}</td></tr>
            <tr><td className="nowrap">运行时状态存储</td><td className="wrap">{h.runtime_store || "未返回"}</td><td>{yes(!!h.runtime_store && h.runtime_store !== "memory")}</td></tr>
            <tr><td className="nowrap">模型服务</td><td className="wrap">{h.llm_provider || "none"}</td><td>{yes(llmOk)}</td></tr>
            <tr><td className="nowrap">前端控制台</td><td className="wrap">{h.console === "react" ? "React 构建版" : "旧版单文件"}</td><td>{yes(h.console === "react")}</td></tr>
          </tbody>
        </table>
      </TableWrap>
      <p className="muted small">运行时状态存储如果是 memory，进程重启后业务状态会清空；演练环境正常，生产要换持久化存储。</p>

      {completeness && wid && (
        <>
          <h3 className="h4">这个工作空间走到哪一步了</h3>
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
  const { wid, toast } = usePlatform();
  const [limit, setLimit] = useState("30");
  const [fetched, setFetched] = useState("30");
  const load = useLoad(
    () => (wid ? api.audit(wid, Number(fetched) || 30) : Promise.resolve([] as AuditEvent[])),
    [wid, fetched],
  );
  const events = load.data || [];
  const exportJson = () => {
    if (!events.length) { toast("还没有可导出的记录", "warn"); return; }
    const blob = new Blob([JSON.stringify(events, null, 2)], { type: "application/json;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `audit-${wid || "workspace"}-${new Date().toISOString().slice(0, 10)}.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast(`已导出 ${events.length} 条操作记录`, "ok");
  };
  return (
    <>
      <form className="inline-create" onSubmit={e => { e.preventDefault(); setFetched(limit); load.reload(); }}>
        <TextInput className="w-xs" type="number" min={10} max={500} value={limit} aria-label="读取条数"
                   onChange={e => setLimit(e.target.value)} />
        <Btn type="submit" size="sm" busy={load.loading}>读取最近操作</Btn>
        <Btn type="button" size="sm" variant="subtle" onClick={exportJson} disabled={!events.length}>导出 JSON</Btn>
      </form>
      {!events.length && <Empty title="暂无操作记录" hint="做过定义、同步或执行之后，这里会有痕迹。" />}
      {!!events.length && (
        <TableWrap>
          <table>
            <thead><tr><th>时间</th><th>操作者</th><th>动作</th><th className="wrap">对象</th><th className="wrap">细节</th></tr></thead>
            <tbody>
              {events.map((e, i) => (
                <tr key={i}>
                  <td className="nowrap mono">{fmtTime(e.ts)}</td>
                  <td className="nowrap">{e.actor}</td>
                  <td className="nowrap mono">{e.action}</td>
                  <td className="wrap">{e.target}</td>
                  <td className="wrap"><ValueView value={e.detail || {}} compact /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      )}
      <p className="muted small">操作记录只追加，不提供界面删除；需要留存证据时从这里导出。</p>
    </>
  );
}

function About() {
  const load = useLoad(() => api.health(), []);
  const h = load.data;
  return (
    <>
      <dl className="kv">
        <div className="kv-pair"><dt>平台</dt><dd>OntoPlatform · 业务世界平台 v{h?.version || "0.3.0"}</dd></div>
        <div className="kv-pair"><dt>前端</dt><dd>React + TypeScript + Vite，构建产物由 Python 后端直接托管</dd></div>
        <div className="kv-pair"><dt>启动</dt><dd className="mono">python -m onto_platform.interfaces.api（一条命令，运行时不需要 Node）</dd></div>
        <div className="kv-pair"><dt>快捷键</dt><dd>Ctrl/⌘ + K 打开命令面板与「问一问」 · Esc 关闭抽屉与弹窗</dd></div>
        <div className="kv-pair"><dt>设计依据</dt><dd>design.txt §41 前端信息架构 / §42 前端设计原则</dd></div>
      </dl>
      {!h && <Empty title="后端未响应" hint="确认服务正在运行，然后点上方状态灯重试。" />}
    </>
  );
}
