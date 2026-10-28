import { useState } from "react";
import {
  ArrowRight, CheckCircle, ListChecks, SealCheck, WarningCircle,
} from "@phosphor-icons/react";
import {
  api, type ObjectTypeSpec, type OntologyLint, type PropertySpec,
  type RelationSpec, type VocabTerm,
} from "../api";
import { useAction, usePlatform } from "../store";
import { OntologyDraftPanel } from "./OntologyDraft";
import {
  Btn, Card, Collapsible, Empty, ErrorNote, Field, KIND_LABEL, LIFECYCLE_LABEL,
  Loading, PageHead, PropertyList, RowEditor, Select, TableWrap, Tag, TextInput, useLoad, type Row,
} from "../ui";

const TYPES = ["string", "number", "integer", "boolean"];
type Panel = "object" | "relation" | "term";

export function OntologyView() {
  const { wid, toast, confirm, refreshRuntime, go, objectTypes } = usePlatform();
  const [panel, setPanel] = useState<Panel>("object");
  const [lint, setLint] = useState<OntologyLint | null>(null);
  const load = useLoad(() => (wid ? api.ontology(wid) : Promise.resolve(null)), [wid]);
  const ont = load.data;
  const frozen = ont?.lifecycle === "published";

  /* ---- 新增对象 ---- */
  const [objName, setObjName] = useState("");
  const [objDesc, setObjDesc] = useState("");
  const [props, setProps] = useState<Row[]>([{ a: "", b: "string" }]);
  const { run: addObject, running: addingObj } = useAction(async () => {
    if (!wid) return;
    if (!objName.trim()) { toast("请填写对象名，例如 Vehicle", "err"); return; }
    const properties: PropertySpec[] = props
      .filter(r => r.a.trim())
      .map(r => ({ name: r.a.trim(), type: r.b || "string" }));
    await api.addObject(wid, { name: objName.trim(), description: objDesc.trim(), properties });
    setObjName(""); setObjDesc(""); setProps([{ a: "", b: "string" }]);
    toast(`对象「${objName.trim()}」已加入草稿`, "ok");
    load.reload();
  }, [wid, objName, objDesc, props]);

  /* ---- 新增关系 ---- */
  const objectNames = Object.keys(ont?.objects || {});
  const [relName, setRelName] = useState("");
  const [relSource, setRelSource] = useState("");
  const [relTarget, setRelTarget] = useState("");
  const [relDesc, setRelDesc] = useState("");
  const { run: addRelation, running: addingRel } = useAction(async () => {
    if (!wid) return;
    if (!relName.trim() || !relSource || !relTarget) { toast("关系名、源对象、目标对象都要填", "err"); return; }
    await api.addRelation(wid, { name: relName.trim(), source: relSource, target: relTarget, description: relDesc.trim() });
    setRelName(""); setRelDesc("");
    toast(`关系「${relName.trim()}」已加入草稿`, "ok");
    load.reload();
  }, [wid, relName, relSource, relTarget, relDesc]);

  /* ---- 新增术语 ---- */
  const [term, setTerm] = useState("");
  const [concept, setConcept] = useState("");
  const [termKind, setTermKind] = useState("object");
  const [aliases, setAliases] = useState("");
  const { run: addTerm, running: addingTerm } = useAction(async () => {
    if (!wid) return;
    if (!term.trim() || !concept.trim()) { toast("术语和它指向的概念都要填", "err"); return; }
    await api.addTerm(wid, {
      term: term.trim(), concept: concept.trim(), kind: termKind,
      aliases: aliases.split(/[，,、]/).map(s => s.trim()).filter(Boolean),
    });
    setTerm(""); setConcept(""); setAliases("");
    toast(`术语「${term.trim()}」已加入草稿`, "ok");
    load.reload();
  }, [wid, term, concept, termKind, aliases]);

  /* ---- 检查 / 发布 / 修订 ---- */
  const { run: validate, running: validating } = useAction(async () => {
    if (!wid) return;
    const r = await api.validateOntology(wid);
    if (r.ok) toast("语义检查通过，可以发布", "ok");
    else toast("还有 " + r.problems.length + " 个问题：" + r.problems.join("；"), "err");
  }, [wid]);

  const { run: runLint, running: linting } = useAction(async () => {
    if (!wid) return;
    const r = await api.lintOntology(wid);
    setLint(r);
    toast(r.ok ? "建模检查没有警告" : `建模检查发现 ${r.warnings.length} 条警告`, r.ok ? "ok" : "warn");
  }, [wid]);

  const { run: publish, running: publishing } = useAction(async () => {
    if (!wid) return;
    await api.publishOntology(wid);
    await refreshRuntime();
    toast("业务世界已启动，现在可以接入数据了", "ok");
    setLint(null);
    load.reload();
  }, [wid]);

  const { run: revise, running: revising } = useAction(async () => {
    if (!wid) return;
    await api.reviseOntology(wid);
    toast("已复制出一个新草稿版本，可以继续演进", "ok");
    load.reload();
  }, [wid]);

  if (load.loading && !ont) {
    return (
      <>
        <PageHead kicker="Ontology" title="本体" desc="读取业务世界定义…" />
        <Loading />
      </>
    );
  }
  if (load.error) {
    return (
      <>
        <PageHead kicker="Ontology" title="本体" desc="本体定义业务世界里有什么、如何相连。" />
        <ErrorNote text={load.error} onRetry={load.reload} />
      </>
    );
  }
  if (!ont) {
    return (
      <>
        <PageHead kicker="Ontology" title="本体"
                  desc="本体回答：这个业务世界里有什么东西，它们之间是什么关系。先把语言对齐，再谈数据与动作。" />
        <Card title="创建第一份本体" sub="名字用于内部标识，建议用英文；说明写给业务同事看。">
          <CreateOntologyForm onDone={load.reload} />
        </Card>
        <Card title="建模顺序" tone="sunken">
          <p>1. 先列对象类型：业务里真实存在、需要被追踪的名词。</p>
          <p>2. 再补属性：每个对象需要记住哪些事实。</p>
          <p>3. 然后连关系：对象之间如何互相引用。</p>
          <p>4. 最后补术语：业务口语如何映射到对象与属性。</p>
        </Card>
      </>
    );
  }

  const objectCount = Object.keys(ont.objects || {}).length;
  const relationCount = Object.keys(ont.relations || {}).length;
  const termCount = Object.keys(ont.vocabulary || {}).length;

  return (
    <>
      <PageHead
        kicker="Ontology"
        title="本体"
        desc={frozen
          ? "已发布，定义已锁定；运行时以发布版本为准。需要演进时复制出新草稿。"
          : "草稿状态：对象、关系与业务术语都还没有生效，发布后才进入运行时。"}
        meta={<>
          <span>名称 <b>{ont.name}</b></span>
          <span>版本 <b>v{ont.version}</b></span>
          <span>对象 <b>{objectCount}</b></span>
          <span>关系 <b>{relationCount}</b></span>
          <span>术语 <b>{termCount}</b></span>
        </>}
        actions={<>
          <Btn variant="subtle" busy={validating} disabled={frozen} onClick={() => void validate()}>
            <ListChecks size={14} /> 语义检查
          </Btn>
          <Btn variant="subtle" busy={linting} onClick={() => void runLint()}>
            <WarningCircle size={14} /> 建模检查
          </Btn>
          {frozen ? (
            <Btn variant="subtle" busy={revising} onClick={() => confirm({
              title: "复制为新草稿版本？",
              body: <p className="muted">当前发布版本会保留；新草稿的版本号与结构版本都加一，可以继续修改。</p>,
              confirmText: "开始修订",
              run: async () => { await revise(); },
            })}>修订</Btn>
          ) : (
            <Btn busy={publishing} onClick={() => confirm({
              title: "发布这个业务世界？",
              danger: true,
              confirmText: "确认发布",
              body: (
                <>
                  <p className="muted">发布后定义将锁定，运行时开始按这份结构接收对象与关系。</p>
                  <dl className="kv">
                    <dt>对象</dt><dd>{objectCount} 个</dd>
                    <dt>关系</dt><dd>{relationCount} 条</dd>
                    <dt>业务术语</dt><dd>{termCount} 个</dd>
                  </dl>
                </>
              ),
              run: async () => { await publish(); },
            })}>
              <SealCheck size={14} /> 发布
            </Btn>
          )}
        </>}
      />

      {frozen && (
        <div className="banner warn">
          本体已发布。直接修改会被拒绝；用右上角「修订」复制出新草稿，再发布升级。
        </div>
      )}

      {lint && (
        <div className={"banner " + (lint.ok ? "info" : "warn")}>
          <b>建模检查 · v{lint.version}</b>{" "}
          {lint.ok ? "没有发现可改进项。" : `发现 ${lint.warnings.length} 条警告（不会阻止发布）：`}
          {!!lint.warnings.length && (
            <ul className="doc-list">
              {lint.warnings.map((w, i) => (
                <li key={`${w.code}-${w.subject}-${i}`}>
                  <span className="mono small">{w.subject}</span> · {w.message}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div className="layout main-stage">
        <div>
          <Card title="已发布结构" sub="运行时的结构版本由发布动作推进；这里展示的是平台侧定义。"
                aside={<Tag kind={frozen ? "ok" : "warn"}>{LIFECYCLE_LABEL[ont.lifecycle] || ont.lifecycle}</Tag>}>
            <Collapsible defaultOpen label="AI 快速建模：粘贴一段业务描述，自动生成草稿">
              <OntologyDraftPanel
                frozen={frozen}
                onApplied={async () => { await refreshRuntime(); load.reload(); }}
              />
            </Collapsible>

            <ObjectsTable objects={ont.objects} />
            <RelationsTable relations={ont.relations} />
            <VocabTable terms={ont.vocabulary} />

            <div className="btn-row">
              {objectCount > 0 && (
                <Btn variant="subtle" size="sm" onClick={() => go("state")}>
                  去对象实例 <ArrowRight size={13} />
                </Btn>
              )}
              <Btn variant="subtle" size="sm" onClick={() => go("data")}>
                去接入数据 <ArrowRight size={13} />
              </Btn>
            </div>
          </Card>

          <Collapsible label="这份本体怎么变成运行时结构">
            <p>发布时编译器会做引用解析、语义校验与类型转换，产出运行时结构交给内核。</p>
            <p>平台不保存第二份业务状态，对象实例只存在于内核里；后续查询与动作都走同一条校验后的链路。</p>
          </Collapsible>
        </div>

        <Card title="添加到草稿" tight aside={frozen ? <Tag kind="warn">已锁定</Tag> : undefined}>
          <div className="subtabs" role="tablist" tabIndex={0} aria-label="新增内容类型">
            {([["object", "对象"], ["relation", "关系"], ["term", "业务术语"]] as [Panel, string][]).map(([k, l]) => (
              <button key={k} type="button" role="tab" aria-selected={panel === k}
                      disabled={frozen} onClick={() => setPanel(k)}>{l}</button>
            ))}
          </div>
          <div className="aside-body">
            {frozen && <div className="banner warn">定义已锁定，不能继续添加；先点右上角「修订」。</div>}

            {panel === "object" && (
              <form className="panel-form" onSubmit={e => { e.preventDefault(); void addObject(); }}>
                <Field label="对象名" hint="用业务里真实存在的名字，例如 Vehicle、TollStation。">
                  <TextInput value={objName} placeholder="Vehicle" onChange={e => setObjName(e.target.value)} disabled={frozen} />
                </Field>
                <Field label="业务含义">
                  <TextInput value={objDesc} placeholder="车辆" onChange={e => setObjDesc(e.target.value)} disabled={frozen} />
                </Field>
                <Field label="属性" top hint="一行一个：属性名 + 类型。">
                  <RowEditor rows={props} onChange={setProps} phA="属性名，如 plate" phB="类型"
                             bKind="select" bOptions={TYPES} />
                </Field>
                <Btn type="submit" busy={addingObj} disabled={frozen}>添加对象</Btn>
              </form>
            )}

            {panel === "relation" && (
              <form className="panel-form" onSubmit={e => { e.preventDefault(); void addRelation(); }}>
                <Field label="关系名"><TextInput value={relName} placeholder="passes" onChange={e => setRelName(e.target.value)} disabled={frozen} /></Field>
                <Field label="源对象">
                  <Select value={relSource} onChange={e => setRelSource(e.target.value)} disabled={frozen}>
                    <option value="">选择对象</option>
                    {objectNames.map(o => <option key={o} value={o}>{o}</option>)}
                  </Select>
                </Field>
                <Field label="目标对象">
                  <Select value={relTarget} onChange={e => setRelTarget(e.target.value)} disabled={frozen}>
                    <option value="">选择对象</option>
                    {objectNames.map(o => <option key={o} value={o}>{o}</option>)}
                  </Select>
                </Field>
                <Field label="业务含义"><TextInput value={relDesc} placeholder="车辆通过收费站" onChange={e => setRelDesc(e.target.value)} disabled={frozen} /></Field>
                <Btn type="submit" busy={addingRel} disabled={frozen}>添加关系</Btn>
              </form>
            )}

            {panel === "term" && (
              <form className="panel-form" onSubmit={e => { e.preventDefault(); void addTerm(); }}>
                <Field label="说法" hint="业务人员嘴里怎么讲，例如「收费站」。">
                  <TextInput value={term} placeholder="收费站" onChange={e => setTerm(e.target.value)} disabled={frozen} />
                </Field>
                <Field label="指向"><TextInput value={concept} placeholder="TollStation 或 TollStation.status" onChange={e => setConcept(e.target.value)} disabled={frozen} /></Field>
                <Field label="种类">
                  <Select value={termKind} onChange={e => setTermKind(e.target.value)} disabled={frozen}>
                    <option value="object">对象</option>
                    <option value="property">属性</option>
                    <option value="relation">关系</option>
                  </Select>
                </Field>
                <Field label="同义词" hint="逗号分隔。同义词越多，AI 越听得懂人话。">
                  <TextInput value={aliases} placeholder="收费场站, 站点" onChange={e => setAliases(e.target.value)} disabled={frozen} />
                </Field>
                <Btn type="submit" busy={addingTerm} disabled={frozen}>添加术语</Btn>
                <button type="button" className="link" onClick={() => go("assistant")}>去 AI 助手试用这些术语</button>
              </form>
            )}

            {!!objectTypes.length && (
              <div className="aside-note">
                <div className="section-title">运行时已生效</div>
                <div className="tag-list">{objectTypes.map(t => <Tag key={t} kind="ok">{t}</Tag>)}</div>
              </div>
            )}
          </div>
        </Card>
      </div>
    </>
  );
}

function CreateOntologyForm({ onDone }: { onDone: () => void }) {
  const { wid, toast } = usePlatform();
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const { run, running } = useAction(async () => {
    if (!wid) return;
    if (!name.trim()) { toast("请填写本体名称", "err"); return; }
    await api.createOntology(wid, name.trim(), desc.trim());
    toast(`本体「${name.trim()}」已创建`, "ok");
    onDone();
  }, [wid, name, desc]);
  return (
    <form className="panel-form" onSubmit={e => { e.preventDefault(); void run(); }}>
      <Field label="本体名称" hint="内部标识，建议英文小写，例如 traffic。">
        <TextInput value={name} placeholder="traffic" onChange={e => setName(e.target.value)} />
      </Field>
      <Field label="一句话说明">
        <TextInput value={desc} placeholder="例如：高速收费与放行的业务世界" onChange={e => setDesc(e.target.value)} />
      </Field>
      <div className="btn-row flat">
        <Btn type="submit" busy={running}><CheckCircle size={14} /> 创建本体</Btn>
      </div>
    </form>
  );
}

function ObjectsTable({ objects }: { objects: Record<string, ObjectTypeSpec> }) {
  const entries = Object.entries(objects);
  if (!entries.length) return <Empty title="还没有对象" hint="在右侧「对象」页签里添加第一个业务对象。" />;
  return (
    <>
      <h3 className="h4">对象（{entries.length}）</h3>
      <TableWrap>
        <table>
          <thead><tr><th>对象</th><th>业务含义</th><th className="wrap">属性</th></tr></thead>
          <tbody>
            {entries.map(([name, o]) => (
              <tr key={name}>
                <td className="nowrap"><b>{name}</b></td>
                <td title={o.description || ""}>{o.description || "–"}</td>
                <td className="wrap">
                  <PropertyList properties={o.properties} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableWrap>
    </>
  );
}

function RelationsTable({ relations }: { relations: Record<string, RelationSpec> }) {
  const entries = Object.entries(relations);
  if (!entries.length) return null;
  return (
    <>
      <h3 className="h4">关系（{entries.length}）</h3>
      <TableWrap>
        <table>
          <thead><tr><th>关系</th><th>源 → 目标</th><th className="wrap">含义</th></tr></thead>
          <tbody>
            {entries.map(([n, r]) => (
              <tr key={n}>
                <td className="nowrap"><b>{n}</b></td>
                <td className="nowrap">{r.source} → {r.target}</td>
                <td className="wrap" title={r.description || ""}>{r.description || "–"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableWrap>
    </>
  );
}

function VocabTable({ terms }: { terms: Record<string, VocabTerm> }) {
  const entries = Object.entries(terms);
  if (!entries.length) return null;
  return (
    <>
      <h3 className="h4">业务术语（{entries.length}）</h3>
      <div className="chips">
        {entries.map(([t, v]) => (
          <span key={t} className="chip" title={`${KIND_LABEL[v.kind] || v.kind} · 同义词：${(v.aliases || []).join("、") || "无"}`}>
            {t} → {v.concept}
          </span>
        ))}
      </div>
    </>
  );
}
