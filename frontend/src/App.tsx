import { Component, useEffect, useState, type ReactNode } from "react";
import {
  CaretRight, Command as CommandIcon, Graph, MagnifyingGlass,
  SidebarSimple, Sparkle, SquaresFour,
} from "@phosphor-icons/react";
import { api } from "./api";
import { LS, PlatformProvider, useAction, usePlatform } from "./store";
import { Btn, Select, Tag } from "./ui";
import { AiDrawer } from "./views/AiDrawer";
import { CommandPalette } from "./CommandPalette";
import { NAV, PAGE_LABEL } from "./nav";
import { Workbench } from "./views/Workbench";
import { OntologyView } from "./views/Ontology";
import { DataView } from "./views/Data";
import { CapabilityView } from "./views/Capability";
import { ApplicationView } from "./views/Application";
import { StateView } from "./views/StateBrowser";
import { OperationsView } from "./views/Operations";
import { AssistantView } from "./views/Assistant";
import { SettingsView } from "./views/Settings";

const VIEWS: Record<string, () => JSX.Element> = {
  workbench: Workbench,
  ontology: OntologyView,
  data: DataView,
  capability: CapabilityView,
  application: ApplicationView,
  state: StateView,
  operations: OperationsView,
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
  componentDidCatch(error: Error, info: { componentStack?: string | null }) {
    console.error("界面渲染异常", error, info.componentStack || "");
  }
  render() {
    if (!this.state.err) return this.props.children;
    return (
      <div className="fatal">
        <div className="page-kicker">Console error</div>
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
  const { nav, wid, go, setAiOpen, aiOpen } = usePlatform();  const [collapsed, setCollapsed] = useState(() => localStorage.getItem(LS.collapsed) === "1");
  const [palette, setPalette] = useState(false);
  const [navOpen, setNavOpen] = useState(false);
  const View = VIEWS[nav] || Workbench;
  const needsWorkspace = nav !== "workbench" && nav !== "settings";

  useEffect(() => { localStorage.setItem(LS.collapsed, collapsed ? "1" : "0"); }, [collapsed]);
  useEffect(() => { setNavOpen(false); }, [nav]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey;
      if (mod && e.key.toLowerCase() === "k") { e.preventDefault(); setPalette(p => !p); }
      if (e.key === "Escape") { setPalette(false); setAiOpen(false); setNavOpen(false); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setAiOpen]);

  return (
    <div className={"app" + (collapsed ? " nav-collapsed" : "") + (navOpen ? " nav-open" : "")}>
      <a className="skip-link" href="#main-content">跳到主内容</a>
      {navOpen && <button type="button" className="nav-scrim" aria-label="关闭导航" onClick={() => setNavOpen(false)} />}
      <SideNav collapsed={collapsed} toggle={() => setCollapsed(c => !c)} />
      <div className="main">
        <TopBar onMenu={() => setNavOpen(o => !o)} onSearch={() => setPalette(true)} />
        <main className="content" id="main-content">
          {needsWorkspace && !wid ? (
            <div className="card narrow-card">
              <div className="page-kicker">Workspace required</div>
              <h2 className="card-lead">还没有选择工作空间</h2>
              <p className="card-sub card-lead-sub">
                工作空间是一套独立的业务世界，包含本体、数据、能力与应用。先在工作台创建或切换一个。
              </p>
              <div className="btn-row">
                <Btn onClick={() => go("workbench")}>去工作台</Btn>
                <Btn variant="subtle" onClick={() => go("settings", { tab: "workspace" })}>管理工作空间</Btn>
              </div>
            </div>
          ) : (
            <View />
          )}
        </main>
      </div>

      <button type="button" className="ai-fab" onClick={() => setAiOpen(true)} title="问一问（在命令面板里也可以）">
        <Sparkle size={15} weight="fill" aria-hidden="true" /> 问一问
      </button>
      {aiOpen && <AiDrawer />}
      <CommandPalette open={palette} onClose={() => setPalette(false)} />
    </div>
  );
}

function SideNav({ collapsed, toggle }: { collapsed: boolean; toggle: () => void }) {
  const { nav, go, workspaceName, wid, objectTypes, completeness } = usePlatform();
  return (
    <aside className="side">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true">
          <Graph size={17} weight="bold" />
        </span>
        {!collapsed && (
          <span className="brand-text">
            <b>OntoPlatform</b>
            <em>业务世界控制台</em>
          </span>
        )}
      </div>

      <nav aria-label="主导航">
        {NAV.map((g, i) => (
          <div key={i}>
            {!!g.group && !collapsed && <div className="nav-group">{g.group}</div>}
            {g.items.map(it => {
              const missingOntology = it.id === "ontology" && completeness?.object_types === 0;
              return (
                <button key={it.id} type="button" className="nav-item" data-active={nav === it.id}
                        onClick={() => go(it.id)} title={collapsed ? it.label : undefined}
                        aria-current={nav === it.id ? "page" : undefined}>
                  <span className="nav-ico" aria-hidden="true">{it.icon}</span>
                  {!collapsed && <span className="nav-label">{it.label}</span>}
                  {!collapsed && it.id === "state" && objectTypes.length > 0 && (
                    <span className="nav-count">{objectTypes.length}</span>
                  )}
                  {!collapsed && missingOntology && <span className="nav-dot" title="还没有定义对象类型" />}
                </button>
              );
            })}
          </div>
        ))}
      </nav>

      <div className="side-foot">
        {!collapsed && (
          <button type="button" className="ws-chip" onClick={() => go("settings", { tab: "workspace" })}
                  title="切换或管理工作空间">
            <span>当前工作空间</span>
            <b>{workspaceName || "未选择"}</b>
            <span>{wid ? wid : "先在工作台创建"}</span>
          </button>
        )}
        {!collapsed && completeness && (
          <div className="side-completeness">
            对象类型 {completeness.object_types} · 关系 {completeness.relation_types} · 能力 {completeness.capabilities}
          </div>
        )}
        <button type="button" className="nav-collapse" onClick={toggle}
                aria-label={collapsed ? "展开导航" : "收起导航"}>
          <SidebarSimple size={14} aria-hidden="true" />
        </button>
      </div>
    </aside>
  );
}

function TopBar({ onMenu, onSearch }: { onMenu: () => void; onSearch: () => void }) {
  const { nav, workspaces, wid, setWorkspace, online, version, llm, llmReady, toast } = usePlatform();
  const { run, running } = useAction(async () => {
    const h = await api.health();
    toast(
      h.llm_provider && h.llm_provider !== "none" ? `服务正常，模型走 ${h.llm_provider}` : "服务正常，未配置模型服务",
      h.llm_provider && h.llm_provider !== "none" ? "ok" : "warn",
    );
  }, []);

  const llmText = llm.mode === "demo" ? "演示模式"
    : llm.mode === "openai" ? (llm.model || "工作空间模型")
    : llmReady ? "平台默认模型" : "未配模型";
  const llmOk = llm.mode !== "platform" || llmReady;

  return (
    <header className="top">
      <button type="button" className="nav-burger" onClick={onMenu} aria-label="打开导航">
        <SquaresFour size={17} />
      </button>
      <label className="top-ws" htmlFor="ws-select">工作空间</label>
      <Select id="ws-select" className="w-lg" value={wid || ""} disabled={!workspaces.length}
              onChange={e => void setWorkspace(e.target.value)} aria-label="当前工作空间">
        {workspaces.length === 0 && <option value="">（还没有工作空间）</option>}
        {workspaces.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}
      </Select>
      <span className="top-page">
        {PAGE_LABEL[nav] || ""}
        <CaretRight size={10} className="crumb-caret" aria-hidden="true" />
      </span>
      <span className="grow" />
      <button type="button" className="top-search" onClick={onSearch} title="打开命令面板">
        <MagnifyingGlass size={14} aria-hidden="true" />
        <span>搜索与命令</span>
        <kbd aria-hidden="true"><CommandIcon size={9} className="kbd-ico" /> K</kbd>
      </button>
      <button type="button" className="brand-mini" onClick={() => void run()} disabled={running}
              title="点击重新检查服务连接">
        <span className={"status-dot" + (online ? "" : " off")} />
        {online ? `v${version}` : "服务不可达"}
        <Tag kind={llmOk ? "ok" : "warn"}>{llmText}</Tag>
      </button>
    </header>
  );
}

/** 供命令面板复用的导航元数据。 */
export function navItems(): { id: string; label: string; icon: ReactNode }[] {
  return NAV.flatMap(g => g.items);
}
