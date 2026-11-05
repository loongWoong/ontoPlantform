<script setup lang="ts">
// 顶栏「下载 PDF」按钮：整站内容打包成一个 PDF，静态文件位于 /ontology-guide.pdf
</script>

<template>
  <a
    class="og-pdf-btn"
    href="/ontology-guide.pdf"
    download="本体建模与应用指南.pdf"
    target="_blank"
    rel="noreferrer"
    title="下载《本体建模与应用指南》完整 PDF（含目录，约 43 节）"
  >
    <span class="og-pdf-badge">PDF</span>
    <span class="og-pdf-label">下载全文</span>
  </a>
</template>
