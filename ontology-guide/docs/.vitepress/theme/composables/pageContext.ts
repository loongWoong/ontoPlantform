/**
 * 构造发送给 LLM 的页面上下文：页面标题、小节标题、选中的正文段落。
 */
export function pageContext(extra?: string) {
  if (typeof document === 'undefined') return extra || ''
  const title =
    (document.querySelector('.vp-doc h1')?.textContent || '').trim() ||
    document.title
  const heads = Array.from(document.querySelectorAll('.vp-doc h2'))
    .slice(0, 10)
    .map((h) => (h.textContent || '').trim())
    .filter(Boolean)

  const parts: string[] = []
  if (title) parts.push('当前页面：《' + title + '》')
  if (heads.length) parts.push('页面小节：\n' + heads.map((h) => '· ' + h).join('\n'))
  if (extra && extra.trim()) parts.push('用户选中的正文内容：\n' + extra.trim())
  return parts.join('\n\n')
}
