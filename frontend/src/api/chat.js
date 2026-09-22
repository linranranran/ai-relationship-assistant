import api from './client'
import { buildChatPayload } from '../validation/requests'

export const sendMessage = (message) => api.post('/chat', buildChatPayload(message))
