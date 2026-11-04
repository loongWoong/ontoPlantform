import { ref } from 'vue'

export type Tab = 'ai' | 'notes'

const open = ref(false)
const tab = ref<Tab>('ai')

export function useUi() {
  function openPanel(t: Tab = 'ai') {
    tab.value = t
    open.value = true
  }
  function close() {
    open.value = false
  }
  return { open, tab, openPanel, close }
}
