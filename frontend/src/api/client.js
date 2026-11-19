/**
 * 后端 API 客户端（唯一出口）。
 *
 * 约定：
 * 1. 所有请求都走相对路径 `/api/...`：开发态由 Vite proxy 转发，生产态与静态站点同源；
 * 2. 错误统一抛 `ApiError`（带 `status` / `code`），页面只需展示 `error.message`；
 * 3. 字段名与 `docs/API.md` 完全一致，前端不做任何改名，避免「两套契约」。
 */

const BASE = ''

export class ApiError extends Error {
  constructor(message, { status = 0, code = '' } = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

async function readError(response) {
  let detail = null
  try {
    detail = await response.json()
  } catch {
    return { message: `请求失败（HTTP ${response.status}）`, code: '' }
  }
  // 后端统一为 { detail: { code, message } }；422 是 FastAPI 校验错误数组
  const payload = detail?.detail
  if (payload && typeof payload === 'object' && !Array.isArray(payload)) {
    return { message: payload.message || `请求失败（HTTP ${response.status}）`, code: payload.code || '' }
  }
  if (Array.isArray(payload)) {
    const first = payload[0]
    const where = Array.isArray(first?.loc) ? first.loc.filter((part) => part !== 'body').join('.') : ''
    return {
      message: where ? `${where}：${first?.msg ?? '参数不合法'}` : first?.msg ?? '参数不合法',
      code: 'VALIDATION_ERROR',
    }
  }
  return { message: `请求失败（HTTP ${response.status}）`, code: '' }
}

async function request(path, { method = 'GET', body, signal } = {}) {
  let response
  try {
    response = await fetch(`${BASE}${path}`, {
      method,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    })
  } catch (error) {
    if (error?.name === 'AbortError') throw error
    throw new ApiError('无法连接后端服务，请确认服务已启动', { code: 'NETWORK_ERROR' })
  }

  if (!response.ok) {
    const { message, code } = await readError(response)
    throw new ApiError(message, { status: response.status, code })
  }
  if (response.status === 204) return null
  return response.json()
}

/** 首页 / 回看列表：`status` 可选 created / live / ended */
export function listSessions({ status, limit = 50, offset = 0 } = {}) {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  if (status) params.set('status', status)
  return request(`/api/sessions?${params.toString()}`)
}

/** 发起讨论：建会话 + 服务端生成阵容，返回 SessionDetail（此时尚未开演） */
export function createSession({ topic, expert_count }) {
  return request('/api/sessions', { method: 'POST', body: { topic, expert_count } })
}

/** 单场详情（刷新后恢复演播厅用） */
export function getSession(sessionId) {
  return request(`/api/sessions/${encodeURIComponent(sessionId)}`)
}

/** 全量发言（按 round 升序） */
export function listMessages(sessionId, { afterRound } = {}) {
  const query = afterRound === undefined ? '' : `?after_round=${afterRound}`
  return request(`/api/sessions/${encodeURIComponent(sessionId)}/messages${query}`)
}

/** 共识 / 分歧整组快照 */
export function listInsights(sessionId) {
  return request(`/api/sessions/${encodeURIComponent(sessionId)}/insights`)
}

/** SSE 订阅地址（EventSource 会用 `Last-Event-ID` 请求头自动续传） */
export function streamUrl(sessionId) {
  return `${BASE}/api/stream/${encodeURIComponent(sessionId)}`
}
