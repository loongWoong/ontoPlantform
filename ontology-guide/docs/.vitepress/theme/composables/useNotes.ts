import { ref, watch } from 'vue'
import { useRoute } from 'vitepress'

const PREFIX = 'ontology-guide:notes:v1:'

const notes = ref('')
const path = ref('')
const saved = ref(false)
let saveTimer: ReturnType<typeof setTimeout> | null = null

function storageKey(p: string) {
  // 去掉 .html 后缀，保证 cleanUrls 开关切换后同一页面的速记仍然对得上
  const clean = (p || '/').replace(/\.html$/, '') || '/'
  return PREFIX + clean
}

function load(p: string) {
  path.value = p
  let v = ''
  try {
    v = localStorage.getItem(storageKey(p)) || ''
  } catch {
    v = ''
  }
  notes.value = v
  saved.value = false
}

function persist() {
  if (typeof window === 'undefined' || !path.value) return
  try {
    localStorage.setItem(storageKey(path.value), notes.value)
    saved.value = true
    if (saveTimer) clearTimeout(saveTimer)
    saveTimer = setTimeout(() => {
      saved.value = false
    }, 1200)
  } catch {
    /* localStorage 不可用（隐私模式/配额满）时静默降级 */
  }
}

/**
 * 按页面隔离的速记本：内容自动保存到 localStorage，可收录正文选中段落。
 */
export function useNotes() {
  const route = useRoute()
  if (typeof window !== 'undefined' && !path.value) load(route.path)
  watch(
    () => route.path,
    (p) => {
      if (typeof window !== 'undefined') load(p)
    }
  )
  watch(notes, () => persist())

  function append(text: string) {
    const t = (text || '').trim()
    if (!t) return
    notes.value = notes.value ? notes.value.replace(/\s+$/, '') + '\n\n' + t : t
  }
  function clear() {
    notes.value = ''
  }
  async function copy() {
    try {
      if (typeof navigator !== 'undefined' && navigator.clipboard && notes.value) {
        await navigator.clipboard.writeText(notes.value)
      }
    } catch {
      /* ignore */
    }
  }
  return { notes, path, saved, append, clear, copy }
}
