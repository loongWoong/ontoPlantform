import { api } from "./api";
import { useAction, usePlatform, type LlmSetting } from "./store";
import { Btn, Field, Select, Tag, TextInput } from "./ui";

export const LLM_MODE_LABEL: Record<string, string> = {
  demo: "演示模式（不需要模型）",
  platform: "平台默认（服务端配置）",
  openai: "OpenAI 兼容服务",
};

/** 模型来源选择器：设置页与 AI 抽屉共用同一份状态（存在浏览器里） */
export function LlmPicker({ compact = false }: { compact?: boolean }) {
  const { llm, setLlm } = usePlatform();
  const openai = llm.mode === "openai" ? llm : { base_url: "", model: "", api_key: "" };
  const pick = (mode: string) =>
    setLlm(mode === "openai"
      ? { mode: "openai", base_url: openai.base_url, model: openai.model, api_key: openai.api_key }
      : mode === "demo" ? { mode: "demo" } : { mode: "platform" });
  const patch = (p: Partial<typeof openai>) => setLlm({ mode: "openai", ...openai, ...p });

  return (
    <div className={compact ? "field-inline" : ""}>
      <Field label="模型来源"
             hint={compact ? undefined : "演示模式用内置脚本跑通「观察 → 行动 → 结论」，不需要密钥；接真实模型时选 OpenAI 兼容服务。"}>
        <Select value={llm.mode} onChange={e => pick(e.target.value)} aria-label="模型来源">
          <option value="platform">{LLM_MODE_LABEL.platform}</option>
          <option value="openai">{LLM_MODE_LABEL.openai}</option>
          <option value="demo">{LLM_MODE_LABEL.demo}</option>
        </Select>
      </Field>
      {llm.mode === "openai" && (
        <div className={compact ? "field-inline" : ""}>
          <Field label="服务地址" hint="留空用环境变量。">
            <TextInput value={openai.base_url} placeholder="https://api.openai.com/v1"
                       onChange={e => patch({ base_url: e.target.value })} />
          </Field>
          <Field label="模型名"><TextInput value={openai.model} placeholder="gpt-4o-mini" onChange={e => patch({ model: e.target.value })} /></Field>
          <Field label="密钥" hint="只在本次请求里使用，平台不落盘。">
            <TextInput type="password" value={openai.api_key} placeholder="sk-…" onChange={e => patch({ api_key: e.target.value })} />
          </Field>
        </div>
      )}
    </div>
  );
}

export function LlmTest() {
  const { llm, toast } = usePlatform();
  const { run, running } = useAction(async () => {
    const h = await api.health();
    const provider = h.llm_provider || "none";
    if (llm.mode === "demo") toast("演示模式：不需要模型服务，用内置脚本走流程", "ok");
    else if (!h.llm_configured || provider === "none")
      toast("服务端还没配模型，AI 功能会明确报错；可先切「演示模式」看流程", "warn");
    else toast(`模型服务就绪：${provider}`, "ok");
  }, [llm]);
  return (
    <div className="btn-row">
      <Btn size="sm" busy={running} onClick={() => void run()}>自检模型连通性</Btn>
      <Tag kind="plain">当前来源：{LLM_MODE_LABEL[llm.mode]}</Tag>
    </div>
  );
}

export type { LlmSetting };
