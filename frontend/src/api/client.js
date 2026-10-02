import axios from 'axios'

const api = axios.create({
  // 支持部署地址和独立界面预览；默认仍连接你在 PyCharm 启动的本地后端。
  baseURL: import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api',
})

// 自动注入 token
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// 401 自动跳转登录
api.interceptors.response.use(
  (res) => res,
  (err) => {
    if (err.response?.status === 401) {
      localStorage.removeItem('token')
      localStorage.removeItem('user')
      window.location.href = '/login'
    }
    return Promise.reject(err)
  },
)

export default api
