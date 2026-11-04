#!/usr/bin/env node
/**
 * 本体指南 · LLM 本地代理（零依赖，Node 18+）
 *
 * 用途：浏览器直连大模型 API（DeepSeek / OpenAI 等）常被 CORS 拦截，报
 * "Failed to fetch"。本代理把浏览器的请求在本地转发到上游接口，绕开跨域限制：
 *
 *   浏览器页面  →  http://127.0.0.1:8788/v1/chat/completions
 *               →  上游 API（如 https://api.deepseek.com/v1/chat/completions）
 *
 * 启动：
 *   pnpm docs:proxy
 *   （可选）LLM_UPSTREAM=https://api.deepseek.com/v1  LLM_API_KEY=sk-xxx  PORT=8788
 *
 * 说明：
 * - 默认只监听 127.0.0.1，仅本机可访问；
 * - API Key 默认由页面请求头 Authorization 透传，不会写进代理；
 * - 设置 LLM_API_KEY 后，页面不填 Key 也能调用（代理兜底使用该 Key）。
 */
import http from 'node:http'

const PORT = Number(process.env.PORT || 8788)
const HOST = process.env.HOST || '127.0.0.1'
const UPSTREAM = (process.env.LLM_UPSTREAM || 'https://api.deepseek.com/v1').replace(/\/+$/, '')
const FALLBACK_KEY = process.env.LLM_API_KEY || ''
const MAX_BODY = 2 * 1024 * 1024
const TIMEOUT_MS = 120000

function setCors(res) {
  res.setHeader('Access-Control-Allow-Origin', '*')
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
  res.setHeader('Access-Control-Allow-Headers', 'Authorization, Content-Type, Accept, X-LLM-Token')
  res.setHeader('Access-Control-Max-Age', '86400')
}

function sendJson(res, status, payload) {
  setCors(res)
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' })
  res.end(JSON.stringify(payload))
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = []
    let size = 0
    req.on('data', (chunk) => {
      size += chunk.length
      if (size > MAX_BODY) {
        reject(new Error('请求体超过 2MB 上限'))
        req.destroy()
        return
      }
      chunks.push(chunk)
    })
    req.on('end', () => resolve(Buffer.concat(chunks)))
    req.on('error', reject)
  })
}

/** /v1/chat/completions → /chat/completions（上游地址通常已自带 /v1） */
function mapPath(urlPath) {
  let p = (urlPath || '/').split('?')[0]
  if (p === '/') return null
  if (p.startsWith('/v1/')) p = p.slice(3)
  return p
}

const server = http.createServer(async (req, res) => {
  setCors(res)

  if (req.method === 'OPTIONS') {
    res.writeHead(204)
    res.end()
    return
  }

  if (req.method === 'GET' && (req.url === '/' || req.url === '/health')) {
    sendJson(res, 200, {
      ok: true,
      upstream: UPSTREAM,
      hasEnvKey: !!FALLBACK_KEY,
      usage: 'POST /v1/chat/completions',
      tip: '在页面「模型配置」里把 API 地址填成 http://127.0.0.1:' + PORT + '/v1'
    })
    return
  }

  if (req.method !== 'POST') {
    sendJson(res, 405, { error: { message: '仅支持 POST /v1/chat/completions（以及 OPTIONS 预检）' } })
    return
  }

  const upstreamPath = mapPath(req.url)
  if (!upstreamPath) {
    sendJson(res, 404, { error: { message: '路径无效，请请求 /v1/chat/completions' } })
    return
  }

  try {
    const body = await readBody(req)
    const authorization =
      req.headers['authorization'] || (FALLBACK_KEY ? 'Bearer ' + FALLBACK_KEY : '')
    if (!authorization) {
      sendJson(res, 401, {
        error: {
          message:
            '缺少 Authorization：请在页面「模型配置」里填写 API Key，或以 LLM_API_KEY=sk-... 启动代理作为兜底。'
        }
      })
      return
    }

    const query = req.url.includes('?') ? '?' + req.url.split('?').slice(1).join('?') : ''
    const upstreamUrl = UPSTREAM + upstreamPath + query

    const upstreamRes = await fetch(upstreamUrl, {
      method: 'POST',
      headers: {
        'Content-Type': req.headers['content-type'] || 'application/json',
        Accept: 'application/json',
        Authorization: authorization
      },
      body,
      signal: AbortSignal.timeout(TIMEOUT_MS)
    })

    const text = await upstreamRes.text()
    res.writeHead(upstreamRes.status, {
      'Content-Type': upstreamRes.headers.get('content-type') || 'application/json; charset=utf-8'
    })
    res.end(text)
  } catch (err) {
    const isTimeout = err && err.name === 'TimeoutError'
    sendJson(res, 502, {
      error: {
        message: isTimeout
          ? '上游接口超时（120 秒无响应）'
          : '代理转发失败：' + ((err && err.message) || String(err))
      }
    })
  }
})

server.on('error', (err) => {
  if (err && err.code === 'EADDRINUSE') {
    console.error('端口 ' + PORT + ' 已被占用，请换一个端口，例如：PORT=8789 pnpm docs:proxy')
  } else {
    console.error('代理启动失败：', err)
  }
  process.exit(1)
})

server.listen(PORT, HOST, () => {
  console.log('LLM 代理已启动：http://' + HOST + ':' + PORT + '/v1  →  ' + UPSTREAM)
  console.log('在页面「模型配置」中把 API 地址改为：http://' + HOST + ':' + PORT + '/v1')
  if (!FALLBACK_KEY) {
    console.log('API Key 由页面请求头透传；也可设置环境变量 LLM_API_KEY 作为兜底。')
  }
})
