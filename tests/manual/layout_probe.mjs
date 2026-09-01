#!/usr/bin/env node
/**
 * 前端布局探针：用真实浏览器（Edge/Chrome headless + CDP）量化 Web 控制台的
 * 布局与可用性指标。优化方案的每一条 P0/P1 都用这里的数字验收。
 *
 * 用法：
 *   1) 起服务：python -m onto_platform.interfaces.api --port 8123
 *   2) 起浏览器：
 *      msedge --headless=new --remote-debugging-port=9333 \
 *             --user-data-dir=%TEMP%/onto-edge --no-first-run about:blank
 *   3) node tests/manual/layout_probe.mjs [base] [cdpPort] > layout-report.json
 *
 * 零依赖（Node >= 22，内置 WebSocket）。退出码：0 = 全部门槛通过，1 = 有未达标项。
 */
const BASE = process.argv[2] || "http://127.0.0.1:8123";
const PORT = Number(process.argv[3] || 9333);
const SIZES = [[1440, 900], [1280, 800], [1024, 768]];

const ver = await (await fetch(`http://127.0.0.1:${PORT}/json/version`)).json();
const ws = new WebSocket(ver.webSocketDebuggerUrl);
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
let mid = 0;
const pend = new Map();
ws.onmessage = e => {
  const m = JSON.parse(e.data);
  if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id); }
};
const cmd = (method, params = {}, sid) => new Promise(res => {
  const id = ++mid; pend.set(id, res);
  ws.send(JSON.stringify({ id, method, params, ...(sid ? { sessionId: sid } : {}) }));
});
const sleep = ms => new Promise(r => setTimeout(r, ms));

const target = (await cmd("Target.createTarget", { url: "about:blank" })).result.targetId;
const sid = (await cmd("Target.attachToTarget", { targetId: target, flatten: true })).result.sessionId;
await cmd("Page.enable", {}, sid);
const ev = async expr => {
  const r = await cmd("Runtime.evaluate",
    { expression: expr, returnByValue: true, awaitPromise: true }, sid);
  if (r.result?.exceptionDetails) return { __err: r.result.exceptionDetails.exception?.description };
  return r.result?.result?.value;
};

/** 页面上每个页签都跑一遍的度量表达式 */
const PROBE = `(() => {
  const vis = el => el.offsetParent !== null || getComputedStyle(el).position === 'fixed';
  const R = el => el.getBoundingClientRect();
  const W = el => Math.round(R(el).width), H = el => Math.round(R(el).height);
  const de = document.documentElement;
  const o = { vp: [innerWidth, innerHeight] };

  // A. 页面级：整页横向滚动 / 固定头部占高 / 首屏内容起点
  o.pageHScroll = de.scrollWidth - de.clientWidth;
  o.docHeight = de.scrollHeight;
  o.stickyChromeH = [...document.querySelectorAll('header, nav, aside')]
    .filter(el => vis(el) && ['sticky','fixed'].includes(getComputedStyle(el).position))
    .reduce((s, el) => s + H(el), 0);
  const first = [...document.querySelectorAll('section.tab.active *')].find(el => vis(el) && R(el).height > 40);
  o.contentStartsAt = first ? Math.round(R(first).top) : null;

  // B. 横向溢出元素（表格没有滚动容器的直接后果）
  o.overflow = [];
  document.querySelectorAll('main *, section *').forEach(el => {
    if (!vis(el) || el.clientWidth <= 0) return;
    if (el.scrollWidth > el.clientWidth + 4 && getComputedStyle(el).overflowX !== 'auto'
        && getComputedStyle(el).overflowX !== 'scroll')
      o.overflow.push({ sel: el.tagName.toLowerCase() + (el.id ? '#' + el.id : '.' + String(el.className).split(' ')[0]),
                        over: el.scrollWidth - el.clientWidth });
  });
  o.overflow = o.overflow.sort((a, b) => b.over - a.over).slice(0, 8);

  // C. 换行的表单行（.row / .fields 里控件跨多行 == 宽度不够）
  o.wrappedRows = [];
  document.querySelectorAll('.row, .fields, .form-grid').forEach(r => {
    const kids = [...r.children].filter(el => vis(el) && R(el).width > 0);
    if (kids.length < 2) return;
    const lines = new Set(kids.map(k => Math.round(R(k).top))).size;
    if (lines > 1) o.wrappedRows.push({
      lines, kids: kids.length,
      widths: kids.map(k => W(k)),
      where: (((r.closest('.card') || {}).querySelector) &&
              (r.closest('.card').querySelector('h3,h4') || {}).textContent || r.parentElement.id || '?')
              .trim().slice(0, 20),
    });
  });

  // D. 过窄控件（<120px 的输入框基本不可用；JSON 输入 <280px 同理）
  o.cramped = [...document.querySelectorAll('input,textarea,select')].filter(vis)
    .map(el => ({ id: el.id || el.name || el.tagName.toLowerCase(), w: W(el),
                  json: /json|映射|mapping|参数|args|steps/i.test(el.placeholder + el.id) }))
    .filter(x => x.w < (x.json ? 280 : 120));

  // E. 表单可访问性：字段必须有常驻 label（placeholder 不算）
  const fields = [...document.querySelectorAll('input,textarea,select')].filter(vis)
    .filter(el => el.type !== 'checkbox');
  o.fields = fields.length;
  o.fieldsLabeled = fields.filter(el =>
    el.closest('label') || (el.id && document.querySelector('label[for="' + el.id + '"]')) ||
    el.getAttribute('aria-label')).length;
  o.forms = document.querySelectorAll('form').length;
  o.clickableSpans = [...document.querySelectorAll('[onclick]')].filter(el => el.tagName !== 'BUTTON').length;

  // F. 表格：表头是否吸顶、是否有横向滚动容器、行数（无上限 = 千行 DOM 卡死）
  o.tables = [...document.querySelectorAll('table')].filter(vis).map(tb => ({
    cols: tb.querySelectorAll('thead th').length,
    rows: tb.querySelectorAll('tbody tr').length,
    w: W(tb),
    stickyHead: getComputedStyle(tb.querySelector('thead th') || tb).position === 'sticky',
    scrollHost: (() => { let p = tb.parentElement;
      while (p && p !== document.body) { const x = getComputedStyle(p).overflowX;
        if (x === 'auto' || x === 'scroll') return true; p = p.parentElement; } return false; })(),
  }));

  // G. 栅格等高造成的空白 / 卡片尺寸 / 内联样式数量
  o.grids = [...document.querySelectorAll('.grid, .cols, main > div[class*=grid]')].filter(vis)
    .map(g => ({ cls: String(g.className).slice(0, 20), heights: [...g.children].map(H) }));
  o.cards = [...document.querySelectorAll('.card')].filter(vis)
    .map(c => ({ n: ((c.querySelector('h3,h4') || {}).textContent || '?').trim().slice(0, 14),
                 w: W(c), h: H(c), nested: c.querySelectorAll('.card').length }));
  o.inlineStyled = [...document.querySelectorAll('[style]')].filter(vis).length;

  // H. 大块提示条（>120px 高的 banner 属于文案问题，不是布局问题）
  o.fatBanners = [...document.querySelectorAll('.banner, .empty, .hint')].filter(vis)
    .map(el => ({ cls: String(el.className).split(' ')[0], h: H(el),
                  chars: (el.textContent || '').replace(/\\s+/g, '').length }))
    .filter(x => x.h > 120);
  return o;
})()`;

const report = { base: BASE, tabs: {} };
// 页签清单从页面里读，不写死：新增/改名页签自动纳入验收
await cmd("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false }, sid);
await cmd("Page.navigate", { url: `${BASE}/#overview` }, sid);
await sleep(1500);
const TABS = await ev(`[...document.querySelectorAll('[data-tab]')].map(b => b.dataset.tab)`);
if (!TABS || !TABS.length) { console.error("页面上找不到 [data-tab] 导航"); process.exit(2); }
report.tabsFound = TABS;

for (const [width, height] of SIZES) {
  await cmd("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: false }, sid);
  for (const tab of TABS) {
    await cmd("Page.navigate", { url: `${BASE}/#${tab}` }, sid);
    await sleep(1200);
    await ev(`(()=>{const b=document.querySelector('[data-tab=${JSON.stringify(tab)}]');if(b)b.click();})()`);
    await sleep(1100);
    report.tabs[`${tab}@${width}`] = await ev(PROBE);
  }
}

/** 交互类缺陷：重复提交 / 结果被清 / N+1 请求 */
await cmd("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false }, sid);
await cmd("Page.navigate", { url: `${BASE}/#overview` }, sid);
await sleep(2000);
report.interaction = {};
// 上一轮探测可能把当前工作空间切到了空空间（localStorage 会记住），先回到有数据的空间
await ev(`(async () => { const w = S.workspaces[0]; if (w) switchWorkspace(w.id); })()`);
await sleep(1500);
// 顺序有讲究：doubleSubmit 会切换当前工作空间，必须最后跑
report.interaction.stateChurn = await ev(`(async () => {
  activateTab('application');
  await new Promise(r => setTimeout(r, 1500));
  const aid = Object.keys(S.appsById)[0]; if (!aid) return { skip: true };
  const q = Object.values(S.appsById[aid].queries || {})[0]; if (!q) return { skip: true };
  await runAppQuery(aid, q.alias);
  const before = document.getElementById('qr-' + aid).innerHTML.length;
  const box = document.getElementById('v-' + aid); if (box) box.value = 'probe' + Date.now();
  await addView(aid);
  await new Promise(r => setTimeout(r, 1500));
  return { resultCharsBefore: before,
           resultCharsAfter: (document.getElementById('qr-' + aid) || { innerHTML: '' }).innerHTML.length };
})()`);
report.interaction.detailFetches = await ev(`(async () => {
  activateTab('runtime'); await new Promise(r => setTimeout(r, 1200));
  let n = 0; const of = window.fetch;
  window.fetch = (...a) => { n++; return of(...a); };
  await showObject('Vehicle/V001');
  await new Promise(r => setTimeout(r, 1500));
  window.fetch = of; return { fetches: n, relations: ((S.runtime || {}).relations || []).length };
})()`);
report.interaction.doubleSubmit = await ev(`(async () => {
  const before = (await jget('/api/workspaces?user=admin')).length;
  document.getElementById('newWsName').value = '探针-双击-' + Date.now();
  createWorkspace(); createWorkspace();
  await new Promise(r => setTimeout(r, 1500));
  return { created: (await jget('/api/workspaces?user=admin')).length - before };
})()`);

/** 宽表压力：真实投影对象往往十几个属性，一张表就能把页面推横向滚动 */
await ev(`(async () => { const w = S.workspaces[0]; if (w) switchWorkspace(w.id); activateTab('runtime'); })()`);
await sleep(1500);
report.stress = await ev(`(() => {
  const host = document.getElementById('stateBody');
  const keep = host.innerHTML;
  const wide = Array.from({ length: 6 }, (_, i) => ({ type: 'Wide', id: 'W' + i,
    properties: Object.fromEntries(Array.from({ length: 14 }, (_, k) =>
      ['property_number_' + (k + 1), 'sample-value-' + k + '-with-long-text-content'])) }));
  host.innerHTML = objectTable(wide);
  const tb = host.querySelector('table');
  const r = {
    tableW: Math.round(tb.getBoundingClientRect().width),
    pageHScroll: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    stickyHead: getComputedStyle(tb.querySelector('thead th')).position === 'sticky',
    scrollHost: (() => { let p = tb.parentElement; while (p && p !== document.body) {
      const x = getComputedStyle(p).overflowX; if (x === 'auto' || x === 'scroll') return true; p = p.parentElement; }
      return false; })(),
  };
  host.innerHTML = keep;
  return r;
})()`);

/** 验收门槛：优化后必须全部满足。code 对应 docs/frontend-layout-plan.md 的问题编号 */
const fails = [];
const ok = (cond, code, msg) => { if (!cond) fails.push(`[${code}] ${msg}`); };
for (const [key, t] of Object.entries(report.tabs)) {
  if (!t || t.__err) continue;
  ok(t.pageHScroll <= 1, "L1", `${key}: 整页横向滚动 ${t.pageHScroll}px`);
  ok(t.overflow.length === 0, "L1", `${key}: ${t.overflow.length} 个元素横向溢出 ${JSON.stringify(t.overflow[0] || {})}`);
  ok(t.tables.every(tb => tb.scrollHost), "L1", `${key}: ${t.tables.filter(tb => !tb.scrollHost).length} 个表格没有横向滚动容器`);
  ok(t.tables.every(tb => !tb.rows || tb.stickyHead), "L1", `${key}: 表头未吸顶`);
  ok(t.wrappedRows.length === 0, "L2", `${key}: ${t.wrappedRows.length} 条表单行换行 ${JSON.stringify(t.wrappedRows[0] || {})}`);
  ok(t.cramped.length === 0, "L2", `${key}: ${t.cramped.length} 个过窄控件 ${JSON.stringify(t.cramped[0] || {})}`);
  ok(t.fields === t.fieldsLabeled, "L3", `${key}: ${t.fields - t.fieldsLabeled}/${t.fields} 字段无常驻 label`);
  ok(t.clickableSpans === 0, "C9", `${key}: ${t.clickableSpans} 个非 button 的 onclick 元素（键盘不可达）`);
  ok(t.stickyChromeH <= 100, "L0", `${key}: 固定头部占高 ${t.stickyChromeH}px`);
  ok(t.fatBanners.length === 0, "C5", `${key}: ${t.fatBanners.length} 个 >120px 的提示条 ${JSON.stringify(t.fatBanners[0] || {})}`);
  ok(t.inlineStyled <= 5, "L0", `${key}: ${t.inlineStyled} 处内联 style`);
}
const it = report.interaction;
if (it.doubleSubmit) ok(it.doubleSubmit.created <= 1, "L5", `双击创建了 ${it.doubleSubmit.created} 个工作空间`);
if (it.stateChurn && !it.stateChurn.skip)
  ok(it.stateChurn.resultCharsAfter > 0, "L4", "新增定义后上一次查询结果被清空");
if (it.detailFetches)
  ok(it.detailFetches.fetches <= 1 + it.detailFetches.relations,
     "C6", `打开 1 个对象详情发了 ${it.detailFetches.fetches} 次请求（关系数 ${it.detailFetches.relations}，应并发）`);
const st = report.stress;
if (st) {
  ok(st.pageHScroll <= 1, "L1", `14 列宽表把页面推出 ${st.pageHScroll}px 横向滚动（表 ${st.tableW}px）`);
  ok(st.scrollHost, "L1", "宽表没有包裹 overflow-x 容器");
  ok(st.stickyHead, "L1", "宽表表头未吸顶");
}

report.pass = fails.length === 0;
report.fails = fails;
console.log(JSON.stringify(report, null, 1));
ws.close();
process.exit(fails.length ? 1 : 0);
