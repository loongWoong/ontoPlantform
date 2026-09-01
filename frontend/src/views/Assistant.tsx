import { useState } from "react";
import { api, type AskResult, type Obj } from "../api";
import { toChoice, useAction, usePlatform } from "../store";
import { LlmPicker } from "../LlmPicker";
import { Btn, Card, Empty, Field, Loading, TableWrap, Tag, TextInput, Timeline } from "../ui";

export function AssistantView() {
  const { wid, objectTypes } = usePlatform();
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
    <div className="layout main-panel">
      <Card title="问业务世界" sub="先在已发布的本体里检索对象、关系与术语，再据此作答 —— 没发布的东西它看不到。">
        <form className="panel-form" onSubmit={e => { e.preventDefault(); void run(); }}>
          <Field label="你的问题" hint="例如「余额不足的车辆有哪些？」「Vehicle/V001 现在什么情况？」">
            <TextInput value={question} placeholder="余额不足的车辆有哪些？" onChange={e => setQuestion(e.target.value)} />
          </Field>
          <div className="btn-row">
            <Btn type="submit" busy={running}>提问</Btn>
            {!suggestions.length && <span className="muted">先去「本体」发布，才有东西可问</span>}
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
          {!running && !answer && <Empty title="还没有提问" hint="问题只在这个工作空间里检索，不会跨空间。" />}
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
              <p className="muted">问题：{asked}</p>

              {!!(answer.objects || []).length && (
                <>
                  <h4 className="h4">依据 · 命中的对象</h4>
                  <AnswerObjects objects={answer.objects} />
                </>
              )}
              {!!(answer.related || []).length && (
                <>
                  <h4 className="h4">依据 · 关联对象</h4>
                  <TableWrap auto>
                    <table>
                      <thead><tr><th>从</th><th>关系</th><th>到</th></tr></thead>
                      <tbody>
                        {answer.related.map((r, i) => (
                          <tr key={i}>
                            <td className="nowrap">{r.from}</td>
                            <td className="nowrap">{r.relation}</td>
                            <td className="nowrap">{r.object.type}/{r.object.id}</td>
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
  );
}

function AnswerObjects({ objects }: { objects: Obj[] }) {
  const { go } = usePlatform();
  return (
    <TableWrap auto>
      <table>
        <thead><tr><th>引用</th><th className="wrap">属性</th><th> </th></tr></thead>
        <tbody>
          {objects.map(o => (
            <tr key={`${o.type}/${o.id}`}>
              <td className="nowrap">{o.type}/{o.id}</td>
              <td className="wrap" title={JSON.stringify(o.properties)}>{JSON.stringify(o.properties)}</td>
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
  const { wid, llm, objectTypes } = usePlatform();
  const [goal, setGoal] = useState("");
  const [steps, setSteps] = useState("8");
  const [run, setRun] = useState<Awaited<ReturnType<typeof api.agent>> | null>(null);

  const { run: fire, running } = useAction(async () => {
    if (!wid || !goal.trim()) return;
    setRun(await api.agent(wid, goal.trim(), Number(steps) || 8, toChoice(llm, objectTypes[0])));
  }, [wid, goal, steps, llm, objectTypes]);

  return (
    <Card title="交给它一件事" sub="不是问答，是让它自己规划：观察 → 行动 → 结论，每一步都留痕。">
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
          <Tag kind={llm.mode === "demo" ? "warn" : "plain"}>
            {llm.mode === "demo" ? "演示模式：脚本化推理" : llm.mode === "openai" ? llm.model || "外部模型" : "平台默认模型"}
          </Tag>
        </div>
      </form>

      <div className="answer-zone">
        {running && <Loading text="它在业务世界里行动…" />}
        {!running && !run && <Empty title="还没有跑过" hint="写一句业务目标，点开始。" />}
        {!running && run && (
          <>
            <div className={`answer final${run.ok ? "" : " err"}`}>{run.answer}</div>
            <p className="muted">{run.ok ? "已完成" : "未完成"} · {(run.steps || []).length} 步 · 目标「{run.goal}」</p>
            <Timeline steps={run.steps || []} />
          </>
        )}
      </div>
    </Card>
  );
}
