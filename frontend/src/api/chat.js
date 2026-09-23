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
