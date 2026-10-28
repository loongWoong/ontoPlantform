import { useEffect, useState } from "react";
import { api, type LlmConfig, type LlmConfigUpdate } from "./api";
import { useAction, usePlatform, type LlmSetting } from "./store";
import { Btn, Field, Loading, Select, Tag, TextInput, useLoad } from "./ui";
import { formatLocal } from "./lib/time";

export const LLM_MODE_LABEL: Record<string, string> = {
  demo: "演示模式（不需要模型）",
  platform: "平台默认（服务端环境变量）",
  openai: "工作空间模型（服务端保存）",
};

/** 模型来源选择器：工作空间级配置，密钥只写不读。
 *  浏览器只保存 mode / base_url / model；密钥提交后立即从界面清空。 */
export function LlmPicker({ compact = false }: { compact?: boolean }) {
  const { wid, llm, setLlm, toast } = usePlatform();
  const openai = llm.mode === "openai" ? llm : null;
  const [baseUrl, setBaseUrl] = useState(openai?.base_url || "");
  const [model, setModel] = useState(openai?.model || "");
  const [key, setKey] = useState("");
  const [saved, setSaved] = useState<LlmConfig | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const load = useLoad(async () => {
    if (!wid) return null;
    try {
      const cfg = await api.llmConfig(wid);
      setLoadError(null);
      return cfg;
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e));
      return null;
    }
  }, [wid]);

  useEffect(() => {
    const cfg = load.data;
    if (!cfg) return;
    setSaved(cfg);
    if (cfg.base_url) setBaseUrl(cfg.base_url);
    if (cfg.model) setModel(cfg.model);
  }, [load.data]);

  useEffect(() => { setSaved(null); setKey(""); setLoadError(null); }, [wid]);

  const pick = (mode: string) => {
    if (mode === "openai") setLlm({ mode: "openai", base_url: baseUrl, model });
    else if (mode === "demo") setLlm({ mode: "demo" });
    else setLlm({ mode: "platform" });
  };

  const { run: save, running } = useAction(async () => {
    if (!wid) return;
    const body: LlmConfigUpdate = {
      provider: "openai",
      base_url: baseUrl.trim(),
      model: model.trim(),
    };
    // 留空表示“不修改已保存密钥”，而不是把密钥清成空字符串。
    if (key.trim()) body.api_key = key.trim();
    const r = await api.configureWorkspaceLlm(wid, body);
    setSaved(r);
    setKey("");
    setLlm({ mode: "openai", base_url: r.base_url || baseUrl, model: r.model || model });
    toast("工作空间模型配置已保存；密钥只写不读，界面不再保留", "ok");
    load.reload();
  }, [wid, baseUrl, model, key]);

  const modeHint = "演示模式用内置脚本跑通「观察 → 行动 → 结论」，不需要密钥；接真实模型时选工作空间模型并保存，密钥不会进入浏览器存储。";

  return (
    <div className={compact ? "field-inline" : ""}>
      <Field label="模型来源" hint={compact ? undefined : modeHint}>
        <Select value={llm.mode} onChange={e => pick(e.target.value)} aria-label="模型来源">
          <option value="platform">{LLM_MODE_LABEL.platform}</option>
          <option value="openai">{LLM_MODE_LABEL.openai}</option>
          <option value="demo">{LLM_MODE_LABEL.demo}</option>
        </Select>
      </Field>
      {llm.mode === "openai" && (
        <div className={compact ? "field-inline" : ""}>
          {load.loading && <Loading text="读取工作空间模型配置…" />}
          {loadError && (
            <div className="banner warn">
              工作空间模型配置接口不可用：{loadError}。这里不会退回到浏览器保存密钥。
            </div>
          )}
          <Field label="服务地址" hint="留空用服务端环境变量。">
            <TextInput value={baseUrl} placeholder="https://api.openai.com/v1"
                       onChange={e => setBaseUrl(e.target.value)} />
          </Field>
          <Field label="模型名"><TextInput value={model} placeholder="gpt-4o-mini" onChange={e => setModel(e.target.value)} /></Field>
          <Field label="密钥" hint="只写不读：保存后服务端只返回掩码（前四位与末四位），输入框会清空。留空表示保持已保存的密钥。">
            <TextInput type="password" value={key} autoComplete="new-password"
                       placeholder={saved?.api_key_masked || (saved?.key_last4 ? `已保存 ****${saved.key_last4}，留空不改` : "sk-…")}
                       onChange={e => setKey(e.target.value)} />
          </Field>
          <div className="btn-row">
            <Btn size="sm" busy={running} onClick={() => void save()}>保存到当前工作空间</Btn>
            {saved?.updated_at && (
              <span className="muted small">
                最近更新 {formatLocal(saved.updated_at)}
                {saved.updated_by ? ` · ${saved.updated_by}` : ""}
                {saved.version ? ` · v${saved.version}` : ""}
              </span>
            )}
          </div>
          <p className="muted small">
            密钥由服务端加密保存，读取接口只返回掩码；浏览器不缓存密钥，localStorage 里只有模式、地址与模型名。
            每次修改都会写操作记录。
          </p>
        </div>
      )}
    </div>
  );
}

export function LlmTest() {
  const { wid, llm, toast } = usePlatform();
  const { run, running } = useAction(async () => {
    if (llm.mode === "demo") { toast("演示模式：不需要模型服务，用内置脚本走流程", "ok"); return; }
    const h = await api.health();
    const provider = h.llm_provider || "none";
    let workspaceCfg: LlmConfig | null = null;
    if (wid) {
      try { workspaceCfg = await api.llmConfig(wid); } catch { workspaceCfg = null; }
    }
    if (workspaceCfg?.provider && workspaceCfg.provider !== "none" && (workspaceCfg.configured || workspaceCfg.has_key)) {
      toast(`工作空间模型已配置：${workspaceCfg.provider}${workspaceCfg.model ? ` · ${workspaceCfg.model}` : ""}；实际调用是否成功以运行结果为准`, "ok");
    } else if (workspaceCfg?.provider && workspaceCfg.provider !== "none") {
      toast("工作空间模型已选择，但还没有保存密钥；请补密钥，或切回平台默认/演示模式", "warn");
    } else if (!h.llm_configured || provider === "none") {
      toast("服务端还没配模型，AI 功能会明确报错；可先切「演示模式」看流程，或保存工作空间模型", "warn");
    } else {
      toast(`平台模型已配置：${provider}；工作空间没有单独配置时使用平台默认`, "ok");
    }
  }, [wid, llm]);
  return (
    <div className="btn-row">
      <Btn size="sm" busy={running} onClick={() => void run()}>检查模型配置</Btn>
      <Tag kind="plain">当前来源：{LLM_MODE_LABEL[llm.mode]}</Tag>
    </div>
  );
}

export type { LlmSetting };
