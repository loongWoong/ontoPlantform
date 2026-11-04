<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useNotes } from '../composables/useNotes'
import { useAi } from '../composables/useAi'
import { useUi } from '../composables/useUi'
import { useSelection } from '../composables/useSelection'
import { pageContext } from '../composables/pageContext'

const { notes, saved, append, clear, copy } = useNotes()
const { config, ask } = useAi()
const { openPanel } = useUi()
const { selected } = useSelection()

const mounted = ref(false)
const flash = ref('')
onMounted(() => {
  mounted.value = true
})

const configured = computed(() => !!(config.value.apiKey && config.value.model))

function notify(msg: string) {
  flash.value = msg
  setTimeout(() => {
    if (flash.value === msg) flash.value = ''
  }, 2600)
}

function grabSelection() {
  if (!selected.value) {
    notify('先在正文中选中要收录的段落')
    return
  }
  append(selected.value)
  notify('已收录选中内容到速记')
}

async function doCopy() {
  await copy()
  notify('速记内容已复制')
}

function doClear() {
  if (notes.value) clear()
}

function explainPage() {
  openPanel('ai')
  if (!configured.value) return
  ask(
    '请结合当前页面，详细解释本页涉及的核心本体概念：先给出定义与作用，再说明与相近概念的区分，最后给出一个建模示例。',
    pageContext()
  )
}

function explainSelection() {
  if (!selected.value) {
    notify('先在正文中选中要解释的内容')
    return
  }
  openPanel('ai')
  if (!configured.value) return
  ask(
    '请详细解释我选中的这段内容：它在当前页面语境下是什么意思？涉及哪些本体概念？常见误区是什么？',
    pageContext(selected.value)
  )
}
</script>

<template>
  <div class="og-aside">
    <section class="og-card">
      <div class="og-head">
        <span>速记</span>
        <span v-if="mounted" class="og-status" :class="{ ok: saved }">{{ saved ? '已自动保存' : '自动保存' }}</span>
        <span v-else class="og-status">自动保存</span>
      </div>
      <textarea
        v-model="notes"
        class="og-note-input"
        rows="6"
        placeholder="在这里粘贴关键段落、关键词、自己的理解或待查问题…（按页面自动保存）"
      ></textarea>
      <div class="og-actions">
        <button class="og-btn" type="button" @click="grabSelection">收录选中</button>
        <button class="og-btn" type="button" @click="doCopy">复制</button>
        <button class="og-btn" type="button" @click="doClear">清空</button>
      </div>
      <p v-if="flash" class="og-flash">{{ flash }}</p>
    </section>

    <section class="og-card">
      <div class="og-head">
        <span>LLM 助手</span>
        <span v-if="mounted" class="og-status" :class="{ ok: configured }">{{ configured ? '已配置' : '未配置' }}</span>
        <span v-else class="og-status">配置</span>
      </div>
      <p class="og-hint">用大模型详细解释本页概念，或解释你在正文里选中的段落。</p>
      <div class="og-actions">
        <button class="og-btn og-primary og-block" type="button" @click="explainPage">解释本页概念</button>
        <button class="og-btn og-block" type="button" @click="explainSelection">解释选中内容</button>
        <button class="og-btn og-block" type="button" @click="openPanel('ai')">打开助手</button>
      </div>
    </section>
  </div>
</template>
