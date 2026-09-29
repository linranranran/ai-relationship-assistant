import api from './client'
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
