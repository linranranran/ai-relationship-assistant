import api from './client'

export const sendMessage = (message) => {
  const user = JSON.parse(localStorage.getItem('user') || '{}')
  return api.post('/chat', {
    message,
    user_id: user.user_id,
    user_name: user.phone || '我',
  })
}