import { type KeyboardEvent, useEffect, useMemo, useState } from "react";
import {
  ArrowRight, CheckCircle, Circle, FileText, GitBranch, ListChecks,
  PencilSimple, Play, Plus, SealCheck, Trash, WarningCircle, X,
} from "@phosphor-icons/react";
import {
  api, type WorkflowCq, type WorkflowCqPreview, type WorkflowCqPriority,
  type WorkflowDetail, type WorkflowReuse, type WorkflowSample,
} from "../api";
import { usePlatform } from "../store";
import {
  Btn, Card, Collapsible, Empty, ErrorNote, Field, Loading, Select,
  Tag, TextInput, Area, useLoad,
} from "../ui";
import {
  cqStatusKind, cqStatusLabel, expectedLabel, joinRefs, parseJsonObject,
  parseJsonRows, progress, queryLabel, roundStatusLabel, splitRefs,
  stepStatusKind,
} from "../lib/workflow";

type ScopeDraft = {
  goal: string;
  summary: string;
  in_scope: string;
  out_of_scope: string;
  actors: string;
};

type CqDraft = {
  code: string;
  question: string;
  priority: WorkflowCqPriority;
  type: string;
  expected_kind: string;
  expected_value: string;
  expected_operator: string;
  expected_rows: string;
  query_kind: string;
  query_object_type: string;
  query_relation: string;
  query_ref: string;
  query_direction: string;
  filters: string;
  model_refs: string;
  sample_refs: string;
  source: string;
  rationale: string;
};

type TargetDraft = {
  min_cq: string;
  max_cq: string;
  p0_pass_rate: string;
  overall_pass_rate: string;
};

type ReuseDraft = {
  title: string;
  decision: WorkflowReuse["decision"];
  source: string;
  reference: string;
  note: string;
  owner: string;
};

type SampleDraft = {
  id?: string;
  name: string;
  description: string;
  objects: string;
  relations: string;
};

const EMPTY_SCOPE: ScopeDraft = {
  goal: "", summary: "", in_scope: "", out_of_scope: "", actors: "",
};

const EMPTY_TARGET: TargetDraft = {
  min_cq: "5", max_cq: "10", p0_pass_rate: "100", overall_pass_rate: "80",
};

function targetDraft(detail: WorkflowDetail | null): TargetDraft {
  const target = detail?.round.target;
  return target ? {
    min_cq: String(target.min_cq),
    max_cq: String(target.max_cq),
    p0_pass_rate: String(target.p0_pass_rate),
    overall_pass_rate: String(target.overall_pass_rate),
  } : EMPTY_TARGET;
}

function targetNumber(value: string, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? Math.max(0, parsed) : fallback;
}

function downloadText(filename: string, content: string) {
  const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

const EMPTY_REUSE: ReuseDraft = {
  title: "", decision: "build", source: "", reference: "", note: "", owner: "",
};

function cqDraft(detail: WorkflowDetail | null, cq?: WorkflowCq): CqDraft {
  const firstType = detail?.ontology.objects[0] || "";
  if (!cq) {
    return {
      code: "", question: "", priority: "P0", type: "competency",
      expected_kind: "row_count", expected_value: "1", expected_operator: "eq",
      expected_rows: "[]", query_kind: "fixture",
      query_object_type: firstType, query_relation: "",
      query_ref: "", query_direction: "out", filters: "{}", model_refs: "",
      sample_refs: "", source: "", rationale: "",
    };
  }
  const expected = cq.expected || { kind: "row_count" };
  return {
    code: cq.code,
    question: cq.question,
    priority: cq.priority,
    type: cq.type,
    expected_kind: expected.kind || "row_count",
    expected_value: String(expected.value ?? 1),
    expected_operator: expected.operator || "eq",
    expected_rows: JSON.stringify(expected.rows || [], null, 2),
    query_kind: cq.query.kind || "fixture",
    query_object_type: cq.query.object_type || "",
    query_relation: cq.query.relation || "",
    query_ref: cq.query.ref || "",
    query_direction: cq.query.direction || "out",
    filters: JSON.stringify(cq.query.filters || {}, null, 2),
    model_refs: joinRefs(cq.model_refs),
    sample_refs: joinRefs(cq.sample_refs),
    source: cq.source || "",
    rationale: cq.rationale || "",
  };
}

function sampleDraft(detail: WorkflowDetail | null, sample?: WorkflowSample): SampleDraft {
  if (!sample) {
    const template = detail?.sample_template;
    return {
      name: template?.name || "第一组样例",
      description: template?.description || "",
      objects: JSON.stringify(template?.objects || {}, null, 2),
      relations: JSON.stringify(template?.relations || [], null, 2),
    };
  }
  const objects: Record<string, Record<string, unknown>[]> = {};
  for (const [type, rows] of Object.entries(sample.objects || {})) {
    objects[type] = rows.map(row => ({ id: row.id, ...row.properties }));
  }
  return {
    id: sample.id,
    name: sample.name,
    description: sample.description || "",
    objects: JSON.stringify(objects, null, 2),
    relations: JSON.stringify(sample.relations || [], null, 2),
  };
}

function gateTone(check: WorkflowDetail["publish_check"] | undefined) {
  if (!check) return "plain" as const;
  if (check.ok) return "ok" as const;
  return check.blockers.length ? "err" as const : "warn" as const;
}

export function OntologyWorkflow({
  ontology, onRefresh, onShowStructure,
}: {
  ontology: { name: string; version: number; lifecycle: string };
  onRefresh: () => Promise<void>;
  onShowStructure: (view: "table" | "graph") => void;
}) {
  const { wid, toast } = usePlatform();
  const [selected, setSelected] = useState("");
  const load = useLoad(
    () => (wid ? api.workflow(wid, selected || undefined) : Promise.resolve(null)),
    [wid, selected]);
  const overview = load.data;
  const detail = overview?.round || null;
  const round = detail?.round || null;
  const rounds = overview?.rounds || [];
  const [busy, setBusy] = useState("");
  const [report, setReport] = useState("");
  const [activeStep, setActiveStep] = useState("");

  useEffect(() => {
    if (!overview) return;
    const known = new Set(overview.rounds.map(item => item.id));
    if (!selected || !known.has(selected)) {
      setSelected(overview.active_round_id || overview.rounds[0]?.id || "");
    }
  }, [overview, selected]);

  // A step card opens one page at a time; switching rounds starts at the
  // first unfinished step.
  useEffect(() => {
    setActiveStep("");
  }, [round?.id]);

  const matrix = useMemo(() => {
    const map: Record<string, WorkflowDetail["trace_matrix"][number]> = {};
    for (const row of detail?.trace_matrix || []) map[row.cq_id] = row;
    return map;
  }, [detail?.trace_matrix]);

  const mutate = async (
    key: string, action: () => Promise<unknown>, done?: string,
    refreshOntology = false, onError?: (message: string) => void,
  ) => {
    if (busy) return;
    setBusy(key);
    try {
      await action();
      if (refreshOntology) await onRefresh();
      if (done) toast(done, "ok");
      load.reload();
    } catch (error) {
      const message = (error as Error).message || "操作失败";
      if (onError) onError(message);
      else toast(message, "err");
    } finally {
      setBusy("");
    }
  };

  /* ---- scope ---- */
  const [scope, setScope] = useState<ScopeDraft>(EMPTY_SCOPE);
  const [target, setTarget] = useState<TargetDraft>(EMPTY_TARGET);
  const [reuseSkipped, setReuseSkipped] = useState(false);
  useEffect(() => {
    if (!round) return;
    const value = round.scope || { summary: "", in_scope: [], out_of_scope: [], actors: [], decisions: [] };
    setScope({
      goal: round.goal || "",
      summary: value.summary || "",
      in_scope: joinRefs(value.in_scope),
      out_of_scope: joinRefs(value.out_of_scope),
      actors: joinRefs(value.actors),
    });
    setTarget(targetDraft(detail));
    setReuseSkipped(Boolean(round.reuse_skipped));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [round?.id]);

  /* ---- create round ---- */
  const [newName, setNewName] = useState("");
  const [newGoal, setNewGoal] = useState("");
  const createRound = () => {
    if (!newName.trim()) { toast("给这一轮起个名字", "err"); return; }
    void mutate("round", async () => {
      const created = await api.createWorkflowRound(wid!, {
        name: newName.trim(), goal: newGoal.trim(),
      });
      setSelected(created.id);
      setNewName(""); setNewGoal("");
    }, "新一轮已开始；若上一版本已发布，系统已复制出草稿", true);
  };
  const nextRound = () => {
    if (!round) return;
    void mutate("next", async () => {
      const created = await api.nextWorkflowRound(wid!, round.id, {
        name: newName.trim() || undefined,
      });
      setSelected(created.id);
      setNewName("");
    }, "已开下一轮，并复制出新的草稿版本", true);
  };

  /* ---- CQ ---- */
  const [cqOpen, setCqOpen] = useState(false);
  const [cqEditing, setCqEditing] = useState<string | null>(null);
  const [cqForm, setCqForm] = useState<CqDraft>(cqDraft(null));
  const [cqError, setCqError] = useState("");
  const [preview, setPreview] = useState<Record<string, WorkflowCqPreview>>({});
  const [importOpen, setImportOpen] = useState(false);
  const [importFormat, setImportFormat] = useState<"auto" | "csv" | "markdown" | "json">("auto");
  const [importText, setImportText] = useState("");
  const [importError, setImportError] = useState("");
  const openImport = () => {
    setImportText("");
    setImportFormat("auto");
    setImportError("");
    setImportOpen(true);
  };
  const importCqs = () => {
    if (!round) return;
    setImportError("");
    if (!importText.trim()) { setImportError("先粘贴 CSV / Markdown / JSON 内容，或选择文件"); return; }
    void mutate("cq-import", async () => {
      const result = await api.importWorkflowCqs(wid!, round.id, {
        content: importText, format: importFormat,
      });
      setImportOpen(false);
      setImportText("");
      toast(`已导入 ${result.imported} 条 CQ`, "ok");
    }, undefined, false, setImportError);
  };
  const exportCqs = (format: "csv" | "markdown" | "json") => {
    if (!round) return;
    void mutate(`cq-export-${format}`, async () => {
      const result = await api.exportWorkflowCqs(wid!, round.id, format);
      downloadText(result.filename, result.content);
    }, undefined);
  };
  const previewCq = (cq: WorkflowCq) => {
    if (!round) return;
    void mutate(`preview-${cq.id}`, async () => {
      const result = await api.runWorkflowCq(wid!, round.id, cq.id);
      setPreview(previous => ({ ...previous, [cq.id]: result }));
      toast(result.result.status === "passed" ? `${cq.code} 试跑通过` : `${cq.code} 试跑未通过`, result.result.status === "passed" ? "ok" : "warn");
    }, undefined);
  };

  const openAddCq = () => {
    setCqForm(cqDraft(detail));
    setCqEditing("new");
    setCqOpen(true);
    setCqError("");
  };
  const openEditCq = (cq: WorkflowCq) => {
    setCqForm(cqDraft(detail, cq));
    setCqEditing(cq.id);
    setCqOpen(true);
    setCqError("");
  };
  const saveCq = () => {
    if (!round) return;
    setCqError("");
    try {
      const expected: Record<string, unknown> = { kind: cqForm.expected_kind };
      if (["rows_contain", "golden_rows"].includes(cqForm.expected_kind)) {
        expected.rows = parseJsonRows(cqForm.expected_rows);
      } else if (cqForm.expected_kind === "row_count") {
        expected.value = Math.max(0, Number(cqForm.expected_value) || 0);
        expected.operator = cqForm.expected_operator;
      } else if (cqForm.expected_kind === "boolean") {
        expected.value = cqForm.expected_value === "true";
      }
      const filters = parseJsonObject(cqForm.filters);
      const query: Record<string, unknown> = { kind: cqForm.query_kind, filters };
      if (cqForm.query_relation.trim()) {
        query.relation = cqForm.query_relation.trim();
        query.ref = cqForm.query_ref.trim();
        query.direction = cqForm.query_direction;
      } else {
        query.object_type = cqForm.query_object_type;
      }
      if (cqForm.expected_kind === "manual") {
        for (const key of Object.keys(query)) delete query[key];
      }
      const body = {
        code: cqForm.code.trim() || undefined,
        question: cqForm.question.trim(),
        priority: cqForm.priority,
        type: cqForm.type,
        expected: expected as unknown as WorkflowCq["expected"],
        query: query as unknown as WorkflowCq["query"],
        model_refs: splitRefs(cqForm.model_refs),
        sample_refs: splitRefs(cqForm.sample_refs),
        source: cqForm.source.trim(),
        rationale: cqForm.rationale.trim(),
      };
      if (!body.question) throw new Error("CQ 要写成一个可回答的问题");
      if (cqForm.expected_kind !== "manual" && !body.query.object_type && !body.query.relation) {
        throw new Error("选择对象类型，或填写关系与起点");
      }
      if (cqForm.expected_kind !== "manual" && body.query.relation && !body.query.ref) {
        throw new Error("关系查询需要填写起点引用，例如 Vehicle/V001");
      }
      void mutate("cq", async () => {
        if (cqEditing && cqEditing !== "new") {
          await api.updateWorkflowCq(wid!, round.id, cqEditing, body);
        } else {
          await api.addWorkflowCq(wid!, round.id, body);
        }
        setCqOpen(false);
        setCqEditing(null);
        setPreview({});
      }, cqEditing === "new" ? "CQ 已加入本轮" : "CQ 已更新");
    } catch (error) {
      setCqError((error as Error).message);
    }
  };

  /* ---- reuse ---- */
  const [reuseForm, setReuseForm] = useState<ReuseDraft>(EMPTY_REUSE);
  const saveReuse = () => {
    if (!round) return;
    if (!reuseForm.title.trim()) { toast("给复用调研写个标题", "err"); return; }
    void mutate("reuse", async () => {
      await api.addWorkflowReuse(wid!, round.id, {
        title: reuseForm.title.trim(),
        decision: reuseForm.decision,
        source: reuseForm.source.trim(),
        reference: reuseForm.reference.trim(),
        note: reuseForm.note.trim(),
        owner: reuseForm.owner.trim(),
      });
      setReuseForm(EMPTY_REUSE);
    }, "复用调研已记录");
  };
  const toggleReuseSkip = () => {
    if (!round) return;
    const next = !reuseSkipped;
    setReuseSkipped(next);
    void mutate("reuse-skip", async () => {
      await api.updateWorkflowRound(wid!, round.id, {
        reuse_skipped: next,
      });
    }, next ? "已标记本轮不复用" : "已取消「不复用」标记");
  };

  /* ---- samples ---- */
  const [sampleOpen, setSampleOpen] = useState(false);
  const [sampleForm, setSampleForm] = useState<SampleDraft>(sampleDraft(null));
  const [sampleError, setSampleError] = useState("");
  const openAddSample = () => {
    setSampleForm(sampleDraft(detail));
    setSampleOpen(true);
    setSampleError("");
  };
  const openEditSample = (sample: WorkflowSample) => {
    setSampleForm(sampleDraft(detail, sample));
    setSampleOpen(true);
    setSampleError("");
  };
  const saveSample = () => {
    if (!round) return;
    setSampleError("");
    try {
      const objects = parseJsonObject(sampleForm.objects);
      const relations = parseJsonRows(sampleForm.relations);
      if (!Object.keys(objects).length) throw new Error("样例至少需要一组对象实例");
      void mutate("sample", async () => {
        const body = {
          name: sampleForm.name.trim() || "样例集",
          description: sampleForm.description.trim(),
          objects: objects as unknown as WorkflowSample["objects"],
          relations: relations as unknown as WorkflowSample["relations"],
        };
        if (sampleForm.id) {
          await api.updateWorkflowSample(wid!, round.id, sampleForm.id, body);
        } else {
          await api.addWorkflowSample(wid!, round.id, body);
        }
        setSampleOpen(false);
        setSampleForm(sampleDraft(detail));
      }, sampleForm.id ? "样例已更新" : "样例集已加入本轮");
    } catch (error) {
      setSampleError((error as Error).message);
    }
  };

  /* ---- run / check / review / publish ---- */
  const runAcceptance = () => {
    if (!round) return;
    void mutate("run", async () => {
      const result = await api.runWorkflowAcceptance(wid!, round.id);
      toast(result.status === "passed" ? "CQ 验证通过" : "CQ 验证未通过，请按结果修正",
            result.status === "passed" ? "ok" : "warn");
    }, undefined);
  };
  const checkRound = () => {
    if (!round) return;
    void mutate("check", async () => {
      const result = await api.checkWorkflowRound(wid!, round.id);
      toast(result.validation.ok ? "语义检查通过" : "语义检查未通过",
            result.validation.ok ? "ok" : "warn");
    }, undefined);
  };
  const [reviewComment, setReviewComment] = useState("");
  const submitReview = (decision: "approve" | "reject" | "changes_requested") => {
    if (!round) return;
    void mutate("review", async () => {
      await api.reviewWorkflowRound(wid!, round.id, {
        decision, comment: reviewComment.trim(),
      });
      setReviewComment("");
    }, decision === "approve" ? "评审已通过，可以发布" : "评审意见已记录");
  };
  const publish = () => {
    if (!round) return;
    void mutate("publish", async () => {
      await api.publishWorkflowRound(wid!, round.id);
    }, "本轮已发布，运行时已切换到新版本", true);
  };
  const loadReport = () => {
    if (!round) return;
    void mutate("report", async () => {
      const value = await api.workflowReport(wid!, round.id);
      setReport(value.markdown);
    });
  };

  if (load.loading && !overview) return <Loading text="读取工程流…" />;
  if (load.error) return <ErrorNote text={load.error} onRetry={load.reload} />;
  if (!overview) return null;

  if (!rounds.length) {
    return (
      <Card title="本体工程流" sub="把「这个本体要回答什么」变成一轮可验收的小版本，而不是一次做完。">
        <div className="wf-empty">
          <div>
            <h3 className="h4">开始第一轮</h3>
            <p className="muted">
              先写范围，再写 5–10 个能力问题；结构、样例、验证、评审都围绕这一批问题。
              发布后开下一轮，不推翻上一轮。
            </p>
          </div>
          <div className="wf-form-grid">
            <Field label="本轮名称">
              <TextInput value={newName} placeholder="第 1 轮 · 报销链路"
                         onChange={event => setNewName(event.target.value)} />
            </Field>
            <Field label="本轮目标">
              <TextInput value={newGoal} placeholder="打通车辆到收费站的查询链路"
                         onChange={event => setNewGoal(event.target.value)} />
            </Field>
          </div>
          <Btn onClick={createRound} busy={busy === "round"}>
            <Plus size={14} /> 开始第一轮
          </Btn>
        </div>
      </Card>
    );
  }

  if (!detail || !round) return <Empty title="这一轮读不出来" hint="刷新后再试。" />;

  const progressValue = progress(detail.steps);
  const latestRun = detail.runs[0] || null;
  const latestReview = detail.reviews[0] || null;
  const gate = detail.publish_check;
  const hasNewerOpenRound = rounds.some(item => item.id !== round.id
    && item.seq > round.seq && item.status !== "accepted");
  const firstOpenStep = detail.steps.find(step => step.status !== "done") || detail.steps[0];
  const activeStepKey = detail.steps.some(step => step.key === activeStep)
    ? activeStep : firstOpenStep.key;
  const activeStepIndex = detail.steps.findIndex(step => step.key === activeStepKey);
  const goStep = (key: string) => {
    setActiveStep(key);
    requestAnimationFrame(() => {
      document.getElementById("wf-panel")?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  };
  const moveStep = (delta: number) => {
    const next = detail.steps[activeStepIndex + delta];
    if (next) goStep(next.key);
  };
  const onStepTabKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const keys = ["ArrowLeft", "ArrowRight", "Home", "End"];
    if (!keys.includes(event.key)) return;
    event.preventDefault();
    const nextIndex = event.key === "Home" ? 0
      : event.key === "End" ? detail.steps.length - 1
        : (activeStepIndex + (event.key === "ArrowRight" ? 1 : -1) + detail.steps.length) % detail.steps.length;
    const next = detail.steps[nextIndex];
    if (!next) return;
    setActiveStep(next.key);
    requestAnimationFrame(() => document.getElementById(`wf-tab-${next.key}`)?.focus());
  };

  return (
    <div className="wf-shell">
      <div className="wf-head">
        <div className="wf-head-main">
          <div className="wf-kicker">Ontology workflow · 第 {round.seq} 轮</div>
          <div className="wf-title-row">
            <h2 className="wf-title">{round.name}</h2>
            <Tag kind={round.status === "accepted" ? "ok" : round.status === "in_review" ? "warn" : "plain"}>
              {roundStatusLabel(round.status)}
            </Tag>
            <Tag kind={ontology.lifecycle === "published" ? "ok" : "warn"}>
              本体 {ontology.lifecycle === "published" ? "已发布" : "草稿"} v{ontology.version}
            </Tag>
          </div>
          <p className="muted small">
            {round.goal || "还没有写这一轮要解决的问题。"}
          </p>
          <div className="wf-progress" aria-label={`八步进度 ${progressValue.percent}%`}>
            <span className="wf-progress-track"><span style={{ width: `${progressValue.percent}%` }} /></span>
            <span className="mono small">{progressValue.done}/{progressValue.total}</span>
          </div>
        </div>
        <div className="wf-head-actions">
          <Select aria-label="选择轮次" value={round.id}
                  onChange={event => setSelected(event.target.value)}>
            {rounds.map(item => (
              <option key={item.id} value={item.id}>
                第 {item.seq} 轮 · {item.name} · {roundStatusLabel(item.status)}
              </option>
            ))}
          </Select>
          {round.status === "accepted" && !hasNewerOpenRound && (
            <div className="wf-next">
              <TextInput aria-label="下一轮名称" value={newName}
                         placeholder="下一轮名称（可选）"
                         onChange={event => setNewName(event.target.value)} />
              <Btn variant="subtle" onClick={nextRound} busy={busy === "next"}>
                <GitBranch size={14} /> 开下一轮
              </Btn>
            </div>
          )}
        </div>
      </div>

      <div className="wf-steps" role="tablist" aria-label="本体工程流八步"
           onKeyDown={onStepTabKeyDown}>
        {detail.steps.map(step => {
          const active = step.key === activeStepKey;
          return (
            <button key={step.key} type="button" role="tab"
                    id={`wf-tab-${step.key}`}
                    aria-selected={active}
                    aria-controls="wf-panel"
                    tabIndex={active ? 0 : -1}
                    className="wf-step"
                    data-status={step.status}
                    data-active={active ? "true" : undefined}
                    onClick={() => goStep(step.key)}>
              <span className="wf-step-no">
                {step.status === "done" ? <CheckCircle size={15} weight="fill" />
                  : step.status === "warn" ? <WarningCircle size={15} weight="fill" />
                    : <Circle size={15} />} {String(step.no).padStart(2, "0")}
              </span>
              <b>{step.title}</b>
              <span>{step.detail}</span>
              <Tag kind={stepStatusKind(step.status)}>{step.status === "done" ? "完成" : step.status === "warn" ? "待修正" : step.status === "current" ? "当前" : "未开始"}</Tag>
            </button>
          );
        })}
      </div>

      <div className="wf-layout">
        <div className="wf-main" id="wf-panel" role="tabpanel"
             aria-labelledby={`wf-tab-${activeStepKey}`}>
          {activeStepKey === "scope" && (
          <Card id="wf-scope" title="01 · 范围" sub="这一轮回答什么、不回答什么；范围不是文档，是取舍。"
                aside={<Tag kind={detail.steps[0]?.status === "done" ? "ok" : ""}>{detail.steps[0]?.status === "done" ? "已记录" : "待补充"}</Tag>}>
            <div className="wf-form-grid">
              <Field label="本轮目标" top>
                <TextInput value={scope.goal} onChange={event => setScope({ ...scope, goal: event.target.value })} />
              </Field>
              <Field label="范围摘要" top>
                <Area rows={3} value={scope.summary} onChange={event => setScope({ ...scope, summary: event.target.value })} />
              </Field>
              <Field label="范围内（逗号分隔）">
                <TextInput value={scope.in_scope} onChange={event => setScope({ ...scope, in_scope: event.target.value })} />
              </Field>
              <Field label="范围外（逗号分隔）">
                <TextInput value={scope.out_of_scope} onChange={event => setScope({ ...scope, out_of_scope: event.target.value })} />
              </Field>
              <Field label="业务参与角色（逗号分隔）">
                <TextInput value={scope.actors} onChange={event => setScope({ ...scope, actors: event.target.value })} />
              </Field>
              <Field label="本轮 CQ 下限" hint="默认 5；达到下限才进入发布门禁。">
                <TextInput value={target.min_cq} inputMode="numeric"
                           onChange={event => setTarget({ ...target, min_cq: event.target.value })} />
              </Field>
              <Field label="本轮 CQ 上限" hint="超过时门禁只提醒，不硬拦。">
                <TextInput value={target.max_cq} inputMode="numeric"
                           onChange={event => setTarget({ ...target, max_cq: event.target.value })} />
              </Field>
              <Field label="P0 通过率目标 %">
                <TextInput value={target.p0_pass_rate} inputMode="decimal"
                           onChange={event => setTarget({ ...target, p0_pass_rate: event.target.value })} />
              </Field>
              <Field label="整体通过率目标 %">
                <TextInput value={target.overall_pass_rate} inputMode="decimal"
                           onChange={event => setTarget({ ...target, overall_pass_rate: event.target.value })} />
              </Field>
            </div>
            <Btn busy={busy === "scope"} onClick={() => void mutate("scope", () => api.updateWorkflowRound(wid!, round.id, {
              goal: scope.goal, scope: {
                summary: scope.summary,
                in_scope: splitRefs(scope.in_scope),
                out_of_scope: splitRefs(scope.out_of_scope),
                actors: splitRefs(scope.actors),
                decisions: round.scope?.decisions || [],
              },
              target: {
                min_cq: targetNumber(target.min_cq, 5),
                max_cq: targetNumber(target.max_cq, 10),
                p0_pass_rate: targetNumber(target.p0_pass_rate, 100),
                overall_pass_rate: targetNumber(target.overall_pass_rate, 80),
              },
            }), "范围与门禁目标已保存")}>保存范围与门禁</Btn>
          </Card>
          )}

          {activeStepKey === "cq" && (
          <Card id="wf-cq" title="02 · 编写能力问题"
                sub="每条 CQ 都要能被查询验证；P0 是这一轮必须答对的题。"
                aside={<div className="wf-inline">
                  <Tag kind={detail.cqs.length >= (round.target?.min_cq || 5) ? "ok" : "warn"}>
                    {detail.cqs.length}/{round.target?.max_cq || 10}
                  </Tag>
                  <Btn size="sm" variant="subtle" onClick={openImport}><FileText size={13} /> 导入</Btn>
                  <Btn size="sm" variant="subtle" busy={busy === "cq-export-csv"}
                       onClick={() => exportCqs("csv")}>导出 CSV</Btn>
                  <Btn size="sm" onClick={openAddCq}><Plus size={13} /> 加 CQ</Btn>
                </div>}>
            {!detail.cqs.length ? (
              <Empty title="还没有 CQ" hint="先写下业务里必须被回答的问题，例如「某车牌是否在某个收费站通行过？」"
                     action={<Btn onClick={openAddCq}><Plus size={14} /> 写第一条 CQ</Btn>} />
            ) : (
              <div className="wf-list">
                {detail.cqs.map(cq => {
                  const result = matrix[cq.id];
                  const trial = preview[cq.id];
                  return (
                    <div key={cq.id} className="wf-row" data-status={result?.status || "open"}>
                      <div className="wf-row-main">
                        <div className="wf-inline">
                          <span className="mono small">{cq.code}</span>
                          <Tag kind={cq.priority === "P0" ? "err" : cq.priority === "P1" ? "warn" : "plain"}>{cq.priority}</Tag>
                          <Tag kind={cqStatusKind(result?.status || "open")}>{cqStatusLabel(result?.status || "open")}</Tag>
                          <span className="muted small">{cq.type}</span>
                          <span className="muted small">{cq.query.kind === "runtime" ? "运行时" : "样例"}</span>
                        </div>
                        <b>{cq.question}</b>
                        <div className="wf-meta">
                          <span>查询：{queryLabel(cq.query)}</span>
                          <span>期望：{expectedLabel(cq.expected)}</span>
                          <span>引用：{cq.model_refs.join(", ") || "—"}</span>
                        </div>
                        {result?.detail && (
                          <p className={result.status === "passed" ? "muted small" : "small wf-fail"}>
                            {result.detail}
                          </p>
                        )}
                        {trial && (
                          <div className={`wf-trial ${trial.result.status === "passed" ? "ok" : "err"}`}>
                            <b>试跑 · {trial.engine === "runtime" ? "运行时状态" : "样例 fixture"} · {cqStatusLabel(trial.result.status)}</b>
                            <span>{trial.result.detail}</span>
                            <span className="muted small">
                              返回 {Number(trial.result.actual?.row_count ?? 0)} 行
                            </span>
                          </div>
                        )}
                      </div>
                      <div className="wf-row-actions">
                        <button type="button" className="icon-btn" title="试跑这条 CQ（不保存运行记录）"
                                onClick={() => previewCq(cq)}><Play size={14} /></button>
                        <button type="button" className="icon-btn" title="编辑 CQ" onClick={() => openEditCq(cq)}><PencilSimple size={14} /></button>
                        <button type="button" className="icon-btn danger" title="删除 CQ"
                                onClick={() => {
                                  if (!window.confirm(`删除 ${cq.code}？`)) return;
                                  setPreview(previous => {
                                    const next = { ...previous };
                                    delete next[cq.id];
                                    return next;
                                  });
                                  void mutate("cq-delete", () => api.deleteWorkflowCq(wid!, round.id, cq.id), "CQ 已删除");
                                }}><Trash size={14} /></button>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
            {importOpen && (
              <div className="wf-form-block">
                <div className="wf-form-head">
                  <h4 className="h4">批量导入 CQ</h4>
                  <button type="button" className="icon-btn" onClick={() => setImportOpen(false)} aria-label="关闭导入"><X size={15} /></button>
                </div>
                <p className="muted small">
                  直接粘贴 CSV、Markdown 表格或 JSON，也可以选择文件。列名可用英文或中文；
                  编号重复会整批拒绝，不会写入半套数据。
                </p>
                <div className="wf-form-grid">
                  <Field label="格式">
                    <Select value={importFormat} onChange={event => setImportFormat(event.target.value as typeof importFormat)}>
                      <option value="auto">自动判断</option>
                      <option value="csv">CSV</option>
                      <option value="markdown">Markdown 表格</option>
                      <option value="json">JSON</option>
                    </Select>
                  </Field>
                  <Field label="选择文件">
                    <input type="file" accept=".csv,.md,.markdown,.json,text/plain"
                           onChange={event => {
                             const file = event.target.files?.[0];
                             if (!file) return;
                             const reader = new FileReader();
                             reader.onload = () => {
                               setImportText(String(reader.result || ""));
                               const name = file.name.toLowerCase();
                               setImportFormat(name.endsWith(".json") ? "json"
                                 : name.endsWith(".md") || name.endsWith(".markdown") ? "markdown" : "csv");
                             };
                             reader.readAsText(file);
                           }} />
                  </Field>
                </div>
                <Field label="导入内容" top>
                  <Area rows={8} value={importText} onChange={event => setImportText(event.target.value)}
                        placeholder={"code,question,priority,expected_kind,expected_value,query_kind,object_type\nCQ-01,有多少车辆？,P0,row_count,2,fixture,Vehicle"} />
                </Field>
                {importError && <div className="banner err">{importError}</div>}
                <div className="btn-row">
                  <Btn busy={busy === "cq-import"} onClick={importCqs}><CheckCircle size={14} /> 导入到本轮</Btn>
                  <Btn variant="subtle" onClick={() => exportCqs("csv")}>导出 CSV</Btn>
                  <Btn variant="subtle" onClick={() => exportCqs("markdown")}>导出 Markdown</Btn>
                  <Btn variant="subtle" onClick={() => exportCqs("json")}>导出 JSON</Btn>
                </div>
              </div>
            )}
            {cqOpen && (
              <div id="wf-cq-form" className="wf-form-block">
                <div className="wf-form-head">
                  <h4 className="h4">{cqEditing === "new" ? "新增 CQ" : "编辑 CQ"}</h4>
                  <button type="button" className="icon-btn" onClick={() => setCqOpen(false)} aria-label="关闭 CQ 表单"><X size={15} /></button>
                </div>
                <div className="wf-form-grid">
                  <Field label="编号"><TextInput value={cqForm.code} placeholder="留空自动 CQ-01"
                                                  onChange={event => setCqForm({ ...cqForm, code: event.target.value })} /></Field>
                  <Field label="优先级">
                    <Select value={cqForm.priority} onChange={event => setCqForm({ ...cqForm, priority: event.target.value as WorkflowCqPriority })}>
                      <option value="P0">P0 · 必须通过</option>
                      <option value="P1">P1</option>
                      <option value="P2">P2</option>
                    </Select>
                  </Field>
                  <Field label="类型">
                    <Select value={cqForm.type} onChange={event => setCqForm({ ...cqForm, type: event.target.value })}>
                      <option value="competency">能力问题</option>
                      <option value="scoping">范围问题</option>
                      <option value="validation">数据校验</option>
                    </Select>
                  </Field>
                  <Field label="来源"><TextInput value={cqForm.source} placeholder="业务方 / 需求单号"
                                                  onChange={event => setCqForm({ ...cqForm, source: event.target.value })} /></Field>
                  <Field label="问题（一句话，可被查询回答）" top>
                    <Area rows={2} value={cqForm.question} placeholder="V001 是否在浦东收费站通行过？"
                          onChange={event => setCqForm({ ...cqForm, question: event.target.value })} />
                  </Field>
                  <Field label="模型引用（逗号分隔）" hint="对象、对象.属性或关系名；用于追踪 CQ 落到哪块结构。">
                    <TextInput value={cqForm.model_refs} placeholder="Vehicle, Vehicle.plate, passes"
                               onChange={event => setCqForm({ ...cqForm, model_refs: event.target.value })} />
                  </Field>
                  <Field label="查询引擎" hint="样例隔离验证，或读取已发布运行时的真实状态。">
                    <Select value={cqForm.query_kind} onChange={event => setCqForm({ ...cqForm, query_kind: event.target.value })}>
                      <option value="fixture">样例 fixture（隔离，可重复）</option>
                      <option value="runtime">运行时 runtime（真实状态）</option>
                    </Select>
                  </Field>
                  <Field label="查询对象类型">
                    <Select value={cqForm.query_object_type} onChange={event => setCqForm({ ...cqForm, query_object_type: event.target.value })}>
                      <option value="">—</option>
                      {detail.ontology.objects.map(name => <option key={name} value={name}>{name}</option>)}
                    </Select>
                  </Field>
                  <Field label="或关系名" hint="填关系时忽略对象类型，按样例关系查邻接对象。">
                    <Select value={cqForm.query_relation} onChange={event => setCqForm({ ...cqForm, query_relation: event.target.value })}>
                      <option value="">—</option>
                      {detail.ontology.relations.map(name => <option key={name} value={name}>{name}</option>)}
                    </Select>
                  </Field>
                  {cqForm.query_relation && (
                    <>
                      <Field label="起点引用"><TextInput value={cqForm.query_ref} placeholder="Vehicle/V001"
                                                        onChange={event => setCqForm({ ...cqForm, query_ref: event.target.value })} /></Field>
                      <Field label="方向">
                        <Select value={cqForm.query_direction} onChange={event => setCqForm({ ...cqForm, query_direction: event.target.value })}>
                          <option value="out">出边 out</option>
                          <option value="in">入边 in</option>
                          <option value="both">双向 both</option>
                        </Select>
                      </Field>
                    </>
                  )}
                  <Field label="过滤条件 JSON" hint='例如 {"plate":"沪A12345"}；支持 prop__contains / prop__in / prop__gte。'>
                    <Area rows={2} value={cqForm.filters} onChange={event => setCqForm({ ...cqForm, filters: event.target.value })} />
                  </Field>
                  {cqForm.query_kind === "runtime" && (
                    <p className="muted small wf-span-2">
                      运行时查询读取已发布本体的当前状态，不需要样例集；本体还没发布时，运行结果会明确报错。
                    </p>
                  )}
                  <Field label="期望结果">
                    <Select value={cqForm.expected_kind} onChange={event => setCqForm({ ...cqForm, expected_kind: event.target.value })}>
                      <option value="row_count">行数</option>
                      <option value="rows_contain">包含指定行</option>
                      <option value="golden_rows">黄金结果</option>
                      <option value="empty">结果为空</option>
                      <option value="boolean">布尔</option>
                      <option value="manual">人工确认</option>
                    </Select>
                  </Field>
                  {cqForm.expected_kind === "row_count" && (
                    <>
                      <Field label="比较">
                        <Select value={cqForm.expected_operator} onChange={event => setCqForm({ ...cqForm, expected_operator: event.target.value })}>
                          <option value="eq">等于</option>
                          <option value="gte">至少</option>
                          <option value="lte">至多</option>
                        </Select>
                      </Field>
                      <Field label="行数"><TextInput value={cqForm.expected_value} inputMode="numeric"
                                                     onChange={event => setCqForm({ ...cqForm, expected_value: event.target.value })} /></Field>
                    </>
                  )}
                  {["rows_contain", "golden_rows"].includes(cqForm.expected_kind) && (
                    <Field label="期望行 JSON" top hint='例如 [{"plate":"沪A12345"}]'>
                      <Area rows={3} value={cqForm.expected_rows} onChange={event => setCqForm({ ...cqForm, expected_rows: event.target.value })} />
                    </Field>
                  )}
                  {cqForm.expected_kind === "boolean" && (
                    <Field label="真/假">
                      <Select value={cqForm.expected_value} onChange={event => setCqForm({ ...cqForm, expected_value: event.target.value })}>
                        <option value="true">为真（有结果）</option>
                        <option value="false">为假（无结果）</option>
                      </Select>
                    </Field>
                  )}
                  <Field label="备注" top>
                    <Area rows={2} value={cqForm.rationale} onChange={event => setCqForm({ ...cqForm, rationale: event.target.value })} />
                  </Field>
                </div>
                {cqError && <div className="banner err">{cqError}</div>}
                <div className="btn-row">
                  <Btn busy={busy === "cq"} onClick={saveCq}><CheckCircle size={14} /> 保存 CQ</Btn>
                  <Btn variant="subtle" onClick={() => setCqOpen(false)}>取消</Btn>
                </div>
              </div>
            )}
          </Card>
          )}

          {activeStepKey === "reuse" && (
          <Card id="wf-reuse" title="03 · 复用调研"
                sub="先看行业标准、已有本体或词表能不能复用；不复用也要留下理由。"
                aside={<Tag kind={detail.steps[2]?.status === "done" ? "ok" : "warn"}>{detail.steps[2]?.status === "done" ? "已记录" : "待调研"}</Tag>}>
            <div className="wf-inline wf-skip">
              <span className="muted small">本轮不复用行业已有模型</span>
              <Btn variant="subtle" size="sm" busy={busy === "reuse-skip"} onClick={toggleReuseSkip}>
                {reuseSkipped ? "取消标记" : "标记不复用"}
              </Btn>
            </div>
            {detail.reuse.length > 0 && (
              <div className="wf-list">
                {detail.reuse.map(item => (
                  <div key={item.id} className="wf-row">
                    <div className="wf-row-main">
                      <div className="wf-inline">
                        <Tag kind="plain">{item.decision}</Tag>
                        <b>{item.title}</b>
                      </div>
                      <div className="wf-meta">
                        <span>来源：{item.source || "—"}</span>
                        <span>引用：{item.reference || "—"}</span>
                        <span>{item.note}</span>
                      </div>
                    </div>
                    <div className="wf-row-actions">
                      <button type="button" className="icon-btn danger" title="删除记录"
                              onClick={() => void mutate("reuse-delete", () => api.deleteWorkflowReuse(wid!, round.id, item.id), "记录已删除")}>
                        <Trash size={14} />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
            <Collapsible label="记录一条复用调研">
              <div className="wf-form-grid">
                <Field label="标题"><TextInput value={reuseForm.title} onChange={event => setReuseForm({ ...reuseForm, title: event.target.value })} /></Field>
                <Field label="结论">
                  <Select value={reuseForm.decision} onChange={event => setReuseForm({ ...reuseForm, decision: event.target.value as ReuseDraft["decision"] })}>
                    <option value="build">自建</option>
                    <option value="reuse">直接复用</option>
                    <option value="extend">扩展复用</option>
                    <option value="skip">不复用</option>
                  </Select>
                </Field>
                <Field label="来源/标准"><TextInput value={reuseForm.source} placeholder="Schema.org、行业本体、内部词表…"
                                                    onChange={event => setReuseForm({ ...reuseForm, source: event.target.value })} /></Field>
                <Field label="链接/版本"><TextInput value={reuseForm.reference} onChange={event => setReuseForm({ ...reuseForm, reference: event.target.value })} /></Field>
                <Field label="结论说明" top><Area rows={2} value={reuseForm.note} onChange={event => setReuseForm({ ...reuseForm, note: event.target.value })} /></Field>
              </div>
              <Btn size="sm" busy={busy === "reuse"} onClick={saveReuse}><Plus size={13} /> 记录调研</Btn>
            </Collapsible>
          </Card>
          )}

          {activeStepKey === "sketch" && (
          <Card id="wf-sketch" title="04 · 概念草图"
                sub="结构编辑仍然走「结构」标签页；这里只确认草图已经长出来。"
                aside={<Tag kind={detail.steps[3]?.status === "done" ? "ok" : "warn"}>{detail.ontology.objects.length} 个对象</Tag>}>
            <div className="wf-structure">
              <div><span className="muted small">对象</span><div className="tag-list">{detail.ontology.objects.length ? detail.ontology.objects.map(name => <Tag key={name} kind="plain">{name}</Tag>) : <span className="muted small">—</span>}</div></div>
              <div><span className="muted small">关系</span><div className="tag-list">{detail.ontology.relations.length ? detail.ontology.relations.map(name => <Tag key={name} kind="plain">{name}</Tag>) : <span className="muted small">—</span>}</div></div>
            </div>
            <div className="btn-row">
              <Btn variant="subtle" size="sm" onClick={() => onShowStructure("table")}>去结构编辑 <ArrowRight size={13} /></Btn>
              <Btn variant="subtle" size="sm" onClick={() => onShowStructure("graph")}>看关系图 <ArrowRight size={13} /></Btn>
            </div>
          </Card>
          )}

          {activeStepKey === "formalize" && (
          <Card id="wf-formalize" title="05 · 形式化"
                sub="平台用编译前的语义检查确认结构可以作为运行时契约；这一步不会改模型。"
                aside={<Tag kind={detail.validation?.ok ? "ok" : "warn"}>{detail.validation?.ok ? "已通过" : "待检查"}</Tag>}>
            {detail.validation?.ok ? (
              <p className="muted small">语义检查已通过 · {detail.validation.checked_at || ""}</p>
            ) : detail.validation?.problems?.length ? (
              <div className="banner warn">
                <b>语义检查问题：</b>
                <ul className="doc-list">{detail.validation.problems.map(problem => <li key={problem}>{problem}</li>)}</ul>
              </div>
            ) : (
              <p className="muted small">还没有运行语义检查。结构改完后点一次；结果会绑定当前本体指纹，改结构后自动过期。</p>
            )}
            <div className="btn-row">
              <Btn busy={busy === "check"} onClick={checkRound}><ListChecks size={14} /> 运行发布前检查</Btn>
            </div>
          </Card>
          )}

          {activeStepKey === "samples" && (
          <Card id="wf-samples" title="06 · 样例实例"
                sub="样例集是隔离的临时数据：只用于验证 CQ，不写进运行时。"
                aside={<div className="wf-inline">
                  <Tag kind={detail.samples.length ? "ok" : "warn"}>{detail.samples.length} 组 / {detail.samples.reduce((sum, sample) => sum + sample.row_count, 0)} 行</Tag>
                  <Btn size="sm" onClick={openAddSample}><Plus size={13} /> 加样例</Btn>
                </div>}>
            {!detail.samples.length ? (
              <Empty title="还没有样例" hint="按本体生成一组最小实例，或粘贴真实脱敏数据。至少覆盖 P0 CQ。"
                     action={<Btn onClick={openAddSample}><Plus size={14} /> 添加样例集</Btn>} />
            ) : (
              <div className="wf-list">
                {detail.samples.map(sample => (
                  <div key={sample.id} className="wf-row">
                    <div className="wf-row-main">
                      <div className="wf-inline"><b>{sample.name}</b><Tag kind="plain">{sample.row_count} 行</Tag></div>
                      <div className="wf-meta">
                        {Object.entries(sample.objects).map(([type, rows]) => <span key={type}>{type} × {rows.length}</span>)}
                        <span>{sample.relations.length} 条关系</span>
                      </div>
                      {sample.description && <p className="muted small">{sample.description}</p>}
                      {sample.validation && !sample.validation.ok && (
                        <div className="banner warn">
                          与当前本体不一致：
                          {(sample.validation.problems || []).slice(0, 3).join("；")}
                        </div>
                      )}
                    </div>
                    <div className="wf-row-actions">
                      <button type="button" className="icon-btn" title="编辑样例" onClick={() => openEditSample(sample)}><PencilSimple size={14} /></button>
                      <button type="button" className="icon-btn danger" title="删除样例"
                              onClick={() => void mutate("sample-delete", () => api.deleteWorkflowSample(wid!, round.id, sample.id), "样例已删除")}>
                        <Trash size={14} />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
            {sampleOpen && (
              <div className="wf-form-block">
                <div className="wf-form-head">
                  <h4 className="h4">{sampleForm.id ? "编辑样例集" : "新增样例集"}</h4>
                  <button type="button" className="icon-btn" onClick={() => setSampleOpen(false)} aria-label="关闭样例表单"><X size={15} /></button>
                </div>
                <div className="wf-form-grid">
                  <Field label="名称"><TextInput value={sampleForm.name} onChange={event => setSampleForm({ ...sampleForm, name: event.target.value })} /></Field>
                  <Field label="说明"><TextInput value={sampleForm.description} onChange={event => setSampleForm({ ...sampleForm, description: event.target.value })} /></Field>
                  <Field label="对象实例 JSON" top hint="按对象类型分组；每行 id 是业务主键，其余字段是属性。">
                    <Area rows={10} value={sampleForm.objects} onChange={event => setSampleForm({ ...sampleForm, objects: event.target.value })} />
                  </Field>
                  <Field label="关系实例 JSON" top hint='例如 [{"name":"passes","source":"Vehicle/V001","target":"TollStation/ST001"}]'>
                    <Area rows={4} value={sampleForm.relations} onChange={event => setSampleForm({ ...sampleForm, relations: event.target.value })} />
                  </Field>
                </div>
                {sampleError && <div className="banner err">{sampleError}</div>}
                <div className="btn-row">
                  <Btn busy={busy === "sample"} onClick={saveSample}><CheckCircle size={14} /> 保存样例</Btn>
                  <Btn variant="subtle" onClick={() => setSampleForm(sampleDraft(detail))}>重新生成模板</Btn>
                  <Btn variant="subtle" onClick={() => setSampleOpen(false)}>取消</Btn>
                </div>
              </div>
            )}
          </Card>
          )}

          {activeStepKey === "acceptance" && (
          <Card id="wf-acceptance" title="07 · CQ 验证"
                sub="每条 CQ 按自己的引擎执行：样例 fixture 隔离可重复，运行时 runtime 读真实状态；自动 CQ 的通过率决定发布门禁，人工 CQ 不计入。"
                aside={<div className="wf-inline">
                  <Btn busy={busy === "run"} onClick={runAcceptance}><Play size={14} /> 运行验证</Btn>
                </div>}>
            {latestRun ? (
              <>
                <div className="wf-run-summary">
                  <div><span className="wf-stat">{latestRun.summary.pass_rate}%</span><span className="muted small">整体通过率</span></div>
                  <div><span className="wf-stat">{latestRun.summary.p0_pass_rate}%</span><span className="muted small">P0 通过率</span></div>
                  <div><span className="wf-stat">{latestRun.summary.passed}/{latestRun.summary.automated}</span><span className="muted small">自动 CQ</span></div>
                  <div><span className="wf-stat">{latestRun.summary.manual}</span><span className="muted small">人工 CQ</span></div>
                  <Tag kind={latestRun.status === "passed" ? "ok" : "err"}>
                    {latestRun.status === "passed" ? "通过" : "未通过"}
                  </Tag>
                </div>
                <p className="muted small">
                  运行 {latestRun.seq} · 引擎 {latestRun.engine === "mixed" ? "fixture + runtime" : latestRun.engine}
                  {" "}· 本体指纹 <span className="mono">{latestRun.ontology_fingerprint}</span>
                  {latestRun.summary.meets_target ? " · 达到本轮目标" : " · 未达到本轮目标"}
                </p>
                <div className="table-wrap">
                  <table className="table compact">
                    <thead><tr><th>CQ</th><th>优先级</th><th>引擎</th><th>状态</th><th>结果</th></tr></thead>
                    <tbody>
                      {latestRun.results.map(result => (
                        <tr key={result.cq_id}>
                          <td className="mono small">{result.code}</td>
                          <td>{result.priority}</td>
                          <td className="small">{result.engine === "runtime" ? "运行时" : "样例"}</td>
                          <td><Tag kind={cqStatusKind(result.status)}>{cqStatusLabel(result.status)}</Tag></td>
                          <td className="small">{result.detail}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {detail.runs.length > 1 && <p className="muted small">历史运行 {detail.runs.length - 1} 次，以最新一次为准。</p>}
              </>
            ) : (
              <Empty title="还没有运行过验证" hint="至少有一条 CQ；样例 CQ 还需要一组样例，运行时 CQ 需要已发布本体。" />
            )}
            {detail.trace_matrix.length > 0 && (
              <Collapsible label={`追踪矩阵（${detail.trace_matrix.length} 条 CQ → 模型/样例/结果）`}>
                <div className="table-wrap">
                  <table className="table compact">
                    <thead><tr><th>CQ</th><th>问题</th><th>模型</th><th>样例</th><th>查询</th><th>状态</th></tr></thead>
                    <tbody>
                      {detail.trace_matrix.map(row => (
                        <tr key={row.cq_id}>
                          <td className="mono small">{row.code}</td>
                          <td className="small">{row.question}</td>
                          <td className="small">{row.model_refs.join(", ") || "—"}</td>
                          <td className="small">{row.sample_refs.join(", ") || "全部"}</td>
                          <td className="small">{queryLabel(row.query)}</td>
                          <td><Tag kind={cqStatusKind(row.status)}>{cqStatusLabel(row.status)}</Tag></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Collapsible>
            )}
          </Card>
          )}

          {activeStepKey === "review" && (
          <Card id="wf-review" title="08 · 评审发布"
                sub="评审看的是本轮证据：CQ、样例、验证结果和语义检查；通过后才允许发布。"
                aside={<Tag kind={gate.ok ? "ok" : "warn"}>{gate.ok ? "门禁已清" : "还有门禁"}</Tag>}>
            {latestReview && (
              <div className={`banner ${latestReview.decision === "approve" ? "info" : "warn"}`}>
                最近评审：{latestReview.decision === "approve" ? "通过" : latestReview.decision === "reject" ? "驳回" : "要求修改"}
                · {latestReview.actor} · {latestReview.comment || "无备注"}
              </div>
            )}
            {round.status !== "accepted" && (
              <>
                <Field label="评审意见" top>
                  <Area rows={2} value={reviewComment} placeholder="哪些 CQ 还有疑问？证据是否足够？"
                        onChange={event => setReviewComment(event.target.value)} />
                </Field>
                <div className="btn-row">
                  <Btn busy={busy === "review"} onClick={() => submitReview("approve")}><SealCheck size={14} /> 评审通过</Btn>
                  <Btn variant="subtle" busy={busy === "review"} onClick={() => submitReview("changes_requested")}>要求修改</Btn>
                  <Btn variant="danger" busy={busy === "review"} onClick={() => submitReview("reject")}>驳回</Btn>
                </div>
              </>
            )}
            {round.status === "accepted" ? (
              <div className="banner info">
                本轮已发布 v{round.published_version}。下一轮从这里继续，不要覆盖已发布版本。
              </div>
            ) : (
              <div className="wf-publish">
                <div>
                  <b>{gate.ok ? "可以发布" : "发布被阻塞"}</b>
                  <p className="muted small">发布动作仍走平台原有的本体发布门禁，并记录本轮证据。</p>
                </div>
                <Btn busy={busy === "publish"} disabled={!gate.ok} onClick={publish}>
                  <SealCheck size={14} /> 发布本轮
                </Btn>
              </div>
            )}
            <div className="btn-row">
              <Btn variant="subtle" size="sm" busy={busy === "report"} onClick={loadReport}><FileText size={13} /> 查看报告</Btn>
              {detail.cqs.length > 0 && <Btn variant="subtle" size="sm" onClick={() => goStep("acceptance")}>回到验证</Btn>}
            </div>
            {report && (
              <div className="wf-report">
                <div className="wf-form-head"><h4 className="h4">本轮报告</h4>
                  <button type="button" className="icon-btn" onClick={() => setReport("")} aria-label="关闭报告"><X size={15} /></button>
                </div>
                <pre>{report}</pre>
              </div>
            )}
          </Card>
          )}

          <div className="wf-pager">
            <Btn variant="subtle" size="sm" disabled={activeStepIndex <= 0}
                 onClick={() => moveStep(-1)}>上一页</Btn>
            <span className="muted small">
              第 {activeStepIndex + 1} / {detail.steps.length} 页
            </span>
            <Btn variant="subtle" size="sm" disabled={activeStepIndex >= detail.steps.length - 1}
                 onClick={() => moveStep(1)}>下一页 <ArrowRight size={13} /></Btn>
          </div>
        </div>

        <aside className="wf-aside">
          <Card title="发布门禁" tone={gate.ok ? "green" : "amber"} id="wf-gate">
            <div className="wf-gate-status">
              <Tag kind={gateTone(gate)}>{gate.ok ? "可发布" : "未清"}</Tag>
              <span className="muted small">{gate.step === "warning" ? "有提醒，无阻塞" : gate.step === "blocked" ? "存在阻塞" : "—"}</span>
            </div>
            {gate.blockers.length > 0 && (
              <div className="wf-gate-list">
                <b className="small">阻塞</b>
                <ul className="doc-list">{gate.blockers.map(item => <li key={item}>{item}</li>)}</ul>
              </div>
            )}
            {gate.warnings.length > 0 && (
              <div className="wf-gate-list">
                <b className="small">提醒</b>
                <ul className="doc-list">{gate.warnings.map(item => <li key={item}>{item}</li>)}</ul>
              </div>
            )}
            {!gate.blockers.length && !gate.warnings.length && <p className="muted small">所有门禁已满足。</p>}
          </Card>
          <Card title="本轮证据" tight>
            <dl className="kv wf-kv">
              <dt>本体指纹</dt><dd className="mono small">{detail.ontology.fingerprint}</dd>
              <dt>CQ</dt><dd>{detail.cqs.length} 条（P0 {detail.cqs.filter(cq => cq.priority === "P0").length}）</dd>
              <dt>样例</dt><dd>{detail.samples.length} 组 / {detail.samples.reduce((sum, sample) => sum + sample.row_count, 0)} 行</dd>
              <dt>验证</dt><dd>{latestRun ? `${latestRun.status === "passed" ? "通过" : "未通过"} · ${latestRun.summary.pass_rate}%` : "未运行"}</dd>
              <dt>评审</dt><dd>{latestReview ? (latestReview.decision === "approve" ? "通过" : latestReview.decision) : "未评审"}</dd>
              <dt>发布</dt><dd>{round.published_version ? `v${round.published_version}` : "未发布"}</dd>
            </dl>
          </Card>
          <Card title="下一步" tight>
            <p className="small">{detail.steps.find(step => step.status !== "done")?.action || "本轮已完成，可以开下一轮。"}</p>
          </Card>
        </aside>
      </div>
    </div>
  );
}
