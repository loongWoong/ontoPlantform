#!/usr/bin/env node
/**
 * 把整站内容渲染成一个 PDF（含封面、目录、43 个章节）
 *
 * 思路：
 *   1. 读取 docs/.vitepress/dist 下已构建的 HTML，按 config.mts 侧边栏顺序提取每页 .vp-doc 正文；
 *   2. 合并为一个带打印样式的 HTML（封面 + 目录 + 各章节，章节间分页）；
 *   3. 用本机 Chrome headless --print-to-pdf 输出到 docs/public/ontology-guide.pdf。
 *
 * 用法：
 *   pnpm docs:build      # 先构建站点（需要最新 dist）
 *   pnpm docs:pdf        # 生成 PDF
 *
 * 可用环境变量：
 *   CHROME_PATH  指定 Chrome/Edge 可执行文件路径
 */
import { execFileSync } from 'node:child_process'
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const DOCS = join(ROOT, 'docs')
const DIST = join(DOCS, '.vitepress', 'dist')
const CONFIG = join(DOCS, '.vitepress', 'config.mts')
const OUT_PDF = join(DOCS, 'public', 'ontology-guide.pdf')

if (!existsSync(join(DIST, 'main.html'))) {
  console.error('未找到构建产物，请先运行：pnpm docs:build')
  process.exit(1)
}

// ---- 1. 按侧边栏顺序得到章节列表 ----
const configText = readFileSync(CONFIG, 'utf8')
const sideStart = configText.indexOf('sidebar:')
const sideEnd = configText.indexOf('outline:', sideStart)
const sideText = configText.slice(sideStart, sideEnd)
const items = []
const pairRe = /\{\s*text:\s*'([^']*)',\s*link:\s*'([^']*)'/g
let m
while ((m = pairRe.exec(sideText))) items.push({ text: m[1], link: m[2] })
if (!items.length) {
  console.error('未能从 config.mts 解析到侧边栏章节')
  process.exit(1)
}

// ---- 2. 提取每页 .vp-doc 正文 ----
function extractDoc(html) {
  const open = /<div[^>]*class="[^"]*\bvp-doc\b[^"]*"[^>]*>/.exec(html)
  if (!open) return ''
  const start = open.index + open[0].length
  const tagRe = /<\/?div\b[^>]*>/g
  tagRe.lastIndex = start
  let depth = 1
  let t
  while ((t = tagRe.exec(html))) {
    if (t[0][1] === '/') depth--
    else depth++
    if (depth === 0) return html.slice(start, t.index)
  }
  return ''
}

function firstH1(html) {
  const h = /<h1[^>]*>([\s\S]*?)<\/h1>/.exec(html)
  if (!h) return ''
  return h[1]
    .replace(/<a class="header-anchor"[\s\S]*?<\/a>/g, '')
    .replace(/<[^>]+>/g, '')
    .replace(/[\u200b\uFEFF]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}

const linkIndex = new Map()
items.forEach((it, i) => {
  linkIndex.set(it.link, i + 1)
  linkIndex.set(it.link.replace(/\/$/, '') || '/', i + 1)
})

const pages = items.map((it, i) => {
  const file = it.link.endsWith('/')
    ? join(DIST, it.link.slice(1), 'index.html')
    : join(DIST, it.link.slice(1) + '.html')
  if (!existsSync(file)) {
    console.warn('缺少页面：' + it.link + '（跳过）')
    return { ...it, html: '', fileMissing: true }
  }
  const full = readFileSync(file, 'utf8')
  let body = extractDoc(full)
  const prefix = 'p' + (i + 1) + '-'
  // 页面内锚点加前缀，避免合并后 ID 冲突
  body = body
    .replace(/id="([^"]+)"/g, (_, id) => `id="${prefix}${id}"`)
    .replace(/href="#([^"]+)"/g, (_, id) => `href="#${prefix}${id}"`)
  // 跨页链接改为指向本章
  body = body.replace(/href="([^"]+)"/g, (raw, href) => {
    if (/^(?:https?:|mailto:|#)/.test(href)) return raw
    const idx = linkIndex.get(href) || linkIndex.get(href.replace(/\/$/, '') || '/')
    return idx ? `href="#p${idx}"` : raw
  })
  const title = firstH1(body) || it.text
  return { ...it, html: body, title }
})

// ---- 3. 复用构建产物里的代码高亮配色 ----
let hljsCss = ''
try {
  const assetDir = join(DIST, 'assets')
  const cssFile = (await import('node:fs')).readdirSync(assetDir).find((f) => f.endsWith('.css'))
  if (cssFile) {
    const css = readFileSync(join(assetDir, cssFile), 'utf8')
    hljsCss = (css.match(/\.hljs-[a-zA-Z0-9_-]+[^{}]*\{[^}]*\}/g) || []).join('\n')
  }
} catch {
  /* 忽略 */
}

// 来源信息：git 仓库地址 + git 用户（默认取本仓库信息，可用环境变量覆盖）
const REPO_URL =
  process.env.PDF_REPO_URL || 'https://github.com/loongWoong/ontoPlantform/tree/main/ontology-guide'
const REPO_NAME = REPO_URL.replace(/^https?:\/\/github\.com\//, '').replace(/\/tree\/.*$/, '')
const GIT_USER = process.env.PDF_GIT_USER || 'loong'

const total = pages.length
const generated = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  dateStyle: 'long'
}).format(new Date())

const tocHtml = pages
  .map((p, i) => `<li><a href="#p${i + 1}">${escapeHtml(p.text)}</a></li>`)
  .join('\n')

const sectionsHtml = pages
  .map(
    (p, i) => `
  <section class="doc-page" id="p${i + 1}">
    <div class="pdf-label">第 ${i + 1} 节 / 共 ${total} 节 · ${escapeHtml(p.text)}</div>
    ${p.html}
  </section>`
  )
  .join('\n')

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c])
}

const printCss = `
@page { size: A4; margin: 16mm 14mm 18mm; }
* { box-sizing: border-box; }
html { -webkit-print-color-adjust: exact; }
body {
  margin: 0;
  color: #1f2328;
  font-family: "Microsoft YaHei", "PingFang SC", "Segoe UI", system-ui, sans-serif;
  font-size: 10.5pt;
  line-height: 1.72;
}
a { color: #1f6feb; text-decoration: none; }
h1, h2, h3, h4 { color: #111827; line-height: 1.35; page-break-after: avoid; }
h1 { font-size: 20pt; margin: 0 0 12px; }
h2 { font-size: 14.5pt; margin: 22px 0 8px; padding-bottom: 4px; border-bottom: 1px solid #e5e7eb; }
h3 { font-size: 12.5pt; margin: 16px 0 6px; }
h4 { font-size: 11pt; margin: 12px 0 4px; }
p { margin: 6px 0; }
ul, ol { margin: 6px 0; padding-left: 22px; }
li { margin: 2px 0; }
p, li { orphans: 3; widows: 3; }
blockquote { margin: 8px 0; padding: 2px 0 2px 12px; border-left: 3px solid #d0d7de; color: #4b5563; }
pre {
  margin: 8px 0;
  padding: 9px 11px;
  background: #f6f8fa;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  font-size: 8.6pt;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: anywhere;
}
code {
  font-family: Consolas, "Cascadia Code", "Courier New", monospace;
  font-size: 8.8pt;
  background: #f3f4f6;
  padding: 0 3px;
  border-radius: 3px;
}
pre code { background: none; padding: 0; font-size: inherit; }
table { width: 100%; border-collapse: collapse; margin: 8px 0; font-size: 9.4pt; page-break-inside: avoid; }
th, td { border: 1px solid #d0d7de; padding: 4px 7px; text-align: left; vertical-align: top; }
th { background: #f6f8fa; }
.custom-block {
  margin: 10px 0;
  padding: 8px 11px;
  border: 1px solid #d0d7de;
  border-left-width: 4px;
  border-radius: 5px;
  page-break-inside: avoid;
}
.custom-block.tip { border-left-color: #10b981; background: #f0fdf4; }
.custom-block.info { border-left-color: #3b82f6; background: #eff6ff; }
.custom-block.warning { border-left-color: #f59e0b; background: #fffbeb; }
.custom-block.danger { border-left-color: #ef4444; background: #fef2f2; }
.custom-block-title { font-weight: 600; margin-bottom: 3px; }
.cover {
  display: flex;
  flex-direction: column;
  justify-content: center;
  min-height: 246mm;
  page-break-after: always;
  text-align: center;
}
.cover .brand { font-size: 11pt; letter-spacing: 3px; color: #6b7280; margin-bottom: 14px; }
.cover h1 { font-size: 32pt; margin: 0 0 10px; }
.cover .tagline { font-size: 13pt; color: #4b5563; margin: 0 0 30px; }
.cover .meta { font-size: 10pt; color: #6b7280; line-height: 1.9; }
.cover-source {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 8px;
  margin-top: 26px;
}
.cover-tag {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 12px;
  border: 1px solid #d0d7de;
  border-radius: 999px;
  background: #f6f8fa;
  color: #1f2328;
  font-size: 9.5pt;
  text-decoration: none;
}
.cover-tag .tag-key {
  font-size: 8pt;
  font-weight: 700;
  letter-spacing: 0.5px;
  color: #fff;
  background: #1f6feb;
  padding: 1px 6px;
  border-radius: 999px;
}
.cover-url {
  margin-top: 10px;
  font-family: Consolas, "Cascadia Code", monospace;
  font-size: 8.5pt;
  color: #4b5563;
  word-break: break-all;
}
.cover-url a { color: #1f6feb; }
.cover .rule { width: 42mm; height: 3px; background: #1f6feb; margin: 18px auto; }
.toc { page-break-after: always; }
.toc h1 { font-size: 20pt; border-bottom: 2px solid #1f6feb; padding-bottom: 6px; margin-bottom: 14px; }
.toc ol { padding-left: 20px; column-count: 1; }
.toc li { margin: 5px 0; font-size: 11pt; }
.doc-page { page-break-before: always; }
.pdf-label { font-size: 8.5pt; color: #9ca3af; margin-bottom: 10px; padding-bottom: 4px; border-bottom: 1px dashed #e5e7eb; }
.header-anchor { display: none; }
.vp-doc h1:first-child { margin-top: 0; }
`

const merged = `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<title>本体建模与应用指南</title>
<style>${printCss}
${hljsCss}</style>
</head>
<body>
  <section class="cover">
    <div class="brand">ONTOLOGY MODELING GUIDE</div>
    <h1>本体建模与应用指南</h1>
    <div class="rule"></div>
    <p class="tagline">从概念体系到形式化模型，再到数据与应用</p>
    <div class="meta">
      共 ${total} 节 · 覆盖 RDF / OWL 2 / SKOS / SHACL / SPARQL、建模方法、实现架构与应用落地<br />
      由站点内容自动生成 · ${escapeHtml(generated)}
    </div>
    <div class="cover-source">
      <a class="cover-tag" href="${escapeHtml(REPO_URL)}" title="${escapeHtml(REPO_URL)}">
        <span class="tag-key">GIT</span>
        <span>${escapeHtml(REPO_NAME)}</span>
      </a>
      <span class="cover-tag" title="git 用户">
        <span class="tag-key">GIT 用户</span>
        <span>${escapeHtml(GIT_USER)}</span>
      </span>
    </div>
    <div class="cover-url">
      <a href="${escapeHtml(REPO_URL)}">${escapeHtml(REPO_URL)}</a>
    </div>
  </section>

  <section class="toc">
    <h1>目录</h1>
    <ol>
${tocHtml}
    </ol>
  </section>
${sectionsHtml}
</body>
</html>
`

// ---- 4. 用 Chrome 打印成 PDF ----
function findChrome() {
  const candidates = [
    process.env.CHROME_PATH,
    'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
    'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe'
  ].filter(Boolean)
  return candidates.find((c) => existsSync(c))
}

const chrome = findChrome()
if (!chrome) {
  console.error('未找到 Chrome/Edge。请安装 Chrome，或设置环境变量 CHROME_PATH 指向浏览器可执行文件。')
  process.exit(1)
}

const tmpProfile = mkdtempSync(join(tmpdir(), 'og-pdf-'))
const tmpHtml = join(tmpProfile, 'ontology-guide-print.html')
writeFileSync(tmpHtml, merged, 'utf8')
mkdirSync(dirname(OUT_PDF), { recursive: true })

const args = [
  '--headless=new',
  '--disable-gpu',
  '--no-first-run',
  '--no-default-browser-check',
  '--user-data-dir=' + tmpProfile,
  '--no-pdf-header-footer',
  '--virtual-time-budget=30000',
  '--print-to-pdf=' + OUT_PDF,
  pathToFileURL(tmpHtml).href
]

console.log('正在渲染 PDF（' + total + ' 节）…')
execFileSync(chrome, args, { stdio: 'inherit', timeout: 10 * 60 * 1000 })

if (!existsSync(OUT_PDF)) {
  console.error('PDF 生成失败：未找到输出文件')
  process.exit(1)
}

const size = (await import('node:fs')).statSync(OUT_PDF).size
const buf = readFileSync(OUT_PDF)
const pageCount = (buf.toString('latin1').match(/\/Type\s*\/Page\b/g) || []).length

// 已构建的话顺手同步到 dist，避免还要再 build 一次
try {
  copyFileSync(OUT_PDF, join(DIST, 'ontology-guide.pdf'))
} catch {
  /* dist 不存在时忽略 */
}

console.log(
  'PDF 已生成：' + OUT_PDF + '\n' +
  '大小：' + (size / 1024 / 1024).toFixed(2) + ' MB，约 ' + pageCount + ' 页（A4，共 ' + total + ' 节）'
)
