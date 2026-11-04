import { useState } from "react";
import { Plus, Trash } from "@phosphor-icons/react";
import {
  api, type Member, type OrgUnit, type SecurityGroup, type SecurityRelation,
} from "../api";
import { useAction, usePlatform } from "../store";
import {
  Btn, Card, Empty, ErrorNote, Field, Loading, Select, Tag, TextInput, useLoad,
} from "../ui";

/** 成员、用户组、关系元组、组织树：同一张授权主体的视图。 */
export function SecurityPeople({ wid, canManage }: {
  wid: string; canManage: boolean;
}) {
  const { toast, confirm } = usePlatform();
  const members = useLoad<Member[]>(() => api.members(wid), [wid]);
  const groups = useLoad<SecurityGroup[]>(() => api.securityGroups(wid), [wid]);
  const relations = useLoad<SecurityRelation[]>(
    () => api.securityRelations(wid), [wid]);
  const tree = useLoad<{ units: OrgUnit[]; count: number }>(
    () => api.securityOrgTree(wid), [wid]);

  const [member, setMember] = useState("");
  const [memberRole, setMemberRole] = useState("viewer");
  const [groupName, setGroupName] = useState("");
  const [groupMembers, setGroupMembers] = useState("");
  const [unitName, setUnitName] = useState("");
  const [unitParent, setUnitParent] = useState("");
  const [unitMembers, setUnitMembers] = useState<Record<string, string>>({});
  const [rel, setRel] = useState({
    resource_type: "workspace", resource_id: wid,
    relation: "viewer", subject_type: "user", subject_id: "",
  });

  const addMember = useAction(async () => {
    if (!member.trim()) { toast("请填写成员标识", "err"); return; }
    await api.addMember(wid, member.trim(), memberRole);
    setMember(""); members.reload();
    toast("成员已加入；角色绑定和成员角色取并集", "ok");
  }, [wid, member, memberRole]);

  const removeMember = (user: string) => confirm({
    title: `把 ${user} 移出工作空间？`,
    body: "移出后该用户的成员角色立即失效；显式角色绑定需要另行删除。",
    confirmText: "移出", danger: true,
    run: async () => {
      await api.removeMember(wid, user);
      members.reload();
      toast("成员已移出", "ok");
    },
  });

  const createGroup = useAction(async () => {
    if (!groupName.trim()) { toast("请填写用户组名称", "err"); return; }
    await api.securityCreateGroup(wid, {
      name: groupName.trim(),
      members: groupMembers.split(",").map(v => v.trim()).filter(Boolean),
    });
    setGroupName(""); setGroupMembers(""); groups.reload();
    toast("用户组已创建，可绑定角色", "ok");
  }, [wid, groupName, groupMembers]);

  const saveGroupMembers = useAction(async (group: SecurityGroup, text: string) => {    const rows = text.split(",").map(v => v.trim()).filter(Boolean);
    await api.securityUpdateGroup(wid, group.id, { members: rows });
    groups.reload();
    toast("用户组成员已更新", "ok");
  }, [wid]);

  const removeGroup = (group: SecurityGroup) => confirm({
    title: `删除用户组 ${group.name}？`,
    body: "引用该组的角色绑定会失效；成员账号本身不会被删除。",
    confirmText: "删除", danger: true,
    run: async () => {
      await api.securityDeleteGroup(wid, group.id);
      groups.reload(); toast("用户组已删除", "ok");
    },
  });

  const createUnit = useAction(async () => {
    if (!unitName.trim()) { toast("请填写组织单元名称", "err"); return; }
    await api.securityCreateOrgUnit(wid, {
      name: unitName.trim(), parent_id: unitParent.trim() || undefined,
    });
    setUnitName(""); setUnitParent(""); tree.reload();
    toast("组织单元已创建；组织路径会写入主体属性", "ok");
  }, [wid, unitName, unitParent]);

  const removeUnit = (unit: OrgUnit) => confirm({
    title: `删除组织单元 ${unit.name}？`,
    body: "有子单元或有成员时删除会被拒绝；先迁移成员或删除子单元。",
    confirmText: "删除", danger: true,
    run: async () => {
      await api.securityDeleteOrgUnit(wid, unit.id);
      tree.reload(); toast("组织单元已删除", "ok");
    },
  });

  const saveUnitMembers = useAction(async (unit: OrgUnit) => {
    const text = unitMembers[unit.id] ?? unit.members.join(", ");
    const rows = text.split(",").map(v => v.trim()).filter(Boolean);
    await api.securitySetOrgMembers(wid, unit.id, rows);
    tree.reload(); toast("组织成员已更新", "ok");
  }, [wid, unitMembers]);

  const createRelation = useAction(async () => {
    if (!rel.subject_id.trim()) { toast("请填写关系主体", "err"); return; }
    await api.securityCreateRelation(wid, { ...rel,
      subject_id: rel.subject_id.trim() });
    setRel({ ...rel, subject_id: "" });
    relations.reload(); toast("关系元组已创建", "ok");
  }, [wid, rel]);

  const removeRelation = (row: SecurityRelation) => confirm({
    title: "删除这条关系元组？",
    body: "删除后依赖该关系的资源级授权立即失效。",
    confirmText: "删除", danger: true,
    run: async () => {
      await api.securityDeleteRelation(wid, {
        resource_type: row.resource_type, resource_id: row.resource_id,
        relation: row.relation, subject_type: row.subject_type,
        subject_id: row.subject_id,
      });
      relations.reload(); toast("关系元组已删除", "ok");
    },
  });

  const errors = [members.error, groups.error, relations.error, tree.error]
    .filter(Boolean) as string[];
  if (errors.length) {
    return <ErrorNote text={errors[0]} onRetry={() => {
      members.reload(); groups.reload(); relations.reload(); tree.reload();
    }} />;
  }
  if (members.loading && groups.loading && tree.loading) return <Loading />;

  return (
    <div className="bento">
      <div className="col-5">
        <Card title="工作空间成员" sub="成员角色与角色绑定取并集；最后一个所有者不能被移出。"
              aside={<Tag kind="plain">{members.data?.length || 0} 人</Tag>}>
          <Field label="成员标识"><TextInput value={member} onChange={e => setMember(e.target.value)} placeholder="alice" /></Field>
          <Field label="成员角色">
            <Select value={memberRole} onChange={e => setMemberRole(e.target.value)}>
              <option value="viewer">只读</option><option value="editor">编辑</option><option value="owner">所有者</option>
            </Select>
          </Field>
          <Btn onClick={() => addMember.run()} busy={addMember.running}
               disabled={!canManage}>{<Plus size={14} />} 添加成员</Btn>
          <div className="tablewrap auto" style={{ marginTop: 10 }}>
            <table className="table">
              <thead><tr><th>成员</th><th>角色</th><th /></tr></thead>
              <tbody>
                {(members.data || []).map(m => (
                  <tr key={m.user}>
                    <td className="mono">{m.user}</td>
                    <td><Tag kind={m.role === "owner" ? "warn" : "plain"}>{m.role}</Tag></td>
                    <td><Btn variant="danger" size="sm" disabled={!canManage}
                             onClick={() => removeMember(m.user)} aria-label={`移出 ${m.user}`}><Trash size={13} /></Btn></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        <Card title="用户组" sub="组成员可被角色绑定引用，支持组内嵌套成员。">
          <Field label="组名"><TextInput value={groupName} onChange={e => setGroupName(e.target.value)} placeholder="operators" /></Field>
          <Field label="成员（逗号分隔）"><TextInput value={groupMembers} onChange={e => setGroupMembers(e.target.value)} placeholder="alice, bob" /></Field>
          <Btn onClick={() => createGroup.run()} busy={createGroup.running}
               disabled={!canManage}>{<Plus size={14} />} 创建用户组</Btn>
          {(groups.data || []).map(g => <GroupRow key={g.id} group={g} busy={saveGroupMembers.running}
            disabled={!canManage} onSave={text => saveGroupMembers.run(g, text)}
            onDelete={() => removeGroup(g)} />)}
        </Card>
      </div>

      <div className="col-7">
        <Card title="组织树" sub="组织路径写入 principal.org_path，可用于数据范围与字段掩码条件。"
              aside={<Tag kind="plain">{tree.data?.count || 0} 个单元</Tag>}>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
            <Field label="单元名称"><TextInput value={unitName} onChange={e => setUnitName(e.target.value)} placeholder="济南分部" /></Field>
            <Field label="父单元 ID" hint="留空为根单元"><TextInput value={unitParent} onChange={e => setUnitParent(e.target.value)} placeholder="可选" /></Field>
            <Btn onClick={() => createUnit.run()} busy={createUnit.running}
                 disabled={!canManage}>{<Plus size={14} />} 新建单元</Btn>
          </div>
          {(tree.data?.units || []).length === 0
            ? <Empty title="还没有组织单元" hint="根单元建立后，子单元按 materialized path 归属。" />
            : (
              <div className="tablewrap auto">
                <table className="table">
                  <thead><tr><th>路径 / 名称</th><th>成员</th><th>操作</th></tr></thead>
                  <tbody>
                    {(tree.data?.units || []).map(unit => (
                      <tr key={unit.id}>
                        <td><span className="mono small">{unit.path}</span><br />{unit.name}</td>
                        <td style={{ minWidth: 220 }}>
                          <TextInput value={unitMembers[unit.id] ?? unit.members.join(", ")}
                                     onChange={e => setUnitMembers({ ...unitMembers, [unit.id]: e.target.value })}
                                     placeholder="成员，逗号分隔" disabled={!canManage} />
                        </td>
                        <td><div className="pill-row">
                          <Btn variant="subtle" size="sm" disabled={!canManage}
                               onClick={() => saveUnitMembers.run(unit)} busy={saveUnitMembers.running}>保存成员</Btn>
                          <Btn variant="danger" size="sm" disabled={!canManage}
                               onClick={() => removeUnit(unit)}><Trash size={13} /></Btn>
                        </div></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
        </Card>

        <Card title="关系元组" sub="资源级授权：主体通过关系指向具体资源，适合单对象共享。">
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
            <Field label="资源类型"><TextInput value={rel.resource_type} onChange={e => setRel({ ...rel, resource_type: e.target.value })} /></Field>
            <Field label="资源 ID"><TextInput value={rel.resource_id} onChange={e => setRel({ ...rel, resource_id: e.target.value })} /></Field>
            <Field label="关系"><TextInput value={rel.relation} onChange={e => setRel({ ...rel, relation: e.target.value })} /></Field>
            <Field label="主体"><TextInput value={rel.subject_id} onChange={e => setRel({ ...rel, subject_id: e.target.value })} placeholder="user:alice 或 alice" /></Field>
            <Btn onClick={() => createRelation.run()} busy={createRelation.running}
                 disabled={!canManage}>{<Plus size={14} />} 添加关系</Btn>
          </div>
          {(relations.data || []).length === 0
            ? <Empty title="还没有关系元组" hint="关系元组不会替代角色绑定，只收窄到单个资源。" />
            : (
              <div className="tablewrap auto">
                <table className="table">
                  <thead><tr><th>资源</th><th>关系</th><th>主体</th><th /></tr></thead>
                  <tbody>
                    {(relations.data || []).map((row, index) => (
                      <tr key={`${row.resource_type}/${row.resource_id}/${row.relation}/${row.subject_id}/${index}`}>
                        <td className="mono small">{row.resource_type}/{row.resource_id}</td>
                        <td className="mono">{row.relation}</td>
                        <td className="small">{row.subject_type}:{row.subject_id}</td>
                        <td><Btn variant="danger" size="sm" disabled={!canManage}
                                 onClick={() => removeRelation(row)}><Trash size={13} /></Btn></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
        </Card>
      </div>
    </div>
  );
}

function GroupRow({ group, disabled, busy, onSave, onDelete }: {
  group: SecurityGroup; disabled: boolean; busy: boolean;
  onSave: (text: string) => void; onDelete: () => void;
}) {
  const [text, setText] = useState(group.members.join(", "));
  return (
    <div className="kv-pair" style={{ marginTop: 10, display: "flex", gap: 8, alignItems: "center" }}>
      <b style={{ minWidth: 100 }}>{group.name}</b>
      <TextInput value={text} onChange={e => setText(e.target.value)}
                 placeholder="成员，逗号分隔" disabled={disabled} />
      <Btn variant="subtle" size="sm" disabled={disabled} busy={busy}
           onClick={() => onSave(text)}>保存</Btn>
      <Btn variant="danger" size="sm" disabled={disabled} onClick={onDelete}><Trash size={13} /></Btn>
    </div>
  );
}

export function SecurityIntegrations({ wid, canManage }: {
  wid: string; canManage: boolean;
}) {
  const { toast, confirm } = usePlatform();
  const users = useLoad<{ users: Awaited<ReturnType<typeof api.securityIdentityUsers>>["users"] }>(
    () => api.securityIdentityUsers(wid), [wid]);
  const hooks = useLoad(() => api.securityWebhooks(wid), [wid]);
  const tasks = useLoad(() => api.securityAsyncTasks(wid), [wid]);
  const [userName, setUserName] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [userGroups, setUserGroups] = useState("");
  const [url, setUrl] = useState("");
  const [eventTypes, setEventTypes] = useState("todo.opened");
  const [secret, setSecret] = useState("");
  const [fn, setFn] = useState("");
  const [target, setTarget] = useState("");
  const [delay, setDelay] = useState("0");
  const [objectType, setObjectType] = useState("Vehicle");

  const upsertUser = useAction(async () => {
    if (!userName.trim()) { toast("请填写 SCIM 用户名", "err"); return; }
    await api.securityUpsertIdentityUser(wid, {
      user_name: userName.trim(), display_name: displayName.trim() || undefined,
      groups: userGroups.split(",").map(v => v.trim()).filter(Boolean),
      provider: "scim", active: true,
    });
    setUserName(""); setDisplayName(""); setUserGroups(""); users.reload();
    toast("身份目录已更新", "ok");
  }, [wid, userName, displayName, userGroups]);

  const removeUser = (name: string) => confirm({
    title: `停用目录用户 ${name}？`,
    body: "停用后该身份不能再通过 OIDC/LDAP/SCIM 登录，历史审计保留。",
    confirmText: "停用", danger: true,
    run: async () => {
      await api.securityDeleteIdentityUser(wid, name);
      users.reload(); toast("目录用户已停用", "ok");
    },
  });

  const createHook = useAction(async () => {
    await api.securityCreateWebhook(wid, {
      url: url.trim(), secret: secret.trim(),
      event_types: eventTypes.split(",").map(v => v.trim()).filter(Boolean),
    });
    setUrl(""); setSecret(""); hooks.reload();
    toast("Webhook 已创建；载荷按字段脱敏后再签名投递", "ok");
  }, [wid, url, secret, eventTypes]);

  const toggleHook = useAction(async (id: string, active: boolean) => {
    await api.securityUpdateWebhook(wid, id, { active: !active });
    hooks.reload(); toast(active ? "已暂停投递" : "已恢复投递", "ok");
  }, [wid]);

  const removeHook = (id: string) => confirm({
    title: "删除这个 Webhook？",
    body: "删除后不再投递任何事件；已投递记录保留在审计中。",
    confirmText: "删除", danger: true,
    run: async () => {
      await api.securityDeleteWebhook(wid, id);
      hooks.reload(); toast("Webhook 已删除", "ok");
    },
  });

  const enqueue = useAction(async () => {
    if (!fn.trim()) { toast("请填写能力名，例如 Vehicle.rebalance", "err"); return; }
    await api.securityEnqueueAsync(wid, {
      function: fn.trim(), target: target.trim() || undefined,
      delay_seconds: Number(delay) || 0,
    });
    setFn(""); setTarget(""); tasks.reload();
    toast("已排期；任务携带签名的授权上下文，执行前会重新校验策略", "ok");
  }, [wid, fn, target, delay]);

  const download = useAction(async (kind: "decisions" | "objects", fmt: string) => {
    await api.downloadSecurityExport(wid, kind, fmt, objectType);
    toast("导出已开始；字段按当前身份脱敏", "ok");
  }, [wid, objectType]);

  return (
    <div className="bento">
      <div className="col-6">
        <Card title="身份目录（SCIM）" sub="外部 IdP 的账号映射；SCIM 端点由独立 Bearer 令牌保护。"
              aside={<Tag kind="plain">{users.data?.users.length || 0} 个账号</Tag>}>
          <Field label="用户名"><TextInput value={userName} onChange={e => setUserName(e.target.value)} placeholder="alice@example.com" /></Field>
          <Field label="显示名"><TextInput value={displayName} onChange={e => setDisplayName(e.target.value)} /></Field>
          <Field label="组（逗号分隔）"><TextInput value={userGroups} onChange={e => setUserGroups(e.target.value)} placeholder="operators" /></Field>
          <Btn onClick={() => upsertUser.run()} busy={upsertUser.running}
               disabled={!canManage}>{<Plus size={14} />} 写入目录</Btn>
          <div className="tablewrap auto" style={{ marginTop: 10 }}>
            <table className="table">
              <thead><tr><th>账号</th><th>组</th><th>状态</th><th /></tr></thead>
              <tbody>
                {(users.data?.users || []).map(u => (
                  <tr key={u.user_name}>
                    <td><span className="mono">{u.user_name}</span><br />{u.display_name || "–"}</td>
                    <td className="small">{(u.groups || []).join(", ") || "–"}</td>
                    <td><Tag kind={u.active === false ? "err" : "ok"}>{u.active === false ? "停用" : "启用"}</Tag></td>
                    <td><Btn variant="danger" size="sm" disabled={!canManage}
                             onClick={() => removeUser(u.id || u.user_name)}><Trash size={13} /></Btn></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        <Card title="异步任务" sub="排期动作不是无主线程：任务保存签名上下文，执行前重算 PDP，失效即拒绝。">
          <Field label="能力名"><TextInput value={fn} onChange={e => setFn(e.target.value)} placeholder="Vehicle.rebalance" /></Field>
          <Field label="目标对象"><TextInput value={target} onChange={e => setTarget(e.target.value)} placeholder="Vehicle/V001" /></Field>
          <Field label="延迟（秒）"><TextInput value={delay} onChange={e => setDelay(e.target.value)} /></Field>
          <Btn onClick={() => enqueue.run()} busy={enqueue.running}
               disabled={!canManage}>排期执行</Btn>
          <div className="tablewrap auto" style={{ marginTop: 10 }}>
            <table className="table">
              <thead><tr><th>任务</th><th>下次运行</th><th>结果</th></tr></thead>
              <tbody>
                {(tasks.data?.tasks || []).map(t => (
                  <tr key={t.id}>
                    <td className="mono small">{t.id}</td>
                    <td className="mono small">{t.next_run_at || "–"}</td>
                    <td className="small">{t.failures ? <Tag kind="err">{t.failures} 次失败</Tag> : <Tag kind="ok">{t.runs} 次成功</Tag>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>

      <div className="col-6">
        <Card title="Webhook 投递" sub="出站事件先按字段义务脱敏，再用工作空间密钥签名；失败进入重试。">
          <Field label="目标 URL"><TextInput value={url} onChange={e => setUrl(e.target.value)} placeholder="https://example.com/hook" /></Field>
          <Field label="事件类型（逗号分隔）"><TextInput value={eventTypes} onChange={e => setEventTypes(e.target.value)} placeholder="todo.opened" /></Field>
          <Field label="签名密钥" hint="只写；列表与详情返回 ***。"><TextInput value={secret} onChange={e => setSecret(e.target.value)} type="password" /></Field>
          <Btn onClick={() => createHook.run()} busy={createHook.running}
               disabled={!canManage}>{<Plus size={14} />} 新建 Webhook</Btn>
          {(hooks.data || []).length === 0
            ? <Empty title="还没有 Webhook" hint="订阅后事件会以脱敏载荷投递。" />
            : (
              <div className="tablewrap auto" style={{ marginTop: 10 }}>
                <table className="table">
                  <thead><tr><th>URL</th><th>事件</th><th>状态</th><th /></tr></thead>
                  <tbody>
                    {(hooks.data || []).map(h => (
                      <tr key={h.id}>
                        <td className="mono small">{h.url}</td>
                        <td className="small">{h.event_types.join(", ")}</td>
                        <td><Tag kind={h.active ? "ok" : "plain"}>{h.active ? "启用" : "暂停"}</Tag></td>
                        <td><div className="pill-row">
                          <Btn variant="subtle" size="sm" disabled={!canManage}
                               onClick={() => toggleHook.run(h.id, h.active)}>{h.active ? "暂停" : "恢复"}</Btn>
                          <Btn variant="danger" size="sm" disabled={!canManage}
                               onClick={() => removeHook(h.id)}><Trash size={13} /></Btn>
                        </div></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
        </Card>

        <Card title="导出与证据" sub="导出经过同一个 PEP：调用者无权读取的对象、被掩码的字段都不会出现在文件里。">
          <Field label="对象类型（对象导出）"><TextInput value={objectType} onChange={e => setObjectType(e.target.value)} /></Field>
          <div className="pill-row" style={{ marginTop: 10 }}>
            <Btn variant="subtle" onClick={() => download.run("decisions", "csv")}
                 busy={download.running}>决策日志 CSV</Btn>
            <Btn variant="subtle" onClick={() => download.run("decisions", "jsonl")}
                 busy={download.running}>决策日志 JSONL</Btn>
            <Btn variant="subtle" onClick={() => download.run("objects", "csv")}
                 busy={download.running}>对象 CSV（脱敏）</Btn>
          </div>
        </Card>
      </div>
    </div>
  );
}
