import api from './client'
import { fetchEventSource } from '@microsoft/fetch-event-source'
import { buildChatPayload } from '../validation/requests'

// Vite 在启动前读取 .env.development.local。0 表示关闭 Axios 的客户端超时；
// 即便如此，浏览器、代理和模型服务仍可能有各自的超时。
const timeoutMs = (raw, fallback) => {
  if (raw === undefined || raw === '') return fallback
  const parsed = Number(raw)
  return Number.isSafeInteger(parsed) && parsed >= 0 ? parsed : fallback
}

const chatTimeoutMs = timeoutMs(import.meta.env.VITE_AGENT_CHAT_TIMEOUT_MS, 60_000)
const resumeTimeoutMs = timeoutMs(
  import.meta.env.VITE_AGENT_RESUME_TIMEOUT_MS,
  chatTimeoutMs,
)

export const createRequestId = () => crypto.randomUUID()

export const sendMessage = (message, requestId) =>
  api.post('/chat', buildChatPayload(message), {
    headers: { 'Idempotency-Key': requestId },
    timeout: chatTimeoutMs,
  })

export const getRequestStatus = (requestId) =>
  api.get(`/chat/requests/${encodeURIComponent(requestId)}`, { timeout: 10_000 })

// 恢复 LangGraph interrupt。payload 三选一：
// { confirmed: boolean } / { candidate_id: string } / { answer: string }
export const resumeInteraction = (interruptId, payload) =>
  api.post('/chat/resume', {
    interrupt_id: interruptId,
    ...payload,
  }, { timeout: resumeTimeoutMs })

// 取消必须提交当前 interrupt_id，由服务端校验并结束检查点中的等待状态。
export const cancelInteraction = (interruptId) =>
  api.post('/chat/cancel', { interrupt_id: interruptId }, { timeout: resumeTimeoutMs })

export const stopRequest = (requestId) =>
  api.post(`/chat/requests/${encodeURIComponent(requestId)}/cancel`)

class FatalStreamError extends Error {}

// 复用库的 SSE 协议解析和重连；使用 Fetch 才能带现有 Bearer 请求头。
// 自动重连只重新 GET 事件，不重发 POST，所以不会重新执行工具。
export const subscribeRequest = (pending, signal, onEvent, onConnection) => {
  let failures = 0
  let terminal = false
  return fetchEventSource(
    `${api.defaults.baseURL}/chat/requests/${encodeURIComponent(pending.requestId)}/events?after=${pending.cursor || 0}`,
    {
      signal,
      openWhenHidden: true,
      headers: { Authorization: `Bearer ${localStorage.getItem('token') || ''}` },
      async onopen(response) {
        if (response.status === 401) {
          localStorage.removeItem('token')
          window.location.href = '/login'
          throw new FatalStreamError('登录已过期')
        }
        if ([404, 409, 422].includes(response.status)) {
          throw new FatalStreamError('进度订阅已失效，请查询当前状态')
        }
        if (!response.ok || !response.headers.get('content-type')?.includes('text/event-stream')) {
          throw new Error('进度连接暂不可用')
        }
        onConnection('connected')
      },
      onmessage(message) {
        if (message.event !== 'agent' || !message.data) return
        const event = JSON.parse(message.data)
        if (event.schema_version !== 1 || event.request_id !== pending.requestId) return
        // 库会更新 Last-Event-ID；应用仍按 seq 去重，避免重连把同一个 delta 拼两次。
        if (event.seq <= (pending.cursor || 0) && event.type !== 'request.snapshot') return
        pending.cursor = event.seq
        // 空快照不代表连接稳定；否则每次断线收到快照就清零，无限高速重连。
        if (event.type !== 'request.snapshot') failures = 0
        terminal = onEvent(event) === true
      },
      onclose() {
        if (!terminal) throw new Error('进度连接断开')
      },
      onerror(error) {
        if (error instanceof FatalStreamError || ++failures > 8) throw error
        onConnection('reconnecting')
        // 有上限的指数退避，避免故障期间浏览器不断请求服务器。
        return Math.min(1000 * 2 ** (failures - 1), 15000)
      },
    },
  )
}
