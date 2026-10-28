import type { ReactNode } from "react";
import {
  Database, GearSix, Graph, Layout, Lightning, Pulse, Sparkle,
  SquaresFour, Target,
} from "@phosphor-icons/react";

export type NavItem = { id: string; label: string; icon: ReactNode };

export const NAV: { group: string; items: NavItem[] }[] = [
  {
    group: "",
    items: [{ id: "workbench", label: "工作台", icon: <SquaresFour size={16} /> }],
  },
  {
    group: "业务世界",
    items: [
      { id: "ontology", label: "本体", icon: <Graph size={16} /> },
      { id: "data", label: "数据", icon: <Database size={16} /> },
      { id: "capability", label: "能力", icon: <Lightning size={16} /> },
      { id: "application", label: "应用", icon: <Layout size={16} /> },
      { id: "state", label: "对象实例", icon: <Target size={16} /> },
    ],
  },
  {
    group: "运行",
    items: [{ id: "operations", label: "运行", icon: <Pulse size={16} /> }],
  },
  {
    group: "平台",
    items: [
      { id: "assistant", label: "AI 助手", icon: <Sparkle size={16} /> },
      { id: "settings", label: "设置", icon: <GearSix size={16} /> },
    ],
  },
];

export const PAGE_LABEL: Record<string, string> = {
  workbench: "工作台", ontology: "本体", data: "数据", capability: "能力",
  application: "应用", state: "对象实例", operations: "运行",
  assistant: "AI 助手", settings: "设置",
};

export function navItems(): NavItem[] {
  return NAV.flatMap(g => g.items);
}
