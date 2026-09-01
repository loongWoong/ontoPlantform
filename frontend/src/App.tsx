import { useEffect, useState } from "react";
import { api } from "./api";
import { LS, PlatformProvider, useAction, usePlatform } from "./store";
import { Component, type ReactNode } from "react";
import { Btn, Select, Tag } from "./ui";
import { AiDrawer } from "./views/AiDrawer";
import { Workbench } from "./views/Workbench";
import { OntologyView } from "./views/Ontology";
import { DataView } from "./views/Data";
import { CapabilityView } from "./views/Capability";
import { ApplicationView } from "./views/Application";
import { StateView } from "./views/StateBrowser";
import { AssistantView } from "./views/Assistant";
import { SettingsView } from "./views/Settings";

const NAV = [
  { group: "", items: [{ id: "workbench", label: "工作台", icon: "◎" }] },
  {
    group: "业务世界",
    items: [
      { id: "ontology", label: "本体", icon: "◈" },
      { id: "data", label: "数据", icon: "▤" },
      { id: "capability", label: "能力", icon: "↯" },
      { id: "application", label: "应用", icon: "▣" },
      { id: "state", label: "对象实例", icon: "⊙" },
    ],
  },
  {
    group: "平台",
    items: [
      { id: "assistant", label: "AI 助手", icon: "✦" },
      { id: "settings", label: "设置", icon: "☰" },
    ],
  },
];

const VIEWS: Record<string, () => JSX.Element> = {
  workbench: Workbench,
  ontology: OntologyView,
  data: DataView,
  capability: CapabilityView,
  application: ApplicationView,
  state: StateView,
  assistant: AssistantView,
  settings: SettingsView,
};

export default function App() {
  return (
    <ErrorBoundary>
      <PlatformProvider>
        <Shell />
      </PlatformProvider>
    </ErrorBoundary>
  );
}

/** 渲染期异常不该整站白屏：兜底成可读的错误 + 重载入口。 */
class ErrorBoundary extends Component<{ children: ReactNode }, { err: Error | null }> {
  state = { err: null as Error | null };
  static getDerivedStateFromError(err: Error) { return { err }; }
  render() {
    if (!this.state.err) return this.props.children;
    return (
      <div className="fatal">
        <h1>界面出错了</h1>
        <p className="muted">{String(this.state.err.message || this.state.err)}</p>
        <div className="btn-row">
          <Btn onClick={() => location.reload()}>重新加载</Btn>
          <Btn variant="subtle" onClick={() => this.setState({ err: null })}>就地重试</Btn>
        </div>
      </div>
    );
  }
}

function Shell() {
  const { nav, wid, go, setAiOpen } = usePlatform();
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem(LS.collapsed) === "1");
  const View = VIEWS[nav] || Workbench;
  const needsWorkspace = nav !== "workbench" && nav !== "settings";

  useEffect(() => { localStorage.setItem(LS.collapsed, collapsed ? "1" : "0"); }, [collapsed]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setAiOpen(true); }
      if (e.key === "Escape") setAiOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setAiOpen]);

  return (
    <div className={"app" + (collapsed ? " nav-collapsed" : "")}>
      <SideNav collapsed={collapsed} toggle={() => setCollapsed(c => !c)} />
      <div className="main">
        <TopBar />
        <main className="content" id="main-content">
          {needsWorkspace && !wid ? (
            <div className="card">
              <div className="card-title">还没有工作空间</div>
              <p className="muted">工作空间是一套独立的业务世界（本体 + 数据 + 能力 + 应用）。先在「工作台」建第一个。</p>
              <div className="btn-row"><Btn variant="tint" onClick={() => go("workbench")}>去创建工作空间</Btn></div>
            </div>
          ) : (
            <View />
          )}
        </main>
      </div>

      <button type="button" className="ai-fab" onClick={() => setAiOpen(true)} title="问一问（Ctrl/⌘ K）">
        <span aria-hidden="true">✦</span> 问一问
      </button>
      <AiDrawer />
    </div>
  );
}

const PAGE_LABEL: Record<string, string> = {
  workbench: "工作台", ontology: "本体", data: "数据", capability: "能力",
  application: "应用", state: "对象实例", assistant: "AI 助手", settings: "设置",
};

function SideNav({ collapsed, toggle }: { collapsed: boolean; toggle: () => void }) {
  const { nav, go, workspaceName, completeness } = usePlatform();
  return (
    <aside className="side">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true">◉</span>
        {!collapsed && (
          <span className="brand-text">
            <b>OntoPlatform</b>
            <em>业务世界平台</em>
          </span>
        )}
      </div>

      <nav aria-label="主导航">
        {NAV.map((g, i) => (
          <div key={i}>
            {!!g.group && !collapsed && <div className="nav-group">{g.group}</div>}
            {g.items.map(it => (
              <button key={it.id} type="button" className="nav-item" data-active={nav === it.id}
                      onClick={() => go(it.id)} title={it.label}>
                <span className="nav-ico" aria-hidden="true">{it.icon}</span>
                {!collapsed && <span>{it.label}</span>}
                {!collapsed && it.id === "ontology" && completeness && completeness.object_types === 0 && (
                  <span className="nav-dot" title="还没有定义对象类型" />
                )}
              </button>
            ))}
          </div>
        ))}
      </nav>

      <div className="side-foot">
        {!collapsed && (
          <>
            <button type="button" className="link" onClick={() => go("settings", { tab: "workspace" })}>
              {workspaceName || "未选择工作空间"}
            </button>
            {completeness && (
              <div className="muted">
                对象 {completeness.object_types} · 关系 {completeness.relation_types} · 能力 {completeness.capabilities}
              </div>
            )}
          </>
        )}
        <button type="button" className="nav-collapse" onClick={toggle}
                aria-label={collapsed ? "展开导航" : "收起导航"}>{collapsed ? "»" : "«"}</button>
      </div>
    </aside>
  );
}

function TopBar() {
  const { nav, workspaces, wid, setWorkspace, online, version, llm, llmReady, toast } = usePlatform();
  const { run, running } = useAction(async () => {
    const h = await api.health();
    toast(
      h.llm_provider && h.llm_provider !== "none" ? `服务正常，模型走 ${h.llm_provider}` : "服务正常，未配置模型服务",
      h.llm_provider && h.llm_provider !== "none" ? "ok" : "warn",
    );
  }, []);

  const llmText = llm.mode === "demo" ? "演示模式"
    : llm.mode === "openai" ? (llm.model || "外部模型")
    : llmReady ? "平台默认" : "未配模型";
  const llmOk = llm.mode !== "platform" || llmReady;   // 服务端事实优先，不看本地选择

  return (
    <header className="top">
      <label className="top-ws" htmlFor="ws-select">工作空间</label>
      <Select id="ws-select" className="w-lg" value={wid || ""} disabled={!workspaces.length}
              onChange={e => void setWorkspace(e.target.value)} aria-label="当前工作空间">
        {workspaces.length === 0 && <option value="">（还没有工作空间）</option>}
        {workspaces.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}
      </Select>
      <span className="grow" />
      <span className="top-page">{PAGE_LABEL[nav] || ""}</span>
      <button type="button" className="brand-mini" onClick={() => void run()} disabled={running}
              title="点击重新检查服务连接">
        <span className={"status-dot" + (online ? "" : " off")} />
        {online ? `v${version}` : "服务不可达"}
        <Tag kind={llmOk ? "ok" : "warn"}>{llmText}</Tag>
      </button>
    </header>
  );
}
