/** 本体编辑抽屉（第二步 A）。
 *
 *  后端 POST /ontology/objects 与 /ontology/relations 是「整对象覆盖」语义：
 *  没传的字段会被写成缺省值（identity 清空、category 变 object、state_machine 变空）。
 *  所以每个抽屉都把用户没有在编辑的字段原样回传，避免「只改一句说明，身份键就丢了」。
 *
 *  属性用 JSON 数组编辑，required/enum/minimum 等 facet 原样往返；删属性只是少提交一项，
 *  仍被 identity、术语、投影映射或应用过滤引用的删除由服务端拒绝，原因直接显示在抽屉里，
 *  不做前端猜测，也不静默吞掉。
 */
import { useState } from "react";
import {
  api, type ObjectTypeSpec, type Ontology, type PropertySpec,
  type RelationSpec, type VocabTerm,
} from "../api";
import { useAction, usePlatform } from "../store";
import { Btn, Drawer, ErrorNote, Field, JsonField, Select, TextInput } from "../ui";

const CATEGORIES: [string, string][] = [
  ["object", "普通对象"],
  ["event", "事件（已经发生过的事）"],
  ["value", "值对象（不可变）"],
];
const CARDINALITIES: [string, string][] = [
  ["one-to-one", "一对一"],
  ["one-to-many", "一对多"],
  ["many-to-one", "多对一"],
  ["many-to-many", "多对多"],
];
const TERM_KINDS: [string, string][] = [
  ["object", "对象"],
  ["property", "属性"],
  ["relation", "关系"],
];
const PROFILES: [string, string][] = [
  ["closed", "封闭世界：没写过的当不存在（缺省）"],
  ["open", "开放世界：没写过的当未知"],
];

/** 下拉里出现「旧值不在候选里」时把它拼到最前面，避免 Select 显示空白、保存时被悄悄改掉。 */
function optionsWith(options: [string, string][], current: string, emptyLabel?: string): [string, string][] {
  const base = emptyLabel ? [[ "", emptyLabel ] as [string, string], ...options] : options;
  if (!current || base.some(([v]) => v === current)) return base;
  return [[current, `${current}（当前值，未在候选里）`], ...base];
}

const splitList = (text: string) =>
  text.split(/[，,、\s]+/).map(s => s.trim()).filter(Boolean);

/** 保存成功才由调用方 toast；失败时既 toast（useAction）又在抽屉里留一条持久原因。 */
function useSave(task: () => Promise<string>, deps: unknown[]) {
  const { toast } = usePlatform();
  const [error, setError] = useState<string | null>(null);
  const { run, running } = useAction(async () => {
    setError(null);
    try {
      const message = await task();
      toast(message, "ok");
    } catch (e) {
      setError((e as Error).message);
      throw e;            // useAction 负责 toast；重新抛出后抽屉保持打开，改完可以再保存
    }
  }, deps);
  return { run, running, error };
}

function parseProperties(text: string): PropertySpec[] {
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch (e) {
    throw new Error(`属性 JSON 语法错误：${(e as Error).message}`);
  }
  if (!Array.isArray(data)) {
    throw new Error('属性必须是数组，例如 [{"name":"plate","type":"string"}]');
  }
  const seen = new Set<string>();
  return data.map((raw, i) => {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
      throw new Error(`第 ${i + 1} 个属性不是对象`);
    }
    const item = raw as Record<string, unknown>;
    const name = typeof item.name === "string" ? item.name.trim() : "";
    if (!name) throw new Error(`第 ${i + 1} 个属性缺少 name`);
    if (typeof item.type !== "string" || !item.type.trim()) {
      throw new Error(`属性「${name}」缺少 type`);
    }
    if (seen.has(name)) throw new Error(`属性「${name}」重复了`);
    seen.add(name);
    return { ...item, name, type: item.type.trim() } as PropertySpec;
  });
}

/* ------------------------------------------------------------------ 元信息 */

export function MetaEditDrawer({ wid, ont, onClose, onSaved }: {
  wid: string; ont: Ontology; onClose: () => void; onSaved: () => void;
}) {
  const [name, setName] = useState(ont.name);
  const [desc, setDesc] = useState(ont.description || "");
  const [profile, setProfile] = useState(ont.profile || "closed");
  const { run, running, error } = useSave(async () => {
    const trimmed = name.trim();
    if (!trimmed) throw new Error("本体名称不能为空");
    if (trimmed === ont.name && desc.trim() === (ont.description || "")
        && profile === (ont.profile || "closed")) {
      return "名称、说明与口径都没有改动，未提交";
    }
    await api.updateOntologyMeta(wid, { name: trimmed, description: desc.trim(), profile });
    onSaved();
    return `本体信息已更新：${trimmed}`;
  }, [wid, ont, name, desc, profile]);

  return (
    <Drawer title="编辑本体信息" sub={`当前 v${ont.version} · ${ont.lifecycle === "draft" ? "草稿" : ont.lifecycle}`}
            onClose={onClose}
            footer={<div className="btn-row flat">
              <Btn busy={running} onClick={() => void run()}>保存信息</Btn>
              <Btn variant="subtle" disabled={running} onClick={onClose}>取消</Btn>
            </div>}>
      {error && <ErrorNote text={error} />}
      <Field label="本体名称" hint="内部标识，建议英文小写；改名不会影响对象与关系。">
        <TextInput value={name} placeholder="traffic" onChange={e => setName(e.target.value)} disabled={running} />
      </Field>
      <Field label="一句话说明" hint="写给业务同事看：这个业务世界覆盖什么。">
        <TextInput value={desc} placeholder="例如：高速收费与放行的业务世界"
                   onChange={e => setDesc(e.target.value)} disabled={running} />
      </Field>
      <Field label="推理口径" hint="影响问答与校验里「没说过的属性」怎么解释；发布后才对运行时生效。">
        <Select value={profile} onChange={e => setProfile(e.target.value)} disabled={running}>
          {optionsWith(PROFILES, profile).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </Select>
      </Field>
      <p className="muted small">只改元信息，不动对象、关系与术语；已发布的本体不能直接改，先「修订」。</p>
    </Drawer>
  );
}

/* ------------------------------------------------------------------ 对象 */

export function ObjectEditDrawer({ wid, name, spec, onClose, onSaved }: {
  wid: string; name: string; spec: ObjectTypeSpec; onClose: () => void; onSaved: () => void;
}) {
  const [desc, setDesc] = useState(spec.description || "");
  const [identity, setIdentity] = useState((spec.identity || []).join("，"));
  const [category, setCategory] = useState(spec.category || "object");
  const [propsText, setPropsText] = useState(() =>
    JSON.stringify(Object.values(spec.properties || {}), null, 2));

  // 实时提示"这次会删掉哪些属性"，让用户在点保存前就知道代价。
  const removed = (() => {
    let next: PropertySpec[];
    try { next = parseProperties(propsText); } catch { return []; }
    const kept = new Set(next.map(p => p.name));
    return Object.keys(spec.properties || {}).filter(n => !kept.has(n));
  })();

  const { run, running, error } = useSave(async () => {
    const properties = parseProperties(propsText);
    await api.addObject(wid, {
      name,
      description: desc.trim(),
      properties,
      identity: splitList(identity),
      category,
      // 状态机不在这个抽屉里编辑，但整对象覆盖必须原样回传，否则会被后端清空。
      state_machine: spec.state_machine ?? null,
    });
    onSaved();
    return `对象「${name}」已更新（属性 ${properties.length} 个）`;
  }, [wid, name, desc, identity, category, propsText, spec]);

  return (
    <Drawer title={`编辑对象 · ${name}`} sub="对象名不可改；删属性只是少提交一项，被引用时服务端会拒绝。"
            onClose={onClose} wide
            footer={<div className="btn-row flat">
              <Btn busy={running} onClick={() => void run()}>保存更正</Btn>
              <Btn variant="subtle" disabled={running} onClick={onClose}>取消</Btn>
            </div>}>
      {error && <ErrorNote text={error} />}
      <Field label="对象名（不可改）">
        <TextInput value={name} disabled />
      </Field>
      <Field label="业务含义" hint="一句话说明这个对象在业务里指什么。">
        <TextInput value={desc} placeholder="车辆" onChange={e => setDesc(e.target.value)} disabled={running} />
      </Field>
      <Field label="身份键" hint="逗号分隔的属性名，用来判断两条记录是不是同一实体，例如 plate。留空表示由后端按缺省 id 派生。">
        <TextInput value={identity} placeholder="plate" onChange={e => setIdentity(e.target.value)} disabled={running} />
      </Field>
      <Field label="元语义" hint="普通对象 / 事件 / 值对象；改动影响之后的投影同步与问答口径。">
        <Select value={category} onChange={e => setCategory(e.target.value)} disabled={running}>
          {optionsWith(CATEGORIES, category).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </Select>
      </Field>
      <JsonField label="属性数组（完整提交）" value={propsText} onChange={setPropsText} rows={15} required
                 hint='每一项至少要有 name 与 type；required/enum/minimum/unique/unit/temporal 等 facet 原样保留。删属性=从数组里去掉一项。' />
      {removed.length > 0 && (
        <p className="muted small">
          这次会删除属性：{removed.join("、")}。仍被身份键、术语、投影映射或应用过滤引用的属性，
          服务端会拒绝并在这里说明原因；运行时里已有的数据不会被改写。
        </p>
      )}
      {spec.state_machine && (
        <p className="muted small">这个对象声明了状态机；本抽屉不改它，保存时原样提交。</p>
      )}
    </Drawer>
  );
}

/* ------------------------------------------------------------------ 关系 */

export function RelationEditDrawer({ wid, name, spec, onClose, onSaved }: {
  wid: string; name: string; spec: RelationSpec; onClose: () => void; onSaved: () => void;
}) {
  const [desc, setDesc] = useState(spec.description || "");
  const [cardinality, setCardinality] = useState(spec.cardinality || "");
  const [inverse, setInverse] = useState(spec.inverse || "");
  const { run, running, error } = useSave(async () => {
    await api.addRelation(wid, {
      name,
      source: spec.source,
      target: spec.target,
      description: desc.trim(),
      cardinality,
      inverse: inverse.trim(),
    });
    onSaved();
    return `关系「${name}」已更新`;
  }, [wid, name, spec, desc, cardinality, inverse]);

  return (
    <Drawer title={`编辑关系 · ${name}`} sub="源对象与目标对象不可改；改指向等于换一条关系。"
            onClose={onClose}
            footer={<div className="btn-row flat">
              <Btn busy={running} onClick={() => void run()}>保存更正</Btn>
              <Btn variant="subtle" disabled={running} onClick={onClose}>取消</Btn>
            </div>}>
      {error && <ErrorNote text={error} />}
      <Field label="源 → 目标（不可改）">
        <TextInput value={`${spec.source} → ${spec.target}`} disabled />
      </Field>
      <Field label="业务含义" hint="用业务语言描述这条关系，例如：车辆通过收费站。">
        <TextInput value={desc} placeholder="车辆通过收费站"
                   onChange={e => setDesc(e.target.value)} disabled={running} />
      </Field>
      <Field label="基数" hint="决定一个对象能连几个对端；留空表示未声明。">
        <Select value={cardinality} onChange={e => setCardinality(e.target.value)} disabled={running}>
          {optionsWith(CARDINALITIES, cardinality, "未声明").map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </Select>
      </Field>
      <Field label="反向关系名" hint="可选。声明后可以从目标端反向查询，例如 passageAt。">
        <TextInput value={inverse} placeholder="passageAt"
                   onChange={e => setInverse(e.target.value)} disabled={running} />
      </Field>
    </Drawer>
  );
}

/* ------------------------------------------------------------------ 术语 */

export function TermEditDrawer({ wid, term, spec, onClose, onSaved }: {
  wid: string; term: string; spec: VocabTerm; onClose: () => void; onSaved: () => void;
}) {
  const [concept, setConcept] = useState(spec.concept);
  const [kind, setKind] = useState(spec.kind || "object");
  const [aliases, setAliases] = useState((spec.aliases || []).join("，"));
  const { run, running, error } = useSave(async () => {
    const target = concept.trim();
    if (!target) throw new Error("术语指向不能为空，例如 TollStation 或 TollStation.status");
    await api.addTerm(wid, {
      term,
      concept: target,
      kind,
      aliases: splitList(aliases),
    });
    onSaved();
    return `术语「${term}」已更新`;
  }, [wid, term, spec, concept, kind, aliases]);

  return (
    <Drawer title={`编辑术语 · ${term}`} sub="说法本身不可改；改说法等于新增一条术语。"
            onClose={onClose}
            footer={<div className="btn-row flat">
              <Btn busy={running} onClick={() => void run()}>保存更正</Btn>
              <Btn variant="subtle" disabled={running} onClick={onClose}>取消</Btn>
            </div>}>
      {error && <ErrorNote text={error} />}
      <Field label="说法（不可改）">
        <TextInput value={term} disabled />
      </Field>
      <Field label="指向" hint="对象名，或「对象.属性」形式的属性，例如 TollStation.status。">
        <TextInput value={concept} placeholder="TollStation.status"
                   onChange={e => setConcept(e.target.value)} disabled={running} />
      </Field>
      <Field label="种类" hint="决定这条术语可以被用在哪些地方。">
        <Select value={kind} onChange={e => setKind(e.target.value)} disabled={running}>
          {optionsWith(TERM_KINDS, kind).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </Select>
      </Field>
      <Field label="同义词" hint="逗号分隔；同义词越多，AI 越听得懂业务口语。">
        <TextInput value={aliases} placeholder="收费场站，站点"
                   onChange={e => setAliases(e.target.value)} disabled={running} />
      </Field>
    </Drawer>
  );
}
