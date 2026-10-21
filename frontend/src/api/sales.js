// API 只携带业务字段；账号身份由已有 Axios Bearer 鉴权链路提供。
import api from './client'

export const getSalesCapabilities = (signal) => api.get('/sales/capabilities', { signal })
export const searchSalesCustomers = (query, signal) => api.get('/sales/customers', { params: { query }, signal })
export const importCustomerChat = (personId, payload) =>
  api.post('/sales/chat-imports', { person_id: personId, payload })
export const getSalesEvidence = (messageId, signal) =>
  api.get(`/sales/messages/${encodeURIComponent(messageId)}`, { signal })
