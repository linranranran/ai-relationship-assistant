import api from './client'

export const register = (phone_number, password) =>
  api.post('/auth/register', { phone_number, password })

export const login = (phone_number, password) =>
  api.post('/auth/login', { phone_number, password })

export const getMe = () => api.get('/auth/me')