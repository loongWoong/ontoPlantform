import { useEffect, useState } from "react";
import { api, type AskResult, type Obj } from "../api";
import { useAction, usePlatform } from "../store";
import { Btn, Drawer, Empty, Field, TableWrap, TextInput } from "../ui";

/** 右下角「问一问」：带本体依据的回答，随时可开，不打断当前页面。 */
export function AiDrawer() {
  const { aiOpen, setAiOpen, wid, aiContext, setAiContext, go, confirm } = usePlatform();
  const [q, setQ] = useState("");
  const [res, setRes] = useState<AskResult | null>(null);

  useEffect(() => { if (aiContext) { setQ(aiContext); setAiContext(""); } }, [aiContext, setAiContext]);

  const { run, running } = useAction(async () => {
    if (!wid) return;
    if (!q.trim()) return;
    setRes(await api.ask(wid, q.trim()));
  }, [wid, q]);

  const openObject = (o: Obj) => {
    const ref = `${o.type}/${o.id}`;
    confirm({
      title: "跳到对象实例？",
      body: <p className="muted">将打开「对象实例」页并定位到 {ref}。</p>,
      run: async () => { go("state", { ref }); setAiOpen(false); },
    });
  };

  if (!aiOpen) return null;
  return (
    <Drawer title="问一问" sub="先在本体里检索，再作答，并给出依据" wide onClose={() => setAiOpen(false)}
            footer={<>
              <Btn busy={running} onClick={() => void run()}>提问</Btn>
              <Btn variant="subtle" onClick={() => setAiOpen(false)}>关闭</Btn>
            </>}>
      <form onSubmit={e => { e.preventDefault(); void run(); }}>
        <Field label="问题" hint="用业务语言问，例如「余额不足的车辆有哪些？」「Vehicle/V001 现在什么情况？」">
          <TextInput value={q} placeholder="余额不足的车辆有哪些？" autoFocus
                     onChange={e => setQ(e.target.value)} />
        </Field>
      </form>
      <p className="muted">这里的回答由平台按已发布的本体与状态<b>检索拼装</b>，不调用模型；
             需要模型推理请用「AI 助手 → 交给它一件事」。</p>

      <div className="answer-zone">
        {!res && <Empty title="还没有回答" hint="回答只依据这个工作空间里已发布的本体与数据。" />}
        {res && (
          <>
            <div className="answer final">{res.answer}</div>
            <div className="chips">
              {(res.concepts || []).map(c => (
                <span key={`${c.term}-${c.concept}`} className="chip on">{c.term} → {c.concept}</span>
              ))}
              <span className="chip">命中对象 {(res.objects || []).length}</span>
              <span className="chip">关联 {(res.related || []).length}</span>
            </div>

            {!!(res.objects || []).length && (
              <>
                <h4 className="h4">它看到了哪些对象</h4>
                <TableWrap auto>
                  <table>
                    <thead><tr><th>引用</th><th className="wrap">属性</th><th> </th></tr></thead>
                    <tbody>
                      {res.objects.map(o => (
                        <tr key={`${o.type}/${o.id}`}>
                          <td className="nowrap">{o.type}/{o.id}</td>
                          <td className="wrap" title={JSON.stringify(o.properties)}>{JSON.stringify(o.properties)}</td>
                          <td className="nowrap"><Btn size="sm" variant="subtle" onClick={() => openObject(o)}>打开</Btn></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </TableWrap>
              </>
            )}

            {!!(res.related || []).length && (
              <>
                <h4 className="h4">顺带看到的关系</h4>
                <TableWrap auto>
                  <table>
                    <thead><tr><th>从</th><th>关系</th><th>到</th></tr></thead>
                    <tbody>
                      {res.related.map((r, i) => (
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
    </Drawer>
  );
}
