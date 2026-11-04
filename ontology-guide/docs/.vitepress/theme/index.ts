import DefaultTheme from 'vitepress/theme'
import { h } from 'vue'
import AsideTools from './components/AsideTools.vue'
import AssistantDrawer from './components/AssistantDrawer.vue'
import LauncherButton from './components/LauncherButton.vue'
import './styles.css'

export default {
  extends: DefaultTheme,
  Layout: () =>
    h(DefaultTheme.Layout, null, {
      'aside-outline-after': () => h(AsideTools),
      'layout-top': () => h(LauncherButton),
      'layout-bottom': () => h(AssistantDrawer)
    })
}
