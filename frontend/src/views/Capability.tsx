import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowRight, Lightning, Play } from "@phosphor-icons/react";
import { api, ApiError, type Capability, type InvokeResult, type Obj } from "../api";
import { useAction, usePlatform } from "../store";
import {
  Btn, Card, Collapsible, Drawer, Empty, Field, JsonField, KIND_LABEL, labelOf,
  LIFECYCLE_LABEL, Loading, PageHead, RowEditor, Select, TableWrap, Tag,
  TextInput, useLoad, type Row,
} from "../ui";
import { StatusBadge } from "../components/StatusBadge";
import { ValueView } from "../components/ValueView";
import { executionStatus, isSuccess, isUnknown } from "../lib/status";
import { contractExampleText, parseContract } from "../lib/contract";
import { newIdempotencyKey } from "../lib/idempotency";
import { formatLocal } from "../lib/time";
import { valuePreview } from "../lib/value";

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
  const [filter, setFilter] = useState<"all" | "published" | "draft">("all");
  const load = useLoad(() => (wid ? api.capabilities(wid) : Promise.resolve([] as Capability[])), [wid]);
  const caps = load.data || [];
  const shown = useMemo(
    () => caps.filter(c => filter === "all" || c.lifecycle === filter),
    [caps, filter],
  );

  const { run: publish, running: publishing } = useAction(async (c: Capability) => {
    if (!wid) return;
    await api.publishCapability(wid, c.id);
    toast(`能力「${c.name}」已发布，业务人员现在可以执行它了`, "ok");
    load.reload();
    void refreshRuntime();
  }, [wid, refreshRuntime]);

  return (
    <>
      <PageHead
        kicker="Capabilities"
        title="能力"
        desc="能力是业务动作的唯一入口：发布后成为可执行动作，每次执行都留痕，并且只能通过它改变世界。"
        meta={<>
          <span>能力 <b>{caps.length}</b></span>
          <span>已发布 <b>{caps.filter(c => c.lifecycle === "published").length}</b></span>
          <span>对象类型 <b>{new Set(caps.map(c => c.target_type)).size}</b></span>
        </>}
        actions={<>
          <Btn variant="subtle" onClick={load.reload} busy={load.loading}>刷新</Btn>
          <Btn variant="subtle" onClick={() => go("state")}>看对象实例 <ArrowRight size={14} /></Btn>
        </>}
      />

      {load.loading && <Loading />}

      <div className="layout main-panel">
        <div>
          <Card title="能力清单" sub="按作用对象执行；未发布的能力不会出现在运行时。"
                aside={
                  <div className="subtabs compact" role="tablist" tabIndex={0} aria-label="按状态筛选">
                    {([["all", "全部"], ["published", "已发布"], ["draft", "草稿"]] as [typeof filter, string][]).map(([k, l]) => (
                      <button key={k} type="button" role="tab" aria-selected={filter === k}
                              onClick={() => setFilter(k)}>{l}</button>
                    ))}
                  </div>
                }>
            {!caps.length && (
              <Empty title="还没有能力" hint="在右侧用业务语言描述它该做什么，例如「给车辆账户充值」。"
                     action={<Btn size="sm" variant="subtle" onClick={() => go("ontology")}>先确认对象类型</Btn>} />
            )}
            {!!caps.length && !shown.length && <Empty title="这个状态下没有能力" hint="切换筛选，或新建一个。" />}
            {!!shown.length && (
              <TableWrap>
                <table>
                  <thead><tr><th>能力</th><th>作用对象</th><th>实现</th><th>状态</th><th>操作</th></tr></thead>
                  <tbody>
                    {shown.map(c => (
                      <tr key={c.id}>
                        <td className="wrap">
                          <b>{c.name}</b>
                          <div className="muted mono small">{c.target_type}.{c.key}</div>
                          {c.description && <div className="muted small">{c.description}</div>}
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
                                     body: <p className="muted">发布后它会挂到「{c.target_type}」上，业务人员和 AI 都能立即执行；执行会真实改变业务状态。</p>,
                                     run: async () => { await publish(c); },
                                   })}>发布</Btn>
                            )}
                            {c.lifecycle === "published" && (
                              <Btn size="sm" variant="tint" onClick={() => setRunTarget(c)}>
                                <Play size={12} weight="fill" /> 执行
                              </Btn>
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

          <Card title="怎么用好能力" sub="先定义、再发布、再执行；执行结果里能看到到底改了什么。">
            <ul className="doc-list">
              <li><b>脚本</b>：一段 Python，适合精确计算与多属性联动。</li>
              <li><b>编排</b>：把「查一遍 → 算一下 → 改一处」串成步骤，不用写代码。</li>
              <li><b>智能目标</b>：只写一句业务目标，交给模型规划（需要模型服务或演示模式）。</li>
            </ul>
            <Collapsible label="编排步骤的字段说明">
              <p className="muted">{HINTS.workflow}</p>
            </Collapsible>
            <Collapsible label="为什么能力是唯一入口">
              <p>直接改对象会绕过校验、权限与事件记录。能力把「一次业务动作」封装成可审计、可回放的单元，平台才能回答「谁在什么时候改变了什么」。</p>
            </Collapsible>
          </Card>
        </div>

        <CreateCapability onDone={load.reload} />
      </div>

      {runTarget && <RunDrawer cap={runTarget} onClose={() => setRunTarget(null)} />}
    </>
  );
}

function CreateCapability({ onDone }: { onDone: () => void }) {
  const { wid, toast, refreshRuntime, objectTypes } = usePlatform();
  const [key, setKey] = useState("");
  const [name, setName] = useState("");
  const [target, setTarget] = useState("");
  const [desc, setDesc] = useState("");
  const [kind, setKind] = useState("python");
  const [impl, setImpl] = useState("");
  const [contract, setContract] = useState("");
  const [inputs, setInputs] = useState<Row[]>([{ a: "amount", b: "number" }]);
  const [open, setOpen] = useState(false);

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
    let contractValue: Record<string, unknown> | undefined;
    if (contract.trim()) {
      const parsed = parseContract(contract);
      if (parsed.error) { toast(parsed.error, "err"); return; }
      contractValue = parsed.value;
    }
    await api.createCapability(wid, {
      key: key.trim(), name: name.trim(), target_type: target, description: desc.trim(),
      inputs: inputs.filter(r => r.a.trim()).map(r => ({ name: r.a.trim(), type: r.b || "string" })),
      implementation,
      ...(contractValue ? { contract: contractValue } : {}),
    });
    toast(`能力「${name.trim()}」已创建草稿，发布后可执行`, "ok");
    setKey(""); setName(""); setDesc(""); setImpl(""); setContract("");
    void refreshRuntime();
    setOpen(false);
    onDone();
  }, [wid, key, name, target, desc, kind, impl, contract, inputs, refreshRuntime]);

  return (
    <Card title="新建能力" sub="发布后才会进入运行时。" tight
          aside={<Btn size="sm" variant="subtle" onClick={() => setOpen(o => !o)}>{open ? "收起" : "打开表单"}</Btn>}>
      {!open && (
        <div className="aside-body">
          <p className="muted small">点右上角打开表单。能力由业务名称、作用对象、参数契约与实现组成。</p>
        </div>
      )}
      {open && (
        <div className="aside-body">
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
            <Field label="一句话说明"><TextInput value={desc} placeholder="给车辆账户充值" onChange={e => setDesc(e.target.value)} /></Field>
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
            <JsonField label="执行契约（可选）" value={contract} onChange={setContract} rows={6}
                       hint='描述执行前后世界状态，不是参数 JSON Schema。顶层键：preconditions / postconditions / effects / idempotent / compensate。' />
            <div className="btn-row">
              <Btn type="submit" busy={running}><Lightning size={14} /> 创建能力</Btn>
              <Btn variant="subtle" type="button" onClick={() => setImpl(TEMPLATES[kind])}>填入示例</Btn>
              <Btn variant="subtle" type="button" onClick={() => setContract(contractExampleText())}>填入契约示例</Btn>
            </div>
          </form>
        </div>
      )}
    </Card>
  );
}

/** 执行抽屉：一次用户动作一个幂等键；未知结果只允许人工查证，不提供重试。 */
function RunDrawer({ cap, onClose }: { cap: Capability; onClose: () => void }) {
  const { wid, toast } = usePlatform();
  const [type, setType] = useState(cap.target_type);
  const [ref, setRef] = useState("");
  const [args, setArgs] = useState<Record<string, string>>({});
  const [result, setResult] = useState<InvokeResult | null>(null);
  const [evidence, setEvidence] = useState("");
  const [transportError, setTransportError] = useState<string | null>(null);
  const keyRef = useRef(newIdempotencyKey());

  const candidates = useLoad(
    () => (wid && type ? api.find(wid, type, {}).catch(() => [] as Obj[]) : Promise.resolve([] as Obj[])),
    [wid, type],
  );
  useEffect(() => {
    if (!ref && candidates.data?.length) setRef(`${candidates.data[0].type}/${candidates.data[0].id}`);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candidates.data]);

  // 参数一旦改动就是新的用户动作：清掉上次超时的提示，让下一次执行生成新键。
  useEffect(() => { setTransportError(null); }, [ref, args]);

  const typed = useMemo(() => cap.inputs || [], [cap.inputs]);
  const { run, running } = useAction(async (reuseKey = false) => {
    if (!wid) return;
    const payload: Record<string, unknown> = {};
    for (const i of typed) {
      const raw = args[i.name];
      if (raw === undefined || raw === "") continue;
      if ((i.type === "number" || i.type === "integer") && Number.isNaN(Number(raw))) {
        toast(`参数 ${i.name} 要填数字，现在填的是「${raw}」`, "err");
        return;
      }
      payload[i.name] = i.type === "number" || i.type === "integer" ? Number(raw)
        : i.type === "boolean" ? raw === "true" : raw;
    }
    // 超时/断网后重试必须复用同一个键；新的用户动作才生成新键。
    if (!reuseKey) keyRef.current = newIdempotencyKey();
    try {
      const r = await api.invoke(wid, {
        function: `${cap.target_type}.${cap.key}`,
        target: ref || null,
        args: payload,
        idempotency_key: keyRef.current,
      });
      setResult(r);
      setTransportError(null);
      const st = executionStatus(r);
      if (isSuccess(st)) toast("执行成功；状态变更与校验记录已写入", "ok");
      else if (isUnknown(st)) toast("结果未知：请求可能已经生效，已停止自动重试；请查证后关闭", "warn");
      else if (st === "contract_failed") toast("契约未通过：运行时返回了结果，但后置条件不成立，按失败处理", "err");
      else toast(`执行${st === "failed" ? "失败" : "未成功"}：${r.error || "没有返回错误详情"}`, "err");
      candidates.reload();
    } catch (e) {
      if (e instanceof ApiError && e.status === 0) {
        setTransportError(e.message);
        // 网络层没有响应时，先按幂等键回查；查到就展示真实记录，避免重复执行。
        try {
          const rec = await api.executionByKey(wid, keyRef.current);
          setResult({
            status: rec.status,
            ok: rec.status === "succeeded",
            execution_id: rec.execution_id,
            error: rec.error ?? rec.last_error ?? null,
            verification: rec.verification,
            started_at: rec.started_at ?? undefined,
            resolution: rec.resolution ?? undefined,
            retryable: rec.status === "unknown",
          });
          setTransportError(null);
          toast("已按幂等键查到这次执行的真实状态", "warn");
        } catch {
          /* 回查也失败时保留原提示，用户可以复用同一个键重试。 */
        }
      } else {
        setTransportError(null);
      }
      throw e;
    }
  }, [wid, cap, ref, args, typed]);

  const resolve = useAction(async (outcome: "applied" | "not_applied") => {
    if (!wid || !result?.execution_id) return;
    const note = evidence.trim();
    const res = await api.resolveExecution(wid, result.execution_id, {
      outcome,
      decision: outcome,
      note,
      evidence: note,
      reason: note,
    });
    setResult(prev => prev ? {
      ...prev,
      status: outcome === "applied" ? "resolved_applied" : "resolved_not_applied",
      resolution: res.resolution ?? { outcome, note },
    } : prev);
    toast(
      outcome === "applied"
        ? "已记录查证结论：效果已生效；未知不会被改写成成功"
        : "已记录查证结论：效果未生效；如需重新执行，请作为一个新的用户动作",
      "warn",
    );
  }, [wid, result, evidence]);

  const status = result ? executionStatus(result) : null;
  const unknown = status ? isUnknown(status) : false;

  return (
    <Drawer title={`执行 · ${cap.name}`} sub={`${cap.target_type}.${cap.key}`} onClose={onClose} wide
            footer={<>
              <Btn busy={running} disabled={unknown}
                   title={unknown ? "结果未知：先查证，随后才能作为一个新的动作执行" : undefined}
                   onClick={() => void run(!!transportError)}>
                {transportError ? "用同一幂等键重试" : "执行"}
              </Btn>
              {unknown && <span className="muted small">未知结果期间不能重新执行</span>}
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
                {o.type}/{o.id} {valuePreview(o.properties?.name ?? o.properties?.plate ?? "", 40)}
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

      {transportError && (
        <div className="banner warn">
          <b>请求没有拿到响应：{transportError}</b>
          <p className="muted small">这不等于失败，也不等于成功。复用同一个幂等键重试不会重复执行；如果仍没有结论，请到运行页按执行编号查证。</p>
        </div>
      )}

      {result && (
        <>
          <div className="result-head">
            <h3>执行结果</h3>
            <StatusBadge status={status || undefined} />
            {result.idempotent_replay && <Tag kind="plain">幂等回放</Tag>}
            {result.execution_id && <span className="muted mono small">执行编号 {result.execution_id}</span>}
          </div>

          <dl className="kv">
            <div className="kv-pair"><dt>执行状态</dt><dd><StatusBadge status={status || undefined} /></dd></div>
            <div className="kv-pair"><dt>开始时间</dt><dd className="mono">{result.started_at ? formatLocal(result.started_at) : "后端未返回"}</dd></div>
            <div className="kv-pair"><dt>可重试标记</dt><dd>{result.retryable ? "retryable=true（仅表示可人工解析，不自动重试）" : "未标记"}</dd></div>
            <div className="kv-pair"><dt>最后错误</dt><dd className="mono">{result.error || "–"}</dd></div>
            {result.resolution && (
              <div className="kv-pair">
                <dt>人工查证</dt>
                <dd><StatusBadge status={result.resolution.outcome === "applied" ? "resolved_applied" : "resolved_not_applied"} /> {result.resolution.note || ""}</dd>
              </div>
            )}
          </dl>

          {unknown && (
            <div className="banner warn">
              <b>结果未知，已停止自动重试</b>
              <p>请求可能已经改变了业务世界，也可能没有。先到外部系统或对象实例查证，再记录结论；不要直接再执行一次。</p>
              {!result.execution_id && <p className="muted small">后端没有返回执行编号，这里无法写入查证结论；请到运行页执行记录里核对。</p>}
              <Field label="查证说明（可选）" hint="写清楚看了哪里、看到什么，便于审计。">
                <TextInput value={evidence} placeholder="例如：在车辆详情里看到余额已增加 100"
                           onChange={e => setEvidence(e.target.value)} />
              </Field>
              <div className="btn-row">
                <Btn size="sm" busy={resolve.running} disabled={!result.execution_id}
                     onClick={() => void resolve.run("applied")}>查证已生效</Btn>
                <Btn size="sm" variant="subtle" busy={resolve.running} disabled={!result.execution_id}
                     onClick={() => void resolve.run("not_applied")}>查证未生效</Btn>
              </div>
            </div>
          )}

          {status === "contract_failed" && (
            <div className="banner err">
              契约未通过：运行时返回了成功结果，但后置条件不成立。这次执行按失败处理，不能对外说状态已按预期改变。
            </div>
          )}

          {result.verification && (
            <>
              <h3 className="h4">契约校验</h3>
              <ValueView value={result.verification} />
            </>
          )}

          {result.output !== undefined && result.output !== null && (
            <>
              <h3 className="h4">返回</h3>
              <ValueView value={result.output} />
            </>
          )}
          {!!result.changes?.length && (
            <>
              <h3 className="h4">改动了什么（{result.changes.length}）</h3>
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
      <td className="wrap"><ValueView value={c.old} compact /> → <b><ValueView value={c.new} compact /></b></td></tr>;
  if (kind === "link")
    return <tr><td className="nowrap">建立关系</td><td className="wrap" colSpan={2}>
      {String(c.source)} –{String(c.relation)}→ {String(c.target)}</td></tr>;
  if (kind === "create_object")
    return <tr><td className="nowrap">新增对象</td><td className="wrap">{String(c.object)}</td>
      <td className="wrap"><ValueView value={c.properties} compact /></td></tr>;
  return <tr><td className="nowrap">{kind || "变更"}</td><td className="wrap" colSpan={2}><ValueView value={c} compact /></td></tr>;
}
