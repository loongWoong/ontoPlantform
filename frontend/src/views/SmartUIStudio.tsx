import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowSquareOut, ArrowsClockwise, CheckCircle, Circle, CircleNotch,
  FloppyDisk, PaperPlaneTilt, Sparkle, StopCircle, WarningCircle, XCircle,
} from "@phosphor-icons/react";
import {
  api, type ApiError, type Application, type LlmCheckResult, type SmartUiFocusOption,
  type SmartUiFocusRequest, type SmartUiMeta, type SmartUiRulePackSummary,
  type UiDraft, type UiGeneration,
  type UiGenerationCost, type UiGenerationDetail, type UiGenerationDiffChange,
  type UiGenerationFocus, type UiGenerationSummary, type UiGenerationUsage,
  type UiRuleTrace, type UiRuleClarify,
} from "../api";
import { usePlatform } from "../store";
import { smartUiFrameSrc, smartUiStandaloneSrc, useSmartUiBridge } from "../smartui-bridge";
import {
  Area, Btn, Card, Drawer, Empty, ErrorNote, Field, Select, Skeleton, Stat,
  TableWrap, Tag, TextInput,
} from "../ui";

const EXAMPLES = [
  "把收费站状态做成值班总览，突出拥堵和空闲",
  "给当前焦点车辆做画像与通行链路",
  "对比各收费站的拥堵与空闲情况",
  "生成收费站运营日报视图",
];

const MODE_LABEL: Record<string, string> = {
  llm: "AI 生成",
  rule: "规则生成 · 无模型调用",
  fallback: "确定性回退",
  deterministic: "确定性模式",
};

const STATUS_LABEL: Record<string, string> = {
  queued: "排队中",
  running: "生成中",
  succeeded: "已生成",
  failed: "失败",
  cancelled: "已取消",
  interrupted: "已中断",
};

const STEP_ICON = {
  ok: <CheckCircle size={14} weight="fill" />,
  warn: <WarningCircle size={14} weight="fill" />,
  err: <XCircle size={14} weight="fill" />,
  running: <CircleNotch size={14} className="studio-spin" />,
  pending: <Circle size={14} />,
} as const;

function timeOf(iso: string | null): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function stepIcon(status: string) {
  const key = (status in STEP_ICON ? status : "pending") as keyof typeof STEP_ICON;
  return STEP_ICON[key];
}

/** 模型状态以 meta.model 为准（服务端按 workspace > platform > none 解析），
 *  不再看全局 llmReady：工作空间自己的模型和平台默认模型必须能区分开。 */
export function modelStatusLabel(model: SmartUiMeta["model"]): { text: string; ok: boolean } {
  const source = model?.source ?? "none";
  const name = model?.model || "";
  if (source === "workspace") return { text: `工作空间模型 · ${name || "未命名"}`, ok: true };
  if (source === "platform") return { text: `平台默认模型 · ${name || "未命名"}`, ok: true };
  return { text: "模型未配置 · 确定性回退", ok: false };
}

const CHECK_ERROR_LABEL: Record<string, string> = {
  auth: "密钥无效或没有权限（鉴权失败）",
  not_found: "端点或模型不存在（检查服务地址与模型名）",
  timeout: "调用超时（网络或代理太慢）",
  rate_limit: "被限流（稍后重试，或换一个模型）",
  not_configured: "没有可用模型：生成会走确定性回退",
  transport: "连接失败（检查服务地址与网络）",
};

/** llm-check 的诊断结果翻成可执行的中文；认不出的原文保留。 */
export function checkResultText(result: LlmCheckResult): string {
  if (result.ok) {
    const latency = result.latencyMs ? ` · ${result.latencyMs} ms` : "";
    return `模型调用成功${latency}（${result.model || result.source}）`;
  }
  const reason = CHECK_ERROR_LABEL[result.errorType || ""] || result.message || "未知错误";
  const extra = result.status ? `（HTTP ${result.status}）` : "";
  return `模型检查未通过：${reason}${extra}`;
}

function statusTagKind(status: string): "ok" | "warn" | "err" | "plain" {
  if (status === "succeeded") return "ok";
  if (status === "failed" || status === "interrupted") return "err";
  if (status === "cancelled") return "plain";
  return "warn";
}

/** usage 的字段名按 provider 兼容：prompt/completion/total 或 input/output。 */
export function usageNumbers(usage: UiGenerationUsage | null | undefined): {
  prompt: number; completion: number; total: number;
} | null {
  if (!usage) return null;
  const prompt = Number(usage.prompt_tokens ?? usage.input_tokens ?? 0);
  const completion = Number(usage.completion_tokens ?? usage.output_tokens ?? 0);
  const total = Number(usage.total_tokens ?? prompt + completion);
  if (!prompt && !completion && !total) return null;
  return { prompt, completion, total };
}

const CURRENCY_SYMBOL: Record<string, string> = {
  CNY: "¥", USD: "$", EUR: "€", JPY: "¥", GBP: "£",
};

/** 成本金额：币种符号 + 金额（费率细节在完整版里）。 */
export function formatCostAmount(cost: UiGenerationCost | null | undefined): string {
  if (!cost) return "未配置费率";
  const symbol = CURRENCY_SYMBOL[(cost.currency || "").toUpperCase()] || `${cost.currency} `;
  const amount = Number(cost.amount);
  const text = Number.isFinite(amount)
    ? amount.toFixed(4).replace(/0+$/, "").replace(/\.$/, "")
    : String(cost.amount);
  return `${symbol}${text || "0"}`;
}

/** 成本展示：金额 + 币种 + 费率；未配置费率时明确说明，而不是显示 0。 */
export function formatCost(cost: UiGenerationCost | null | undefined): string {
  if (!cost) return "未配置费率";
  const symbol = CURRENCY_SYMBOL[(cost.currency || "").toUpperCase()] || `${cost.currency} `;
  const input = Number(cost.inputRatePer1k);
  const output = Number(cost.outputRatePer1k);
  return `${formatCostAmount(cost)}（输入 ${symbol}${input}/1k · 输出 ${symbol}${output}/1k）`;
}

/** 版本链/最近生成里的短标签：成功时直接标模式，避免笼统的“成功”。 */
export function generationLabel(item: UiGenerationSummary): string {
  if (item.status === "succeeded") return MODE_LABEL[item.mode] || "已生成";
  return STATUS_LABEL[item.status] || item.status;
}

/** 失败/中断时把错误文本翻成可操作的中文提示。 */
export function failureHints(generation: UiGeneration): string[] {
  const httpStatus = Number(generation.trace?.status);
  const haystack = [
    generation.error,
    generation.validation?.error,
    generation.trace?.errorType,
    generation.trace?.status,
  ].filter((value) => value !== null && value !== undefined && value !== "").join(" ");
  const hints: string[] = [];
  const push = (hint: string) => {
    if (!hints.includes(hint)) hints.push(hint);
  };
  if (httpStatus === 401
      || /401|unauthori[sz]ed|invalid.{0,12}api.{0,6}key|authenticat|鉴权/i.test(haystack)) {
    push("鉴权失败（401）：检查工作空间 / 平台的 API Key 是否正确、是否过期，修正后重试。");
  }
  if (httpStatus === 404
      || /404|model.{0,8}not.{0,8}found|does not exist|no such model|端点不存在/i.test(haystack)) {
    push("端点或模型不存在（404）：检查模型端点地址与模型名是否配置正确。");
  }
  if (/timeout|timed out|超时|ETIMEDOUT|ESOCKETTIMEDOUT/i.test(haystack)) {
    push("调用超时：检查网络 / 代理与端点可达性，或换用响应更快的模型后重试。");
  }
  if (httpStatus === 429 || /429|rate.?limit|too many requests|限流|quota|额度/i.test(haystack)) {
    push("被限流或额度不足（429）：稍后重试，或检查模型额度 / 计费配置。");
  }
  if (/not_configured|未配置模型|没有可用模型|no model|llm.{0,10}disabled/i.test(haystack)) {
    push("没有可用模型：先在「模型设置」里选择工作空间模型，或直接改用确定性模式生成。");
  }
  if (/promptBytes|上下文过大|too large|MAX_PROMPT/i.test(haystack)) {
    push("上下文过大：缩小数据范围、减少对象类型或动作后再试。");
  }
  if (/JSON|json/i.test(haystack) && /不是合法|invalid|parse|extract/i.test(haystack)) {
    push("模型输出不是合法 JSON：换一个更稳定的模型再试，或改用确定性模式。");
  }
  if (/显式模型模式|explicit.{0,12}llm|mode.{0,6}llm/i.test(haystack)) {
    push("显式模型模式不会自动回退：修正模型配置后重试，或改用自动 / 确定性模式。");
  }
  if (generation.status === "interrupted") {
    push("服务重启导致任务中断：点「重试」用同一问题重新生成即可，历史记录仍保留。");
  }
  if (!hints.length) {
    push("先点「重试（确定性）」拿一个可用视图；仍失败时再检查模型配置与服务日志。");
  }
  return hints;
}

/** 生成记录里的焦点（object/key/value）统一展示成「对象 · 属性=值」。 */
export function focusText(focus: UiGenerationFocus | null | undefined): string {
  if (!focus) return "";
  return `${focus.object || "对象"} · ${focus.key}=${focus.value}`;
}

const CHANGE_KIND_LABEL: Record<string, string> = {
  object: "对象",
  goal: "目标",
  focus: "数据范围",
  view: "视角",
  action: "动作",
  displayName: "标题",
};

/** diff 的 before/after 可能是字符串、焦点对象或整段视图声明，统一转成一行文本。 */
function changeValueText(value: unknown): string {
  if (value === null || value === undefined || value === "") return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) {
    return value.map((item) => changeValueText(item)).filter(Boolean).join("、");
  }
  if (typeof value === "object") {
    const obj = value as Record<string, unknown>;
    if (typeof obj.key === "string" && typeof obj.value === "string") {
      return `${obj.key}=${obj.value}`;   // context.focus
    }
    if (typeof obj.title === "string" && obj.title) return obj.title;   // view
    if (typeof obj.perspective === "string") return obj.perspective;
    return JSON.stringify(value);
  }
  return String(value);
}

/** 一条 diff 变化翻成中文动作：新增 / 移除 / before → after。 */
export function diffChangeText(change: UiGenerationDiffChange): {
  text: string; tone: "add" | "del" | "change";
} {
  const hadBefore = change.before !== null && change.before !== undefined;
  const hasAfter = change.after !== null && change.after !== undefined;
  const before = changeValueText(change.before);
  const after = changeValueText(change.after);
  if (!hadBefore && hasAfter) {
    return {
      text: change.kind === "action" ? "新增动作" : `新增${after ? ` ${after}` : ""}`,
      tone: "add",
    };
  }
  if (hadBefore && !hasAfter) {
    return {
      text: change.kind === "action" ? "移除动作" : `移除${before ? ` ${before}` : ""}`,
      tone: "del",
    };
  }
  return { text: `${before || "—"} → ${after || "—"}`, tone: "change" };
}

/** 版本链：当前版本在最左，箭头指向上一版；点击旧版本可切过去查看。 */
function LineageChain({
  lineage, currentId, onSelect,
}: {
  lineage: UiGenerationSummary[];
  currentId: string;
  onSelect: (gid: string) => void;
}) {
  if (!lineage.length) return null;
  return (
    <div className="studio-lineage" aria-label="版本链">
      <span className="studio-lineage-label">版本链</span>
      <div className="studio-lineage-nodes">
        {lineage.map((item, index) => {
          const active = item.id === currentId;
          return (
            <span key={item.id} className="studio-lineage-item">
              {index > 0 ? <span className="studio-lineage-arrow" aria-hidden="true">←</span> : null}
              <button
                type="button"
                className={`studio-lineage-node ${active ? "is-active" : ""}`}
                aria-current={active ? "true" : undefined}
                title={`${item.question}${item.instruction ? `（修改：${item.instruction}）` : ""}`}
                onClick={() => {
                  if (!active) onSelect(item.id);
                }}
              >
                v{item.version}
                <span className="studio-lineage-status">{generationLabel(item)}</span>
              </button>
            </span>
          );
        })}
      </div>
    </div>
  );
}

function StepList({ generation }: { generation: UiGeneration }) {
  return (
    <ol className="studio-steps">
      {generation.steps.map((step) => (
        <li key={step.key} className={`studio-step is-${step.status}`}>
          <span className="studio-step-icon">{stepIcon(step.status)}</span>
          <span className="studio-step-body">
            <b>{step.label}</b>
            <span>{step.detail || "—"}</span>
          </span>
          <span className="studio-step-time mono">
            {step.durationMs != null ? `${step.durationMs} ms` : step.status === "running" ? "…" : "—"}
          </span>
        </li>
      ))}
    </ol>
  );
}

function ValidationList({ generation }: { generation: UiGeneration }) {
  const checks = generation.validation.checks ?? [];
  const pruned = generation.validation.pruned ?? [];
  return (
    <div className="studio-validation">
      {checks.map((check) => (
        <div key={check.key} className={`studio-check is-${check.status}`}>
          <span className="studio-check-icon">{stepIcon(check.status)}</span>
          <span className="studio-check-body">
            <b>{check.label}</b>
            <span>{check.detail}</span>
          </span>
        </div>
      ))}
      {pruned.length ? (
        <div className="studio-pruned">
          <span className="studio-pruned-label">已剪除（平台裁决）</span>
          {pruned.map((item) => (
            <div key={`${item.kind}-${item.key}`} className="studio-pruned-row">
              <Tag kind="warn">
                {item.kind === "action" ? "动作"
                  : item.kind === "view" ? "视角"
                    : item.kind === "object" ? "对象" : "焦点"}
              </Tag>
              <span className="mono">{item.key || "—"}</span>
              <span>{item.reason}</span>
            </div>
          ))}
        </div>
      ) : null}
      {generation.validation.fallbackReason ? (
        <p className="studio-note is-warn">
          {generation.validation.fallbackReason}。视图仍由同一套 L4 编译器生成，链路可审计。
        </p>
      ) : null}
    </div>
  );
}

/** 规则决策轨迹：证明“无模型”时目标/视角是依据什么选的。 */
function RuleTracePanel({
  trace, clarify, onUseQuestion,
}: {
  trace: UiRuleTrace | null | undefined;
  clarify: UiRuleClarify[] | undefined;
  onUseQuestion?: (question: string) => void;
}) {
  const matched = trace?.matched ?? [];
  const candidates = trace?.candidates ?? [];
  const skipped = trace?.skipped ?? [];
  const pruned = trace?.pruned ?? [];
  const questions = clarify ?? [];
  if (!trace && !questions.length) return null;
  return (
    <Card
      title="规则决策"
      sub="命中/跳过/剪除都可解释；置信度是规则启发式评分，不是模型概率。"
      aside={<Tag kind="ok">{trace?.pack?.id || "内置规则包"}</Tag>}
    >
      <div className="studio-rule-summary">
        <span><b>{trace?.goal || "—"}</b><small>目标</small></span>
        <span><b>{trace?.object || "—"}</b><small>对象</small></span>
        <span><b>{Math.round((trace?.confidence ?? 0) * 100)}%</b><small>置信度</small></span>
        <span><b>{matched.length}</b><small>命中规则</small></span>
        <span><b>{candidates.length}</b><small>候选视角</small></span>
      </div>
      {trace?.pack?.hash ? (
        <p className="studio-rule-hash mono">hash {String(trace.pack.hash).slice(0, 12)}…</p>
      ) : null}
      {matched.length ? (
        <div className="studio-rule-section">
          <h4>命中规则</h4>
          <ul className="studio-rule-list">
            {matched.map((item) => (
              <li key={item.ruleId}>
                <span className="studio-rule-id mono">{item.ruleId}</span>
                <span className="studio-rule-priority">P{item.priority}</span>
                <span className="studio-rule-reason">{item.reason || item.effects.join(" / ")}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="muted small">没有规则命中：使用默认目标与视角顺序。</p>
      )}
      {candidates.length ? (
        <div className="studio-rule-section">
          <h4>候选视角</h4>
          <div className="studio-rule-candidates">
            {candidates.map((item) => (
              <span key={item.perspective} className="studio-rule-candidate">
                <b>{item.title || item.perspective}</b>
                <small>{item.perspective} · {item.weight.toFixed(2)}</small>
              </span>
            ))}
          </div>
        </div>
      ) : null}
      {skipped.length || pruned.length ? (
        <div className="studio-rule-section">
          <details>
            <summary>未采用（{skipped.length + pruned.length}）</summary>
            <ul className="studio-rule-list">
              {skipped.map((item) => (
                <li key={`skip:${item.ruleId}`}>
                  <span className="studio-rule-id mono">{item.ruleId}</span>
                  <span className="studio-rule-priority">P{item.priority}</span>
                  <span className="studio-rule-reason">{item.reason}</span>
                </li>
              ))}
              {pruned.map((item, index) => (
                <li key={`prune:${item.kind}:${item.key}:${index}`}>
                  <span className="studio-rule-id mono">{item.kind}</span>
                  <span className="studio-rule-reason">{item.key} · {item.reason}</span>
                </li>
              ))}
            </ul>
          </details>
        </div>
      ) : null}
      {questions.length ? (
        <div className="studio-rule-section">
          <h4>规则建议补充</h4>
          {questions.map((item) => (
            <div key={item.id} className="studio-rule-clarify">
              <p>{item.question}</p>
              <div className="studio-rule-candidates">
                {item.options.map((option) => (
                  <button
                    key={option.id}
                    type="button"
                    className="studio-example"
                    onClick={() => onUseQuestion?.(option.label)}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </Card>
  );
}

interface StudioProps {  wid: string;
  meta: SmartUiMeta;
  generationId: string | null;
  onSelect: (gid: string) => void;
  onOpenHistory: () => void;
}

export function SmartUIStudio({ wid, meta, generationId, onSelect, onOpenHistory }: StudioProps) {
  const { toast, go } = usePlatform();
  const [question, setQuestion] = useState(meta.defaultQuestion || EXAMPLES[0]);
  const [role, setRole] = useState(meta.roles.find((item) => item.id === meta.roles[0]?.id)?.id ?? "owner");
  const roleOptions = meta.roles.length ? meta.roles : [{ id: "owner", name: "空间所有者", glyph: "主" }];
  const [mode, setMode] = useState<"auto" | "rule" | "deterministic">("auto");
  const [rulePacks, setRulePacks] = useState<SmartUiRulePackSummary[]>([]);
  const [rulePackId, setRulePackId] = useState("");
  const effectiveRulePack = rulePackId || meta.boundRulePackId || "";
  const modelState = modelStatusLabel(meta.model);
  const willUseModel = meta.model?.will_use_model ?? false;
  const [generation, setGeneration] = useState<UiGenerationDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [refine, setRefine] = useState("");
  const [recent, setRecent] = useState<UiGenerationSummary[]>([]);
  const [drafts, setDrafts] = useState<UiDraft[]>([]);
  const [pollTick, setPollTick] = useState(0);
  const [reloadKey, setReloadKey] = useState(0);
  const [checking, setChecking] = useState(false);
  const [checkResult, setCheckResult] = useState<LlmCheckResult | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [publishOpen, setPublishOpen] = useState(false);
  const [publishTarget, setPublishTarget] = useState<"new" | "existing">("new");
  const [publishName, setPublishName] = useState("");
  const [publishAppId, setPublishAppId] = useState("");
  const [publishApps, setPublishApps] = useState<Application[]>([]);
  const [publishing, setPublishing] = useState(false);
  // “数据范围（可选）”：默认平台焦点；选择器状态独立，不随问题清空。
  const [focus, setFocus] = useState<SmartUiFocusOption | null>(null);
  const [entityQuery, setEntityQuery] = useState("");
  const [entities, setEntities] = useState<SmartUiFocusOption[]>([]);
  const [entityTotal, setEntityTotal] = useState(0);
  const [loadingEntities, setLoadingEntities] = useState(false);
  const focusObject = meta.focus?.objectType || "";
  const frameRef = useRef<HTMLIFrameElement>(null);
  const postAuthToken = useSmartUiBridge(frameRef, wid, reloadKey);

  const refreshSide = useCallback(async () => {
    try {
      const [list, draftList] = await Promise.all([
        api.smartUiGenerations(wid, { limit: 6 }),
        api.smartUiDrafts(wid),
      ]);
      setRecent(list.items);
      setDrafts(draftList.items);
    } catch {
      // 侧栏数据不影响主流程
    }
  }, [wid]);

  useEffect(() => {
    void refreshSide();
  }, [refreshSide]);

  // 规则包列表用于“规则生成”模式；加载失败不影响模型/确定性路径。
  useEffect(() => {
    let cancelled = false;
    api.smartUiRulePacks(wid)
      .then((list) => {
        if (cancelled) return;
        setRulePacks(list.items);
        setRulePackId((prev) => prev || list.boundRulePackId || "");
      })
      .catch(() => {
        if (!cancelled) setRulePacks([]);
      });
    return () => {
      cancelled = true;
    };
  }, [wid]);

  // 打开已有生成（从 URL、最近列表或生成记录进入）时读取完整记录。
  useEffect(() => {
    if (!generationId) {
      setGeneration(null);
      setLoadError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    api.smartUiGeneration(wid, generationId)
      .then((next) => {
        if (cancelled) return;
        setGeneration(next);
        setLoadError(null);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setLoadError((error as ApiError).message || String(error));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [wid, generationId]);

  // 生成任务在后台线程执行：这里只轮询状态，不阻塞 console 的 15s 请求预算。
  useEffect(() => {
    if (!generation || (generation.status !== "queued" && generation.status !== "running")) {
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      api.smartUiGeneration(wid, generation.id)
        .then((next) => {
          if (cancelled) return;
          setGeneration(next);
          if (next.status === "succeeded" || next.status === "failed"
              || next.status === "cancelled" || next.status === "interrupted") {
            void refreshSide();
          }
          setPollTick((tick) => tick + 1);
        })
        .catch((error: unknown) => {
          if (cancelled) return;
          setLoadError((error as ApiError).message || String(error));
        });
    }, 700);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [wid, generation?.id, generation?.status, pollTick, refreshSide]);

  // 工作空间切换：旧实例焦点作废，回到平台默认焦点；选择器状态独立，不随问题清空。
  useEffect(() => {
    setFocus(null);
    setEntityQuery("");
    setEntities([]);
    setEntityTotal(0);
  }, [wid]);

  // 数据范围候选实例：按平台默认对象类型 + 关键字（防抖）加载；
  // 没有实例或接口失败都不阻塞生成（仍可用平台默认焦点）。
  useEffect(() => {
    if (!wid) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      setLoadingEntities(true);
      api.smartUiEntities(wid, {
        object: focusObject || undefined,
        q: entityQuery.trim() || undefined,
        limit: 20,
      })
        .then((result) => {
          if (cancelled) return;
          setEntities(result.items);
          setEntityTotal(result.total);
        })
        .catch(() => {
          if (!cancelled) {
            setEntities([]);
            setEntityTotal(0);
          }
        })
        .finally(() => {
          if (!cancelled) setLoadingEntities(false);
        });
    }, entityQuery ? 250 : 0);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      setLoadingEntities(false);
    };
  }, [wid, focusObject, entityQuery]);

  const toFocusRequest = (option: SmartUiFocusOption | null): SmartUiFocusRequest | undefined => (
    option
      ? { objectType: option.objectType, key: option.key, value: option.value }
      : undefined
  );

  /** 重试优先用当前选择，其次沿用原生成记录里的焦点。 */
  const retryFocus = (): SmartUiFocusRequest | undefined => {
    const chosen = toFocusRequest(focus);
    if (chosen) return chosen;
    if (generation?.focus) {
      return {
        objectType: generation.focus.object,
        key: generation.focus.key,
        value: generation.focus.value,
      };
    }
    return undefined;
  };

  const startGeneration = async () => {
    if (!question.trim() || creating) return;
    setCreating(true);
    setLoadError(null);
    try {
      const chosen = toFocusRequest(focus);
      const created = await api.smartUiGenerate(wid, {
        question: question.trim(),
        roles: [role],
        mode,
        ...(mode === "rule" && effectiveRulePack ? { rule_pack_id: effectiveRulePack } : {}),
        ...(chosen ? { focus: chosen } : {}),
      });
      setGeneration(created);
      onSelect(created.id);
      void refreshSide();
    } catch (error) {
      setLoadError((error as ApiError).message || String(error));
    } finally {
      setCreating(false);
    }
  };

  const checkModel = async () => {
    if (checking || !wid) return;
    setChecking(true);
    setCheckResult(null);
    setLoadError(null);
    try {
      setCheckResult(await api.llmCheck(wid));
    } catch (error) {
      setCheckResult({
        ok: false,
        errorType: "transport",
        status: null,
        latencyMs: 0,
        message: (error as ApiError).message || String(error),
        model: "",
        source: "none",
      });
    } finally {
      setChecking(false);
    }
  };

  const cancelGeneration = async () => {
    if (!generation || cancelling) return;
    setCancelling(true);
    setLoadError(null);
    try {
      const next = await api.smartUiCancel(wid, generation.id);
      // cancel 只返回 generation 本体，不带 lineage/diff：合并保留版本链信息。
      setGeneration((prev) => (prev && prev.id === next.id ? { ...prev, ...next } : next));
      void refreshSide();
      toast("已取消这次生成；记录保留在历史里", "ok");
    } catch (error) {
      setLoadError((error as ApiError).message || String(error));
    } finally {
      setCancelling(false);
    }
  };

  /** 失败/中断/取消后重开一条同问题的新记录：自动=让平台再试模型，确定性=直接走回退。
   *  数据范围跟随当前选择；没有选择时沿用原记录的焦点，保证重试不丢失范围。 */
  const retryGeneration = async (mode: "auto" | "rule" | "deterministic") => {
    if (!generation || creating) return;
    setCreating(true);
    setLoadError(null);
    try {
      const chosen = retryFocus();
      const created = await api.smartUiGenerate(wid, {
        question: generation.question,
        roles: generation.roles.length ? generation.roles : [role],
        mode,
        ...(mode === "rule" && (generation.rule_pack_id || effectiveRulePack)
          ? { rule_pack_id: generation.rule_pack_id || effectiveRulePack }
          : {}),
        ...(chosen ? { focus: chosen } : {}),
      });
      setGeneration(created);
      onSelect(created.id);
      void refreshSide();
    } catch (error) {
      setLoadError((error as ApiError).message || String(error));
    } finally {
      setCreating(false);
    }
  };

  const refineGeneration = async () => {
    if (!generation || !refine.trim() || creating) return;
    setCreating(true);
    setLoadError(null);
    try {
      // 数据范围透传：当前选择优先，否则沿用父记录 focus（后端也会继承，这里显式传避免被覆盖为空）。
      const chosen = retryFocus();
      const created = await api.smartUiRefine(wid, generation.id, refine.trim(), chosen);
      setGeneration(created);
      setRefine("");
      onSelect(created.id);
      void refreshSide();
    } catch (error) {
      setLoadError((error as ApiError).message || String(error));
    } finally {
      setCreating(false);
    }
  };

  const saveDraft = async () => {
    if (!generation) return;
    try {
      const draft = await api.smartUiAccept(wid, generation.id);
      toast(`已保存草稿 v${draft.version}`, "ok");
      void refreshSide();
    } catch (error) {
      toast((error as ApiError).message, "err");
    }
  };

  // 发布弹窗：新建应用，或复用已有草稿应用（已发布应用不可变，不能作为目标）。
  useEffect(() => {
    if (!publishOpen || !wid) return;
    let cancelled = false;
    api.applications(wid)
      .then((list) => {
        if (!cancelled) setPublishApps(list.filter((item) => item.lifecycle !== "published"));
      })
      .catch(() => {
        // 列表拉不到时仍允许发布到“新建应用”
      });
    return () => {
      cancelled = true;
    };
  }, [publishOpen, wid]);

  const openPublish = () => {
    if (!generation) return;
    const title = generation.blueprint?.displayName || generation.blueprint?.name || "智能视图";
    setPublishName(title);
    setPublishTarget("new");
    setPublishAppId("");
    setPublishOpen(true);
  };

  const doPublish = async () => {
    if (!generation || publishing) return;
    if (publishTarget === "existing" && !publishAppId) {
      toast("先选择一个草稿应用，或改回“新建应用”", "err");
      return;
    }
    setPublishing(true);
    try {
      const result = await api.smartUiPublish(wid, generation.id,
        publishTarget === "existing"
          ? { application_id: publishAppId }
          : { name: publishName.trim() });
      setPublishOpen(false);
      toast(`已发布到应用「${result.application.name}」`, "ok");
      void refreshSide();
      go("application", { app: result.application.id, tab: "ai" });
    } catch (error) {
      toast((error as ApiError).message || String(error), "err");
    } finally {
      setPublishing(false);
    }
  };

  const currentDraft = drafts.find((draft) => draft.generation_id === generation?.id);
  const running = generation?.status === "queued" || generation?.status === "running";
  const ready = generation?.status === "succeeded";
  // tokens/成本：优先用 metrics 里的（后端成功时写入 _usage_cost），summary 兜底。
  const usage = usageNumbers(generation?.metrics?.usage ?? generation?.usage);
  const cost = generation?.cost ?? generation?.metrics?.cost ?? null;
  // 模型生成成功但没有成本：说明部署没配费率，明确写出来而不是显示 0。
  const costText = cost
    ? formatCost(cost)
    : usage || generation?.mode === "llm"
      ? "未配置费率"
      : "—";
  // 模型输出不是合法 JSON 时后端把原始输出截断到 ≤2000 字符写进 trace.rawPreview。
  const rawPreview = typeof generation?.trace?.rawPreview === "string"
    ? generation.trace.rawPreview
    : "";
  const previewSrc = ready && generation
    ? `${smartUiFrameSrc(wid, generation.id)}&r=${reloadKey}`
    : "";

  return (
    <div className="studio-grid">
      <aside className="studio-left">
        <Card
          title="描述你想要的视图"
          sub="模型只提议蓝图，平台负责裁决与编译；不会执行模型生成的代码。"
          aside={
            <div className="btn-row">
              <Tag kind={modelState.ok ? "ok" : "warn"}>{modelState.text}</Tag>
              <Btn variant="subtle" size="sm" busy={checking} onClick={() => void checkModel()}>
                <CheckCircle size={13} /> 检查模型
              </Btn>
            </div>
          }
        >
          <Field label="业务问题" hint={`${question.trim().length}/500 字`}>
            <Area
              rows={5}
              value={question}
              maxLength={500}
              placeholder="例如：把收费站状态做成值班总览，突出拥堵和空闲"
              onChange={(event) => setQuestion(event.target.value)}
            />
          </Field>
          <div className="studio-examples">
            {EXAMPLES.map((item) => (
              <button
                key={item}
                type="button"
                className="studio-example"
                onClick={() => setQuestion(item)}
              >
                {item}
              </button>
            ))}
          </div>
          <div className="studio-form-row">
            <Field label="查看角色" hint="角色决定哪些动作可执行">
              <Select value={role} onChange={(event) => setRole(event.target.value)}>
                {roleOptions.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}（{item.id}）
                  </option>
                ))}
              </Select>
            </Field>
          </div>
          <Field
            label="数据范围（可选）"
            hint={focus ? `${focus.objectType} · 按 ${focus.key} 定位` : "不选则使用平台默认焦点"}
          >
            <div className="studio-focus">
              <div className="studio-focus-current">
                {focus ? (
                  <>
                    <span className="studio-focus-chip">
                      <b>{focus.objectType}</b>
                      <span className="mono">{focus.key}</span>
                      <span>{focus.value}</span>
                    </span>
                    <button type="button" className="studio-link" onClick={() => setFocus(null)}>
                      清空
                    </button>
                  </>
                ) : (
                  <span className="muted small">
                    平台默认焦点
                    {meta.focus?.value ? `：${meta.focus.value}` : "（由工作空间数据决定）"}
                  </span>
                )}
              </div>
              <TextInput
                value={entityQuery}
                aria-label="搜索数据范围实例"
                placeholder={`搜索${focusObject ? ` ${focusObject} ` : ""}实例值或名称`}
                onChange={(event) => setEntityQuery(event.target.value)}
              />
              <div className="studio-focus-options" role="listbox" aria-label="数据范围实例">
                <button
                  type="button"
                  role="option"
                  aria-selected={!focus}
                  className={`studio-focus-option ${!focus ? "is-active" : ""}`}
                  onClick={() => setFocus(null)}
                >
                  <span>平台默认焦点</span>
                  <span className="studio-focus-option-label">不限制到具体实例</span>
                </button>
                {entities.map((item) => {
                  const selected = focus?.objectType === item.objectType
                    && focus?.key === item.key && focus?.value === item.value;
                  return (
                    <button
                      key={`${item.objectType}:${item.key}:${item.value}`}
                      type="button"
                      role="option"
                      aria-selected={selected}
                      className={`studio-focus-option ${selected ? "is-active" : ""}`}
                      onClick={() => setFocus(item)}
                    >
                      <span className="mono">{item.value}</span>
                      {item.label && item.label !== item.value ? (
                        <span className="studio-focus-option-label">{item.label}</span>
                      ) : null}
                    </button>
                  );
                })}
              </div>
              {loadingEntities ? <p className="muted small">正在加载实例…</p> : null}
              {!loadingEntities && !entities.length ? (
                <p className="studio-note">
                  {entityQuery.trim()
                    ? "没有匹配的实例；换个关键字，或使用平台默认焦点。"
                    : "当前工作空间没有可用实例；生成仍会使用平台默认焦点。"}
                </p>
              ) : null}
              {!loadingEntities && entityTotal > entities.length ? (
                <p className="muted small">已显示 {entities.length}/{entityTotal} 个，输入关键字缩小范围。</p>
              ) : null}
            </div>
          </Field>
          <Field
            label="生成方式"
            hint={mode === "rule"
              ? "不调用模型：用规则包 + 本体事实决定目标与视角"
              : mode === "deterministic"
                ? "跳过模型与规则，直接用 L3 关键词规划"
                : "有模型时先由模型提议，失败/不可用时自动回退"}
          >
            <div className="studio-mode" role="radiogroup" aria-label="生成方式">
              {([
                ["auto", "自动", "模型优先，失败回退"],
                ["rule", "规则生成", "无模型调用"],
                ["deterministic", "确定性", "仅关键词规划"],
              ] as const).map(([id, label, desc]) => (
                <button
                  key={id}
                  type="button"
                  role="radio"
                  aria-checked={mode === id}
                  className={`studio-mode-option ${mode === id ? "is-active" : ""}`}
                  onClick={() => setMode(id)}
                >
                  <b>{label}</b>
                  <span>{desc}</span>
                </button>
              ))}
            </div>
          </Field>
          {mode === "rule" ? (
            <Field
              label="规则包"
              hint={rulePacks.length
                ? "规则包只声明目标/视角/权重/标题，不产生布局、样式或脚本"
                : "规则包列表不可用；将使用内置默认规则包"}
            >
              <Select
                value={effectiveRulePack}
                onChange={(event) => setRulePackId(event.target.value)}
              >
                <option value="">内置默认规则包</option>
                {rulePacks.filter((item) => item.id !== "onto.rules.default").map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name} · {item.version}（{item.status}，{item.ruleCount} 条）
                  </option>
                ))}
              </Select>
              <p className="studio-note">
                规则引擎是纯函数：命中/跳过/剪除都会写入生成轨迹，最终仍由同一套 L4 编译器生成程序。
              </p>
            </Field>
          ) : null}
          <Btn onClick={() => void startGeneration()} busy={creating} disabled={!question.trim()}>
            <Sparkle size={14} /> {mode === "rule" ? "按规则生成" : "生成视图"}
          </Btn>
          {!willUseModel ? (
            <p className="studio-note">
              当前工作空间没有可用模型：生成会明确标记为「确定性回退」，仍可预览、保存与发布。
              配置模型后可点上方「检查模型」验证端点是否可用。
            </p>
          ) : null}
          {checkResult ? (
            <p className={`studio-note ${checkResult.ok ? "is-ok" : "is-warn"}`}>
              {checkResultText(checkResult)}
              {checkResult.ok && checkResult.responsePreview
                ? ` · 回复示例：${checkResult.responsePreview}`
                : ""}
            </p>
          ) : null}
        </Card>

        <Card title="最近生成" aside={
          <button type="button" className="studio-link" onClick={onOpenHistory}>
            全部记录
          </button>
        }>
          {recent.length ? (
            <ul className="studio-recent">
              {recent.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    className={`studio-recent-item ${item.id === generation?.id ? "is-active" : ""}`}
                    onClick={() => onSelect(item.id)}
                  >
                    <span className="studio-recent-title">{item.question}</span>
                    <span className="studio-recent-meta">
                      <Tag kind={statusTagKind(item.status)}>
                        {STATUS_LABEL[item.status] || item.status}
                      </Tag>
                      <span className="studio-recent-mode">
                        {MODE_LABEL[item.mode] || item.mode}
                      </span>
                      <span className="mono">v{item.version}</span>
                      <span>{timeOf(item.created_at)}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="studio-empty-text">还没有生成记录。写下第一个业务问题。</p>
          )}
        </Card>
      </aside>

      <section className="studio-center">
        <Card
          title={generation?.blueprint?.displayName || "生成预览"}
          sub={
            ready
              ? `${MODE_LABEL[generation.mode] || generation.mode} · L0 → L4 确定性编译 · 预览使用当前真实数据`
              : "生成完成后在这里挂载隔离 iframe 预览；刷新不会重新规划布局。"
          }
          aside={
            generation ? (
              <div className="studio-preview-tags">
                <Tag>{`v${generation.version}`}</Tag>
                {generation.parent_id ? <Tag kind="plain">迭代版本</Tag> : null}
                <Tag kind={generation.mode === "fallback" ? "warn" : "plain"}>
                  {MODE_LABEL[generation.mode] || generation.mode}
                </Tag>
                {generation.published_application_id ? <Tag kind="ok">已发布</Tag> : null}
              </div>
            ) : null
          }
          tight={ready}
        >
          {!generationId ? (
            <div className="studio-placeholder">
              <Empty
                title="还没有生成视图"
                hint="在左侧输入业务问题，或点选一个示例。生成台会先让模型提议蓝图，再由平台校验对象、视角、动作与数据范围，最后交给确定性编译器。"
              />
            </div>
          ) : loading && !generation ? (
            <Skeleton rows={8} />
          ) : loadError && !generation ? (
            <div className="studio-placeholder">
              <ErrorNote text={loadError} onRetry={() => setPollTick((tick) => tick + 1)} />
            </div>
          ) : running ? (
            <div className="studio-running">
              <div className="studio-running-head">
                <CircleNotch size={18} className="studio-spin" />
                <div>
                  <b>{generation?.status === "queued" ? "已排队，等待生成" : "正在生成视图"}</b>
                  <span>背景线程执行上下文、提议、裁决与编译；完成后自动加载预览。</span>
                </div>
                <Btn variant="subtle" size="sm" busy={cancelling}
                     onClick={() => void cancelGeneration()}>
                  <StopCircle size={13} /> 取消
                </Btn>
              </div>
              {generation ? <StepList generation={generation} /> : <Skeleton rows={4} />}
            </div>
          ) : generation?.status === "failed" || generation?.status === "interrupted"
              || generation?.status === "cancelled" ? (
            <div className="studio-placeholder">
              <ErrorNote
                text={
                  generation.status === "failed"
                    ? `生成失败：${generation.error || "未知错误"}。失败版本不会渲染预览；可以重试，或改用确定性回退。`
                    : generation.status === "cancelled"
                      ? "这次生成已取消；记录保留在历史里，可以按原问题重新生成。"
                      : "上次生成被中断（服务重启或任务丢失）；可以重试，或改用确定性回退。"
                }
                onRetry={() => void retryGeneration("auto")}
              />
              <div className="btn-row">
                <Btn size="sm" busy={creating} onClick={() => void retryGeneration("auto")}>
                  <ArrowsClockwise size={13} /> 重试（自动）
                </Btn>
                <Btn size="sm" variant="subtle" busy={creating}
                     onClick={() => void retryGeneration("deterministic")}>
                  重试（确定性）
                </Btn>
              </div>
              <StepList generation={generation} />
            </div>
          ) : ready && generation ? (
            <div className="studio-preview">
              <div className="studio-preview-bar">
                <span className="mono">
                  {generation.object || "—"} · {generation.view_count} 个视角 ·{" "}
                  {(generation.trace.programHash as string || "").replace("sha256:", "").slice(0, 12) || "—"}
                </span>
                {generation.focus ? (
                  <Tag kind="plain">
                    当前数据范围：{generation.focus.key}={generation.focus.value}
                  </Tag>
                ) : null}
                <div className="btn-row">
                  <Btn variant="subtle" size="sm" onClick={() => setReloadKey((key) => key + 1)}>
                    <ArrowsClockwise size={13} /> 刷新预览
                  </Btn>
                  <Btn
                    variant="subtle"
                    size="sm"
                    onClick={() => window.open(smartUiStandaloneSrc(wid, generation.id), "_blank", "noopener,noreferrer")}
                  >
                    <ArrowSquareOut size={13} /> 新窗口
                  </Btn>
                </div>
              </div>
              <iframe
                key={`${generation.id}-${reloadKey}`}
                ref={frameRef}
                className="smartui-frame studio-frame"
                title="AI 生成的智能视图"
                src={previewSrc}
                onLoad={postAuthToken}
                sandbox="allow-scripts allow-forms allow-popups"
              />
            </div>
          ) : null}
        </Card>

        {generation && (generation.ruleTrace || generation.clarify?.length) ? (
          <RuleTracePanel
            trace={generation.ruleTrace}
            clarify={generation.clarify}
            onUseQuestion={(next) => setQuestion(next)}
          />
        ) : null}

        {ready && generation ? (
          <Card title="继续迭代" sub="新版本继承当前蓝图；每次修改都生成一条可追溯记录，旧版本不受影响。">
            <div className="studio-refine">
              <Area
                rows={2}
                value={refine}
                maxLength={500}
                placeholder="例如：只看拥堵站点，并把状态放在第一屏"
                onChange={(event) => setRefine(event.target.value)}
              />
              <Btn onClick={() => void refineGeneration()} busy={creating} disabled={!refine.trim()}>
                继续修改
              </Btn>
            </div>
          </Card>
        ) : null}
      </section>

      <aside className="studio-right">
        <Card
          title="生成过程"
          aside={<Tag kind={statusTagKind(generation?.status || "queued")}>
            {STATUS_LABEL[generation?.status || "queued"]}
          </Tag>}
        >
          {generation ? <StepList generation={generation} /> : <p className="studio-empty-text">生成后显示上下文、提议、裁决、编译五个步骤。</p>}
          {generation ? (
            <div className="studio-metrics">
              <span>
                <b>模式</b>
                <span>
                  {MODE_LABEL[generation.mode] || generation.mode}
                  {generation.requested_mode && generation.requested_mode !== generation.mode
                    ? `（请求 ${generation.requested_mode}）`
                    : ""}
                </span>
              </span>
              <span><b>总耗时</b><span className="mono">{generation.metrics.totalMs != null ? `${generation.metrics.totalMs} ms` : "—"}</span></span>
              <span><b>编译</b><span className="mono">{generation.metrics.compileMs != null ? `${generation.metrics.compileMs} ms` : "—"}</span></span>
              <span><b>Prompt</b><span className="mono">{generation.trace.promptBytes ? `${generation.trace.promptBytes} B` : "—"}</span></span>
              <span><b>Program</b><span className="mono">{(generation.trace.programHash as string || "—").replace("sha256:", "").slice(0, 10)}</span></span>
              <span><b>引擎</b><span className="mono">{String(generation.trace.engineVersion || "—")}</span></span>
              <span><b>本体</b><span className="mono">{String(generation.trace.ontologyVersion || "—")}</span></span>
              <span title="输入 / 输出 / 合计">
                <b>Tokens</b>
                <span className="mono">
                  {usage ? `${usage.prompt} / ${usage.completion} / ${usage.total}` : "—"}
                </span>
              </span>
              <span>
                <b>成本</b>
                <span
                  className="mono"
                  title={cost
                    ? "按部署配置的费率估算"
                    : "未配置 ONTO_SMARTUI_INPUT_COST_PER_1K / ONTO_SMARTUI_OUTPUT_COST_PER_1K"}
                >
                  {costText}
                </span>
              </span>
              {generation.rule_pack_id ? (
                <span>
                  <b>规则包</b>
                  <span className="mono" title={generation.rule_pack_hash || ""}>
                    {generation.rule_pack_id}
                    {generation.rule_pack_hash
                      ? ` · ${generation.rule_pack_hash.replace("sha256:", "").slice(0, 8)}`
                      : ""}
                  </span>
                </span>
              ) : null}
              {generation.focus ? (
                <span>
                  <b>数据范围</b>
                  <span className="mono">{generation.focus.key}={generation.focus.value}</span>
                </span>
              ) : null}
            </div>
          ) : null}
        </Card>

        {generation && (generation.status === "failed" || generation.status === "interrupted") ? (
          <Card
            title="失败诊断"
            sub="失败 / 中断的任务不会渲染预览；这里保留错误原文、模型信息与可执行的排查建议。"
            aside={<Tag kind="err">{STATUS_LABEL[generation.status]}</Tag>}
          >
            <div className="studio-failure">
              <div className="studio-failure-row">
                <b>错误</b>
                <span>{generation.error || generation.validation?.error || "任务未返回错误信息"}</span>
              </div>
              <div className="studio-failure-row">
                <b>模型</b>
                <span className="mono">
                  {generation.model || "—"}
                  {generation.provider ? `（${generation.provider}）` : ""}
                </span>
              </div>
              {generation.trace?.errorType ? (
                <div className="studio-failure-row">
                  <b>错误类型</b>
                  <span className="mono">{String(generation.trace.errorType)}</span>
                </div>
              ) : null}
              {generation.trace?.status !== null && generation.trace?.status !== undefined
                  && generation.trace?.status !== "" ? (
                <div className="studio-failure-row">
                  <b>HTTP 状态</b>
                  <span className="mono">{String(generation.trace.status)}</span>
                </div>
              ) : null}
            </div>
            {rawPreview ? (
              <details className="studio-raw-preview">
                <summary>模型原始输出（截断）</summary>
                <pre className="mono">{rawPreview}</pre>
              </details>
            ) : null}
            {generation.validation?.checks?.length ? (
              <div className="studio-failure-checks">
                <span className="studio-failure-label">校验明细</span>
                <ValidationList generation={generation} />
              </div>
            ) : null}
            <div className="studio-failure-hints">
              <span className="studio-failure-label">可以这样处理</span>
              <ul>
                {failureHints(generation).map((hint) => <li key={hint}>{hint}</li>)}
              </ul>
            </div>
          </Card>
        ) : null}

        {generation ? (
          <Card
            title="相对上一版"
            sub="只比较蓝图结构（对象 / 目标 / 数据范围 / 视角 / 动作 / 标题），不代表预览像素差异。"
            aside={
              generation.diff ? (
                <Tag kind={generation.diff.changed ? "warn" : "plain"}>
                  {generation.diff.changed ? `${generation.diff.changes.length} 处变化` : "无结构变化"}
                </Tag>
              ) : null
            }
          >
            <LineageChain
              lineage={generation.lineage ?? []}
              currentId={generation.id}
              onSelect={onSelect}
            />
            {generation.diff ? (
              generation.diff.changed ? (
                <ul className="studio-diff">
                  {generation.diff.changes.map((change, index) => {
                    const { text, tone } = diffChangeText(change);
                    return (
                      <li
                        key={`${change.kind}:${change.key}:${index}`}
                        className={`studio-diff-row is-${tone}`}
                      >
                        <Tag kind="plain">{CHANGE_KIND_LABEL[change.kind] || change.kind}</Tag>
                        <span className="mono">{change.key}</span>
                        <span className="studio-diff-text">{text}</span>
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <p className="studio-note">相对上一版无结构变化（蓝图结构与上一版相同）。</p>
              )
            ) : (
              <p className="studio-note">
                {generation.parent_id
                  ? "上一版没有可比较的蓝图（可能生成失败或未完成）。"
                  : "这是版本链的第一版；继续修改后会在这里显示相对上一版的变化。"}
              </p>
            )}
          </Card>
        ) : null}

        {generation ? (
          <Card
            title="平台裁决"
            sub="模型提议的对象、视角、动作与焦点都要通过校验；不合法项被剪除且留下原因。"
            aside={<Tag kind={generation.validation.ok ? "ok" : "err"}>
              {generation.validation.ok ? "通过" : "未通过"}
            </Tag>}
          >
            <ValidationList generation={generation} />
          </Card>
        ) : null}

        {ready && generation ? (
          <Card title="保存与发布">
            <div className="studio-actions">
              <Btn variant="subtle" onClick={() => void saveDraft()}>
                <FloppyDisk size={14} /> 保存草稿
              </Btn>
              <Btn onClick={openPublish} disabled={Boolean(currentDraft?.status === "published" && generation.published_application_id)}>
                <PaperPlaneTilt size={14} /> {currentDraft?.status === "published" ? "已发布" : "发布到应用"}
              </Btn>
            </div>
            <div className="studio-draft-meta">
              <span>草稿版本</span>
              <b className="mono">{currentDraft ? `v${currentDraft.version}` : "未保存"}</b>
              <span>发布状态</span>
              <b>{currentDraft?.status === "published" ? `应用 ${currentDraft.application_id}` : "未发布"}</b>
            </div>
            {currentDraft?.status === "published" && currentDraft.application_id ? (
              <button
                type="button"
                className="studio-link"
                onClick={() => go("application", { app: currentDraft.application_id ?? "" })}
              >
                打开已发布应用
              </button>
            ) : null}
          </Card>
        ) : null}

        {generation && generation.trace.model ? (
          <Card title="审计指纹" sub="同样的 prompt 与本体快照可复现同一 program hash。">
            <div className="studio-fingerprint">
              <span><b>模型</b><span className="mono">{String(generation.trace.model)}</span></span>
              <span><b>Provider</b><span className="mono">{String(generation.trace.provider || "—")}</span></span>
              <span><b>Prompt hash</b><span className="mono">{String(generation.trace.promptHash || "—")}</span></span>
              <span><b>Program hash</b><span className="mono">{String(generation.trace.programHash || "—")}</span></span>
            </div>
          </Card>
        ) : null}
      </aside>

      {publishOpen && generation ? (
        <Drawer
          title="发布到应用"
          sub={`发布当前版本 v${generation.version} 的生成快照；应用里的声明式视图 / 查询不变，只附加 ui_artifact。`}
          onClose={() => setPublishOpen(false)}
          footer={
            <div className="btn-row">
              <Btn busy={publishing} onClick={() => void doPublish()}>
                <PaperPlaneTilt size={14} /> 发布
              </Btn>
              <Btn variant="subtle" onClick={() => setPublishOpen(false)}>取消</Btn>
            </div>
          }
        >
          <div className="studio-publish-targets">
            <label className={`studio-publish-target ${publishTarget === "new" ? "is-active" : ""}`}>
              <input type="radio" name="publish-target" checked={publishTarget === "new"}
                     onChange={() => setPublishTarget("new")} />
              <span>
                <b>新建应用</b>
                <span>发布时自动创建一个草稿应用，只带 ui_artifact 快照。</span>
              </span>
            </label>
            <label className={`studio-publish-target ${publishTarget === "existing" ? "is-active" : ""}`}>
              <input type="radio" name="publish-target" checked={publishTarget === "existing"}
                     onChange={() => setPublishTarget("existing")} />
              <span>
                <b>已有草稿应用</b>
                <span>把快照附加到一个已有草稿应用（已发布应用不可变，不能选）。</span>
              </span>
            </label>
          </div>
          {publishTarget === "new" ? (
            <Field label="应用名称" hint="留空时使用生成蓝图的名称。">
              <TextInput value={publishName} maxLength={64}
                         onChange={(event) => setPublishName(event.target.value)} />
            </Field>
          ) : (
            <Field label="草稿应用"
                   hint={publishApps.length ? undefined : "当前工作空间没有可附加的草稿应用；可以改回“新建应用”。"}>
              <Select value={publishAppId} onChange={(event) => setPublishAppId(event.target.value)}>
                <option value="">选择一个草稿应用…</option>
                {publishApps.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}（{item.lifecycle}）
                  </option>
                ))}
              </Select>
            </Field>
          )}
          <p className="studio-note">
            发布后应用保持草稿状态；已发布应用不可就地改，需要线上生效时先克隆为草稿再发布。
          </p>
        </Drawer>
      ) : null}
    </div>
  );
}

interface HistoryProps {
  wid: string;
  onOpen: (gid: string) => void;
}

type HistoryFilter = "all" | "succeeded" | "failed" | "fallback" | "cancelled" | "interrupted";

const HISTORY_FILTERS: { key: HistoryFilter; label: string; hint: string }[] = [
  { key: "all", label: "全部", hint: "不按状态过滤" },
  { key: "succeeded", label: "成功", hint: "生成成功（模型生成或确定性回退）" },
  { key: "failed", label: "失败", hint: "生成失败，可在详情里看失败诊断" },
  { key: "fallback", label: "回退", hint: "模型不可用时改用确定性回退（按 mode=fallback 服务端过滤）" },
  { key: "cancelled", label: "取消", hint: "用户取消的生成" },
  { key: "interrupted", label: "中断", hint: "服务重启导致中断" },
];

export function SmartUIGenerationHistory({ wid, onOpen }: HistoryProps) {
  const { go, toast, confirm } = usePlatform();
  const [items, setItems] = useState<UiGenerationSummary[]>([]);
  const [drafts, setDrafts] = useState<UiDraft[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<HistoryFilter>("all");
  const [query, setQuery] = useState("");
  const [objectFilter, setObjectFilter] = useState("");
  const [applied, setApplied] = useState({ q: "", object: "" });
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState<10 | 20>(10);
  const [total, setTotal] = useState(0);
  const [objectOptions, setObjectOptions] = useState<string[]>([]);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [draftBusy, setDraftBusy] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  // 搜索 / 对象输入防抖，避免每敲一个字都打接口。
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setApplied({ q: query.trim(), object: objectFilter.trim() });
    }, 300);
    return () => window.clearTimeout(timer);
  }, [query, objectFilter]);

  // 过滤条件变化后回到第一页。
  useEffect(() => {
    setPage(0);
  }, [filter, applied.q, applied.object, pageSize]);

  // 对象筛选项：meta 里的对象类型 + 已出现过的对象。
  useEffect(() => {
    let cancelled = false;
    api.smartUiMeta(wid)
      .then((meta) => {
        if (cancelled) return;
        setObjectOptions((prev) => {
          const merged = new Set(prev);
          (meta.objectTypes || []).forEach((object) => merged.add(object));
          return [...merged].sort();
        });
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [wid]);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // 「回退」是 mode=fallback 的服务端过滤，其余是 status 精确过滤。
      const list = await api.smartUiGenerations(wid, {
        limit: pageSize,
        offset: page * pageSize,
        q: applied.q || undefined,
        object: applied.object || undefined,
        status: filter === "all" || filter === "fallback" ? undefined : filter,
        mode: filter === "fallback" ? "fallback" : undefined,
      });
      const maxPage = Math.max(0, Math.ceil(list.total / pageSize) - 1);
      const safePage = Math.min(page, maxPage);
      if (safePage !== page) setPage(safePage);
      const nextItems = list.items;
      setItems(nextItems);
      setTotal(list.total);
      setObjectOptions((prev) => {
        const merged = new Set(prev);
        nextItems.forEach((item) => {
          if (item.object) merged.add(item.object);
        });
        return [...merged].sort();
      });
      const draftList = await api.smartUiDrafts(wid);
      setDrafts(draftList.items);
    } catch (err) {
      setError((err as ApiError).message || String(err));
    } finally {
      setLoading(false);
    }
  }, [wid, filter, applied.q, applied.object, page, pageSize]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const hasNext = (page + 1) * pageSize < total;
  const firstIndex = total ? page * pageSize + 1 : 0;
  const lastIndex = Math.min(total, (page + 1) * pageSize);

  const describeDraftError = (err: unknown): string => {
    const message = (err as ApiError).message || String(err);
    if (/published draft/i.test(message)) {
      return "已发布的草稿属于应用契约，不能删除；如需调整请在应用侧处理。";
    }
    if (/draft name must not be empty/i.test(message)) return "草稿名称不能为空。";
    if (/draft not found/i.test(message)) return "草稿不存在或已被删除，刷新后再试。";
    return message;
  };

  const startRename = (draft: UiDraft) => {
    setRenamingId(draft.id);
    setRenameValue(draft.name);
  };

  const describeGenerationError = (err: unknown): string => {
    const message = (err as ApiError).message || String(err);
    if (/still running|cancel it first/i.test(message)) {
      return "这条生成还在排队 / 运行中：先在生成台取消，再删除。";
    }
    if (/referenced by a published draft|archive the application/i.test(message)) {
      return "这条生成被已发布草稿引用：需要先在应用侧归档 / 处理，才能删除。";
    }
    if (/generation not found|not found/i.test(message)) {
      return "生成记录不存在或已被删除，刷新后再试。";
    }
    return message;
  };

  const removeGeneration = (item: UiGenerationSummary) => confirm({
    title: `删除生成记录 v${item.version}？`,
    danger: true,
    confirmText: "确认删除",
    body: (
      <p className="muted">
        会删除这条生成记录，以及它关联的未发布草稿快照（若有）。运行中或已被已发布草稿引用的记录不能删除。
        这个操作不可撤销。
      </p>
    ),
    run: async () => {
      setDeletingId(item.id);
      try {
        const result = await api.smartUiDeleteGeneration(wid, item.id);
        toast(result.drafts
          ? `已删除生成记录，并连带删除 ${result.drafts} 份未发布草稿`
          : "已删除生成记录", "ok");
        void refresh();
      } catch (err) {
        toast(describeGenerationError(err), "err");
      } finally {
        setDeletingId(null);
      }
    },
  });

  const saveRename = async (draft: UiDraft) => {
    const name = renameValue.trim();
    if (!name) {
      toast("草稿名称不能为空", "warn");
      return;
    }
    setDraftBusy(draft.id);
    try {
      await api.smartUiRenameDraft(wid, draft.id, { name });
      toast(`草稿已重命名为「${name}」`, "ok");
      setRenamingId(null);
      void refresh();
    } catch (err) {
      toast(describeDraftError(err), "err");
    } finally {
      setDraftBusy(null);
    }
  };

  const removeDraft = (draft: UiDraft) => confirm({
    title: `删除草稿「${draft.name}」？`,
    danger: true,
    confirmText: "确认删除",
    body: (
      <p className="muted">
        只删除这条草稿快照；生成记录仍保留，可以重新保存为草稿。已发布的草稿属于应用契约，不能删除。
        这个操作不可撤销。
      </p>
    ),
    run: async () => {
      setDraftBusy(draft.id);
      try {
        await api.smartUiDeleteDraft(wid, draft.id);
        toast("草稿已删除", "ok");
        void refresh();
      } catch (err) {
        toast(describeDraftError(err), "err");
      } finally {
        setDraftBusy(null);
      }
    },
  });

  return (
    <div className="studio-history">
      <Card
        title="生成记录"
        sub="每一条记录保留提问、裁决摘要、prompt/program 指纹与版本链；点开可回到生成台继续查看或迭代。"
        aside={
          <div className="btn-row">
            <span className="muted small">{total ? `共 ${total} 条` : "暂无记录"}</span>
            <Btn variant="subtle" onClick={() => void refresh()} busy={loading}>
              <ArrowsClockwise size={14} /> 刷新
            </Btn>
          </div>
        }
        tight
      >
        <div className="studio-history-tools">
          <TextInput
            className="studio-history-search"
            value={query}
            placeholder="搜索提问 / 修改要求 / 记录 ID"
            aria-label="搜索生成记录"
            onChange={(event) => setQuery(event.target.value)}
          />
          <div className="segmented studio-history-status" role="tablist" aria-label="按状态筛选">
            {HISTORY_FILTERS.map(({ key, label, hint }) => (
              <button
                key={key}
                type="button"
                role="tab"
                title={hint}
                aria-selected={filter === key}
                className={filter === key ? "is-active" : ""}
                onClick={() => setFilter(key)}
              >
                {label}
              </button>
            ))}
          </div>
          <TextInput
            value={objectFilter}
            list="studio-history-objects"
            placeholder="按对象筛选（可输入）"
            aria-label="按对象筛选"
            onChange={(event) => setObjectFilter(event.target.value)}
          />
          <datalist id="studio-history-objects">
            {objectOptions.map((object) => <option key={object} value={object} />)}
          </datalist>
          <Select
            value={String(pageSize)}
            aria-label="每页条数"
            onChange={(event) => setPageSize(Number(event.target.value) === 20 ? 20 : 10)}
          >
            <option value="10">每页 10 条</option>
            <option value="20">每页 20 条</option>
          </Select>
        </div>
        {error ? <ErrorNote text={error} onRetry={() => void refresh()} /> : null}
        {loading && !items.length ? <Skeleton rows={6} /> : null}
        {!loading && !items.length && !error ? (
          <div className="studio-history-empty">
            <Empty
              title="没有匹配的生成记录"
              hint={applied.q || applied.object || filter !== "all"
                ? "换个关键词 / 状态 / 对象再试，或清空筛选。"
                : "在 AI 生成台写一个业务问题，生成记录会出现在这里。"}
            />
          </div>
        ) : null}
        {items.length ? (
          <TableWrap>
            <table className="studio-history-table">
              <thead>
                <tr>
                  <th>时间</th>
                  <th>提问</th>
                  <th>修改要求</th>
                  <th>版本</th>
                  <th>状态</th>
                  <th>模式</th>
                  <th>对象 / 视角</th>
                  <th>Program</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => {
                  const itemRunning = item.status === "queued" || item.status === "running";
                  const itemPublished = Boolean(item.published_application_id);
                  const busy = deletingId === item.id;
                  return (
                    <tr key={item.id} className="studio-history-row" onClick={() => onOpen(item.id)}>
                      <td className="mono">{timeOf(item.created_at)}</td>
                      <td className="studio-history-question">{item.question}</td>
                      <td className="studio-history-instruction">
                        {item.instruction ? item.instruction : <span className="muted">—</span>}
                      </td>
                      <td className="mono">v{item.version}{item.parent_id ? " · 迭代" : ""}</td>
                      <td>
                        <Tag kind={statusTagKind(item.status)}>
                          {STATUS_LABEL[item.status] || item.status}
                        </Tag>
                      </td>
                      <td>
                        {MODE_LABEL[item.mode] || item.mode}
                        {item.model ? <span className="studio-row-sub mono">{item.model}</span> : null}
                        {item.cost ? (
                          <span className="studio-row-sub mono" title={formatCost(item.cost)}>
                            {formatCostAmount(item.cost)}
                          </span>
                        ) : null}
                      </td>
                      <td>
                        {item.object || "—"}
                        {item.view_count ? <span className="studio-row-sub">{item.view_count} 个视角</span> : null}
                        {item.focus ? (
                          <span className="studio-row-sub studio-row-focus">数据范围 {focusText(item.focus)}</span>
                        ) : null}
                      </td>
                      <td className="mono">{(item.program_hash || "—").replace("sha256:", "").slice(0, 10)}</td>
                      <td>
                        <div className="studio-row-actions">
                          <button
                            type="button"
                            className="studio-link"
                            onClick={(event) => {
                              event.stopPropagation();
                              onOpen(item.id);
                            }}
                          >
                            打开
                          </button>
                          <span
                            className="studio-row-action-hint"
                            title={itemRunning
                              ? "运行中：先取消再删除"
                              : itemPublished
                                ? "已被发布草稿引用：需先在应用侧处理"
                                : "删除这条生成记录（不可撤销）"}
                          >
                            <button
                              type="button"
                              className="studio-link is-danger"
                              aria-label="删除生成记录"
                              disabled={busy || itemRunning || itemPublished}
                              onClick={(event) => {
                                event.stopPropagation();
                                removeGeneration(item);
                              }}
                            >
                              {busy ? "删除中…" : "删除"}
                            </button>
                          </span>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </TableWrap>
        ) : null}
        <div className="studio-pager">
          <span className="muted">
            {total ? `共 ${total} 条 · 第 ${firstIndex}-${lastIndex} 条` : "共 0 条"}
          </span>
          <div className="btn-row">
            <Btn
              size="sm"
              variant="subtle"
              disabled={page === 0}
              onClick={() => setPage((value) => Math.max(0, value - 1))}
            >
              上一页
            </Btn>
            <Btn
              size="sm"
              variant="subtle"
              disabled={!hasNext}
              onClick={() => setPage((value) => value + 1)}
            >
              下一页
            </Btn>
          </div>
        </div>
      </Card>

      <Card title="草稿与已发布" sub="Studio 的保存是草稿快照；发布后只给应用附加 ui_artifact，不改声明式视图。">
        {drafts.length ? (
          <div className="studio-draft-list">
            {drafts.map((draft) => {
              const published = draft.status === "published";
              const renaming = renamingId === draft.id;
              const busy = draftBusy === draft.id;
              return (
                <div key={draft.id} className="studio-draft-row">
                  <div className="studio-draft-main">
                    {renaming ? (
                      <div className="studio-draft-rename">
                        <TextInput
                          value={renameValue}
                          maxLength={64}
                          autoFocus
                          aria-label="草稿名称"
                          placeholder="草稿名称"
                          onChange={(event) => setRenameValue(event.target.value)}
                          onKeyDown={(event) => {
                            if (event.key === "Enter") void saveRename(draft);
                            if (event.key === "Escape") setRenamingId(null);
                          }}
                        />
                        <Btn size="sm" busy={busy} disabled={!renameValue.trim()}
                             onClick={() => void saveRename(draft)}>
                          保存
                        </Btn>
                        <button type="button" className="studio-link"
                                onClick={() => setRenamingId(null)}>
                          取消
                        </button>
                      </div>
                    ) : (
                      <>
                        <b>{draft.name}</b>
                        <span className="mono">
                          v{draft.version} · {draft.program_hash?.replace("sha256:", "").slice(0, 10) || "—"}
                        </span>
                      </>
                    )}
                  </div>
                  <div className="studio-draft-side">
                    <Tag kind={published ? "ok" : "plain"}>{published ? "已发布" : "草稿"}</Tag>
                    <span>{timeOf(draft.updated_at)}</span>
                    {draft.application_id ? (
                      <button
                        type="button"
                        className="studio-link"
                        onClick={() => go("application", { app: draft.application_id ?? "" })}
                      >
                        打开应用
                      </button>
                    ) : null}
                    {!renaming ? (
                      <>
                        <button type="button" className="studio-link" disabled={busy}
                                onClick={() => startRename(draft)}>
                          重命名
                        </button>
                        <button
                          type="button"
                          className="studio-link"
                          disabled={busy || published}
                          title={published
                            ? "已发布草稿属于应用契约，不能删除"
                            : "删除这条草稿快照（不可撤销）"}
                          onClick={() => removeDraft(draft)}
                        >
                          删除
                        </button>
                      </>
                    ) : null}
                    {published ? <span className="muted small">已发布，需在应用侧处理</span> : null}
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <p className="studio-empty-text">还没有草稿。生成并保存后会出现在这里。</p>
        )}
      </Card>
    </div>
  );
}

export function studioStats(meta: SmartUiMeta): { label: string; value: string | number; foot: string }[] {
  const total = Object.values(meta.counts ?? {}).reduce((sum, n) => sum + n, 0);
  return [
    { label: "工作空间对象", value: total, foot: `${meta.objectTypes.length} 个对象类型` },
    { label: "可执行动作", value: meta.actions.length, foot: `${meta.capabilities.length} 个能力` },
    { label: "默认问题", value: meta.defaultQuestion, foot: "生成台预填" },
  ];
}

export function StudioStatRow({ meta }: { meta: SmartUiMeta }) {
  return (
    <div className="stat-row smartui-stats">
      {studioStats(meta).map((stat) => (
        <Stat key={stat.label} label={stat.label} value={stat.value} foot={stat.foot} />
      ))}
    </div>
  );
}
