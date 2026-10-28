import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  ArrowClockwise, ArrowSquareOut, GearSix, MagnifyingGlass, Plus,
  Sparkle, SquaresFour, Check,
} from "@phosphor-icons/react";
import { usePlatform } from "./store";
import { navItems } from "./nav";

interface Item {
  id: string;
  label: string;
  hint?: string;
  group: string;
  icon: ReactNode;
  run: () => void;
}

/** 命令面板：导航、切工作空间、常用动作都走键盘，不靠鼠标找菜单。 */
export function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { go, workspaces, wid, setWorkspace, setAiOpen, refreshRuntime, toast } = usePlatform();
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    setQ(""); setActive(0);
    const t = window.setTimeout(() => input.current?.focus(), 30);
    return () => window.clearTimeout(t);
  }, [open]);

  const items = useMemo<Item[]>(() => {
    const nav: Item[] = navItems().map(it => ({
      id: "nav:" + it.id,
      label: it.label,
      hint: "页面",
      group: "前往",
      icon: it.icon,
      run: () => go(it.id),
    }));
    const spaces: Item[] = workspaces.map(w => ({
      id: "ws:" + w.id,
      label: `切换到 ${w.name}`,
      hint: w.id,
      group: "工作空间",
      icon: w.id === wid ? <Check size={15} weight="bold" /> : <SquaresFour size={15} />,
      run: () => { if (w.id !== wid) void setWorkspace(w.id); },
    }));
    const acts: Item[] = [
      {
        id: "act:ask",
        label: "问一问（带本体依据）",
        hint: "AI",
        group: "操作",
        icon: <Sparkle size={15} />,
        run: () => setAiOpen(true),
      },
      {
        id: "act:new",
        label: "新建工作空间",
        hint: "设置",
        group: "操作",
        icon: <Plus size={15} />,
        run: () => go("settings", { tab: "workspace" }),
      },
      {
        id: "act:health",
        label: "刷新运行时索引",
        hint: "运行时",
        group: "操作",
        icon: <ArrowClockwise size={15} />,
        run: () => { void refreshRuntime().then(() => toast("运行时索引已刷新", "ok")); },
      },
      {
        id: "act:settings",
        label: "模型与工作空间设置",
        hint: "设置",
        group: "操作",
        icon: <GearSix size={15} />,
        run: () => go("settings", { tab: "llm" }),
      },
      {
        id: "act:copy",
        label: "复制当前页面链接",
        hint: "分享",
        group: "操作",
        icon: <ArrowSquareOut size={15} />,
        run: () => navigator.clipboard?.writeText(location.href)
          .then(() => toast("链接已复制", "ok"))
          .catch(() => toast("浏览器不允许复制", "err")),
      },
    ];
    return [...nav, ...acts, ...spaces];
  }, [go, workspaces, wid, setWorkspace, setAiOpen, refreshRuntime, toast]);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return items;
    return items.filter(it =>
      (it.label + " " + (it.hint || "") + " " + it.group).toLowerCase().includes(needle));
  }, [items, q]);

  useEffect(() => {
    if (active >= filtered.length) setActive(0);
  }, [filtered.length, active]);

  if (!open) return null;

  const choose = (it: Item | undefined) => {
    if (!it) return;
    onClose();
    // 让关闭动画先提交，再切页，避免焦点抢在卸载前
    window.setTimeout(it.run, 0);
  };

  const groups = Array.from(new Set(filtered.map(i => i.group)));

  return (
    <div className="cmdk-scrim" role="dialog" aria-modal="true" aria-label="命令面板"
         onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="cmdk">
        <div className="cmdk-input">
          <MagnifyingGlass size={17} aria-hidden="true" />
          <input ref={input} value={q} aria-label="搜索页面、工作空间与命令"
                 placeholder="搜索页面、工作空间或命令"
                 onChange={e => { setQ(e.target.value); setActive(0); }}
                 onKeyDown={e => {
                   if (e.key === "ArrowDown") { e.preventDefault(); setActive(a => Math.min(a + 1, filtered.length - 1)); }
                   else if (e.key === "ArrowUp") { e.preventDefault(); setActive(a => Math.max(a - 1, 0)); }
                   else if (e.key === "Enter") { e.preventDefault(); choose(filtered[active]); }
                   else if (e.key === "Escape") { e.preventDefault(); onClose(); }
                 }} />
          <kbd>esc</kbd>
        </div>
        <div className="cmdk-list" role="listbox" aria-label="命令结果">
          {filtered.length === 0 && <div className="cmdk-empty">没有匹配的页面或命令</div>}
          {groups.map(g => (
            <div key={g}>
              <div className="cmdk-group">{g}</div>
              {filtered.filter(i => i.group === g).map(it => {
                const index = filtered.indexOf(it);
                return (
                  <button key={it.id} type="button" className="cmdk-item" role="option"
                          aria-selected={index === active} data-active={index === active}
                          onMouseMove={() => setActive(index)}
                          onClick={() => choose(it)}>
                    <span className="nav-ico" aria-hidden="true">{it.icon}</span>
                    <span>{it.label}</span>
                    {it.hint && <span className="hint">{it.hint}</span>}
                  </button>
                );
              })}
            </div>
          ))}
        </div>
        <div className="cmdk-foot">
          <span>↑↓ 选择</span><span>↵ 打开</span><span>esc 关闭</span>
          <span className="end">OntoPlatform</span>
        </div>
      </div>
    </div>
  );
}
