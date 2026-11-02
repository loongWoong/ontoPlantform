import { useEffect, useState } from "react";
import { api, type LlmCheckResult, type LlmConfig, type LlmConfigUpdate } from "./api";
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

const CHECK_ERROR_LABEL: Record<string, string> = {
  auth: "密钥无效或没有权限（鉴权失败）",
  not_found: "端点或模型不存在（检查服务地址与模型名）",
  timeout: "调用超时（网络或代理太慢）",
  rate_limit: "被限流（稍后重试，或换一个模型）",
  not_configured: "没有可用模型：AI 生成会走确定性回退",
  transport: "连接失败（检查服务地址与网络）",
};

/** POST /llm-config/check 的诊断结果：服务端真实调用一次端点，HTTP 200。 */
export function LlmTest() {
  const { wid, llm, toast } = usePlatform();
  const [result, setResult] = useState<LlmCheckResult | null>(null);
  const { run, running } = useAction(async () => {
    if (llm.mode === "demo") { toast("演示模式：不需要模型服务，用内置脚本走流程", "ok"); return; }
    if (!wid) { toast("先选择一个工作空间，再检查模型配置", "warn"); return; }
    setResult(null);
    const r = await api.llmCheck(wid);
    setResult(r);
    if (r.ok) {
      toast(`模型调用成功${r.latencyMs ? ` · ${r.latencyMs} ms` : ""}（${r.model || r.source}）`, "ok");
    } else {
      const reason = CHECK_ERROR_LABEL[r.errorType || ""] || r.message || "未知错误";
      toast(`模型检查未通过：${reason}`, "warn");
    }
  }, [wid, llm]);
  return (
    <div>
      <div className="btn-row">
        <Btn size="sm" busy={running} onClick={() => void run()}>检查模型配置</Btn>
        <Tag kind="plain">当前来源：{LLM_MODE_LABEL[llm.mode]}</Tag>
      </div>
      {result ? (
        <div className={`banner ${result.ok ? "info" : "warn"}`} style={{ marginTop: 10 }}>
          <b>{result.ok ? "模型调用成功" : "模型检查未通过"}</b>
          <p className="muted small" style={{ margin: "4px 0 0" }}>
            {result.ok
              ? `模型 ${result.model || "—"} · 来源 ${result.source} · ${result.latencyMs} ms${result.responsePreview ? ` · 回复示例：${result.responsePreview}` : ""}`
              : `${CHECK_ERROR_LABEL[result.errorType || ""] || result.message || "未知错误"}${result.status != null ? `（HTTP ${result.status}）` : ""} · 来源 ${result.source}`}
          </p>
        </div>
      ) : null}
    </div>
  );
}

export type { LlmSetting };
