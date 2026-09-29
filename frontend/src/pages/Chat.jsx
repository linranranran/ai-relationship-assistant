import { useState, useRef, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, Input, Loading, MessagePlugin } from 'tdesign-react'
import {
  cancelInteraction,
  createRequestId,
  getRequestStatus,
  resumeInteraction,
  sendMessage,
} from '../api/chat'
import { MAX_CHAT_MESSAGE_LENGTH, validateChatMessage } from '../validation/requests'

const readPendingRequest = (storageKey) => {
  const rawPending = localStorage.getItem(storageKey)
  if (!rawPending) return null
  try {
    const pending = JSON.parse(rawPending)
    if (typeof pending.requestId === 'string' && pending.requestId
      && typeof pending.text === 'string' && pending.text) return pending
  } catch {
    // 本地缓存损坏时丢弃旧请求，避免下一次输入继续走错误的恢复流程。
  }
  localStorage.removeItem(storageKey)
  return null
}

const clearInteractions = (messages) =>
  messages.map((message) => ({ ...message, interaction: undefined }))

const clearPendingPrompts = (messages) =>
  clearInteractions(messages.filter((message) => !message.retry && !message.statusCheck))

const loadPendingMessages = (storageKey) => {
  const pending = readPendingRequest(storageKey)
  if (!pending) return []
  return [
    { role: 'user', content: pending.text },
    pending.interaction
      ? {
          role: 'assistant',
          content: pending.response,
          interaction: pending.interaction,
        }
      : pending.statusCheck
        ? {
            role: 'assistant',
            content: '恢复请求可能仍在后端执行，请查询当前状态。',
            statusCheck: pending,
          }
      : {
          role: 'assistant',
          content: '上一次请求没有收到最终结果，可以使用原请求 ID 查询或重试。',
          retry: pending,
        },
  ]
}

export default function Chat() {
  const navigate = useNavigate()
  const user = JSON.parse(localStorage.getItem('user') || '{}')
  const pendingStorageKey = `agent_pending_request:${user.user_id || user.phone || 'current'}`
  const [messages, setMessages] = useState(() => loadPendingMessages(pendingStorageKey))
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const bottomRef = useRef(null)
  const pendingInteraction = readPendingRequest(pendingStorageKey)?.interaction

  // 自动滚动到底部
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const appendAgentResponse = (data, pending) => {
    const interaction = data.confirmation || data.clarification
    if (interaction) {
      localStorage.setItem(pendingStorageKey, JSON.stringify({
        ...pending,
        response: data.response,
        interaction,
      }))
      setMessages((prev) => [
        ...clearPendingPrompts(prev),
        { role: 'assistant', content: data.response, interaction },
      ])
      return
    }

    if (data.status === 'RUNNING') {
      localStorage.setItem(pendingStorageKey, JSON.stringify({
        text: pending.text,
        requestId: pending.requestId,
        statusCheck: true,
      }))
      setMessages((prev) => [
        ...clearPendingPrompts(prev),
        { role: 'assistant', content: data.response, statusCheck: pending },
      ])
      return
    }

    localStorage.removeItem(pendingStorageKey)
    setMessages((prev) => [
      ...clearPendingPrompts(prev),
      { role: 'assistant', content: data.response },
    ])
  }

  const showStatusCheck = (pending, message) => {
    // HTTP 超时不代表 LangGraph 恢复失败。先保留 request_id，停止使用旧
    // interrupt_id；后端完成后可通过 GET /requests/{request_id} 找回新交互。
    localStorage.setItem(pendingStorageKey, JSON.stringify({
      text: pending.text,
      requestId: pending.requestId,
      statusCheck: true,
    }))
    setMessages((prev) => [
      ...clearPendingPrompts(prev),
      { role: 'assistant', content: message, statusCheck: pending },
    ])
  }

  const submitRequest = async (text, requestId, appendUserMessage) => {
    if (sending) return
    const pending = { text, requestId }
    localStorage.setItem(pendingStorageKey, JSON.stringify(pending))
    setMessages((prev) => {
      const withoutRetryPrompt = clearPendingPrompts(prev)
      return appendUserMessage
        ? [...withoutRetryPrompt, { role: 'user', content: text }]
        : withoutRetryPrompt
    })
    setSending(true)

    try {
      const res = await sendMessage(text, requestId)
      appendAgentResponse(res.data, pending)
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
    if (readPendingRequest(pendingStorageKey)?.interaction) {
      MessagePlugin.warning('当前任务正在等待交互，请先补充、选择或取消当前任务。')
      return
    }
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

  const handleSubmitClarification = async (interaction) => {
    if (sending) return
    if (readPendingRequest(pendingStorageKey)?.interaction?.interrupt_id
      !== interaction.interrupt_id) {
      MessagePlugin.warning('补充信息已失效，请查询最新状态。')
      return
    }
    const validation = validateChatMessage(input)
    if (!validation.valid) {
      MessagePlugin.warning(validation.error)
      return
    }
    const text = input.trim()
    setInput('')
    await submitResume(interaction, { answer: text }, text)
  }

  const handleRetry = async (pending) => {
    await submitRequest(pending.text, pending.requestId, false)
  }

  const handleCheckStatus = async (pending) => {
    if (sending) return
    setSending(true)
    try {
      const res = await getRequestStatus(pending.requestId)
      appendAgentResponse(res.data, pending)
    } catch (err) {
      const detail = err.response?.data?.detail || '暂时无法查询任务状态'
      showStatusCheck(pending, `${detail}。后端恢复后可再次查询。`)
      MessagePlugin.error(detail)
    } finally {
      setSending(false)
    }
  }

  const submitResume = async (interaction, payload, visibleAnswer) => {
    if (sending) return
    const pending = readPendingRequest(pendingStorageKey)
    if (pending?.interaction?.interrupt_id !== interaction.interrupt_id) {
      setMessages((prev) => [
        ...clearInteractions(prev),
        { role: 'assistant', content: '交互信息已失效，请直接输入新问题。' },
      ])
      return
    }
    setSending(true)
    if (visibleAnswer) {
      setMessages((prev) => [
        ...clearInteractions(prev),
        { role: 'user', content: visibleAnswer },
      ])
    } else {
      setMessages((prev) => clearInteractions(prev))
    }

    try {
      const res = await resumeInteraction(interaction.interrupt_id, payload)
      appendAgentResponse(res.data, pending)
    } catch (err) {
      const detail = err.response?.data?.detail || '未收到恢复请求的响应'
      const expired = err.response?.status === 409
        && detail === '交互信息已失效，请重新发起操作'
      try {
        const statusRes = await getRequestStatus(pending.requestId)
        const latest = statusRes.data
        const latestInteraction = latest.confirmation || latest.clarification
        // 409 后若数据库仍保存同一个旧中断，不能再次把失效按钮展示出来。
        if (!expired || latestInteraction?.interrupt_id !== interaction.interrupt_id) {
          appendAgentResponse(latest, pending)
          return
        }
      } catch {
        // 状态服务也不可用时，保留 request_id 供稍后查询。
      }
      if (expired) {
        localStorage.removeItem(pendingStorageKey)
        // 自由文本可能本来就是一个新问题；还给输入框，由用户决定是否发送。
        if (typeof payload.answer === 'string') setInput(payload.answer)
      }
      if (expired) {
        setMessages((prev) => [
          ...clearPendingPrompts(prev),
          { role: 'assistant', content: `${detail}。请直接输入新问题。` },
        ])
      } else {
        showStatusCheck(pending, `${detail}。可能仍在后端执行，请查询当前状态。`)
      }
      MessagePlugin.error(detail)
    } finally {
      setSending(false)
    }
  }

  const submitCancel = async (interaction) => {
    if (sending) return
    const pending = readPendingRequest(pendingStorageKey)
    if (pending?.interaction?.interrupt_id !== interaction.interrupt_id) {
      MessagePlugin.warning('这条交互已不是当前任务，请查询最新状态。')
      return
    }
    setSending(true)
    try {
      const res = await cancelInteraction(interaction.interrupt_id)
      appendAgentResponse(res.data, pending)
    } catch (err) {
      const detail = err.response?.data?.detail || '未收到取消任务的响应'
      try {
        const statusRes = await getRequestStatus(pending.requestId)
        const latest = statusRes.data
        const latestInteraction = latest.confirmation || latest.clarification
        // 检查点已失效而数据库仍留着旧交互时，解除本地卡住的输入框。
        if (err.response?.status !== 409
          || latestInteraction?.interrupt_id !== interaction.interrupt_id) {
          appendAgentResponse(latest, pending)
          return
        }
      } catch {
        // 状态查询不可用时，保留 request_id，供稍后再查。
      }
      if (err.response?.status === 409) {
        localStorage.removeItem(pendingStorageKey)
        setMessages((prev) => [
          ...clearPendingPrompts(prev),
          { role: 'assistant', content: `${detail}。请重新发起新问题。` },
        ])
      } else {
        showStatusCheck(pending, `${detail}。请查询当前状态，确认取消是否完成。`)
      }
      MessagePlugin.error(detail)
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
              {msg.statusCheck && (
                <div style={{ marginTop: 10 }}>
                  <Button
                    size="small"
                    variant="outline"
                    disabled={sending}
                    onClick={() => handleCheckStatus(msg.statusCheck)}
                  >
                    查询当前状态
                  </Button>
                </div>
              )}
              {msg.interaction?.tool_names && (
                <div style={{ marginTop: 10, display: 'flex', gap: 8 }}>
                  <Button
                    size="small"
                    theme="primary"
                    disabled={sending}
                    onClick={() => submitResume(msg.interaction, { confirmed: true }, '确认执行')}
                  >
                    确认
                  </Button>
                </div>
              )}
              {msg.interaction?.clarification_type === 'CANDIDATE_SELECTION' && (
                <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {msg.interaction.candidates.map((candidate) => (
                    <Button
                      key={candidate.id}
                      size="small"
                      variant="outline"
                      disabled={sending}
                      onClick={() => submitResume(
                        msg.interaction,
                        { candidate_id: candidate.id },
                        `选择：${candidate.label}`,
                      )}
                    >
                      {candidate.label}{candidate.description ? ` · ${candidate.description}` : ''}
                    </Button>
                  ))}
                </div>
              )}
              {msg.interaction?.clarification_type === 'FREE_TEXT' && (
                <div style={{ marginTop: 8 }}>
                  <div style={{ marginBottom: 8, fontSize: 12, color: '#777' }}>
                    在下方输入补充信息，然后明确点击“提交补充信息”。
                  </div>
                  <Button
                    size="small"
                    theme="primary"
                    disabled={sending}
                    onClick={() => handleSubmitClarification(msg.interaction)}
                  >
                    提交补充信息
                  </Button>
                </div>
              )}
              {msg.interaction && (
                <div style={{ marginTop: 10 }}>
                  <Button
                    size="small"
                    variant="outline"
                    disabled={sending}
                    onClick={() => submitCancel(msg.interaction)}
                  >
                    取消当前任务
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
          disabled={sending || Boolean(pendingInteraction)}
        >
          发送新问题
        </Button>
      </footer>
    </div>
  )
}
