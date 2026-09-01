import { useState } from "react";
import { api, type ObjectTypeSpec, type PropertySpec, type RelationSpec, type VocabTerm } from "../api";
import { useAction, usePlatform } from "../store";
import {
  Btn, Card, Collapsible, Empty, Field, KIND_LABEL, LIFECYCLE_LABEL, Loading,
  RowEditor, Select, TableWrap, Tag, TextInput, useLoad, type Row,
} from "../ui";

const TYPES = ["string", "number", "integer", "boolean"];
type Panel = "object" | "relation" | "term";

export function OntologyView() {
  const { wid, toast, confirm, refreshRuntime, go } = usePlatform();
  const [panel, setPanel] = useState<Panel>("object");
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
  }, [wid, relName, relSource, relTarget, relDesc, objectNames.join(",")]);

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

  /* ---- 校验 / 发布 ---- */
  const { run: validate, running: validating } = useAction(async () => {
    if (!wid) return;
    const r = await api.validateOntology(wid);
    if (r.ok) toast("检查通过，可以发布", "ok");
    else toast("还有 " + r.problems.length + " 个问题：" + r.problems.join("；"), "err");
  }, [wid]);

  const { run: publish, running: publishing } = useAction(async () => {
    if (!wid || !ont) return;
    await api.publishOntology(wid);
    await refreshRuntime();
    toast("业务世界已启动，现在可以接入数据了", "ok");
    load.reload();
  }, [wid, ont]);

  if (load.loading && !ont) return <Loading />;
  if (!ont) {
    return (
      <Card title="本体" sub="本体回答：这个业务世界里有什么东西，它们之间是什么关系。">
        <Empty title="还没有本体" hint="先给它起个业务名字，例如 traffic。"
               action={<CreateOntologyForm onDone={load.reload} />} />
      </Card>
    );
  }

  return (
    <div className="layout main-panel">
      <div>
        <Card
          title={<>本体 · {ont.name} <Tag kind={frozen ? "ok" : "warn"}>{LIFECYCLE_LABEL[ont.lifecycle]}</Tag></>}
          sub={frozen
            ? "已发布，定义已锁定；运行时以发布版本为准。"
            : "草稿状态，可以继续添加对象、关系与业务术语。"}
          aside={<>
            <Btn variant="subtle" busy={validating} disabled={frozen} onClick={() => void validate()}>检查</Btn>
            <Btn busy={publishing || frozen} disabled={frozen}
                 onClick={() => confirm({
                   title: "发布这个业务世界？",
                   danger: true,
                   confirmText: "确认发布",
                   body: (
                     <>
                       <p className="muted">发布后定义将锁定，运行时开始按这份结构接收对象与关系。</p>
                       <dl className="kv">
                         <dt>对象</dt><dd>{Object.keys(ont.objects).length} 个</dd>
                         <dt>关系</dt><dd>{Object.keys(ont.relations).length} 条</dd>
                         <dt>业务术语</dt><dd>{Object.keys(ont.vocabulary).length} 个</dd>
                       </dl>
                     </>
                   ),
                   run: async () => { await publish(); },
                 })}>
              {frozen ? "已发布" : "发布"}
            </Btn>
          </>}>

          {frozen && <div className="banner warn">需要大改？v0.1 的演进方式是新建一个工作空间重新建模。</div>}

          <ObjectsTable objects={ont.objects} />
          <RelationsTable relations={ont.relations} />
          <VocabTable terms={ont.vocabulary} />

          <Collapsible label="这份本体怎么变成运行时结构">
            <p className="muted">
              发布时编译器会做引用解析、语义校验与类型转换，产出运行时结构交给内核；
              平台不保存第二份业务状态，对象实例只存在于内核里。
            </p>
          </Collapsible>
        </Card>
      </div>

      <Card title="添加到草稿" tight>
        <div className="subtabs" role="tablist">
          {([["object", "对象"], ["relation", "关系"], ["term", "业务术语"]] as [Panel, string][]).map(([k, l]) => (
            <button key={k} type="button" role="tab" aria-selected={panel === k}
                    disabled={frozen} onClick={() => setPanel(k)}>{l}</button>
          ))}
        </div>
        {frozen && <div className="banner warn">定义已锁定，不能继续添加。</div>}

        {panel === "object" && (
          <form className="panel-form" onSubmit={e => { e.preventDefault(); void addObject(); }}>
            <Field label="对象名" hint="用业务里真实存在的名字，例如 Vehicle、TollStation。">
              <TextInput value={objName} placeholder="Vehicle" onChange={e => setObjName(e.target.value)} />
            </Field>
            <Field label="业务含义">
              <TextInput value={objDesc} placeholder="车辆" onChange={e => setObjDesc(e.target.value)} />
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
            <Field label="关系名"><TextInput value={relName} placeholder="passes" onChange={e => setRelName(e.target.value)} /></Field>
            <Field label="源对象">
              <Select value={relSource} onChange={e => setRelSource(e.target.value)}>
                <option value="">选择对象</option>
                {objectNames.map(o => <option key={o} value={o}>{o}</option>)}
              </Select>
            </Field>
            <Field label="目标对象">
              <Select value={relTarget} onChange={e => setRelTarget(e.target.value)}>
                <option value="">选择对象</option>
                {objectNames.map(o => <option key={o} value={o}>{o}</option>)}
              </Select>
            </Field>
            <Field label="业务含义"><TextInput value={relDesc} placeholder="车辆通过收费站" onChange={e => setRelDesc(e.target.value)} /></Field>
            <Btn type="submit" busy={addingRel} disabled={frozen}>添加关系</Btn>
          </form>
        )}

        {panel === "term" && (
          <form className="panel-form" onSubmit={e => { e.preventDefault(); void addTerm(); }}>
            <Field label="说法" hint="业务人员嘴里怎么讲，例如「收费站」。">
              <TextInput value={term} placeholder="收费站" onChange={e => setTerm(e.target.value)} />
            </Field>
            <Field label="指向"><TextInput value={concept} placeholder="TollStation 或 TollStation.status" onChange={e => setConcept(e.target.value)} /></Field>
            <Field label="种类">
              <Select value={termKind} onChange={e => setTermKind(e.target.value)}>
                <option value="object">对象</option>
                <option value="property">属性</option>
                <option value="relation">关系</option>
              </Select>
            </Field>
            <Field label="同义词" hint="逗号分隔。同义词越多，AI 越听得懂人话。">
              <TextInput value={aliases} placeholder="收费场站, 站点" onChange={e => setAliases(e.target.value)} />
            </Field>
            <Btn type="submit" busy={addingTerm} disabled={frozen}>添加术语</Btn>
            <button type="button" className="link" onClick={() => go("assistant")}>想知道术语怎么用？去 AI 助手页试试</button>
          </form>
        )}
      </Card>
    </div>
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
    <form className="inline-create" onSubmit={e => { e.preventDefault(); void run(); }}>
      <TextInput value={name} placeholder="本体名称，如 traffic" aria-label="本体名称" onChange={e => setName(e.target.value)} />
      <TextInput value={desc} placeholder="一句话说明（可选）" aria-label="本体说明" onChange={e => setDesc(e.target.value)} />
      <Btn type="submit" busy={running}>创建</Btn>
    </form>
  );
}

function ObjectsTable({ objects }: { objects: Record<string, ObjectTypeSpec> }) {
  const entries = Object.entries(objects);
  if (!entries.length) return <Empty title="还没有对象" hint="在右侧「对象」页签里添加第一个业务对象。" />;
  return (
    <>
      <h4 className="h4">对象（{entries.length}）</h4>
      <TableWrap>
        <table>
          <thead><tr><th>对象</th><th>业务含义</th><th className="wrap">属性</th></tr></thead>
          <tbody>
            {entries.map(([name, o]) => (
              <tr key={name}>
                <td className="nowrap"><b>{name}</b></td>
                <td title={o.description || ""}>{o.description || "—"}</td>
                <td className="wrap">
                  {Object.values(o.properties || {}).map(p => <code key={p.name}>{p.name}:{p.type}</code>).join(" ") || "—"}
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
      <h4 className="h4">关系（{entries.length}）</h4>
      <TableWrap>
        <table>
          <thead><tr><th>关系</th><th>源 → 目标</th><th className="wrap">含义</th></tr></thead>
          <tbody>
            {entries.map(([n, r]) => (
              <tr key={n}>
                <td className="nowrap"><b>{n}</b></td>
                <td className="nowrap">{r.source} → {r.target}</td>
                <td className="wrap" title={r.description || ""}>{r.description || "—"}</td>
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
      <h4 className="h4">业务术语（{entries.length}）</h4>
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
