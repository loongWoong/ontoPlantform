import { ref } from 'vue'

const selected = ref('')
let listening = false

function onSelectionChange() {
  if (typeof window === 'undefined') return
  const sel = window.getSelection()
  if (!sel || sel.isCollapsed) return
  const text = sel.toString().replace(/\s+/g, ' ').trim()
  if (!text || text.length < 2) return
  const node = sel.anchorNode
  const el =
    node && node.nodeType === Node.TEXT_NODE
      ? (node.parentElement as HTMLElement | null)
      : (node as HTMLElement | null)
  if (el && el.closest && el.closest('.vp-doc')) {
    selected.value = text.slice(0, 4000)
  }
}

/**
 * 跟踪用户在正文（.vp-doc）里的选中文本，供「解释选中内容」/「收录选中」使用。
 */
export function useSelection() {
  if (typeof window !== 'undefined' && !listening) {
    listening = true
    document.addEventListener('selectionchange', onSelectionChange)
  }
  return { selected }
}
