import { useState } from "react";
import { Sparkle } from "@phosphor-icons/react";
import { api, type AskResult, type Obj } from "../api";
import { toChoice, useAction, usePlatform } from "../store";
import { LlmPicker } from "../LlmPicker";
import {
  Btn, Card, Empty, Field, Loading, PageHead, TableWrap, Tag, TextInput, Timeline,
} from "../ui";
import { StatusBadge } from "../components/StatusBadge";
import { ValueView } from "../components/ValueView";
import { executionStatus } from "../lib/status";

export function AssistantView() {
  const { wid, objectTypes, go } = usePlatform();
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<AskResult | null>(null);
  const [asked, setAsked] = useState("");

  const { run, running } = useAction(async () => {
    if (!wid || !question.trim()) return;
    setAsked(question.trim());
    setAnswer(await api.ask(wid, question.trim()));
  }, [wid, question]);

  const suggestions = [
    objectTypes[0] ? `${objectTypes[0]} 都有哪些？` : "",
    objectTypes[0] ? `${objectTypes[0]} 之间是什么关系？` : "",
    "这个业务世界里都能做什么动作？",
  ].filter(Boolean);

  return (
    <>
      <PageHead
        kicker="Assistant"
        title="AI 助手"
        desc="先在本体里检索对象、关系与业务术语，再据此作答；没发布的东西它看不到，也不会凭空编造对象。"
        meta={<>
          <span>对象类型 <b>{objectTypes.length}</b></span>
          <span>检索范围 <b>当前工作空间</b></span>
          <span>回答依据 <b>本体 + 运行时</b></span>
        </>}
        actions={objectTypes.length ? (
          <Btn variant="subtle" onClick={() => go("state")}>浏览对象实例</Btn>
        ) : (
          <Btn variant="subtle" onClick={() => go("ontology")}>先发布本体</Btn>
        )}
      />

      <div className="layout cols-2">
        <Card title="问业务世界" sub="用业务语言提问，回答里会列出依据。">
          <form className="panel-form" onSubmit={e => { e.preventDefault(); void run(); }}>
            <Field label="你的问题" hint="例如「余额不足的车辆有哪些？」「Vehicle/V001 现在什么情况？」">
              <TextInput value={question} placeholder="余额不足的车辆有哪些？" onChange={e => setQuestion(e.target.value)} />
            </Field>
            <div className="btn-row">
              <Btn type="submit" busy={running}><Sparkle size={14} /> 提问</Btn>
              {!suggestions.length && <span className="muted small">先去「本体」发布，才有东西可问。</span>}
            </div>
            {!!suggestions.length && (
              <div className="chips">
                {suggestions.map(s => (
                  <button key={s} type="button" className="chip" onClick={() => setQuestion(s)}>{s}</button>
                ))}
              </div>
            )}
          </form>

          <div className="answer-zone">
            {running && <Loading text="正在检索本体并作答…" />}
            {!running && !answer && <Empty title="还没有提问" hint="问题只在这个工作空间里检索，不会跨工作空间。" />}
            {!running && answer && (
              <>
                <div className="answer final">{answer.answer}</div>
                <div className="chips">
                  {(answer.concepts || []).map(c => (
                    <span key={`${c.term}-${c.concept}`} className="chip on">{c.term} → {c.concept}</span>
                  ))}
                  <span className="chip">命中对象 {(answer.objects || []).length}</span>
                  <span className="chip">关联 {(answer.related || []).length}</span>
                </div>
                <p className="muted small">问题：{asked}</p>

                {!!(answer.objects || []).length && (
                  <>
                    <h3 className="h4">依据 · 命中的对象</h3>
                    <AnswerObjects objects={answer.objects} />
                  </>
                )}
                {!!(answer.related || []).length && (
                  <>
                    <h3 className="h4">依据 · 关联对象</h3>
                    <TableWrap auto>
                      <table>
                        <thead><tr><th>从</th><th>关系</th><th>到</th></tr></thead>
                        <tbody>
                          {answer.related.map((r, i) => (
                            <tr key={i}>
                              <td className="nowrap mono">{r.from}</td>
                              <td className="nowrap">{r.relation}</td>
                              <td className="nowrap mono">{r.object.type}/{r.object.id}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </TableWrap>
                  </>
                )}
              </>
            )}
          </div>
        </Card>

        <GoalRunner />
      </div>
    </>
  );
}

function AnswerObjects({ objects }: { objects: Obj[] }) {
  const { go } = usePlatform();
  return (
    <TableWrap auto>
      <table>
        <thead><tr><th>引用</th><th className="wrap">属性</th><th>操作</th></tr></thead>
        <tbody>
          {objects.map(o => (
            <tr key={`${o.type}/${o.id}`}>
              <td className="nowrap mono">{o.type}/{o.id}</td>
              <td className="wrap"><ValueView value={o.properties} compact /></td>
              <td className="nowrap">
                <Btn size="sm" variant="subtle" onClick={() => go("state", { ref: `${o.type}/${o.id}` })}>打开</Btn>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableWrap>
  );
}

function GoalRunner() {
  const { wid, llm, objectTypes, toast } = usePlatform();
  const [goal, setGoal] = useState("");
  const [steps, setSteps] = useState("8");
  const [run, setRun] = useState<Awaited<ReturnType<typeof api.agent>> | null>(null);

  const { run: fire, running } = useAction(async () => {
    if (!wid || !goal.trim()) return;
    const r = await api.agent(wid, goal.trim(), Number(steps) || 8,
      llm.mode === "demo" ? toChoice(llm, objectTypes[0]) : null);
    setRun(r);
    const st = executionStatus(r);
    if (st === "succeeded") toast("目标已完成，并通过完成校验", "ok");
    else if (st === "failed") toast("目标未完成：任务状态是 failed", "err");
    else toast(`目标结果未知或未完成（${st}）：不要按已完成对外承诺`, "warn");
  }, [wid, goal, steps, llm, objectTypes]);

  const status = run ? executionStatus(run) : null;
  return (
    <Card title="交给它一件事" sub="不是问答，是让它自己规划：观察 → 行动 → 结论；结论以任务状态与完成校验为准。"
          aside={<Tag kind={llm.mode === "demo" ? "warn" : "plain"}>
            {llm.mode === "demo" ? "演示模式" : llm.mode === "openai" ? llm.model || "工作空间模型" : "平台默认模型"}
          </Tag>}>
      <form className="panel-form" onSubmit={e => { e.preventDefault(); void fire(); }}>
        <Field label="业务目标" hint="写业务意图，不要写步骤；步骤由它自己规划。">
          <TextInput value={goal} placeholder="检查余额不足的车辆，并给出处置建议" onChange={e => setGoal(e.target.value)} />
        </Field>
        <Field label="最多步数" hint="步数越多越聪明，也越慢。">
          <TextInput className="w-xs" type="number" min={1} max={24} value={steps} onChange={e => setSteps(e.target.value)} />
        </Field>
        <LlmPicker />
        <div className="btn-row">
          <Btn type="submit" busy={running}>开始</Btn>
        </div>
      </form>

      <div className="answer-zone">
        {running && <Loading text="它在业务世界里行动…" />}
        {!running && !run && <Empty title="还没有跑过" hint="写一句业务目标，点开始。" />}
        {!running && run && (
          <>
            <div className={`answer final${status === "succeeded" ? "" : " err"}`}>{run.answer || "（没有返回结论）"}</div>
            <div className="result-head">
              <StatusBadge status={status || undefined} />
              <span className="muted small">{(run.steps || []).length} 步 · 目标「{run.goal}」</span>
              {run.trace_id && <span className="muted mono small">轨迹 {run.trace_id}</span>}
              {run.run_id && <span className="muted mono small">运行 {run.run_id}</span>}
            </div>
            <p className="muted small">
              {run.trace_id || run.run_id
                ? "本次运行步骤已保存；是否完成以任务状态和完成校验为准。"
                : "本次运行没有返回轨迹编号，步骤只在本页显示，不能当作已持久化的凭证。"}
            </p>
            {!!run.failed_predicates?.length && (
              <div className="banner warn">未满足的完成条件：{run.failed_predicates.join("；")}</div>
            )}
            {run.verification && (
              <>
                <h3 className="h4">完成校验</h3>
                <ValueView value={run.verification} />
              </>
            )}
            <Timeline steps={run.steps || []} />
          </>
        )}
      </div>
    </Card>
  );
}
