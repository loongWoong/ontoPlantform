import { useMemo, useState } from "react";
import { api, type AuditEvent, type Ontology, type RuntimeDescribe } from "../api";
import { useAction, usePlatform } from "../store";
import {
  Btn, Card, Collapsible, Empty, Field, LIFECYCLE_LABEL, Loading,
  TableWrap, Tag, TextInput, useLoad,
} from "../ui";

interface Bundle {
  ont: Ontology | null;
  rt: RuntimeDescribe | null;
  sources: number;
  projections: number;
  caps: number;
  capsPublished: number;
  apps: number;
  objects: number;
  audit: AuditEvent[];
}

/** 工作台：design.txt §41 主流程五步 + 真实计数 + 下一步直达 */
export function Workbench() {
  const { wid, workspaceName, go, toast, reloadWorkspaces, online } = usePlatform();
  const [name, setName] = useState("");

  const { run: createWs, running } = useAction(async () => {
    const trimmed = name.trim();
    if (!trimmed) { toast("请填写工作空间名称", "err"); return; }
    await api.createWorkspace(trimmed, "");
    setName("");
    await reloadWorkspaces();
    toast(`工作空间「${trimmed}」已创建，下一步定义本体`, "ok");
    go("ontology");
  }, [name]);

  const bundle = useLoad<Bundle | null>(async () => {
    if (!wid) return null;
    const [ont, rt, sources, projections, caps, apps, audit] = await Promise.all([
      api.ontology(wid).catch(() => null),
      api.runtime(wid).catch(() => null),
      api.datasources(wid).catch(() => []),
      api.projections(wid).catch(() => []),
      api.capabilities(wid).catch(() => []),
      api.applications(wid).catch(() => []),
      api.audit(wid, 6).catch(() => [] as AuditEvent[]),
    ]);
    return {
      ont, rt,
      sources: sources.length, projections: projections.length,
      caps: caps.length, capsPublished: caps.filter(c => c.lifecycle === "published").length,
      apps: apps.length,
      objects: rt ? (rt.objects || []).length : 0,
      audit,
    };
  }, [wid]);

  const b = bundle.data;
  const steps = useMemo(() => [
    {
      n: "1", title: "创建工作空间", done: !!wid,
      desc: wid ? `已就绪 · ${workspaceName}` : online ? "还没开始" : "服务没连上，先检查 onto-api 是否在运行",
      to: "", label: "",
    },
    {
      n: "2", title: "定义本体", done: !!b && Object.keys(b.ont?.objects || {}).length > 0,
      desc: b ? `${Object.keys(b.ont?.objects || {}).length} 个对象 · ${Object.keys(b.ont?.relations || {}).length} 条关系 · ${Object.keys(b.ont?.vocabulary || {}).length} 个术语`
              : "读取中…",
      to: "ontology", label: "去定义",
    },
    {
      n: "3", title: "发布业务世界", done: !!b?.rt,
      desc: b?.rt ? `已发布 · ${(b.rt.functions || []).length} 个可执行动作` : "发布之后对象实例才能进来",
      to: "ontology", label: "去发布",
    },
    {
      n: "4", title: "接入现实数据", done: !!b && b.projections > 0,
      desc: b ? `${b.sources} 个数据源 · ${b.projections} 个映射 · 已进来 ${b.objects} 个对象实例` : "读取中…",
      to: "data", label: "去接入",
    },
    {
      n: "5", title: "沉淀能力 · 组装应用", done: !!b && b.capsPublished > 0 && b.apps > 0,
      desc: b ? `${b.capsPublished}/${b.caps} 个能力已发布 · ${b.apps} 个应用` : "读取中…",
      to: "capability", label: "去创建",
    },
  ], [b, wid, workspaceName, online]);

  return (
    <>
      <Card title={wid ? `工作台 · ${workspaceName}` : "工作台"}
            sub="按这五步走一遍，就得到一个能被业务人员和 AI 一起使用的业务世界。">
        <div className="steps">
          {steps.map(s => (
            <div className="step" key={s.n} data-done={s.done ? "true" : "false"}>
              <span className="step-n">STEP {s.n}{s.done ? " · 完成" : ""}</span>
              <span className="step-t">{s.title}</span>
              <span className="step-c">{s.desc}</span>
              {s.label && !!wid && <Btn size="sm" variant="subtle" onClick={() => go(s.to)}>{s.label}</Btn>}
            </div>
          ))}
        </div>

        {!wid && (
          <form className="panel-form" onSubmit={e => { e.preventDefault(); void createWs(); }}>
            <Field label="新工作空间" hint="工作空间是隔离容器：各自拥有独立的本体、数据、能力与应用。">
              <TextInput value={name} placeholder="例如：高速运营" onChange={e => setName(e.target.value)} />
            </Field>
            <Btn type="submit" busy={running}>创建并开始建模</Btn>
            {!online && <span className="muted">检测不到服务，请先运行 python -m onto_platform.interfaces.api</span>}
          </form>
        )}
      </Card>

      <div className="layout cols-2">
        <Card title="业务世界状态" sub="只展示运行时里真正生效的定义。">
          {!wid && <Empty title="还没有工作空间" hint="在上方创建第一个工作空间。" />}
          {wid && bundle.loading && <Loading />}
          {wid && !bundle.loading && !b?.rt && (
            <Empty title="业务世界还没启动" hint="到「本体」页把定义发布出去，这里会出现运行时索引。"
                   action={<Btn size="sm" onClick={() => go("ontology")}>去发布本体</Btn>} />
          )}
          {b?.rt && <RuntimeIndex rt={b.rt} ont={b.ont} />}
        </Card>

        <Card title="最近发生了什么"
              aside={<Btn size="sm" variant="subtle" onClick={() => go("settings", { tab: "audit" })}>全部记录</Btn>}>
          {!wid && <Empty title="暂无记录" />}
          {!!wid && !!b && b.audit.length === 0 && <Empty title="暂无记录" hint="任何创建、发布、同步都会留痕。" />}
          {!!b?.audit.length && (
            <TableWrap auto>
              <table>
                <thead><tr><th>时间</th><th>动作</th><th>对象</th></tr></thead>
                <tbody>
                  {b.audit.map((e, i) => (
                    <tr key={i}>
                      <td className="nowrap" title={e.ts}>{e.ts.replace("T", " ").slice(5, 19)}</td>
                      <td className="nowrap"><Tag kind="plain">{e.action}</Tag></td>
                      <td title={e.target}>{e.target}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
          )}
        </Card>
      </div>

      <Collapsible label="三条链路怎么串起来（平台与内核的分工）">
        <p className="muted">
          定义 → 发布：本体经编译器翻译成运行时结构，平台不复制第二份业务状态。
          数据 → 状态：数据源经映射与投影写入内核，需要时再同步。
          语言 → 行动：术语把「收费站」翻译成业务对象，业务人员和 AI 都只能通过能力改变世界，
          每次改变都留下可追溯的记录。
        </p>
      </Collapsible>
    </>
  );
}

function RuntimeIndex({ rt, ont }: { rt: RuntimeDescribe; ont: Ontology | null }) {
  const life = ont?.lifecycle || "none";
  return (
    <>
      <div className="result-head">
        <h4>已生效结构</h4>
        <Tag kind={life === "published" ? "ok" : "warn"}>{LIFECYCLE_LABEL[life] || life}</Tag>
        <span className="muted">版本 v{rt.ontology?.version ?? "-"}</span>
      </div>
      <dl className="kv">
        <dt>对象类型</dt><dd>{(rt.objects || []).map(o => o.name).join("、") || "—"}</dd>
        <dt>关系</dt><dd>{(rt.relations || []).map(r => `${r.source} —${r.name}→ ${r.target}`).join("；") || "—"}</dd>
        <dt>可执行动作</dt><dd>{(rt.functions || []).map(f => f.name).join("、") || "—"}</dd>
      </dl>
    </>
  );
}
