import { ref } from 'vue'

export interface LlmConfig {
  baseUrl: string
  model: string
  apiKey: string
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  error?: boolean
}

const STORAGE_KEY = 'ontology-guide:llm:v1'

/** 本地代理默认地址（运行 pnpm docs:proxy 后可在配置里一键填入） */
export const LOCAL_PROXY_URL = 'http://127.0.0.1:8788/v1'

export const DEFAULTS: LlmConfig = {
  baseUrl: 'https://api.deepseek.com/v1',
  model: 'deepseek-chat',
  apiKey: ''
}

const SYSTEM_PROMPT = [
  '你是一位本体建模（Ontology Modeling）与语义网（Semantic Web）专家，长期使用 RDF、OWL 2、SKOS、SHACL、SPARQL 做企业级知识建模。',
  '请用简体中文、面向工程实践回答。要求：',
  '1. 先给出清晰定义，并说明它解决什么问题；',
  '2. 说明它与相近概念的区别（例如 OWL 公理 vs SHACL 约束、SKOS broader vs rdfs:subClassOf、OWA vs CWA）；',
  '3. 给出一个简短的 Turtle/SPARQL 示例或业务场景；',
  '4. 指出常见误区或建模建议；',
  '5. 结论要克制、准确，不确定时明确说明，不要编造标准条款、版本号或性能数字。',
  '回答控制在 600 字以内，用 Markdown 列表或小标题组织。'
].join('\n')

const config = ref<LlmConfig>({ ...DEFAULTS })
const configLoaded = ref(false)
const messages = ref<ChatMessage[]>([])
const loading = ref(false)

export function loadConfig() {
  if (typeof window === 'undefined' || configLoaded.value) return
  configLoaded.value = true
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) {
      const saved = JSON.parse(raw) as Partial<LlmConfig>
      config.value = { ...DEFAULTS, ...saved }
    }
  } catch {
    /* 配置损坏时回退默认值 */
  }
}

export function saveConfig() {
  if (typeof window === 'undefined') return
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(config.value))
  } catch {
    /* ignore */
  }
}

export function isConfigured() {
  return !!(config.value.apiKey && config.value.model && config.value.baseUrl)
}

async function ask(question: string, context: string) {
  loadConfig()
  const q = (question || '').trim()
  if (!q || loading.value) return
  if (!config.value.apiKey) {
    messages.value.push({
      role: 'assistant',
      content: '请先在「模型配置」里填写 API Key、模型名与 API 地址。配置只保存在你本机浏览器中。',
      error: true
    })
    return
  }
  messages.value.push({ role: 'user', content: q })
  loading.value = true
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 90000)
  try {
    const base = config.value.baseUrl.replace(/\/+$/, '')
    const res = await fetch(base + '/chat/completions', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: 'Bearer ' + config.value.apiKey
      },
      body: JSON.stringify({
        model: config.value.model,
        temperature: 0.3,
        max_tokens: 4096,
        stream: false,
        messages: [
          { role: 'system', content: SYSTEM_PROMPT },
          {
            role: 'user',
            content: context ? q + '\n\n【页面上下文】\n' + context : q
          }
        ]
      }),
      signal: controller.signal
    })
    if (!res.ok) {
      const body = await res.text().catch(() => '')
      throw new Error('HTTP ' + res.status + (body ? '：' + body.slice(0, 400) : ''))
    }
    const data = await res.json()
    const choice = data?.choices?.[0]
    let content: string = choice?.message?.content || ''
    // 思考型模型（如 deepseek 推理模型）在 max_tokens 偏小时可能只返回 reasoning_content
    const reasoning: string = choice?.message?.reasoning_content || ''
    if (!content && reasoning) {
      content =
        '（模型只返回了推理过程，未给出正式回答；通常是 max_tokens 偏小或输出被截断，可重试。）\n\n' +
        reasoning.slice(0, 3000)
    }
    messages.value.push({
      role: 'assistant',
      content: content || '（模型返回为空，请检查模型名与接口地址；思考型模型可适当调大 max_tokens 后重试。）'
    })
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e)
    const aborted = e instanceof Error && e.name === 'AbortError'
    const networkish = /Failed to fetch|Load failed|NetworkError|ERR_/i.test(msg)
    const hint = networkish
      ? '\n\n这通常是浏览器跨域（CORS）或网络不通导致的：多数大模型 API（如 DeepSeek/OpenAI）不允许网页直接跨域调用。' +
        '\n解决办法：在本目录运行 `pnpm docs:proxy` 启动本地代理，然后在「模型配置」里点「填入本地代理」或把 API 地址改为 http://127.0.0.1:8788/v1，保存后重试。' +
        '\n（本地模型如 Ollama 也可直接把地址填为 http://localhost:11434/v1，并设置 OLLAMA_ORIGINS=*）'
      : '\n\n常见原因：API Key 无效或额度不足、模型名不对、接口地址不是兼容 OpenAI 的 /chat/completions 端点。'
    messages.value.push({
      role: 'assistant',
      error: true,
      content: aborted ? '请求超时（90 秒）。请检查网络或稍后重试。' : '请求失败：' + msg + hint
    })
  } finally {
    clearTimeout(timer)
    loading.value = false
  }
}

export function useAi() {
  loadConfig()
  return { config, messages, loading, saveConfig, ask }
}
