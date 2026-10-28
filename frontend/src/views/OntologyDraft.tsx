/** AI 快速建模：一段业务描述 → 后端 LLM 解析出对象/关系/业务术语 → 预览 → 写入草稿本体。
 *
 *  契约：POST /api/workspaces/{wid}/ontology/draft
 *        {text, apply:false, llm?}   只解析预览
 *        {draft, apply:true}         把预览得到的 draft 原样回传写入（不再调模型，
 *                                    保证写进去的就是用户看到的那份）
 *  响应：{applied, draft, warnings, ontology?}
 *  `llm` 仅在用户显式选了「OpenAI 兼容服务」时透传；平台默认交给服务端配置，
 *  演示模式的脚本是给 AI 助手用的，不能当解析脚本，因此也不透传。
 */
import { useRef, useState, type ChangeEvent } from "react";
import { api, type DraftResult } from "../api";
import { LLM_MODE_LABEL } from "../LlmPicker";
import { useAction, usePlatform } from "../store";
import { Area, Btn, Empty, Field, PropertyList, TableWrap } from "../ui";

const MAX_FILE_BYTES = 512 * 1024;
const TERM_KIND_LABEL: Record<string, string> = {
  object: "对象", property: "属性", relation: "关系",
};

export function OntologyDraftPanel({ frozen, onApplied }: {
  frozen: boolean;
  onApplied: () => void | Promise<void>;
}) {
  const { wid, llm, toast, confirm, go } = usePlatform();
  const [text, setText] = useState("");
  const [result, setResult] = useState<DraftResult | null>(null);
  const [parsedText, setParsedText] = useState("");   // 预览时的文本：写入必须基于「看到的那份」
  const fileRef = useRef<HTMLInputElement>(null);

  // 建模解析不是 agent，演示脚本的结构是「观察 → 行动 → 结论」，不能当本体 JSON；
  // 因此这里从不随请求注入 provider，统一走「设置 → 模型服务」保存的平台模型。
  const payload = () => ({ text: text.trim() });
  /** 当前文本对应的预览结果是否可写入：文本没改、还没写过 */
  const previewFresh = !!result && !result.applied && parsedText === text.trim();

  /* ---- 解析预览（apply=false，不写任何数据） ---- */
  const { run: preview, running: parsing } = useAction(async () => {
    if (!wid || !text.trim()) return;
    const r = await api.draftOntology(wid, { ...payload(), apply: false });
    setResult(r);
    setParsedText(text.trim());
    const d = r.draft || {};
    const counts = `${(d.objects || []).length} 对象 · ${(d.relations || []).length} 关系 · ${(d.vocabulary || []).length} 术语`;
    const n = (r.warnings || []).length;
    if (n) toast(`解析完成（${counts}），有 ${n} 条提示`, "warn");
    else toast(`解析完成：${counts}`, "ok");
  }, [wid, text, llm]);

  /* ---- 解析并写入：把预览得到的 draft 回传，后端不再调模型 ---- */
  const writeDraft = () => {
    if (!wid) return;
    if (!text.trim()) { toast("先粘贴一段业务描述，再解析写入", "err"); return; }
    if (frozen) { toast("本体已发布，定义已锁定，不能再写入", "err"); return; }
    if (!result) { toast("请先「解析预览」核对结果：写入的就是你看到的那份", "warn"); return; }
    if (parsedText !== text.trim()) { toast("文本改过了，请重新「解析预览」再写入，保证写进去的和看到的一致", "warn"); return; }
    if (result.applied) { toast("这份结果已经写入过草稿；要再写一次请重新解析预览", "warn"); return; }
    const draft = result.draft || {};
    confirm({
      title: "把预览结果写入草稿本体？",
      danger: true,
      confirmText: "确认写入",
      body: (
        <>
          <p className="muted">
            写入的就是刚刚预览的那份结果（不会再调用模型），同名定义会覆盖。
            已发布的本体不能改。
          </p>
          <dl className="kv">
            <dt>对象</dt><dd>{(draft.objects || []).length} 个</dd>
            <dt>关系</dt><dd>{(draft.relations || []).length} 条</dd>
            <dt>业务术语</dt><dd>{(draft.vocabulary || []).length} 个</dd>
          </dl>
        </>
      ),
      run: async () => {
        const r = await api.draftOntology(wid, { draft, apply: true });
        setResult(r);
        await onApplied();          // 重新加载本体（对象/关系/术语）与侧栏完成度
        toast("已写入草稿本体，对象、关系、术语列表已刷新", "ok");
      },
    });
  };

  /* ---- 从文本文件读进文本框：只读入，不上传 ---- */
  const readFile = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";          // 清空，允许再次选择同一个文件
    if (!file) return;
    if (file.size > MAX_FILE_BYTES) {
      toast("文件超过 512KB，请只保留描述本体的关键段落", "err");
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      const t = String(reader.result || "");
      if (!t.trim()) { toast(`「${file.name}」是空文件`, "warn"); return; }
      setText(t);
      toast(`已从「${file.name}」读入文本`, "ok");
    };
    reader.onerror = () => toast("读取文件失败，请换一个纯文本文件", "err");
    reader.readAsText(file, "utf-8");
  };

  const d = result?.draft || {};
  const objects = d.objects || [];
  const relations = d.relations || [];
  const vocabulary = d.vocabulary || [];
  const warnings = result?.warnings || [];
  const total = objects.length + relations.length + vocabulary.length;

  return (
    <div>
      <p className="muted">
        把一段业务描述（需求说明、会议记录都行）粘进来，AI 会拆成对象、关系和业务术语：
        先「解析预览」核对，确认无误再「解析并写入」；写进去的就是你看到的那份结果。
      </p>
      {frozen && (
        <div className="banner warn">本体已发布，定义已锁定：这里只能解析预览，不能写入。</div>
      )}

      <form onSubmit={e => { e.preventDefault(); if (text.trim()) void preview(); }}>
        <Field label="业务描述" top
               hint="写清有哪些业务对象、各自的属性、它们之间怎么关联，以及业务人员怎么称呼它们。">
          <Area rows={7} value={text} aria-label="业务描述"
                placeholder="例如：车辆经过收费站时记一笔通行记录，记录车牌、时间、金额；车辆属于某个车主，收费站位于某条高速上……"
                onChange={e => setText(e.target.value)}
                onKeyDown={e => {
                  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                    e.preventDefault();
                    if (text.trim()) void preview();
                  }
                }} />
        </Field>
        <div className="btn-row">
          <Btn type="submit" variant="tint" busy={parsing} disabled={!text.trim()}>
            解析预览
          </Btn>
          <Btn disabled={!text.trim() || frozen} onClick={writeDraft}>解析并写入</Btn>
          <Btn variant="subtle" onClick={() => fileRef.current?.click()}>从文件读取</Btn>
          <input ref={fileRef} type="file" hidden
                 accept=".txt,.md,.markdown,.text,.json,.csv,text/plain"
                 aria-label="选择业务描述文件" onChange={readFile} />
        </div>
        {!!text.trim() && !previewFresh && !result?.applied && (
          <p className="muted">还没预览或文本改过了：先「解析预览」，写入的只可能是预览过的那份结果。</p>
        )}
      </form>

      <div className="chips">
        <span className="chip">
          模型来源：{LLM_MODE_LABEL[llm.mode] || llm.mode}
          {llm.mode === "openai" && llm.model ? ` · ${llm.model}` : ""}
        </span>
        {llm.mode === "demo" && (
          <span className="chip">演示模式不驱动建模，本次按平台服务端配置调用</span>
        )}
        <Btn variant="link" size="sm" onClick={() => go("settings", { tab: "llm" })}>
          去设置里换模型
        </Btn>
      </div>

      <div className="answer-zone">
        {!result && <Empty title="还没有解析结果" hint="先写一段业务描述，点「解析预览」看看 AI 拆成了什么。" />}
        {result && (
          <>
            <div className="chips">
              <span className="chip on">对象 {objects.length}</span>
              <span className="chip on">关系 {relations.length}</span>
              <span className="chip on">业务术语 {vocabulary.length}</span>
              <span className="chip">{result.applied ? "已写入草稿" : "仅预览 · 未写入"}</span>
            </div>
            {!!warnings.length && (
              <div className="banner warn">
                <b>后端提示（{warnings.length}）</b>
                <ul className="doc-list">{warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
              </div>
            )}
            {!total && (
              <Empty title="这次没有解析出内容"
                     hint="换一段更具体的业务描述，或到「设置 · 模型服务」确认模型可用后重试。" />
            )}

            {!!objects.length && (
              <>
                <h3 className="h4">对象（{objects.length}）</h3>
                <TableWrap auto>
                  <table>
                    <thead><tr><th>对象</th><th className="wrap">业务含义</th><th className="wrap">属性</th></tr></thead>
                    <tbody>
                      {objects.map((o, i) => (
                        <tr key={`${o.name}-${i}`}>
                          <td className="nowrap"><b>{o.name}</b></td>
                          <td className="wrap">{o.description || "–"}</td>
                          <td className="wrap">
                            <PropertyList properties={o.properties} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </TableWrap>
              </>
            )}

            {!!relations.length && (
              <>
                <h3 className="h4">关系（{relations.length}）</h3>
                <TableWrap auto>
                  <table>
                    <thead><tr><th>关系</th><th>源 → 目标</th><th>基数</th><th className="wrap">含义</th></tr></thead>
                    <tbody>
                      {relations.map((r, i) => (
                        <tr key={`${r.name}-${i}`}>
                          <td className="nowrap"><b>{r.name}</b></td>
                          <td className="nowrap">{r.source} → {r.target}</td>
                          <td className="nowrap">{r.cardinality || "–"}</td>
                          <td className="wrap">{r.description || "–"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </TableWrap>
              </>
            )}

            {!!vocabulary.length && (
              <>
                <h3 className="h4">业务术语（{vocabulary.length}）</h3>
                <TableWrap auto>
                  <table>
                    <thead><tr><th>说法</th><th>指向</th><th>种类</th><th className="wrap">同义词</th></tr></thead>
                    <tbody>
                      {vocabulary.map((v, i) => (
                        <tr key={`${v.term}-${i}`}>
                          <td className="nowrap">{v.term}</td>
                          <td className="nowrap">{v.concept}</td>
                          <td className="nowrap">{TERM_KIND_LABEL[v.kind || "object"] || v.kind || "–"}</td>
                          <td className="wrap">{(v.aliases || []).join("、") || "–"}</td>
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
    </div>
  );
}
