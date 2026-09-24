import api from './client'
import { buildChatPayload } from '../validation/requests'

export const createRequestId = () => crypto.randomUUID()

export const sendMessage = (message, requestId) =>
  api.post('/chat', buildChatPayload(message), {
    headers: { 'Idempotency-Key': requestId },
    timeout: 60_000,
  })

export const getRequestStatus = (requestId) =>
  api.get(`/chat/requests/${encodeURIComponent(requestId)}`)

// 恢复 LangGraph interrupt。payload 三选一：
// { confirmed: boolean } / { candidate_id: string } / { answer: string }
export const resumeInteraction = (interruptId, payload) =>
  api.post('/chat/resume', {
    interrupt_id: interruptId,
    ...payload,
  }, { timeout: 60_000 })
