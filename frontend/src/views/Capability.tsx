import { useEffect, useMemo, useState } from "react";
import { api, type Capability, type Obj } from "../api";
import { useAction, usePlatform } from "../store";
import {
  Btn, Card, Collapsible, Drawer, Empty, Field, JsonField, KIND_LABEL, labelOf,
  LIFECYCLE_LABEL, Loading, RowEditor, Select, TableWrap, Tag, TextInput, useLoad, type Row,
} from "../ui";

const HINTS: Record<string, string> = {
  python: "写一段 Python，必须定义 def execute(ctx)。ctx 提供：object() 目标对象、args 参数、get(ref)、find(类型, **过滤)、related(ref, 关系, 方向)、set(引用, 属性, 值)、link/unlink。return 的内容就是执行结果。",
  workflow: "写步骤数组。每步形如 {\"kind\":\"find|related|get|count|set|set_property|invoke\", \"as\":\"别名\", …}：find 要 object_type+filter；related 要 ref+relation+direction；get 要 ref；count 要 list；set_property 要 ref+properties；invoke 要 function+target+args。用 {{target}}、{{args.参数名}}、{{steps.别名}} 引用。别名叫 output 的步骤即最终结果。",
  agent: "写一句业务目标。平台会用「观察 → 行动 → 结论」循环去实现，{target} 与 {args} 会被替换成实际对象与参数。需要先在设置里配好模型（或用演示模式）。",
};

const TEMPLATES: Record<string, string> = {
  python: `def execute(ctx):
    obj = ctx.object()                       # 目标对象
    balance = obj.get("balance") or 0
    ctx.set(obj.ref, "balance", balance + ctx.args["amount"])
    return {"balance": balance + ctx.args["amount"]}`,
  workflow: `[
  {"kind": "related", "ref": "{{target}}", "relation": "passes", "direction": "in", "as": "vehicles"},
  {"kind": "count", "list": "{{steps.vehicles}}", "as": "total"},
  {"kind": "set", "name": "report", "value": "共 {{steps.total}} 条关联记录", "as": "output"}
]`,
  agent: "检查目标对象当前状态，发现问题给出处置建议。",
};

export function CapabilityView() {
  const { wid, toast, confirm, go, refreshRuntime } = usePlatform();
  const [runTarget, setRunTarget] = useState<Capability | null>(null);
  const load = useLoad(() => (wid ? api.capabilities(wid) : Promise.resolve([] as Capability[])), [wid]);
  const caps = load.data || [];

  const { run: publish, running: publishing } = useAction(async (c: Capability) => {
    if (!wid) return;
    await api.publishCapability(wid, c.id);
    toast(`能力「${c.name}」已发布，业务人员现在可以执行它了`, "ok");
    load.reload();
    void refreshRuntime();          // 已发布能力数变了，侧栏与工作台要同步
  }, [wid, refreshRuntime]);

  if (load.loading && !load.data) return <Loading />;

  return (
    <>
      <Card title="能力清单" sub="能力是业务动作的唯一入口：发布后成为可执行动作，每次执行都留痕。">
        {!caps.length && <Empty title="还没有能力" hint="在右侧用业务语言描述它该做什么。" />}
        {!!caps.length && (
          <TableWrap>
            <table>
              <thead><tr><th>能力</th><th>作用对象</th><th>实现</th><th>状态</th><th> </th></tr></thead>
              <tbody>
                {caps.map(c => (
                  <tr key={c.id}>
                    <td className="wrap">
                      <b>{c.name}</b>
                      {c.description && <div className="muted">{c.description}</div>}
                    </td>
                    <td className="nowrap">{c.target_type}</td>
                    <td className="nowrap"><Tag kind="plain">{labelOf(KIND_LABEL, String(c.implementation?.kind || ""))}</Tag></td>
                    <td className="nowrap">
                      <Tag kind={c.lifecycle === "published" ? "ok" : "warn"}>{LIFECYCLE_LABEL[c.lifecycle]}</Tag>
                    </td>
                    <td className="nowrap">
                      <div className="pill-row">
                        {c.lifecycle !== "published" && (
                          <Btn size="sm" busy={publishing}
                               onClick={() => confirm({
                                 title: `发布能力「${c.name}」？`,
                                 confirmText: "发布",
                                 body: <p className="muted">发布后它会挂到「{c.target_type}」上，业务人员和 AI 都能立即执行，执行会真实改变业务状态。</p>,
                                 run: async () => { await publish(c); },
                               })}>发布</Btn>
                        )}
                        {c.lifecycle === "published" && (
                          <Btn size="sm" variant="tint" onClick={() => setRunTarget(c)}>执行</Btn>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
      </Card>

      <div className="layout main-panel">
        <Card title="怎么用好能力" sub="先定义、再发布、再执行；执行结果里能看到改了什么。">
          <ul className="muted doc-list">
            <li><b>脚本</b>：一段 Python，适合精确计算与多属性联动。</li>
            <li><b>编排</b>：把「查一遍 → 算一下 → 改一处」串成步骤，不用写代码。</li>
            <li><b>智能目标</b>：只写一句业务目标，交给模型自己规划（需要模型服务或演示模式）。</li>
          </ul>
          <Collapsible label="编排步骤的字段说明">
            <p className="muted">{HINTS.workflow}</p>
          </Collapsible>
          <div className="btn-row">
            <Btn size="sm" variant="subtle" onClick={() => go("state")}>先去看有哪些对象可以当执行目标</Btn>
          </div>
        </Card>

        <CreateCapability onDone={load.reload} />
      </div>

      {runTarget && <RunDrawer cap={runTarget} onClose={() => setRunTarget(null)} />}
    </>
  );
}

function CreateCapability({ onDone }: { onDone: () => void }) {
  const { wid, toast, refreshRuntime } = usePlatform();
  const [key, setKey] = useState("");
  const [name, setName] = useState("");
  const [target, setTarget] = useState("");
  const [desc, setDesc] = useState("");
  const [kind, setKind] = useState("python");
  const [impl, setImpl] = useState("");
  const [inputs, setInputs] = useState<Row[]>([{ a: "amount", b: "number" }]);
  const { objectTypes } = usePlatform();

  const { run, running } = useAction(async () => {
    if (!wid) return;
    if (!key.trim() || !name.trim() || !target) { toast("标识、名称、作用对象都要填", "err"); return; }
    if (!impl.trim()) { toast("请填写实现内容", "err"); return; }
    let implementation: Record<string, unknown> = { kind };
    if (kind === "workflow") {
      try { implementation = { kind, steps: JSON.parse(impl) }; }
      catch { toast("编排步骤不是合法 JSON", "err"); return; }
    } else if (kind === "agent") {
      implementation = { kind, goal: impl.trim() };
    } else {
      implementation = { kind, source: impl };
    }
    await api.createCapability(wid, {
      key: key.trim(), name: name.trim(), target_type: target, description: desc.trim(),
      inputs: inputs.filter(r => r.a.trim()).map(r => ({ name: r.a.trim(), type: r.b || "string" })),
      implementation,
    });
    toast(`能力「${name.trim()}」已创建草稿，发布后可执行`, "ok");
    setKey(""); setName(""); setDesc(""); setImpl("");
    void refreshRuntime();          // 能力总数变了，侧栏计数要跟上
    onDone();
  }, [wid, key, name, target, desc, kind, impl, inputs, refreshRuntime]);

  return (
    <Card title="新建能力" tight>
      <form className="panel-form" onSubmit={e => { e.preventDefault(); void run(); }}>
        <Field label="业务名称" hint="业务人员看到的名字，例如「车辆充值」。">
          <TextInput value={name} placeholder="车辆充值" onChange={e => setName(e.target.value)} />
        </Field>
        <Field label="标识" hint="英文短词，用于内部引用，例如 recharge。">
          <TextInput value={key} placeholder="recharge" onChange={e => setKey(e.target.value)} />
        </Field>
        <Field label="作用对象">
          <Select value={target} onChange={e => setTarget(e.target.value)}>
            <option value="">选择对象类型</option>
            {objectTypes.map(t => <option key={t} value={t}>{t}</option>)}
          </Select>
        </Field>
        <Field label="一句话说明"><TextInput value={desc} placeholder="给车辆账户充值" onChange={e => desc !== undefined && setDesc(e.target.value)} /></Field>
        <Field label="参数" top hint="一行一个：参数名 + 类型。执行时会按这里自动生成输入框。">
          <RowEditor rows={inputs} onChange={setInputs} phA="参数名，如 amount" phB="类型"
                     bKind="select" bOptions={["string", "number", "integer", "boolean"]} />
        </Field>
        <Field label="怎么实现">
          <Select value={kind} onChange={e => { setKind(e.target.value); setImpl(TEMPLATES[e.target.value] || ""); }}>
            <option value="python">脚本（Python）</option>
            <option value="workflow">编排（步骤）</option>
            <option value="agent">智能目标</option>
          </Select>
        </Field>
        <JsonField label="实现内容" value={impl} onChange={setImpl} rows={kind === "agent" ? 3 : 10}
                   hint={HINTS[kind]} />
        <div className="btn-row">
          <Btn type="submit" busy={running}>创建能力</Btn>
          <Btn variant="subtle" type="button" onClick={() => setImpl(TEMPLATES[kind])}>填入示例</Btn>
        </div>
      </form>
    </Card>
  );
}

/** 执行抽屉：按能力声明的参数自动生成表单 + 目标对象选择器 + 结果分区 */
function RunDrawer({ cap, onClose }: { cap: Capability; onClose: () => void }) {
  const { wid, toast } = usePlatform();
  const [type, setType] = useState(cap.target_type);
  const [ref, setRef] = useState("");
  const [args, setArgs] = useState<Record<string, string>>({});
  const [result, setResult] = useState<{ ok: boolean; output?: unknown; changes?: Record<string, unknown>[]; execution_id?: string; error?: string | null } | null>(null);

  const candidates = useLoad(
    () => (wid && type ? api.find(wid, type, {}).catch(() => [] as Obj[]) : Promise.resolve([] as Obj[])),
    [wid, type],
  );
  useEffect(() => {
    if (!ref && candidates.data?.length) setRef(`${candidates.data[0].type}/${candidates.data[0].id}`);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candidates.data]);

  const typed = useMemo(() => cap.inputs || [], [cap.inputs]);
  const { run, running } = useAction(async () => {
    if (!wid) return;
    const payload: Record<string, unknown> = {};
    for (const i of typed) {
      const raw = args[i.name];
      if (raw === undefined || raw === "") continue;
      if ((i.type === "number" || i.type === "integer") && Number.isNaN(Number(raw))) {
        toast(`参数 ${i.name} 要填数字，现在起「${raw}」`, "err");
        return;
      }
      payload[i.name] = i.type === "number" || i.type === "integer" ? Number(raw)
        : i.type === "boolean" ? raw === "true" : raw;
    }
    const r = await api.invoke(wid, { function: `${cap.target_type}.${cap.key}`, target: ref || null, args: payload });
    setResult(r);
    toast(r.ok ? "执行完成，业务状态已更新" : "执行未成功：" + (r.error || "未知原因"), r.ok ? "ok" : "err");
    candidates.reload();
  }, [wid, cap, ref, args, typed]);

  return (
    <Drawer title={`执行 · ${cap.name}`} sub={`${cap.target_type}.${cap.key}`} onClose={onClose} wide
            footer={<>
              <Btn busy={running} onClick={() => void run()}>执行</Btn>
              <Btn variant="subtle" onClick={onClose}>关闭</Btn>
            </>}>
      <Field label="目标对象" hint="从已同步进来的对象里选，也可以直接写引用。">
        <div className="field-inline">
          <Select className="w-md" value={type} onChange={e => { setType(e.target.value); setRef(""); }} aria-label="对象类型">
            <option value={cap.target_type}>{cap.target_type}</option>
          </Select>
          <Select className="w-lg" value={ref} onChange={e => setRef(e.target.value)} aria-label="具体对象">
            {!candidates.data?.length && <option value="">{candidates.loading ? "读取中…" : "没有可选对象，先去同步数据"}</option>}
            {(candidates.data || []).map(o => (
              <option key={`${o.type}/${o.id}`} value={`${o.type}/${o.id}`}>
                {o.type}/{o.id} {String(o.properties?.name ?? o.properties?.plate ?? "")}
              </option>
            ))}
          </Select>
          <TextInput className="w-md" value={ref} onChange={e => setRef(e.target.value)} aria-label="对象引用" placeholder="Vehicle/V001" />
        </div>
      </Field>

      {typed.length === 0 && <p className="muted">这个能力不需要参数。</p>}
      {typed.map(i => (
        <Field key={i.name} label={i.name} hint={i.type === "number" ? "数值" : i.type === "boolean" ? "true / false" : "文本"}>
          {i.type === "boolean" ? (
            <Select value={args[i.name] ?? "true"} onChange={e => setArgs(a => ({ ...a, [i.name]: e.target.value }))}>
              <option value="true">是</option><option value="false">否</option>
            </Select>
          ) : (
            <TextInput type={i.type === "number" || i.type === "integer" ? "number" : "text"}
                       value={args[i.name] ?? ""} placeholder={`参数 ${i.name}`}
                       onChange={e => setArgs(a => ({ ...a, [i.name]: e.target.value }))} />
          )}
        </Field>
      ))}

      {result && (
        <>
          <div className="result-head">
            <h4>执行结果</h4>
            <Tag kind={result.ok ? "ok" : "err"}>{result.ok ? "成功" : "失败"}</Tag>
            {result.execution_id && <span className="muted">执行编号 {result.execution_id}</span>}
          </div>
          {result.error && <div className="banner err">{result.error}</div>}
          {result.output !== undefined && result.output !== null && (
            <>
              <h4 className="h4">返回</h4>
              <pre>{JSON.stringify(result.output, null, 2)}</pre>
            </>
          )}
          {!!result.changes?.length && (
            <>
              <h4 className="h4">改动了什么（{result.changes.length}）</h4>
              <TableWrap auto>
                <table>
                  <thead><tr><th>类型</th><th className="wrap">对象</th><th className="wrap">变化</th></tr></thead>
                  <tbody>
                    {result.changes.map((c, i) => <ChangeRow key={i} c={c} />)}
                  </tbody>
                </table>
              </TableWrap>
            </>
          )}
        </>
      )}
    </Drawer>
  );
}

export function ChangeRow({ c }: { c: Record<string, unknown> }) {
  const kind = String(c.kind || "");
  if (kind === "set_property")
    return <tr><td className="nowrap">属性变更</td><td className="wrap">{String(c.object)}.{String(c.property)}</td>
      <td className="wrap">{String(c.old)} → <b>{String(c.new)}</b></td></tr>;
  if (kind === "link")
    return <tr><td className="nowrap">建立关系</td><td className="wrap" colSpan={2}>
      {String(c.source)} —{String(c.relation)}→ {String(c.target)}</td></tr>;
  if (kind === "create_object")
    return <tr><td className="nowrap">新增对象</td><td className="wrap">{String(c.object)}</td>
      <td className="wrap" title={JSON.stringify(c.properties)}>{JSON.stringify(c.properties)}</td></tr>;
  return <tr><td className="nowrap">{kind || "变更"}</td><td className="wrap" colSpan={2}>{JSON.stringify(c)}</td></tr>;
}
