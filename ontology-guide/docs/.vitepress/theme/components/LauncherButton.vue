<script setup lang="ts">
import { useUi } from '../composables/useUi'

const { openPanel } = useUi()
</script>

<template>
  <button class="og-launcher" type="button" title="速记 / LLM 助手" @click="openPanel('ai')">
    <span aria-hidden="true">✎</span>
    <span>速记 / AI</span>
  </button>
</template>
