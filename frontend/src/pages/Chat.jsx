import { useState, useRef, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, Input, Loading, MessagePlugin } from 'tdesign-react'
import { createRequestId, sendMessage } from '../api/chat'
import { MAX_CHAT_MESSAGE_LENGTH, validateChatMessage } from '../validation/requests'

export default function Chat() {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const bottomRef = useRef(null)
  const navigate = useNavigate()

  const user = JSON.parse(localStorage.getItem('user') || '{}')
  const pendingStorageKey = `agent_pending_request:${user.user_id || user.phone || 'current'}`

  useEffect(() => {
    const rawPending = localStorage.getItem(pendingStorageKey)
    if (!rawPending) return
    try {
      const pending = JSON.parse(rawPending)
      if (pending.requestId && pending.text) {
        setMessages([
          { role: 'user', content: pending.text },
          {
            role: 'assistant',
            content: '上一次请求没有收到最终结果，可以使用原请求 ID 查询或重试。',
            retry: pending,
          },
        ])
      }
    } catch {
      localStorage.removeItem(pendingStorageKey)
    }
  }, [pendingStorageKey])

  // 自动滚动到底部
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const submitRequest = async (text, requestId, appendUserMessage) => {
    if (sending) return
    const pending = { text, requestId }
    localStorage.setItem(pendingStorageKey, JSON.stringify(pending))
    setMessages((prev) => {
      const withoutRetryPrompt = prev.filter((message) => !message.retry)
      return appendUserMessage
        ? [...withoutRetryPrompt, { role: 'user', content: text }]
        : withoutRetryPrompt
    })
    setSending(true)

    try {
      const res = await sendMessage(text, requestId)
      if (res.data.status === 'RUNNING') {
        setMessages((prev) => [
          ...prev,
          { role: 'assistant', content: res.data.response, retry: pending },
        ])
      } else {
        localStorage.removeItem(pendingStorageKey)
        setMessages((prev) => [
          ...prev,
          { role: 'assistant', content: res.data.response },
        ])
      }
    } catch (err) {
      const detail = err.response?.data?.detail || '请求超时或网络异常'
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: `${detail}。重试会继续使用原来的 request_id，不会创建新任务。`,
          retry: pending,
        },
      ])
      MessagePlugin.error(detail)
    } finally {
      setSending(false)
    }
  }

  const handleSend = async () => {
    if (sending) return
    const validation = validateChatMessage(input)
    if (!validation.valid) {
      MessagePlugin.warning(validation.error)
      return
    }
    const text = input.trim()
    const requestId = createRequestId()
    setInput('')
    await submitRequest(text, requestId, true)
  }

  const handleRetry = async (pending) => {
    await submitRequest(pending.text, pending.requestId, false)
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
              {msg.retry && (
                <div style={{ marginTop: 10 }}>
                  <Button
                    size="small"
                    variant="outline"
                    disabled={sending}
                    onClick={() => handleRetry(msg.retry)}
                  >
                    使用原请求重试
                  </Button>
                </div>
              )}
            </div>
            {msg.role === 'user' && (
              <span style={{ marginLeft: 8, flexShrink: 0, fontSize: 20 }}>👤</span>
            )}
          </div>
        ))}
        {sending && (
          <div style={{ display: 'flex', marginBottom: 16 }}>
            <span style={{ marginRight: 8, fontSize: 20 }}>🤖</span>
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
          maxLength={MAX_CHAT_MESSAGE_LENGTH}
          disabled={sending}
          style={{ flex: 1 }}
        />
        <Button
          theme="primary"
          onClick={handleSend}
          loading={sending}
          disabled={sending}
        >
          发送
        </Button>
      </footer>
    </div>
  )
}
