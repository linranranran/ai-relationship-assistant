import { useState, useRef, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, Input, Loading, MessagePlugin } from 'tdesign-react'
import { sendMessage } from '../api/chat'

export default function Chat() {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const bottomRef = useRef(null)
  const navigate = useNavigate()

  const user = JSON.parse(localStorage.getItem('user') || '{}')

  // 自动滚动到底部
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const handleSend = async () => {
    const text = input.trim()
    if (!text) return

    // 显示用户消息
    setMessages((prev) => [...prev, { role: 'user', content: text }])
    setInput('')
    setSending(true)

    try {
      const res = await sendMessage(text)
      setMessages((prev) => [...prev, { role: 'assistant', content: res.data.response }])
    } catch (err) {
      MessagePlugin.error(err.response?.data?.detail || '发送失败')
    } finally {
      setSending(false)
    }
  }

  const handleLogout = () => {
    localStorage.removeItem('token')
    localStorage.removeItem('user')
    navigate('/login')
  }

  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column', background: '#f5f5f5' }}>
      {/* 顶栏 */}
      <header style={{
        padding: '12px 16px', background: '#0052d9', color: '#fff',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      }}>
        <span style={{ fontWeight: 600 }}>AI 人际关系助手</span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span style={{ fontSize: 13, opacity: 0.85 }}>{user.phone}</span>
          <Button variant="text" style={{ color: '#fff' }} onClick={handleLogout}>
            退出
          </Button>
        </div>
      </header>

      {/* 消息列表 */}
      <main style={{ flex: 1, overflowY: 'auto', padding: 16 }}>
        {messages.length === 0 && (
          <div style={{ textAlign: 'center', color: '#999', marginTop: 120 }}>
            <p style={{ fontSize: 18, marginBottom: 8 }}>👋 你好！</p>
            <p>试试说：我认识了新朋友张三 / 查一下李四是谁 / 我和张三什么关系</p>
          </div>
        )}
        {messages.map((msg, i) => (
          <div key={i} style={{
            display: 'flex', marginBottom: 16,
            justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start',
          }}>
            {msg.role === 'assistant' && (
              <span style={{ marginRight: 8, flexShrink: 0, fontSize: 20 }}>🤖</span>
            )}
            <div style={{
              maxWidth: '70%', padding: '10px 16px', borderRadius: 12,
              background: msg.role === 'user' ? '#0052d9' : '#fff',
              color: msg.role === 'user' ? '#fff' : '#333',
              boxShadow: '0 1px 3px rgba(0,0,0,0.06)',
              whiteSpace: 'pre-wrap', wordBreak: 'break-word',
            }}>
              {msg.content}
            </div>
            {msg.role === 'user' && (
              <Avatar size="small" style={{ marginLeft: 8, flexShrink: 0 }}>👤</Avatar>
            )}
          </div>
        ))}
        {sending && (
          <div style={{ display: 'flex', marginBottom: 16 }}>
            <Avatar size="small" style={{ marginRight: 8 }}>🤖</Avatar>
            <div style={{ padding: '10px 16px', borderRadius: 12, background: '#fff' }}>
              <Loading size="small" text="思考中..." />
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </main>

      {/* 输入框 */}
      <footer style={{
        padding: 12, background: '#fff', borderTop: '1px solid #e7e7e7',
        display: 'flex', gap: 8,
      }}>
        <Input
          value={input}
          onChange={setInput}
          onEnter={handleSend}
          placeholder="输入消息，按 Enter 发送..."
          style={{ flex: 1 }}
        />
        <Button
          theme="primary"
          icon={<SendIcon />}
          onClick={handleSend}
          loading={sending}
        >
          发送
        </Button>
      </footer>
    </div>
  )
}