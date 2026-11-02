import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowSquareOut, ArrowsClockwise, CheckCircle, Circle, CircleNotch,
  FloppyDisk, PaperPlaneTilt, Sparkle, WarningCircle, XCircle,
} from "@phosphor-icons/react";
import { api, type ApiError, type SmartUiMeta, type UiDraft, type UiGeneration, type UiGenerationSummary } from "../api";
import { usePlatform } from "../store";
import { smartUiFrameSrc, smartUiStandaloneSrc, useSmartUiBridge } from "../smartui-bridge";
import {
  Area, Btn, Card, Empty, ErrorNote, Field, Select, Skeleton, Stat, TableWrap, Tag,
} from "../ui";

const EXAMPLES = [
  "把收费站状态做成值班总览，突出拥堵和空闲",
  "给当前焦点车辆做画像与通行链路",
  "对比各收费站的拥堵与空闲情况",
  "生成收费站运营日报视图",
];

const MODE_LABEL: Record<string, string> = {
  llm: "模型生成",
  fallback: "确定性回退",
  deterministic: "确定性编译",
};

const STATUS_LABEL: Record<string, string> = {
  queued: "排队中",
  running: "生成中",
  succeeded: "已生成",
  failed: "失败",
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

interface StudioProps {
  wid: string;
  meta: SmartUiMeta;
  generationId: string | null;
  onSelect: (gid: string) => void;
  onOpenHistory: () => void;
}

export function SmartUIStudio({ wid, meta, generationId, onSelect, onOpenHistory }: StudioProps) {
  const { toast, confirm, go, llmReady, llm } = usePlatform();
  const [question, setQuestion] = useState(meta.defaultQuestion || EXAMPLES[0]);
  const [role, setRole] = useState(meta.roles.find((item) => item.id === meta.roles[0]?.id)?.id ?? "owner");
  const roleOptions = meta.roles.length ? meta.roles : [{ id: "owner", name: "空间所有者", glyph: "主" }];
  const modelLabel = llmReady
    ? llm.mode === "openai" ? llm.model || "自建模型"
      : llm.mode === "demo" ? "演示模型"
        : "平台默认模型"
    : "未配置";
  const [generation, setGeneration] = useState<UiGeneration | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [refine, setRefine] = useState("");
  const [recent, setRecent] = useState<UiGenerationSummary[]>([]);
  const [drafts, setDrafts] = useState<UiDraft[]>([]);
  const [pollTick, setPollTick] = useState(0);
  const [reloadKey, setReloadKey] = useState(0);
  const frameRef = useRef<HTMLIFrameElement>(null);
  const postAuthToken = useSmartUiBridge(frameRef, wid, reloadKey);

  const refreshSide = useCallback(async () => {
    try {
      const [list, draftList] = await Promise.all([
        api.smartUiGenerations(wid, 6),
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
          if (next.status === "succeeded" || next.status === "failed") {
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

  const startGeneration = async () => {
    if (!question.trim() || creating) return;
    setCreating(true);
    setLoadError(null);
    try {
      const created = await api.smartUiGenerate(wid, {
        question: question.trim(),
        roles: [role],
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
      const created = await api.smartUiRefine(wid, generation.id, refine.trim());
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

  const publish = () => {
    if (!generation) return;
    const title = generation.blueprint?.displayName || generation.blueprint?.name || "智能视图";
    confirm({
      title: "发布到应用",
      body: (
        <p>
          将当前版本 <b>v{generation.version}</b> 的生成快照发布到一个新的应用草稿。
          发布后应用里的 views/queries 不变，只附加 <code>ui_artifact</code> 快照；已发布应用保持不可变。
        </p>
      ),
      confirmText: "发布",
      run: async () => {
        const result = await api.smartUiPublish(wid, generation.id, { name: title });
        toast(`已发布到应用「${result.application.name}」`, "ok");
        void refreshSide();
        go("application", { app: result.application.id });
      },
    });
  };

  const currentDraft = drafts.find((draft) => draft.generation_id === generation?.id);
  const running = generation?.status === "queued" || generation?.status === "running";
  const ready = generation?.status === "succeeded";
  const previewSrc = ready && generation
    ? `${smartUiFrameSrc(wid, generation.id)}&r=${reloadKey}`
    : "";

  return (
    <div className="studio-grid">
      <aside className="studio-left">
        <Card
          title="描述你想要的视图"
          sub="模型只提议蓝图，平台负责裁决与编译；不会执行模型生成的代码。"
          aside={<Tag kind={llmReady ? "ok" : "warn"}>{llmReady ? `模型 · ${modelLabel}` : "模型未配置"}</Tag>}
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
          <Btn onClick={() => void startGeneration()} busy={creating} disabled={!question.trim()}>
            <Sparkle size={14} /> 生成视图
          </Btn>
          {!llmReady ? (
            <p className="studio-note">
              当前工作空间未配置模型：生成会明确标记为「确定性回退」，仍可预览、保存与发布。
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
                      <Tag kind={item.status === "succeeded" ? "ok" : item.status === "failed" ? "err" : "warn"}>
                        {STATUS_LABEL[item.status] || item.status}
                      </Tag>
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
                {generation.mode === "fallback" ? <Tag kind="warn">确定性回退</Tag> : null}
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
              </div>
              {generation ? <StepList generation={generation} /> : <Skeleton rows={4} />}
            </div>
          ) : generation?.status === "failed" ? (
            <div className="studio-placeholder">
              <ErrorNote
                text={`生成失败：${generation.error || "未知错误"}。请调整问题后重试；失败版本不会渲染预览。`}
                onRetry={() => void startGeneration()}
              />
              <StepList generation={generation} />
            </div>
          ) : ready && generation ? (
            <div className="studio-preview">
              <div className="studio-preview-bar">
                <span className="mono">
                  {generation.object || "—"} · {generation.view_count} 个视角 ·{" "}
                  {(generation.trace.programHash as string || "").replace("sha256:", "").slice(0, 12) || "—"}
                </span>
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
          aside={<Tag kind={ready ? "ok" : generation?.status === "failed" ? "err" : "plain"}>
            {STATUS_LABEL[generation?.status || "queued"]}
          </Tag>}
        >
          {generation ? <StepList generation={generation} /> : <p className="studio-empty-text">生成后显示上下文、提议、裁决、编译五个步骤。</p>}
          {generation ? (
            <div className="studio-metrics">
              <span><b>总耗时</b><span className="mono">{generation.metrics.totalMs != null ? `${generation.metrics.totalMs} ms` : "—"}</span></span>
              <span><b>编译</b><span className="mono">{generation.metrics.compileMs != null ? `${generation.metrics.compileMs} ms` : "—"}</span></span>
              <span><b>Prompt</b><span className="mono">{generation.trace.promptBytes ? `${generation.trace.promptBytes} B` : "—"}</span></span>
              <span><b>Program</b><span className="mono">{(generation.trace.programHash as string || "—").replace("sha256:", "").slice(0, 10)}</span></span>
              <span><b>引擎</b><span className="mono">{String(generation.trace.engineVersion || "—")}</span></span>
              <span><b>本体</b><span className="mono">{String(generation.trace.ontologyVersion || "—")}</span></span>
            </div>
          ) : null}
        </Card>

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
              <Btn onClick={publish} disabled={Boolean(currentDraft?.status === "published" && generation.published_application_id)}>
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
    </div>
  );
}

interface HistoryProps {
  wid: string;
  onOpen: (gid: string) => void;
}

export function SmartUIGenerationHistory({ wid, onOpen }: HistoryProps) {
  const { go } = usePlatform();
  const [items, setItems] = useState<UiGenerationSummary[]>([]);
  const [drafts, setDrafts] = useState<UiDraft[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<"all" | "llm" | "fallback" | "failed">("all");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [list, draftList] = await Promise.all([
        api.smartUiGenerations(wid, 100),
        api.smartUiDrafts(wid),
      ]);
      setItems(list.items);
      setDrafts(draftList.items);
    } catch (err) {
      setError((err as ApiError).message || String(err));
    } finally {
      setLoading(false);
    }
  }, [wid]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const filtered = items.filter((item) => {
    if (filter === "all") return true;
    if (filter === "failed") return item.status === "failed";
    return item.mode === filter;
  });

  return (
    <div className="studio-history">
      <Card
        title="生成记录"
        sub="每一条记录保留提问、裁决摘要、prompt/program 指纹与版本链；点开可回到生成台继续查看或迭代。"
        aside={
          <div className="btn-row">
            <div className="segmented">
              {([
                ["all", "全部"],
                ["llm", "模型生成"],
                ["fallback", "确定性回退"],
                ["failed", "失败"],
              ] as const).map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  className={filter === key ? "is-active" : ""}
                  onClick={() => setFilter(key)}
                >
                  {label}
                </button>
              ))}
            </div>
            <Btn variant="subtle" onClick={() => void refresh()} busy={loading}>
              <ArrowsClockwise size={14} /> 刷新
            </Btn>
          </div>
        }
        tight
      >
        {error ? <ErrorNote text={error} onRetry={() => void refresh()} /> : null}
        {loading && !items.length ? <Skeleton rows={6} /> : null}
        {!loading && !filtered.length ? (
          <div className="studio-history-empty">
            <Empty title="没有匹配的生成记录" hint="在 AI 生成台写一个业务问题，生成记录会出现在这里。" />
          </div>
        ) : null}
        {filtered.length ? (
          <TableWrap>
            <table className="studio-history-table">
              <thead>
                <tr>
                  <th>时间</th>
                  <th>提问</th>
                  <th>版本</th>
                  <th>状态</th>
                  <th>模式</th>
                  <th>对象 / 视角</th>
                  <th>Program</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((item) => (
                  <tr key={item.id} className="studio-history-row" onClick={() => onOpen(item.id)}>
                    <td className="mono">{timeOf(item.created_at)}</td>
                    <td className="studio-history-question">{item.question}</td>
                    <td className="mono">v{item.version}{item.parent_id ? " · 迭代" : ""}</td>
                    <td>
                      <Tag kind={item.status === "succeeded" ? "ok" : item.status === "failed" ? "err" : "warn"}>
                        {STATUS_LABEL[item.status] || item.status}
                      </Tag>
                    </td>
                    <td>{MODE_LABEL[item.mode] || item.mode}{item.model ? <span className="studio-row-sub mono">{item.model}</span> : null}</td>
                    <td>{item.object || "—"}{item.view_count ? <span className="studio-row-sub">{item.view_count} 个视角</span> : null}</td>
                    <td className="mono">{(item.program_hash || "—").replace("sha256:", "").slice(0, 10)}</td>
                    <td>
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
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        ) : null}
      </Card>

      <Card title="草稿与已发布" sub="Studio 的保存是草稿快照；发布后只给应用附加 ui_artifact，不改声明式视图。">
        {drafts.length ? (
          <div className="studio-draft-list">
            {drafts.map((draft) => (
              <div key={draft.id} className="studio-draft-row">
                <div>
                  <b>{draft.name}</b>
                  <span className="mono">
                    v{draft.version} · {draft.program_hash?.replace("sha256:", "").slice(0, 10) || "—"}
                  </span>
                </div>
                <div className="studio-draft-side">
                  <Tag kind={draft.status === "published" ? "ok" : "plain"}>
                    {draft.status === "published" ? "已发布" : "草稿"}
                  </Tag>
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
                </div>
              </div>
            ))}
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
