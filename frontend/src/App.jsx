import { Routes, Route, Navigate } from 'react-router-dom'
import Login from './pages/Login'
import Chat from './pages/Chat'

function App() {
  const token = localStorage.getItem('token')

  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/chat" element={token ? <Chat /> : <Navigate to="/login" />} />
      <Route path="*" element={<Navigate to={token ? '/chat' : '/login'} />} />
    </Routes>
  )
}

export default App