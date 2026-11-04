import { useMemo, useState } from "react";
import {
  ArrowClockwise, CheckCircle, Fingerprint, Plus, ShieldWarning, Trash,
} from "@phosphor-icons/react";
import {
  api, type PolicyDraftResult, type SecurityAuthorizeResult, type SecurityBinding,
  type SecurityDecision, type SecurityDryRunResult, type SecurityPolicy,
  type SecurityPolicyDiff, type SecurityPolicyList, type SecurityRoleList,
  type SecurityScopeList,
} from "../api";
import { useAction, usePlatform } from "../store";
import {
  Area, Btn, Card, Empty, ErrorNote, Field, Loading, PageHead, Select,
  Stat, Tag, TextInput, useLoad,
} from "../ui";
import { SecurityIntegrations, SecurityPeople } from "./SecurityPeople";

const TABS = [
  { id: "overview", label: "授权总览" },
  { id: "bindings", label: "角色与绑定" },
  { id: "people", label: "成员与组织" },
  { id: "preview", label: "决策预览" },
  { id: "policies", label: "策略发布" },
  { id: "integrations", label: "身份与集成" },
  { id: "decisions", label: "决策日志" },
];

const ROLE_LABEL: Record<string, string> = {
  viewer: "只读", editor: "编辑", owner: "所有者",
};
const STATUS_LABEL: Record<string, string> = {
  draft: "草稿", validated: "已校验", approved: "已审批",
  published: "已发布", deprecated: "已废弃",
};

export function SecurityView() {
  const { wid, params, go, toast, confirm } = usePlatform();
  const requestedTab = String(params.tab || "overview");
  const tab = TABS.some(t => t.id === requestedTab) ? requestedTab : "overview";
  const [action, setAction] = useState("workspace.read");
  const [resourceType, setResourceType] = useState("workspace");
  const [resourceId, setResourceId] = useState("");
  const [attributes, setAttributes] = useState("{}");
  const [previewSubject, setPreviewSubject] = useState("");
  const [recordPreview, setRecordPreview] = useState(false);
  const [preview, setPreview] = useState<SecurityAuthorizeResult | null>(null);
  const [editorText, setEditorText] = useState("");
  const [editorId, setEditorId] = useState("");
  const [draftPrompt, setDraftPrompt] = useState("");
  const [draftMode, setDraftMode] = useState("auto");
  const [draftResult, setDraftResult] = useState<PolicyDraftResult | null>(null);

  const roles = useLoad<SecurityRoleList | null>(
    () => (wid ? api.securityRoles(wid) : Promise.resolve(null)), [wid]);
  const scopes = useLoad<SecurityScopeList | null>(
    () => (wid ? api.securityScopes(wid) : Promise.resolve(null)), [wid]);
  const policies = useLoad<SecurityPolicyList | null>(
    () => (wid ? api.securityPolicies(wid) : Promise.resolve(null)), [wid]);
  const bindings = useLoad<SecurityBinding[] | null>(
    () => (wid ? api.securityBindings(wid) : Promise.resolve(null)), [wid]);
  const decisions = useLoad<SecurityDecision[] | null>(
    () => (wid ? api.securityDecisions(wid, 100) : Promise.resolve(null)), [wid]);

  const policy = policies.data?.active;
  const actionRows = useMemo(() => {
    const rows = (policy?.document?.actionPolicies as Record<string, unknown>[]) || [];
    return rows.map(row => ({
      action: String(row.action || ""),
      resource: String(row.resource || ""),
      roles: (row.allowRoles as string[]) || [],
      scope: String(row.scope || ""),
      relation: row.relation ? String(row.relation) : "",
      masks: (row.fieldMasks as Record<string, unknown>[]) || [],
    }));
  }, [policy]);

  const runPreview = useAction(async () => {
    if (!wid) return;
    let parsed: Record<string, unknown> = {};
    if (attributes.trim()) {
      try { parsed = JSON.parse(attributes) as Record<string, unknown>; }
      catch { toast("对象属性必须是合法 JSON", "err"); return; }
    }
    const result = await api.securityAuthorize(wid, {
      action, resource_type: resourceType || "workspace",
      resource_id: resourceId, attributes: parsed,
      subject: previewSubject.trim() || undefined,
    }, recordPreview);
    setPreview(result);
    if (recordPreview) decisions.reload();
    toast(result.decision.allow ? "PDP 允许本次访问" : "PDP 拒绝本次访问", result.decision.allow ? "ok" : "warn");
  }, [wid, action, resourceType, resourceId, attributes, previewSubject, recordPreview]);

  const createDraft = useAction(async () => {
    if (!wid || !policy) return;
    await api.securityCreatePolicy(wid, {
      document: policy.document,
      note: `从 ${policy.id} v${policy.version} 复制`,
    });
    policies.reload();
    toast("已创建策略草稿；先校验，再由另一位所有者审批", "ok");
  }, [wid, policy]);

  const draftPolicy = useAction(async () => {
    if (!wid || !draftPrompt.trim()) {
      toast("用一句话描述想要的权限变化", "err");
      return;
    }
    const result = await api.securityDraftPolicy(wid, {
      prompt: draftPrompt.trim(), mode: draftMode,
    });
    setDraftResult(result);
    const errors = result.validation?.errors || [];
    toast(errors.length
      ? `草案已生成但校验未通过：${errors[0]}`
      : "草案已生成；它不会自动保存或发布", errors.length ? "warn" : "ok");
  }, [wid, draftPrompt, draftMode]);

  const useDraft = useAction(async () => {
    if (!wid || !draftResult) return;
    const created = await api.securityCreatePolicy(wid, {
      document: draftResult.document, note: `AI 草案：${draftPrompt}`,
    });
    setDraftResult(null); setDraftPrompt("");
    policies.reload();
    openEditor(created);
    toast("草案已保存为草稿；请先校验，再由另一位所有者审批", "ok");
  }, [wid, draftResult, draftPrompt]);

  const openEditor = (p: SecurityPolicy) => {
    setEditorId(p.id);
    setEditorText(JSON.stringify(p.document, null, 2));
    setToolPolicy(p.id);
    setToolDiff(null);
    setDryResult(null);
  };

  const saveDraft = useAction(async () => {
    if (!wid || !editorId) return;
    let document: Record<string, unknown>;
    try {
      document = JSON.parse(editorText) as Record<string, unknown>;
    } catch {
      toast("策略 JSON 不合法，未保存", "err");
      return;
    }
    const current = (policies.data?.policies || []).find(p => p.id === editorId);
    if (!current) return;
    await api.securityUpdatePolicy(wid, editorId, {
      document,
      note: "控制台编辑",
      expected_version: current.version,
    });
    policies.reload(); scopes.reload(); roles.reload();
    toast("草稿已保存；校验通过后才能进入审批", "ok");
  }, [wid, editorId, editorText, policies.data]);

  const [subjectType, setSubjectType] = useState("user");
  const [subjectId, setSubjectId] = useState("");
  const [bindingRole, setBindingRole] = useState("viewer");
  const [bindingScope, setBindingScope] = useState("workspace");
  const addBinding = useAction(async () => {
    if (!wid) return;
    if (!subjectId.trim()) { toast("请填写主体标识", "err"); return; }
    await api.securityCreateBinding(wid, {
      subject_type: subjectType, subject_id: subjectId.trim(),
      role: bindingRole, scope_type: bindingScope,
      scope_id: bindingScope === "resource" ? resourceId : undefined,
    });
    setSubjectId("");
    bindings.reload(); roles.reload();
    toast("绑定已创建", "ok");
  }, [wid, subjectType, subjectId, bindingRole, bindingScope, resourceId]);

  const removeBinding = useAction(async (bid: string) => {
    if (!wid) return;
    await api.securityDeleteBinding(wid, bid);
    bindings.reload(); roles.reload();
    toast("绑定已删除", "ok");
  }, [wid]);

  const validate = useAction(async (pid: string) => {
    if (!wid) return;
    const result = await api.securityValidatePolicy(wid, pid);
    policies.reload();
    const errors = result.validation?.errors || [];
    toast(errors.length ? `校验未通过：${errors[0]}` : "校验通过，等待另一位所有者审批",
      errors.length ? "err" : "ok");
  }, [wid]);

  const approve = useAction(async (pid: string) => {
    if (!wid) return;
    await api.securityApprovePolicy(wid, pid);
    policies.reload();
    toast("策略草稿已审批；发布需要另一位所有者", "ok");
  }, [wid]);

  const publish = useAction(async (pid: string, expected?: number) => {
    if (!wid) return;
    await api.securityPublishPolicy(wid, pid, expected);
    policies.reload(); scopes.reload(); roles.reload();
    toast("策略版本已发布，新请求立即使用该版本", "ok");
  }, [wid]);

  const rollback = useAction(async (pid: string, expected?: number) => {
    if (!wid) return;
    confirm({
      title: "回滚到这个历史版本？",
      body: "回滚会把该历史策略复制为一个新的发布版本，旧版本不会被修改；当前版本会转为已废弃。",
      confirmText: "回滚",
      danger: true,
      run: async () => {
        await api.securityRollbackPolicy(wid, pid, expected);
        policies.reload(); scopes.reload(); roles.reload();
        toast("已从该历史版本生成新的生效版本", "ok");
      },
    });
  }, [wid, confirm]);

  const [toolPolicy, setToolPolicy] = useState("");
  const [toolDiff, setToolDiff] = useState<SecurityPolicyDiff | null>(null);
  const [dryAction, setDryAction] = useState("workspace.read");
  const [drySubject, setDrySubject] = useState("");
  const [dryResult, setDryResult] = useState<SecurityDryRunResult | null>(null);
  const loadDiff = useAction(async (pid: string) => {
    if (!wid) return;
    setToolPolicy(pid);
    setDryResult(null);
    setToolDiff(await api.securityPolicyDiff(wid, pid));
  }, [wid]);
  const runDryRun = useAction(async (pid: string) => {
    if (!wid) return;
    setToolPolicy(pid);
    setToolDiff(null);
    setDryResult(await api.securityDryRun(wid, pid, {
      action: dryAction,
      resource_type: "workspace",
      resource_id: wid,
      subject: drySubject.trim() || undefined,
    }));
  }, [wid, dryAction, drySubject]);

  if (!wid) return <Empty title="先选择工作空间" hint="权限策略按工作空间隔离。" />;
  if (roles.error || scopes.error || policies.error
      || bindings.error || decisions.error) {
    return <ErrorNote
      text={roles.error || scopes.error || policies.error
        || bindings.error || decisions.error || ""}
      onRetry={() => {
        roles.reload(); scopes.reload(); policies.reload();
        bindings.reload(); decisions.reload();
      }} />;
  }
  if (roles.loading && policies.loading) return <Loading text="读取权限策略" />;

  const label = policy?.document?.metadata as Record<string, unknown> | undefined;
  const roleCount = roles.data?.roles.length || 0;
  const canManage = roles.data?.can_manage === true;
  const canPublish = roles.data?.can_publish === true;
  const roleLabel = (role: string) => {
    const custom = roles.data?.roles.find(r => r.id === role)?.displayName;
    return custom || ROLE_LABEL[role] || role;
  };
  const bindingCount = bindings.data?.length || 0;
  const decisionCount = (decisions.data || []).length || 0;
  const denied = (decisions.data || []).filter(d => !d.allow).length;
  const fieldMaskCount = actionRows.reduce((n, row) => n + row.masks.length, 0)
    + ((policy?.document?.fieldPolicies as unknown[] | undefined)?.length || 0);
  const fieldPolicies = (policy?.document?.fieldPolicies as Record<string, unknown>[] | undefined) || [];

  return (
    <>
      <PageHead
        kicker="Security"
        title="权限控制台"
        desc="统一策略内核：认证后按租户、角色、数据范围与字段脱敏逐层收敛；所有拒绝默认关闭。"
        meta={<>
          <Tag kind={policy?.status === "published" ? "ok" : "warn"}>
            {policy ? `${policy.id === "builtin" ? "内置策略" : policy.id} · v${policy.version}` : "读取中"}
          </Tag>
          <Tag kind="plain">deny-by-default</Tag>
          <Tag kind="plain">工作空间 = 租户边界</Tag>
          {!canManage && <Tag kind="warn">只读身份 · 需要所有者才能修改策略</Tag>}
        </>}
        actions={<Btn variant="subtle" onClick={() => { roles.reload(); scopes.reload(); policies.reload(); bindings.reload(); decisions.reload(); }}>
          <ArrowClockwise size={14} /> 刷新
        </Btn>}
      />

      <div className="subtabs" role="tablist" tabIndex={0} aria-label="权限控制台分组">
        {TABS.map(t => (
          <button key={t.id} type="button" role="tab" aria-selected={tab === t.id}
                  id={`security-tab-${t.id}`}
                  aria-controls={`security-panel-${t.id}`}
                  tabIndex={tab === t.id ? 0 : -1}
                  onKeyDown={e => {
                    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
                    e.preventDefault();
                    const index = TABS.findIndex(item => item.id === tab);
                    const delta = e.key === "ArrowRight" ? 1 : -1;
                    const next = TABS[(index + delta + TABS.length) % TABS.length];
                    go("security", { tab: next.id });
                  }}
                  onClick={() => go("security", { tab: t.id })}>{t.label}</button>
        ))}
      </div>

      <div role="tabpanel" id={`security-panel-${tab}`}
           aria-labelledby={`security-tab-${tab}`} tabIndex={0}>
      {tab === "overview" && (
        <>
          <div className="bento">
            <div className="col-3"><Stat label="角色" value={roleCount}
              foot={`${roles.data?.roles.filter(r => r.inherits?.length).length || 0} 个继承角色 · 策略 v${roles.data?.policy_version ?? 0}`} /></div>
            <div className="col-3"><Stat label="角色绑定" value={bindingCount}
              foot="用户 / 组 / 服务主体到角色的授权" /></div>
            <div className="col-3"><Stat label="动作规则" value={actionRows.length}
              foot={`${Object.keys(scopes.data?.scopes || {}).length} 个数据范围`} /></div>
            <div className="col-3"><Stat label="字段掩码" value={fieldMaskCount}
              foot="作为允许决策的强制义务执行" tone={fieldMaskCount ? "green" : "sunken"} /></div>
          </div>

          <div className="bento gap-top">
            <div className="col-8">
              <Card title="授权矩阵"
                    sub="动作 × 资源 × 允许角色。策略由内置动作矩阵生成；自定义策略发布后立即替换。"
                    aside={<Tag kind="plain">{policy ? `${policy.hash.slice(0, 12)}…` : "–"}</Tag>}>
                {actionRows.length === 0 ? <Empty title="没有动作规则" hint="当前策略没有 actionPolicies。" />
                  : (
                    <div className="tablewrap auto">
                      <table className="table">
                        <thead><tr><th>动作</th><th>资源</th><th>允许角色</th><th>范围 / 关系</th></tr></thead>
                        <tbody>
                          {actionRows.map(row => (
                            <tr key={`${row.action}:${row.resource}`}>
                              <td className="mono">{row.action}</td>
                              <td className="mono">{row.resource}</td>
                              <td><div className="pill-row">{row.roles.map(r => <Tag key={r} kind="plain">{roleLabel(r)}</Tag>)}</div></td>
                              <td className="mono small">{row.scope || "–"}{row.relation ? ` · relation=${row.relation}` : ""}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
              </Card>
            </div>
            <div className="col-4">
              <Card title="数据范围" sub="范围作为行过滤下推到查询，不信任前端传参。">
                {Object.keys(scopes.data?.scopes || {}).length === 0
                  ? <Empty title="没有范围" hint="未声明数据范围时使用工作空间范围。" />
                  : (
                    <dl className="kv">
                      {Object.entries(scopes.data?.scopes || {}).map(([name, spec]) => (
                        <div className="kv-pair" key={name}>
                          <dt className="mono">{name}</dt>
                          <dd className="mono small">{JSON.stringify(spec.filter || spec.match, null, 0)}</dd>
                        </div>
                      ))}
                    </dl>
                  )}
              </Card>
              <Card title="角色继承" sub="角色取并集后由 PDP 展开；未知角色不产生权限。">
                <dl className="kv">
                  {(roles.data?.roles || []).map(role => (
                    <div className="kv-pair" key={role.id}>
                      <dt>{roleLabel(role.id)} <span className="muted small">{role.id}</span></dt>
                      <dd className="mono small">{role.inherits?.length ? `继承 ${role.inherits.map(roleLabel).join(" / ")}` : "基础角色"}</dd>
                    </div>
                  ))}
                </dl>
              </Card>
              <Card title="字段保护" sub="字段掩码是 allow 的义务；未注册 masker 时决策降级为拒绝。">
                {fieldPolicies.length === 0 && actionRows.every(row => row.masks.length === 0)
                  ? <Empty title="没有字段掩码" hint="字段策略为可选；一旦声明就会在读取、导出与 Agent 上下文中执行。" />
                  : (
                    <dl className="kv">
                      {fieldPolicies.map((spec, index) => (
                        <div className="kv-pair" key={`global-${index}`}>
                          <dt className="mono">{String(spec.field || "?")}</dt>
                          <dd className="mono small">全局 · {String(spec.mask || spec.effect || "mask")}</dd>
                        </div>
                      ))}
                      {actionRows.flatMap(row => row.masks.map((mask, index) => (
                        <div className="kv-pair" key={`${row.action}-${index}`}>
                          <dt className="mono">{String(mask.field || "?")}</dt>
                          <dd className="mono small">{row.action} · {String(mask.mask || "mask")}</dd>
                        </div>
                      )))}
                    </dl>
                  )}
              </Card>
            </div>
          </div>

          <div className="bento gap-top">
            <div className="col-6">
              <Card title="最近决策" sub="预览默认无副作用；勾选写入日志后可追溯证据。"
                    aside={<Btn variant="link" size="sm" onClick={() => go("security", { tab: "decisions" })}>全部</Btn>}>
                {(decisions.data || []).slice(0, 6).map(d => (
                  <div className="security-decision-row" key={d.decision_id}>
                    <span className={"tag " + (d.allow ? "ok" : "err")}>{d.allow ? "允许" : "拒绝"}</span>
                    <span className="mono small">{d.action}</span>
                    <span className="muted small">{d.principal_id} · {d.reason}</span>
                  </div>
                ))}
                {decisionCount === 0 && <Empty title="还没有决策" hint="在「决策预览」里发起一次授权请求。" />}
              </Card>
            </div>
            <div className="col-6">
              <Card title="AI 安全护栏" sub="Agent 不能超过调用者权限，也不接受来自蓝图提示词的越权指令。">
                <dl className="kv">
                  <div className="kv-pair"><dt>调用者上下文</dt><dd>Agent 主体继承调用者角色并取交集；被调用能力再执行一次 PDP。</dd></div>
                  <div className="kv-pair"><dt>蓝图权限</dt><dd>确定性校验 + 人工审批；AI 只能提议草稿，不能直接发布策略。</dd></div>
                  <div className="kv-pair"><dt>失败语义</dt><dd>缺少上下文、未知义务、无法脱敏字段一律拒绝。</dd></div>
                </dl>
              </Card>
            </div>
          </div>
        </>
      )}

      {tab === "bindings" && (
        <div className="bento">
          <div className="col-4">
            <Card title="新增绑定" sub="绑定把主体映射到角色；条件为空时生效于整个工作空间。">
              <Field label="主体类型">
                <Select value={subjectType} onChange={e => setSubjectType(e.target.value)}>
                  <option value="user">用户</option>
                  <option value="group">用户组</option>
                  <option value="service">服务账号</option>
                </Select>
              </Field>
              <Field label="主体标识" hint="例如 alice 或 operators">
                <TextInput value={subjectId} onChange={e => setSubjectId(e.target.value)} placeholder="alice" />
              </Field>
              <Field label="角色">
                <Select value={bindingRole} onChange={e => setBindingRole(e.target.value)}>
                  {(roles.data?.roles || []).map(r => <option key={r.id} value={r.id}>{roleLabel(r.id)}</option>)}
                </Select>
              </Field>
              <Field label="范围" hint="资源级授权请使用关系元组。">
                <Select value={bindingScope} onChange={e => setBindingScope(e.target.value)}>
                  <option value="workspace">工作空间</option>
                  <option value="tenant">租户</option>
                </Select>
              </Field>
              <Btn onClick={() => addBinding.run()} busy={addBinding.running}
                   disabled={!canManage}>{<Plus size={14} />} 创建绑定</Btn>
            </Card>
          </div>
          <div className="col-8">
            <Card title="当前绑定" sub="角色绑定与成员角色取并集；删除绑定不会删除成员。"
                  aside={<Tag kind="plain">{bindingCount} 条</Tag>}>
              {bindings.loading ? <Loading /> : (bindings.data || []).length === 0
                ? <Empty title="还没有显式绑定" hint="成员角色仍然来自工作空间成员关系。" />
                : (
                  <div className="tablewrap auto">
                    <table className="table">
                      <thead><tr><th>主体</th><th>角色</th><th>范围</th><th>条件</th><th /></tr></thead>
                      <tbody>
                        {(bindings.data || []).map(b => (
                          <tr key={b.id}>
                            <td><span className="mono">{b.subject_id}</span> <Tag kind="plain">{b.subject_type}</Tag></td>
                            <td>{roleLabel(b.role)}</td>
                            <td className="mono small">{b.scope_type}{b.scope_id ? ` · ${b.scope_id}` : ""}</td>
                            <td className="mono small">{b.conditions && Object.keys(b.conditions).length ? JSON.stringify(b.conditions) : "–"}</td>
                            <td><Btn variant="danger" size="sm" onClick={() => removeBinding.run(b.id)}
                                     busy={removeBinding.running} disabled={!canManage}
                                     aria-label={`删除 ${b.subject_id} 的 ${b.role} 绑定`}><Trash size={13} /></Btn></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
            </Card>
          </div>
        </div>
      )}

      {tab === "preview" && (
        <div className="bento">
          <div className="col-5">
            <Card title="授权请求" sub="这里直接调用 PEP→PDP，结果不是前端推断。">
              <Field label="动作">
                <Select value={action} onChange={e => setAction(e.target.value)}>
                  {actionRows.map(row => <option key={`${row.action}:${row.resource}`} value={row.action}>{row.action}</option>)}
                  {actionRows.every(row => row.action !== action) && <option value={action}>{action}</option>}
                </Select>
              </Field>
              <Field label="资源类型">
                <TextInput value={resourceType} onChange={e => setResourceType(e.target.value)} placeholder="Vehicle" />
              </Field>
              <Field label="以谁的身份试算" hint="留空表示当前用户；调用者仍需具备 security.read。">
                <TextInput value={previewSubject} onChange={e => setPreviewSubject(e.target.value)} placeholder="alice" />
              </Field>
              <Field label="资源 ID">
                <TextInput value={resourceId} onChange={e => setResourceId(e.target.value)} placeholder="10086" />
              </Field>
              <Field label="对象属性 JSON" hint="只作为 PDP 输入，不会被持久化。">
                <Area rows={8} value={attributes} onChange={e => setAttributes(e.target.value)} />
              </Field>
              <label className="muted small" style={{ display: "flex", gap: 6, alignItems: "center", marginBottom: 10 }}>
                <input type="checkbox" checked={recordPreview}
                       onChange={e => setRecordPreview(e.target.checked)} />
                写入决策日志（关闭后为无副作用预览）
              </label>
              <Btn onClick={() => runPreview.run()} busy={runPreview.running}>
                <Fingerprint size={14} /> 请求决策
              </Btn>
            </Card>
          </div>
          <div className="col-7">
            <Card title="决策结果" sub="允许时同时返回必须执行的义务；义务无法执行时结果降级为拒绝。">
              {!preview ? <Empty title="等待一次决策" hint="提交左侧请求后，这里显示 allow / deny、理由、策略版本与义务。" />
                : (
                  <>
                    <div className={"security-verdict " + (preview.decision.allow ? "ok" : "err")}>
                      {preview.decision.allow ? <CheckCircle size={20} weight="fill" /> : <ShieldWarning size={20} weight="fill" />}
                      <b>{preview.decision.allow ? "ALLOW" : "DENY"}</b>
                      <span className="mono small">{preview.decision.reason}</span>
                      <span className="mono small">{preview.decision.duration_ms ?? 0} ms · policy v{preview.decision.policy_version}</span>
                    </div>
                    <dl className="kv">
                      <div className="kv-pair"><dt>主体</dt><dd className="mono">{preview.principal.id} · {preview.principal.roles.map(roleLabel).join(" / ")}</dd></div>
                      <div className="kv-pair"><dt>租户 / 工作空间</dt><dd className="mono">{preview.tenant.tenant_id} / {preview.tenant.workspace_id}</dd></div>
                      <div className="kv-pair"><dt>资源</dt><dd className="mono">{preview.decision.resource_type}{preview.decision.resource_id ? `/${preview.decision.resource_id}` : ""}</dd></div>
                      <div className="kv-pair"><dt>决定 ID</dt><dd className="mono">{preview.decision.decision_id}</dd></div>
                      <div className="kv-pair"><dt>义务</dt><dd className="mono small">{preview.decision.obligations.length ? JSON.stringify(preview.decision.obligations) : "无"}</dd></div>
                      <div className="kv-pair"><dt>数据范围</dt><dd className="mono small">{preview.decision.scope ? JSON.stringify(preview.decision.scope) : "未被范围收窄"}</dd></div>
                    </dl>
                  </>
                )}
            </Card>
          </div>
        </div>
      )}

      {tab === "policies" && (
        <>
          <div className="bento">
          <div className="col-5">
            <Card title="当前生效策略" sub="内置策略由动作矩阵生成；自定义策略必须经过校验、审批与发布。"
                  aside={<Tag kind={policy?.status === "published" ? "ok" : "warn"}>{policy ? STATUS_LABEL[policy.status] || policy.status : "–"}</Tag>}>
              <dl className="kv">
                <div className="kv-pair"><dt>策略</dt><dd className="mono">{policy?.id || "–"} · v{policy?.version ?? 0}</dd></div>
                <div className="kv-pair"><dt>来源</dt><dd>{label?.source ? String(label.source) : "内置动作矩阵"}</dd></div>
                <div className="kv-pair"><dt>哈希</dt><dd className="mono small">{policy?.hash || "–"}</dd></div>
                <div className="kv-pair"><dt>创建 / 发布</dt><dd className="small">{policy?.created_by || "系统"} · {policy?.published_at || policy?.created_at || "–"}</dd></div>
              </dl>
              <Btn onClick={() => createDraft.run()} busy={createDraft.running} disabled={!canManage}>
                <Plus size={14} /> 基于当前策略新建草稿
              </Btn>
            </Card>
          </div>
          <div className="col-7">
            <Card title="策略版本" sub="已发布版本不可变；回滚会生成新版本，不修改历史。">
              {policies.loading ? <Loading /> : (policies.data?.policies || []).length === 0
                ? <Empty title="只有内置策略" hint="新建草稿后可在这里校验、审批和发布。" />
                : (
                  <div className="tablewrap auto">
                    <table className="table">
                      <thead><tr><th>版本</th><th>状态</th><th>创建者</th><th>审批</th><th>哈希 / 校验</th><th>操作</th></tr></thead>
                      <tbody>
                        {(policies.data?.policies || []).map(p => (
                          <tr key={p.id}>
                            <td className="mono">v{p.version} · {p.id}</td>
                            <td><Tag kind={p.status === "published" ? "ok" : p.status === "approved" ? "warn" : "plain"}>{STATUS_LABEL[p.status] || p.status}</Tag></td>
                            <td className="small">{p.created_by || "–"}</td>
                            <td className="small">
                              {p.approval_progress
                                ? `${p.approval_progress.received}/${p.approval_progress.required}${p.approval_progress.satisfied ? " 已满足" : " 待审批"}`
                                : (p.approved_by || "–")}
                              {(p.approvals || []).length > 0 && (
                                <><br /><span className="muted mono small">{(p.approvals || []).map(a => a.actor).join(", ")}</span></>
                              )}
                            </td>
                            <td className="mono small">{p.hash.slice(0, 10)}…{p.validation?.valid === false && <><br /><span className="muted">{p.validation.errors?.[0] || "校验失败"}</span></>}</td>
                            <td>
                              <div className="pill-row">
                                <Btn variant="subtle" size="sm" onClick={() => loadDiff.run(p.id)} busy={loadDiff.running}>差异</Btn>
                                {(p.status === "draft" || p.status === "validated") && <Btn variant="subtle" size="sm" onClick={() => openEditor(p)} disabled={!canManage}>编辑</Btn>}
                                <Btn variant="subtle" size="sm" onClick={() => runDryRun.run(p.id)} busy={runDryRun.running} disabled={!canManage}>试算</Btn>
                                {p.status === "draft" && <Btn variant="subtle" size="sm" onClick={() => validate.run(p.id)} busy={validate.running} disabled={!canManage}>校验</Btn>}
                                {p.status === "validated" && <Btn variant="subtle" size="sm" onClick={() => approve.run(p.id)} busy={approve.running} disabled={!canManage}>审批</Btn>}
                                {p.status === "approved" && <Btn variant="subtle" size="sm" onClick={() => publish.run(p.id, p.version)} busy={publish.running} disabled={!canPublish}>发布</Btn>}
                                {p.status === "deprecated" && <Btn variant="subtle" size="sm" onClick={() => rollback.run(p.id, policy?.version)} busy={rollback.running} disabled={!canPublish}>回滚到此版本</Btn>}
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
            </Card>
          </div>
        </div>
          <Card title="AI 策略草案"
                sub="模型只生成候选文档；保存、校验、审批、发布仍走同一条生命周期，草案不会自动生效。"
                aside={<Tag kind="plain">{draftResult ? `来源 ${draftResult.source}` : "未生成"}</Tag>}>
            <div style={{ display: "flex", gap: 10, alignItems: "flex-end", flexWrap: "wrap" }}>
              <Field label="用一句话描述权限变化" hint="例如：只允许只读角色查看车辆，手机号打码。">
                <TextInput value={draftPrompt} onChange={e => setDraftPrompt(e.target.value)}
                           placeholder="只允许 viewer 读取车辆，并隐藏车主手机号" style={{ minWidth: 420 }} />
              </Field>
              <Field label="生成方式">
                <Select value={draftMode} onChange={e => setDraftMode(e.target.value)}>
                  <option value="auto">自动（模型失败时回退模板）</option>
                  <option value="deterministic">仅模板，不调用模型</option>
                  <option value="llm">必须由模型生成</option>
                </Select>
              </Field>
              <Btn onClick={() => draftPolicy.run()} busy={draftPolicy.running}
                   disabled={!canPublish}>生成草案</Btn>
            </div>
            {draftResult && (
              <>
                <div className={"security-verdict " + (draftResult.validation.valid ? "ok" : "err")}
                     style={{ marginTop: 10 }}>
                  {draftResult.validation.valid
                    ? <CheckCircle size={20} weight="fill" />
                    : <ShieldWarning size={20} weight="fill" />}
                  <b>{draftResult.validation.valid ? "草案可保存" : "草案需修改"}</b>
                  <span className="mono small">
                    {(draftResult.validation.errors || []).join("；") || "确定性校验通过"}
                  </span>
                </div>
                <div className="muted small" style={{ margin: "8px 0" }}>
                  stored={String(draftResult.stored)} · published={String(draftResult.published)} ·
                  {" "}下一步：{draftResult.next.join(" → ")}
                </div>
                <Area value={JSON.stringify(draftResult.document, null, 2)} rows={12} readOnly
                      spellCheck={false}
                      style={{ width: "100%", fontFamily: "var(--mono, monospace)", fontSize: 12 }} />
                <div className="pill-row" style={{ marginTop: 10 }}>
                  <Btn onClick={() => useDraft.run()} busy={useDraft.running}
                       disabled={!canPublish || !draftResult.validation.valid}>保存为草稿并编辑</Btn>
                  <Btn variant="subtle" onClick={() => setDraftResult(null)}>丢弃草案</Btn>
                </div>
              </>
            )}
          </Card>
          {editorId && (
            <Card title={`策略编辑 · ${editorId}`}
                  sub="保存后会重新跑确定性校验；无效策略保持草稿，不会替换生效版本。"
                  aside={<Btn variant="subtle" size="sm" onClick={() => setEditorId("")}>关闭</Btn>}>
              <Field label="策略 JSON" hint="包含 roles、scopes、actionPolicies、fieldPolicies 等段。">
                <Area value={editorText} onChange={e => setEditorText(e.target.value)}
                      rows={16} spellCheck={false}
                      style={{ width: "100%", fontFamily: "var(--mono, monospace)", fontSize: 12 }} />
              </Field>
              <div className="pill-row" style={{ marginTop: 10 }}>
                <Btn onClick={() => saveDraft.run()} busy={saveDraft.running} disabled={!canManage}>保存草稿</Btn>
                <span className="muted small">保存后若校验通过，状态变为「已校验」，再由另一位所有者审批。</span>
              </div>
            </Card>
          )}
          {toolPolicy && (
            <Card title={`策略工具 · ${toolPolicy}`}
                  sub="dry-run 以目标策略计算一次决策；差异对比当前生效策略，发布前确认影响面。"
                  aside={<Btn variant="subtle" size="sm" onClick={() => { setToolPolicy(""); setToolDiff(null); setDryResult(null); }}>关闭</Btn>}>
              <div style={{ display: "flex", gap: 10, alignItems: "flex-end", flexWrap: "wrap" }}>
                <Field label="动作">
                  <TextInput value={dryAction} onChange={e => setDryAction(e.target.value)} placeholder="workspace.read" />
                </Field>
                <Field label="试算主体" hint="留空表示当前用户">
                  <TextInput value={drySubject} onChange={e => setDrySubject(e.target.value)} placeholder="bob" />
                </Field>
                <Btn onClick={() => runDryRun.run(toolPolicy)} busy={runDryRun.running} disabled={!canManage}>按此策略试算</Btn>
                <Btn variant="subtle" onClick={() => loadDiff.run(toolPolicy)} busy={loadDiff.running}>对比生效策略</Btn>
              </div>
              {toolDiff && (
                <dl className="kv" style={{ marginTop: 12 }}>
                  <div className="kv-pair"><dt>对比基线</dt><dd className="mono">{toolDiff.against}</dd></div>
                  <div className="kv-pair"><dt>新增动作</dt><dd className="mono small">{toolDiff.actionsAdded.length ? toolDiff.actionsAdded.map(([a, r]) => `${a}/${r}`).join("，") : "无"}</dd></div>
                  <div className="kv-pair"><dt>移除动作</dt><dd className="mono small">{toolDiff.actionsRemoved.length ? toolDiff.actionsRemoved.map(([a, r]) => `${a}/${r}`).join("，") : "无"}</dd></div>
                  <div className="kv-pair"><dt>角色 / 范围</dt><dd className="mono small">+{toolDiff.rolesAdded.join(",") || "–"} / -{toolDiff.rolesRemoved.join(",") || "–"}；+{toolDiff.scopesAdded.join(",") || "–"} / -{toolDiff.scopesRemoved.join(",") || "–"}</dd></div>
                </dl>
              )}
              {dryResult && (
                <>
                  <div className={"security-verdict " + (dryResult.decision.allow ? "ok" : "err")}>
                    {dryResult.decision.allow ? <CheckCircle size={20} weight="fill" /> : <ShieldWarning size={20} weight="fill" />}
                    <b>{dryResult.decision.allow ? "ALLOW" : "DENY"}</b>
                    <span className="mono small">{dryResult.decision.reason}</span>
                    <span className="mono small">subject {dryResult.subject} · v{dryResult.policy_version}</span>
                  </div>
                  <div className="muted small">该结果来自目标策略的 PDP 计算；不会修改任何数据，也不代表当前生效策略。</div>
                  {dryResult.validation?.valid === false && (
                    <div className="security-verdict err" style={{ marginTop: 8 }}>
                      <ShieldWarning size={18} weight="fill" />
                      <b>策略未通过校验</b>
                      <span className="mono small">{dryResult.validation.errors?.join("；") || "未知错误"}</span>
                    </div>
                  )}
                </>
              )}
            </Card>
          )}
        </>
      )}

      {tab === "people" && (
        <SecurityPeople wid={wid} canManage={canManage} />
      )}

      {tab === "integrations" && (
        <SecurityIntegrations wid={wid} canManage={canManage} />
      )}

      {tab === "decisions" && (
        <Card title="决策日志" sub="主动留痕的试算与被拒绝的 PEP 检查；用于回答「谁、在什么策略版本下、为什么被允许/拒绝」。"
              aside={<Btn variant="subtle" size="sm" onClick={() => decisions.reload()}><ArrowClockwise size={13} /> 刷新</Btn>}>
          {decisions.loading ? <Loading /> : (decisions.data || []).length === 0
            ? <Empty title="还没有决策" hint="在「决策预览」里发起一次授权请求。" />
            : (
              <div className="tablewrap auto">
                <table className="table">
                  <thead><tr><th>时间</th><th>结果</th><th>动作 / 主体</th><th>理由</th><th>策略</th><th>义务</th></tr></thead>
                  <tbody>
                    {(decisions.data || []).map(d => (
                      <tr key={d.decision_id}>
                        <td className="mono small">{d.created_at}</td>
                        <td><Tag kind={d.allow ? "ok" : "err"}>{d.allow ? "允许" : "拒绝"}</Tag></td>
                        <td className="small"><span className="mono">{d.action}</span><br />{d.principal_id}</td>
                        <td className="mono small">{d.reason}</td>
                        <td className="mono small">{d.policy_id} v{d.policy_version}</td>
                        <td className="mono small">{d.obligations.length ? d.obligations.map(o => o.kind).join(", ") : "–"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <div className="muted small">显示最近 {Math.min(decisionCount, 100)} 条 · 拒绝 {denied} 条</div>
              </div>
            )}
        </Card>
      )}
      </div>
    </>
  );
}
