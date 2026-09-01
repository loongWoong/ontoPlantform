#!/usr/bin/env node
/**
 * React 控制台布局/交互探针（取代 layout_probe.mjs 对旧单页控制台的探测）。
 *
 * 与旧探针的区别：不再依赖旧控制台的全局函数与 DOM id，全部走真实用户路径 ——
 * 点侧栏、填表单、点按钮，然后用 CDP 量真实像素。验收编号沿用
 * docs/frontend-layout-plan.md：L0 外壳 / L1 横向溢出 / L2 控件尺寸 /
 * L3 表单可访问性 / L4 状态被清 / L5 重复提交 / C5 提示条。
 *
 * 用法：
 *   1) python -m onto_platform.interfaces.api --port 8123
 *   2) msedge --headless=new --remote-debugging-port=9333 \
 *          --user-data-dir=%TEMP%/onto-edge --no-first-run about:blank
 *   3) node tests/manual/react_layout_probe.mjs [base] [cdpPort]
 *
 * 零依赖（Node >= 22 内置 WebSocket）。退出码 0 = 全部门槛通过。
 */
const BASE = process.argv[2] || "http://127.0.0.1:8123";
const PORT = Number(process.argv[3] || 9333);
const SIZES = [[1440, 900], [1280, 800], [1024, 768]];
const sleep = ms => new Promise(r => setTimeout(r, ms));

const ver = await (await fetch(`http://127.0.0.1:${PORT}/json/version`)).json();
const ws = new WebSocket(ver.webSocketDebuggerUrl);
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
let mid = 0;
const pend = new Map();
ws.onmessage = e => { const m = JSON.parse(e.data); if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id); } };
const cmd = (method, params = {}, sid) => new Promise(res => {
  const id = ++mid; pend.set(id, res);
  ws.send(JSON.stringify({ id, method, params, ...(sid ? { sessionId: sid } : {}) }));
});

const target = (await cmd("Target.createTarget", { url: "about:blank" })).result.targetId;
const sid = (await cmd("Target.attachToTarget", { targetId: target, flatten: true })).result.sessionId;
await cmd("Page.enable", {}, sid);
const ev = async expr => {
  const r = await cmd("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true }, sid);
  if (r.result?.exceptionDetails) return { __err: r.result.exceptionDetails.exception?.description };
  return r.result?.result?.value;
};
const size = (w, h) => cmd("Emulation.setDeviceMetricsOverride",
  { width: w, height: h, deviceScaleFactor: 1, mobile: false }, sid);

/** 通用度量：与旧探针同一套门槛，选择器换成 React 控制台的真实结构 */
const PROBE = `(() => {
  const vis = el => el.offsetParent !== null || getComputedStyle(el).position === 'fixed';
  const R = el => el.getBoundingClientRect();
  const W = el => Math.round(R(el).width), H = el => Math.round(R(el).height);
  const de = document.documentElement;
  const o = { vp: [innerWidth, innerHeight] };

  o.pageHScroll = de.scrollWidth - de.clientWidth;
  o.docHeight = de.scrollHeight;
  // 侧栏是通高的，不算「固定头部占高」；只统计顶部条
  o.stickyChromeH = [...document.querySelectorAll('header.top')].filter(vis).reduce((s, el) => s + H(el), 0);
  // 侧栏必须是真正的「栏」：窄、粘住、内容在它右边开始（不是整幅横条）
  const rail = document.querySelector('aside.side');
  o.sideW = rail ? W(rail) : null;
  o.sidePos = rail ? getComputedStyle(rail).position : null;
  const mainEl = document.querySelector('.app > .main');
  o.mainLeft = mainEl ? Math.round(R(mainEl).left) : null;
  o.sideIsRail = !!rail && W(rail) <= 260 && ['sticky', 'fixed'].includes(getComputedStyle(rail).position)
    && o.mainLeft !== null && o.mainLeft >= W(rail) - 1;

  o.overflow = [];
  document.querySelectorAll('.content *, .drawer *, .modal *').forEach(el => {
    if (!vis(el) || el.clientWidth <= 0) return;
    // input/textarea 的 scrollWidth 由内容决定（长值/长 placeholder），不是布局破损
    if (['INPUT','TEXTAREA','SELECT'].includes(el.tagName)) return;
    if (el.scrollWidth > el.clientWidth + 4 && !['auto','scroll'].includes(getComputedStyle(el).overflowX))
      o.overflow.push({ sel: el.tagName.toLowerCase() + (el.className ? '.' + String(el.className).split(' ')[0] : ''),
                        over: el.scrollWidth - el.clientWidth });
  });
  o.overflow = o.overflow.sort((a, b) => b.over - a.over).slice(0, 8);

  // 表单行：同一行里的控件必须落在同一条基线上（换行 == 宽度不够）
  o.wrappedRows = [];
  document.querySelectorAll('.inline-create, .field-inline, .searchbar, .rows-edit .r, .btn-row').forEach(r => {
    const kids = [...r.children].filter(el => vis(el) && R(el).width > 0 && el.tagName !== 'SPAN');
    if (kids.length < 2) return;
    if (getComputedStyle(r).flexWrap === 'wrap' && r.classList.contains('wrap')) return; // 有意换行的容器
    if (getComputedStyle(r).flexDirection === 'column') return;                            // 窄栏里刻意竖排
    // 只有「本该并排却被拆到两行」才算缺陷。刻意竖排（flex column / 单列栅格 /
    // 孩子横向互不重叠）不算缺陷，窄屏响应式折成单列正是设计意图。
    const cs = getComputedStyle(r);
    if (cs.display.includes('grid') && cs.gridTemplateColumns.trim().split(/\s+/).length <= 1) return;
    const xs = kids.map(k => [R(k).left, R(k).right]);
    const overlaps = xs.some((a, i) => xs.some((b, j) => j > i && a[0] < b[1] - 2 && b[0] < a[1] - 2));
    if (!overlaps) return;
    // 底对齐的行（align-items:end）看底边是否同一条基线，顶边不同是设计
    const align = getComputedStyle(r).alignItems;
    const edge = ['flex-end','end','baseline'].includes(align) ? (k => R(k).bottom) : (k => R(k).top);
    const vals = kids.map(edge).sort((a, b) => a - b);
    let lines = 1;
    for (let i = 1; i < vals.length; i++) if (vals[i] - vals[i - 1] > 3) lines++;   // 3px 内算同一条基线
    if (lines > 1) o.wrappedRows.push({
      lines, kids: kids.length, widths: kids.map(k => W(k)),
      where: (((r.closest('.card') || {}).querySelector) && (r.closest('.card').querySelector('h3,h4,h2') || {}).textContent || '?').trim().slice(0, 20),
    });
  });

  o.cramped = [...document.querySelectorAll('input,textarea,select')].filter(vis)
    .filter(el => !['checkbox','radio'].includes(el.type))   // 勾选框本来就只有 14px
    .map(el => ({ id: el.getAttribute('aria-label') || el.id || el.tagName.toLowerCase(), w: W(el),
                  wide: /json|实现内容|编排步骤|映射|脚本|steps/i.test((el.placeholder||'') + (el.getAttribute('aria-label')||'')) }))
    .filter(x => x.w < (x.wide ? 280 : 110));

  const fields = [...document.querySelectorAll('input,textarea,select')].filter(vis).filter(el => el.type !== 'checkbox');
  o.fields = fields.length;
  o.fieldsLabeled = fields.filter(el => el.closest('label') || (el.id && document.querySelector('label[for="'+el.id+'"]'))
    || el.getAttribute('aria-label')).length;
  o.unlabeled = fields.filter(el => !(el.closest('label') || (el.id && document.querySelector('label[for="'+el.id+'"]'))
    || el.getAttribute('aria-label')))
    .map(el => (el.placeholder || el.type || el.tagName) + '').slice(0, 5);
  o.clickableSpans = [...document.querySelectorAll('[onclick]')].filter(el => el.tagName !== 'BUTTON').length;

  o.tables = [...document.querySelectorAll('table')].filter(vis).map(tb => ({
    cols: tb.querySelectorAll('thead th').length,
    rows: tb.querySelectorAll('tbody tr').length,
    w: W(tb),
    stickyHead: getComputedStyle(tb.querySelector('thead th') || tb).position === 'sticky',
    scrollHost: (() => { let p = tb.parentElement; while (p && p !== document.body) {
      const x = getComputedStyle(p).overflowX; if (x === 'auto' || x === 'scroll') return true; p = p.parentElement; } return false; })(),
  }));

  o.cards = [...document.querySelectorAll('.card')].filter(vis).map(c => ({ w: W(c), h: H(c) }));
  o.inlineStyled = [...document.querySelectorAll('[style]')].filter(vis).length;
  // C5 量的是「一大坨文字提示」：带表单/按钮的空状态属于操作区，不算提示条
  o.fatBanners = [...document.querySelectorAll('.banner, .empty, .hint')].filter(vis)
    .filter(el => !el.querySelector('form, button'))
    .map(el => ({ cls: String(el.className).split(' ')[0], h: H(el), chars: (el.textContent||'').trim().replace(/\s+/g,'').length }))
    .filter(x => x.h > 120);
  return o;
})()`;

const report = { base: BASE, tabs: {} };
await size(1440, 900);
await cmd("Page.navigate", { url: `${BASE}/#/workbench` }, sid);
await sleep(2500);

/* ---------------- 夹具：一个内容齐全、可复现的工作空间 ----------------
   所有度量与交互回归都跑在它上面，避免被上一次探针残留的 localStorage /
   空工作空间影响；同时它保证「一条关系挂 3 条边」这类场景真的可测。 */
const FIXTURE = `探针-控制台验收`;
report.setup = await ev(`(async () => {
  const j = async (p, m, b) => {
    const r = await fetch('/api/workspaces' + p + (p.includes('?') ? '&' : '?') + 'user=admin',
      { method: m || 'GET', headers: { 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
    return { status: r.status, body: await r.json().catch(() => null) };
  };
  const list = (await j('', 'GET')).body || [];
  let ws = list.find(w => w.name === ${JSON.stringify(FIXTURE)});
  if (!ws) {
    ws = (await j('', 'POST', { name: ${JSON.stringify(FIXTURE)}, description: '探针夹具：本体 + 数据 + 能力 + 应用齐全' })).body;
    const w = '/' + ws.id;
    await j(w + '/ontology', 'POST', { name: 'probe', description: '' });
    for (const o of ['Probe', 'Node1', 'Node2', 'Node3'])
      await j(w + '/ontology/objects', 'POST', { name: o, description: '', properties: [{ name: 'tag', type: 'string' }] });
    for (const r of [{ name: 'r1', source: 'Probe', target: 'Node1' },
                     { name: 'r2', source: 'Probe', target: 'Node2' },
                     { name: 'r3', source: 'Node3', target: 'Probe' }])
      await j(w + '/ontology/relations', 'POST', { ...r, description: '' });
    await j(w + '/ontology/validate', 'POST', {});
    await j(w + '/ontology/publish', 'POST', {});
    const ds = (await j(w + '/datasources', 'POST', {
      name: '夹具数据', kind: 'static',
      assets: ['Probe', 'Node1', 'Node2', 'Node3'].map(t => ({ name: t.toLowerCase(), config: { rows: [{ pid: 'P001', tag: t }] } })),
    })).body;
    const pj = (await j(w + '/projections', 'POST', {
      name: '夹具投影', datasource_id: ds.id,
      object_mappings: ['Probe', 'Node1', 'Node2', 'Node3'].map(t => ({
        asset: t.toLowerCase(), object_type: t, id_field: 'pid',
        fields: [{ source_field: 'tag', property: 'tag' }] })),
    })).body;
    await j(w + '/projections/' + pj.id + '/run', 'POST', {});
    const cap = (await j(w + '/capabilities', 'POST', {
      key: 'touch', name: '打个标记', target_type: 'Probe', description: '把 tag 改成已检查',
      inputs: [{ name: 'note', type: 'string' }],
      implementation: { kind: 'python', source: 'def execute(ctx): o = ctx.object(); ctx.set(o.ref, "tag", "checked"); return {"tag": "checked"}' },
    })).body;
    await j(w + '/capabilities/' + cap.id + '/publish', 'POST', {});
    const app = (await j(w + '/applications', 'POST', { name: '夹具应用', description: '' })).body;
    await j(w + '/applications/' + app.id + '/queries', 'POST', { alias: 'all_probes', title: '全部探针', object_type: 'Probe', filters: {}, expand: [] });
    await j(w + '/applications/' + app.id + '/views', 'POST', { alias: 'probes', title: '探针列表', kind: 'table', object_type: 'Probe', properties: [], query_alias: '' });
    await j(w + '/applications/' + app.id + '/publish', 'POST', {});
  }
  const mine = ((await j('', 'GET')).body || []).find(x => x.name === ${JSON.stringify(FIXTURE)});
  const proj = ((await j('/' + mine.id + '/projections', 'GET')).body || [])[0];
  const run = proj ? (await j('/' + mine.id + '/projections/' + proj.id + '/run', 'POST', {})).body : null;
  const rt = await j('/' + mine.id + '/runtime', 'GET');
  return { workspace: mine.id, run, relations: ((rt.body || {}).relations || []).length };
})()`);
const FIXTURE_WID = (report.setup || {}).workspace || "";
if (!FIXTURE_WID) {
  console.error("夹具工作空间创建失败，度量不可信：", JSON.stringify(report.setup));
  process.exit(2);
}
await ev(`localStorage.setItem('onto.workspace', ${JSON.stringify(FIXTURE_WID)})`);
await cmd("Page.reload", {}, sid);
await sleep(2600);


const PAGES = await ev(`[...document.querySelectorAll('.nav-item')].map(b => (b.getAttribute('title')||'') || b.textContent.trim())`);
report.pagesFound = PAGES;
// 路由从侧栏按钮的顺序 + 已知 hash 表推导：直接读组件里的 data 属性更稳
const ROUTE_LIST = await ev(`(() => {
  const map = { '工作台':'workbench','本体':'ontology','数据':'data','能力':'capability',
                '应用':'application','对象实例':'state','AI 助手':'assistant','设置':'settings' };
  return [...document.querySelectorAll('.nav-item')].map(b => map[(b.getAttribute('title')||'').trim()] || null).filter(Boolean);
})()`);
if (!ROUTE_LIST?.length) { console.error("侧栏导航没找到，页面可能没渲染"); process.exit(2); }
report.routes = ROUTE_LIST;

// 先确保选中了有数据的工作空间
await ev(`(() => { const sel = document.querySelector('#ws-select'); return sel ? sel.value : null; })()`);

for (const [w, h] of SIZES) {
  await size(w, h);
  for (const r of ROUTE_LIST) {
    await cmd("Page.navigate", { url: `${BASE}/#${r}` }, sid);
    await sleep(1400);
    report.tabs[`${r}@${w}`] = await ev(PROBE);
  }
}

/* ---------------- 交互回归 ---------------- */
await size(1440, 900);
const clickText = (sel, text) => ev(`(() => {
  const el = [...document.querySelectorAll(${JSON.stringify(sel)})]
    .find(b => (b.textContent||'').includes(${JSON.stringify(text)}));
  if (!el) return false; el.click(); return true;
})()`);
const type = (aria, value) => ev(`(() => {
  const el = document.querySelector('[aria-label=${JSON.stringify(aria)}]');
  if (!el) return false;
  const proto = el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, ${JSON.stringify(value)});
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
  return true;
})()`);

// L5 重复提交：连点两次「创建并进入」只应产生 1 个工作空间
await cmd("Page.navigate", { url: `${BASE}/#/workbench` }, sid);
await sleep(1200);
report.workspaceBefore = await ev(`(document.querySelector('#ws-select') || {}).value || null`);
await cmd("Page.navigate", { url: `${BASE}/#/settings?tab=workspace` }, sid);
await sleep(1600);
await ev(`(async () => {
  const before = await (await fetch('/api/workspaces?user=admin')).json();
  window.__before = before.length;
})()`);
const typedName = await type("名称", `探针-双击-${Date.now()}`);
report.interaction = { typedName };
await ev(`(() => {
  const f = [...document.querySelectorAll('form.inline-create')].find(f => (f.textContent||'').includes('创建并进入'));
  const b = f && f.querySelector('button[type=submit]');
  if (b) { b.click(); b.click(); b.click(); return true } return false;
})()`);
await sleep(2500);
report.interaction.doubleSubmit = await ev(`(async () => {
  const after = await (await fetch('/api/workspaces?user=admin')).json();
  return { created: after.length - window.__before };
})()`);

// 回到刚才那个工作空间（「创建并进入」会把当前空间切到新建的空空间）
await cmd("Page.navigate", { url: `${BASE}/#/workbench` }, sid);
await sleep(1500);
await ev(`(() => {
  const sel = document.querySelector('#ws-select'); if (!sel) return false;
  const want = ${JSON.stringify(FIXTURE_WID)};
  if (!want) return false;
  Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(sel, want);
  sel.dispatchEvent(new Event('change', { bubbles: true })); return true;
})()`);
await sleep(1800);

// L4 状态被清：跑一次应用查询，然后新增定义，结果必须还在
await cmd("Page.navigate", { url: `${BASE}/#/application` }, sid);
await sleep(2000);
await clickText('.subtabs button', '查询');
await sleep(500);
const ran = await clickText('button', '运行');
await sleep(1800);
report.interaction.stateChurn = { ran, before: await ev(`document.querySelectorAll('.stage-result table tbody tr').length`) };
await type("视图标识", `probe_view_${Date.now()}`);
await type("显示名", "探针视图");
await ev(`(() => { const b = [...document.querySelectorAll('button')].find(x => (x.textContent||'').trim() === '添加视图'); if (b) b.click(); return !!b })()`);
await sleep(2200);
report.interaction.stateChurn.after = await ev(`document.querySelectorAll('.stage-result table tbody tr').length`);
report.interaction.stateChurn.resultRowsBefore = report.interaction.stateChurn.before;

// L1 宽表压力：把真实表格扩到 14 列，验证滚动容器与吸顶表头（走的是同一套 CSS）
await cmd("Page.navigate", { url: `${BASE}/#/state` }, sid);
await sleep(2200);
report.stress = await ev(`(() => {
  const wrap = document.querySelector('.content .tablewrap') || document.querySelector('.content table')?.parentElement;
  const tb = wrap && wrap.querySelector('table'); if (!tb) return { skip: 'no table on 对象实例' };
  const cols = 14;
  const head = tb.querySelector('thead tr'); const body = tb.querySelector('tbody');
  const keep = tb.outerHTML;
  for (let k = 0; k < cols; k++) { const th = document.createElement('th'); th.textContent = 'property_number_' + (k+1); head.appendChild(th); }
  [...body.querySelectorAll('tr')].forEach(tr => {
    for (let k = 0; k < cols; k++) { const td = document.createElement('td'); td.textContent = 'sample-value-' + k + '-with-long-text-content'; tr.appendChild(td); }
  });
  const r = {
    tableW: Math.round(tb.getBoundingClientRect().width),
    pageHScroll: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    stickyHead: getComputedStyle(tb.querySelector('thead th')).position === 'sticky',
    scrollHost: (() => { let p = tb.parentElement; while (p && p !== document.body) {
      const x = getComputedStyle(p).overflowX; if (x === 'auto' || x === 'scroll') return true; p = p.parentElement; } return false; })(),
  };
  tb.outerHTML = keep;
  return r;
})()`);

// L1 关系导航：抽屉里 N 条关系应当并发取，不该串行排队
await cmd("Page.navigate", { url: `${BASE}/#/state?type=Probe` }, sid);
await sleep(2200);
report.detail = await ev(`(async () => {
  const btn = [...document.querySelectorAll('.content table tbody button.link')]
    .find(b => (b.textContent||'').includes('/'));
  if (!btn) return { skip: '对象实例表里没有可点的对象' };
  btn.click();
  await new Promise(r => setTimeout(r, 2200));
  const rel = [...document.querySelectorAll('.drawer .subtabs button')].find(b => (b.textContent||'').includes('关系'));
  if (rel) rel.click();
  await new Promise(r => setTimeout(r, 600));
  const ents = performance.getEntriesByType('resource').filter(e => e.name.includes('/state/related'));
  const sum = ents.reduce((s, e) => s + (e.responseEnd - e.startTime), 0);
  const span = ents.length ? Math.max(...ents.map(e => e.responseEnd)) - Math.min(...ents.map(e => e.startTime)) : 0;
  return { fetches: ents.length, span: Math.round(span), sum: Math.round(sum),
           drawer: (document.querySelector('.drawer h2') || {}).textContent || null,
           concurrent: ents.length >= 2 && span < sum * 0.8 };
})()`);

/* ---------------- 路由与状态可复现性（深链可分享） ---------------- */
report.routing = {};
await cmd("Page.navigate", { url: `${BASE}/#/state?type=Probe` }, sid);
await sleep(2200);
report.routing.deepLinkNoWid = await ev(`JSON.stringify({
  hash: location.hash,
  rows: document.querySelectorAll('.content table tbody tr').length,
})`);
await ev(`(() => { const b = [...document.querySelectorAll('.nav-item')].find(x => (x.getAttribute('title')||'') === '应用'); if (b) b.click(); return !!b })()`);
await sleep(1800);
report.routing.navKeepsWorkspace = await ev(`location.hash`);
await ev(`(() => { const b = [...document.querySelectorAll('.list-pick button')][0]; if (b) b.click(); return !!b })()`);
await sleep(1500);
report.routing.appInUrl = await ev(`location.hash`);
await cmd("Page.reload", {}, sid);
await sleep(2600);
report.routing.afterReload = await ev(`JSON.stringify({
  hash: location.hash,
  selected: ((document.querySelector('.list-pick button[aria-pressed="true"]') || {}).textContent || '').trim(),
})`);
report.routing.navKeyHoldsPage = await ev(`localStorage.getItem('onto.nav')`);

/* ---------------- 门槛 ---------------- */
const fails = [];
const ok = (cond, code, msg) => { if (!cond) fails.push(`[${code}] ${msg}`); };
for (const [key, t] of Object.entries(report.tabs)) {
  if (!t || t.__err) { fails.push(`[ERR] ${key} 度量失败 ${JSON.stringify(t || {})}`); continue; }
  ok(t.pageHScroll <= 1, "L1", `${key}: 整页横向滚动 ${t.pageHScroll}px`);
  ok(t.overflow.length === 0, "L1", `${key}: ${t.overflow.length} 个元素横向溢出 ${JSON.stringify(t.overflow[0] || {})}`);
  ok(t.tables.every(tb => tb.scrollHost), "L1", `${key}: ${t.tables.filter(tb => !tb.scrollHost).length} 个表格没有横向滚动容器`);
  ok(t.wrappedRows.length === 0, "L2", `${key}: ${t.wrappedRows.length} 条表单行换行 ${JSON.stringify(t.wrappedRows[0] || {})}`);
  ok(t.cramped.length === 0, "L2", `${key}: ${t.cramped.length} 个过窄控件 ${JSON.stringify(t.cramped[0] || {})}`);
  ok(t.fields === t.fieldsLabeled, "L3", `${key}: ${t.fields - t.fieldsLabeled}/${t.fields} 字段无常驻 label ${JSON.stringify(t.unlabeled || [])}`);
  ok(t.clickableSpans === 0, "L3", `${key}: ${t.clickableSpans} 个 span[onclick]`);
  ok(t.stickyChromeH <= 60, "L0", `${key}: 顶部固定条占高 ${t.stickyChromeH}px`);
  if ((t.vp || [])[0] > 860) {
    ok(t.sideIsRail, "L0", `${key}: 侧栏不是常驻导轨（宽 ${t.sideW}px，position ${t.sidePos}，主区从 ${t.mainLeft}px 开始）`);
  }
  ok(t.fatBanners.length === 0, "C5", `${key}: ${t.fatBanners.length} 个 >120px 提示条 ${JSON.stringify(t.fatBanners[0] || {})}`);
  ok(t.inlineStyled === 0, "L0", `${key}: ${t.inlineStyled} 处内联 style`);
}
const it = report.interaction;
if (it.doubleSubmit) ok(it.typedName && it.doubleSubmit.created === 1, "L5",
  `连点三次提交：填表=${it.typedName}，实际创建 ${it.doubleSubmit.created} 个（应为 1）`);
if (it.stateChurn && it.stateChurn.ran) ok(it.stateChurn.before > 0 && it.stateChurn.after >= it.stateChurn.before, "L4",
  `新增定义后结果行数 ${it.stateChurn.before} → ${it.stateChurn.after}`);
if (report.stress && !report.stress.skip) {
  ok(report.stress.pageHScroll <= 1, "L1", `14 列宽表把页面推出 ${report.stress.pageHScroll}px（表 ${report.stress.tableW}px）`);
  ok(report.stress.scrollHost, "L1", "宽表没有 overflow-x 容器");
  ok(report.stress.stickyHead, "L1", "宽表表头未吸顶");
}
const rt = typeof report.routing.appInUrl === "string" ? report.routing.appInUrl : "";
ok(/#\/w\//.test(rt), "L3", `点导航后 URL 丢了工作空间：${rt}（深链必须可分享）`);
ok(/[?&]app=/.test(rt), "L3", `选中的应用没进 URL：${rt}`);
const rr = (() => { try { return JSON.parse(report.routing.afterReload); } catch { return {}; } })();
ok(!!rr.selected && /[?&]app=/.test(rr.hash || ""), "L3",
  `刷新后选中的定义丢了：${JSON.stringify(report.routing.afterReload)}`);
ok(!["open", "collapsed", "1", "0"].includes(report.routing.navKeyHoldsPage), "L3",
  `onto.nav 键被折叠状态占用，页签记忆失效：${report.routing.navKeyHoldsPage}`);
if (report.detail && !report.detail.skip) ok(report.detail.concurrent, "L1",
  `关系导航没有并发：${report.detail.fetches} 次请求跨 ${report.detail.span}ms，串行累计 ${report.detail.sum}ms`);

report.fails = fails;
console.log(JSON.stringify(report, null, 2));
console.error(`\n=== ${fails.length} 项未达标 ===`);
fails.forEach(f => console.error(f));
process.exit(fails.length ? 1 : 0);
