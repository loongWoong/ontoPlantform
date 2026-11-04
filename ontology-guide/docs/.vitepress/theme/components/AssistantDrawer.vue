<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useData, useRoute } from 'vitepress'
import { useNotes } from '../composables/useNotes'
import { useAi, LOCAL_PROXY_URL } from '../composables/useAi'
import { useUi } from '../composables/useUi'
import { useSelection } from '../composables/useSelection'
import { pageContext } from '../composables/pageContext'

const { open, tab, openPanel, close } = useUi()
const { notes, append, clear, copy } = useNotes()
const { config, messages, loading, saveConfig, ask } = useAi()
const { selected } = useSelection()
const { page } = useData()
const route = useRoute()

const mounted = ref(false)
const configOpen = ref(false)
const question = ref('')
const flash = ref('')
const listEl = ref<HTMLElement | null>(null)

const pageTitle = computed(() => page.value.title || '当前页面')
const configured = computed(() => !!(config.value.apiKey && config.value.model))

function notify(msg: string) {
  flash.value = msg
  setTimeout(() => {
    if (flash.value === msg) flash.value = ''
  }, 2600)
}

function useLocalProxy() {
  config.value.baseUrl = LOCAL_PROXY_URL
  notify('已填入本地代理地址，请点「保存配置」后重试')
}

function scrollBottom() {
  const el = listEl.value
  if (el) el.scrollTop = el.scrollHeight
}

function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape' && open.value) close()
}

onMounted(() => {
  mounted.value = true
  window.addEventListener('keydown', onKey)
})
onUnmounted(() => window.removeEventListener('keydown', onKey))

watch(open, async (v) => {
  if (!v) return
  configOpen.value = !configured.value
  await nextTick()
  scrollBottom()
})
watch(
  () => messages.value.length,
  async () => {
    await nextTick()
    scrollBottom()
  }
)

function onEnter(e: KeyboardEvent) {
  if (e.isComposing) return
  e.preventDefault()
  send()
}

async function send() {
  const q = question.value.trim()
  if (!q || loading.value) return
  question.value = ''
  await ask(q, pageContext(selected.value))
}

function quick(kind: 'page' | 'selection' | 'example' | 'compare') {
  if (loading.value) return
  if (kind === 'selection' && !selected.value) {
    notify('先在正文中选中要解释的内容')
    return
  }
  const prompts: Record<string, string> = {
    page: '请结合当前页面，详细解释本页涉及的核心本体概念：先给出定义与作用，再说明与相近概念的区分，最后给出一个建模示例。',
    selection: '请详细解释我选中的这段内容：它在当前页面语境下是什么意思？涉及哪些本体概念？常见误区是什么？',
    example: '请用一个具体的业务场景例子，说明本页概念如何落地到实际建模中。',
    compare: '请对比本页涉及的相近本体概念，说明它们的区别与选用建议。'
  }
  ask(prompts[kind], pageContext(kind === 'selection' ? selected.value : ''))
}

function clearChat() {
  messages.value = []
}

async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text)
    notify('已复制到剪贴板')
  } catch {
    notify('复制失败，请手动选择复制')
  }
}

function insertNote(text: string) {
  append('来自 AI 助手：\n' + text)
  notify('已插入速记，可在「速记」页签查看')
}

function grabSelection() {
  if (!selected.value) {
    notify('先在正文中选中要收录的段落')
    return
  }
  append(selected.value)
  notify('已收录选中内容')
}

async function doCopyNotes() {
  await copy()
  notify('速记内容已复制')
}

function clearNotes() {
  if (notes.value) clear()
}

function downloadNotes() {
  if (!notes.value) {
    notify('速记为空，先写点内容吧')
    return
  }
  const slug = route.path.replace(/[^a-zA-Z0-9\u4e00-\u9fa5]+/g, '-').replace(/^-+|-+$/g, '')
  const body = '# 速记 · ' + pageTitle.value + '\n\n' + notes.value + '\n'
  const blob = new Blob([body], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'ontology-notes-' + (slug || 'page') + '.md'
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 2000)
  notify('已导出 .md 文件')
}
</script>

<template>
  <Teleport v-if="mounted && open" to="body">
    <div class="og-portal">
      <div class="og-overlay" @click="close"></div>
      <aside class="og-drawer" role="dialog" aria-modal="true" aria-label="速记与 LLM 助手">
        <header class="og-drawer-head">
          <div class="og-tabs">
            <button type="button" :class="{ active: tab === 'ai' }" @click="openPanel('ai')">AI 助手</button>
            <button type="button" :class="{ active: tab === 'notes' }" @click="openPanel('notes')">速记</button>
          </div>
          <div style="display: flex; align-items: center; gap: 8px">
            <button v-if="tab === 'ai' && messages.length" class="og-btn" type="button" @click="clearChat">清空对话</button>
            <button class="og-close" type="button" aria-label="关闭" @click="close">×</button>
          </div>
        </header>

        <!-- AI 助手 -->
        <div v-show="tab === 'ai'" class="og-drawer-body">
          <section class="og-config">
            <div class="og-config-head" @click="configOpen = !configOpen">
              <span>模型配置</span>
              <span>{{ configured ? config.model : '未配置（点击展开）' }}</span>
            </div>
            <div v-show="configOpen" class="og-config-body">
              <label>
                API 地址（OpenAI 兼容）
                <input v-model="config.baseUrl" placeholder="https://api.deepseek.com/v1" />
              </label>
              <label>
                模型名
                <input v-model="config.model" placeholder="deepseek-chat" />
              </label>
              <label>
                API Key
                <input v-model="config.apiKey" type="password" autocomplete="off" placeholder="sk-..." />
              </label>
              <div class="og-config-actions">
                <button
                  class="og-btn og-primary"
                  type="button"
                  @click="saveConfig(); configOpen = false; notify('配置已保存（仅本机浏览器）')"
                >
                  保存配置
                </button>
                <button class="og-btn" type="button" @click="useLocalProxy">填入本地代理</button>
                <span>Key 只存于本机 localStorage，不会上传到本站。</span>
              </div>
              <p class="og-hint">
                兼容 OpenAI Chat Completions 的端点均可：DeepSeek、OpenAI、Ollama（http://localhost:11434/v1）、vLLM 等。
                浏览器直连常被 CORS 拦截（报 Failed to fetch）：先在本目录运行 <code>pnpm docs:proxy</code>
                启动本地代理，再点「填入本地代理」（地址 http://127.0.0.1:8788/v1），保存后重试。
              </p>
            </div>
          </section>

          <div class="og-context">当前页面：{{ pageTitle }}</div>
          <div v-if="selected" class="og-context">已选中：{{ selected.slice(0, 140) }}…</div>

          <div ref="listEl" class="og-msgs">
            <div v-if="!messages.length" class="og-empty">
              向大模型提问本页的本体概念，例如「OWL 公理和 SHACL 约束怎么分工」「SKOS broader 和
              rdfs:subClassOf 有什么区别」。也可以先选中正文段落，再点「解释选中」。
            </div>
            <div
              v-for="(m, i) in messages"
              :key="i"
              :class="['og-msg', m.role, m.error ? 'error' : '']"
            >
              <div class="og-msg-body">{{ m.content }}</div>
              <div v-if="m.role === 'assistant' && !m.error" class="og-msg-actions">
                <button class="og-btn" type="button" @click="insertNote(m.content)">插入速记</button>
                <button class="og-btn" type="button" @click="copyText(m.content)">复制</button>
              </div>
            </div>
            <div v-if="loading" class="og-msg assistant">
              <div class="og-msg-body">正在生成，请稍候…</div>
            </div>
          </div>

          <div class="og-quick">
            <button class="og-btn" type="button" @click="quick('page')">解释本页概念</button>
            <button class="og-btn" type="button" @click="quick('selection')">解释选中</button>
            <button class="og-btn" type="button" @click="quick('example')">给个例子</button>
            <button class="og-btn" type="button" @click="quick('compare')">概念对比</button>
          </div>

          <div class="og-input">
            <textarea
              v-model="question"
              class="og-ai-input"
              rows="2"
              placeholder="输入问题（Enter 发送，Shift+Enter 换行）"
              @keydown.enter.exact="onEnter"
            ></textarea>
            <button class="og-btn og-primary" type="button" :disabled="loading || !question.trim()" @click="send">
              发送
            </button>
          </div>
          <p v-if="flash" class="og-flash">{{ flash }}</p>
        </div>

        <!-- 速记 -->
        <div v-show="tab === 'notes'" class="og-drawer-body og-notes-body">
          <div class="og-head">
            <span>本页速记</span>
            <span class="og-status">{{ notes.length }} 字符 · 自动保存</span>
          </div>
          <textarea
            v-model="notes"
            class="og-note-input"
            placeholder="粘贴关键段落、关键词、自己的理解或待查问题…（按页面保存在浏览器本地）"
          ></textarea>
          <div class="og-actions">
            <button class="og-btn" type="button" @click="grabSelection">收录选中</button>
            <button class="og-btn" type="button" @click="doCopyNotes">复制</button>
            <button class="og-btn" type="button" @click="downloadNotes">导出 .md</button>
            <button class="og-btn" type="button" @click="clearNotes">清空</button>
          </div>
          <p v-if="flash" class="og-flash">{{ flash }}</p>
          <p class="og-hint">速记按页面路径保存在浏览器本地；导出的 .md 可以粘进 Obsidian、Notion 等笔记系统。</p>
        </div>
      </aside>
    </div>
  </Teleport>
</template>
