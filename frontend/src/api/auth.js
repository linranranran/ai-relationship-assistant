import api from './client'
import { buildAuthPayload } from '../validation/requests'

export const register = (phone_number, password) =>
  api.post('/auth/register', buildAuthPayload(phone_number, password))

export const login = (phone_number, password) =>
  api.post('/auth/login', buildAuthPayload(phone_number, password))

export const getMe = () => api.get('/auth/me')
