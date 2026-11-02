import type { ReactNode } from "react";
import type { SmartUiMeta } from "./api";
import { registerUISlot } from "./ui-slots";
import { SmartUIGenerationHistory, SmartUIStudio } from "./views/SmartUIStudio";

export const AI_TAB_SLOT = "aiview.tabs";

export interface AITabContext {
  wid: string;
  meta: SmartUiMeta;
  generationId: string | null;
  onSelect: (generationId: string) => void;
  onOpenHistory: () => void;
}

export interface AITabDefinition {
  label: string;
  hint: string;
  render: (context: AITabContext) => ReactNode;
}

/**
 * Built-in AI视图 tabs are registered exactly like a bundled plugin would be.
 * The shell (AIView) only knows the slot, not these two entries.
 */
registerUISlot<AITabDefinition>({
  id: "studio",
  slot: AI_TAB_SLOT,
  order: 10,
  builtin: true,
  data: {
    label: "AI 生成台",
    hint: "模型提议 · 平台编译",
    render: (context) => (
      <SmartUIStudio
        wid={context.wid}
        meta={context.meta}
        generationId={context.generationId}
        onSelect={context.onSelect}
        onOpenHistory={context.onOpenHistory}
      />
    ),
  },
});

registerUISlot<AITabDefinition>({
  id: "history",
  slot: AI_TAB_SLOT,
  order: 20,
  builtin: true,
  data: {
    label: "生成记录",
    hint: "版本、裁决与指纹",
    render: (context) => (
      <SmartUIGenerationHistory wid={context.wid} onOpen={context.onSelect} />
    ),
  },
});
