import api from './client'

export const sendMessage = (message) =>
  api.post('/chat', { message })