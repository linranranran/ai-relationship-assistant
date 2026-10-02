import { useState, useRef, useEffect, useLayoutEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { MessagePlugin } from 'tdesign-react'
import {
  cancelInteraction,
  createRequestId,
  getRequestStatus,
  getChatHistory,
  resumeInteraction,
  sendMessage,
  subscribeRequest,
  stopRequest,
} from '../api/chat'
import { validateChatMessage } from '../validation/requests'
import { historyMessages, makeMessage, mergeMessages } from '../chat/messages'
import ChatLayout from '../chat/ChatLayout'
import './Chat.css'

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
    makeMessage('user', pending.text, pending.requestId),
    pending.interaction
      ? makeMessage('assistant', pending.response, pending.requestId, {
          key: `${pending.requestId}:interaction:${pending.interaction.interrupt_id}`,
          interaction: pending.interaction,
        })
      : pending.statusCheck
        ? makeMessage('assistant', '正在找回上次任务的最新状态…', pending.requestId, {
            key: `${pending.requestId}:pending`,
            statusCheck: pending,
          })
      : makeMessage('assistant', '上次任务尚未完成，可以查询状态或继续执行。', pending.requestId, {
          key: `${pending.requestId}:pending`,
          retry: pending,
        }),
  ]
}

export default function Chat() {
  const navigate = useNavigate()
  const user = JSON.parse(localStorage.getItem('user') || '{}')
  const pendingStorageKey = `agent_pending_request:${user.user_id || user.phone || 'current'}`
  const [messages, setMessages] = useState([])
  const [historyLoading, setHistoryLoading] = useState(true)
  const [historyError, setHistoryError] = useState('')
  const [historyPage, setHistoryPage] = useState({ hasMore: false, cursor: null })
  const [loadingOlder, setLoadingOlder] = useState(false)
  const [historyReload, setHistoryReload] = useState(0)
  const [awayFromBottom, setAwayFromBottom] = useState(false)
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [streaming, setStreaming] = useState(false)
  const [connection, setConnection] = useState('connected')
  const [progress, setProgress] = useState([])
  const [draft, setDraft] = useState(null)
  const streamRef = useRef(null)
  const busy = sending || streaming || historyLoading
  const bottomRef = useRef(null)
  const scrollRef = useRef(null)
  const composerRef = useRef(null)
  const stickToBottom = useRef(true)
  const pageAbortRef = useRef(null)
  const prependAnchor = useRef(null)
  const pendingInteraction = readPendingRequest(pendingStorageKey)?.interaction

  // 只有用户仍在底部时才跟随增量。翻阅旧消息时，不让每个 token 把页面拉走。
  useEffect(() => {
    if (stickToBottom.current && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [messages, draft, progress, historyLoading])

  useLayoutEffect(() => {
    const anchor = prependAnchor.current
    prependAnchor.current = null
    if (anchor?.element.isConnected && scrollRef.current) {
      // 只补偿阅读锚点的位置变化。底部草稿新增高度不会改变该消息的位置，
      // 因而不会被错误地计入“历史消息高度”。在浏览器绘制前恢复，避免闪跳。
      scrollRef.current.scrollTop += anchor.element.getBoundingClientRect().top - anchor.top
    }
  }, [messages])

  const appendAgentResponse = (data, pending) => {
    const interaction = data.confirmation || data.clarification
    if (interaction) {
      localStorage.setItem(pendingStorageKey, JSON.stringify({
        ...pending,
        response: data.response,
        interaction,
      }))
      setMessages((prev) => mergeMessages(clearPendingPrompts(prev), [
        makeMessage('assistant', data.response, pending.requestId, {
          key: `${pending.requestId}:interaction:${interaction.interrupt_id}`, interaction,
        }),
      ]))
      return
    }

    if (data.status === 'RUNNING') {
      localStorage.setItem(pendingStorageKey, JSON.stringify({
        text: pending.text,
        requestId: pending.requestId,
        statusCheck: true,
      }))
      setMessages((prev) => mergeMessages(clearPendingPrompts(prev), [
        makeMessage('assistant', data.response, pending.requestId, {
          key: `${pending.requestId}:pending`, statusCheck: pending,
        }),
      ]))
      return
    }

    if (data.status === 'FAILED') {
      // failed 是可恢复状态；查询失败结果后仍保留原 request_id 和原提问。
      // 后端会从检查点继续，并读取已持久化的人工答案，不能创建一个新任务。
      const retry = { text: pending.text, requestId: pending.requestId }
      localStorage.setItem(pendingStorageKey, JSON.stringify(retry))
      setMessages((prev) => mergeMessages(clearPendingPrompts(prev), [
        makeMessage('assistant', data.response, pending.requestId, {
          key: `${pending.requestId}:pending`, retry,
        }),
      ]))
      return
    }

    localStorage.removeItem(pendingStorageKey)
    setMessages((prev) => mergeMessages(clearPendingPrompts(prev), [
      makeMessage('assistant', data.response, pending.requestId),
    ]))
  }

  const showStatusCheck = (pending, message) => {
    // HTTP 超时不代表 LangGraph 恢复失败。先保留 request_id，停止使用旧
    // interrupt_id；后端完成后可通过 GET /requests/{request_id} 找回新交互。
    localStorage.setItem(pendingStorageKey, JSON.stringify({
      text: pending.text,
      requestId: pending.requestId,
      statusCheck: true,
    }))
    setMessages((prev) => mergeMessages(clearPendingPrompts(prev), [
      makeMessage('assistant', message, pending.requestId, {
        key: `${pending.requestId}:pending`, statusCheck: pending,
      }),
    ]))
  }

  const startStream = (pending) => {
    streamRef.current?.abort()
    const controller = new AbortController()
    streamRef.current = controller
    setStreaming(true)
    setConnection('connected')
    setProgress([])
    setDraft(null)
    // 游标属于 request_id；恢复/失败重试换 run_id，但序号继续递增。
    // 新建页面订阅时草稿已清空，从事件头重建当前批次的草稿。连接内部的断线
    // 重连仍按 Last-Event-ID 补读；不能拿旧游标搭配一个空草稿继续拼接。
    const subscription = { ...pending, cursor: 0 }
    subscribeRequest(subscription, controller.signal, (event) => {
      const stored = readPendingRequest(pendingStorageKey)
      if (stored?.requestId === pending.requestId) {
        localStorage.setItem(pendingStorageKey, JSON.stringify({ ...stored, cursor: event.seq }))
      }
      if (event.type === 'request.snapshot' && event.run_id !== subscription.runId) {
        // 权威快照发现另一个页面已经启动恢复批次：切换批次并丢弃旧草稿。
        subscription.runId = event.run_id
        setDraft(null)
        setProgress([])
        if (stored?.requestId === pending.requestId) {
          localStorage.setItem(pendingStorageKey, JSON.stringify({ ...stored,
            runId: event.run_id, cursor: event.seq, interaction: undefined }))
        }
      }
      // 历史批次的终止事件不能结束刚刚提交的恢复任务。
      if (event.run_id !== subscription.runId) return false
      if (event.type === 'stage.started' || event.type.startsWith('tool.')) {
        setProgress((previous) => [...previous.slice(-19), {
          seq: event.seq, message: event.message, type: event.type,
        }])
      }
      if (event.type === 'response.started') {
        setDraft({ id: event.response_id, text: '' })
      } else if (event.type === 'response.delta') {
        // 重新生成时替换草稿；旧 response_id 的延迟事件不能污染新答案。
        setDraft((previous) => previous?.id === event.response_id
          ? { ...previous, text: previous.text + event.delta }
          : previous)
      }
      const response = event.payload?.response
      const terminal = ['request.completed', 'request.failed', 'request.cancelled', 'interaction.required'].includes(event.type)
        || (event.type === 'request.snapshot' && !['QUEUED', 'RUNNING'].includes(response?.status))
      if (terminal && response) {
        setDraft(null)
        setStreaming(false)
        appendAgentResponse(response, { ...pending, cursor: event.seq })
        controller.abort()
        return true
      }
      return false
    }, setConnection).catch(() => {
      if (controller.signal.aborted) return
      setStreaming(false)
      setDraft(null)
      // 网络故障没有证明业务失败，保留原请求，供查询/重新订阅。
      showStatusCheck(readPendingRequest(pendingStorageKey) || pending,
        '暂时收不到进度，后台任务仍可继续。请查询当前状态。')
    })
  }

  const receiveResponse = (data, pending) => {
    if (['QUEUED', 'RUNNING'].includes(data.status) && data.run_id) {
      const active = { ...pending, runId: data.run_id, interaction: undefined, statusCheck: true }
      localStorage.setItem(pendingStorageKey, JSON.stringify(active))
      setMessages((previous) => clearPendingPrompts(previous))
      startStream(active)
    } else {
      streamRef.current?.abort()
      setStreaming(false)
      setDraft(null)
      appendAgentResponse(data, pending)
    }
  }

  useEffect(() => {
    // 数据库是历史记录的来源；localStorage 只补足尚未收到受理回执的请求。
    // 先装入历史，再查询任务权威状态，最后订阅 SSE，防止历史覆盖流式新消息。
    const controller = new AbortController()
    let disposed = false
    setHistoryLoading(true)
    setStreaming(false)
    setHistoryError('')
    const initialize = async () => {
      let pending = readPendingRequest(pendingStorageKey)
      try {
        const { data } = await getChatHistory(null, controller.signal)
        if (disposed) return
        setMessages(historyMessages(data.messages))
        setHistoryPage({ hasMore: data.has_more, cursor: data.next_cursor })
        if (data.pending_request) {
          // 即使换浏览器，也能发现服务端的待处理任务，不依赖旧页面缓存。
          pending = { requestId: data.pending_request.request_id,
            text: data.pending_request.message, statusCheck: true }
          localStorage.setItem(pendingStorageKey, JSON.stringify(pending))
        }
      } catch (error) {
        if (disposed) return
        setHistoryError(error.response?.data?.detail || '历史消息暂时加载失败')
      }
      if (pending && !disposed) {
        setMessages((previous) => mergeMessages(previous, loadPendingMessages(pendingStorageKey)))
        try {
          const { data } = await getRequestStatus(pending.requestId)
          if (!disposed) receiveResponse(data, { ...pending, cursor: 0 })
        } catch (error) {
          if (disposed) return
          if (error.response?.status === 404) {
            // 服务端明确不存在的请求不能永远占住输入框；保留输入供用户重发。
            localStorage.removeItem(pendingStorageKey)
            setMessages((previous) => clearPendingPrompts(previous))
            setInput(pending.text)
          }
          // 其他网络故障仍保留查询/重试按钮，不把 HTTP 错误当作业务取消。
        }
      }
      if (!disposed) setHistoryLoading(false)
    }
    initialize()
    return () => {
      disposed = true
      controller.abort()
      pageAbortRef.current?.abort()
      streamRef.current?.abort() // 只关闭订阅，绝不取消后端任务。
    }
    // 按登录用户只恢复一次。receiveResponse 每次渲染都会重新创建，加入依赖
    // 会使每个 token 都关闭/重建连接；异步回调只调用稳定的状态 setter。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingStorageKey, historyReload])

  const loadOlder = async () => {
    if (loadingOlder || !historyPage.cursor) return
    const controller = new AbortController()
    pageAbortRef.current = controller
    stickToBottom.current = false
    setLoadingOlder(true)
    try {
      const { data } = await getChatHistory(historyPage.cursor, controller.signal)
      if (controller.signal.aborted) return
      const scroll = scrollRef.current
      if (scroll) {
        // 网络等待期间用户可能继续滚动，必须在应用数据前取当前阅读位置。
        // React 消息 key 稳定，prepend 不会卸载这条消息，可以直接保存 DOM 锚点。
        const visible = [...scroll.querySelectorAll('.chat-message')].find(
          (element) => element.getBoundingClientRect().bottom > scroll.getBoundingClientRect().top,
        ) || scroll.querySelector('.chat-progress') || bottomRef.current
        if (visible) prependAnchor.current = { element: visible, top: visible.getBoundingClientRect().top }
      }
      setMessages((previous) => mergeMessages(previous, historyMessages(data.messages), true))
      setHistoryPage({ hasMore: data.has_more, cursor: data.next_cursor })
    } catch (error) {
      if (!controller.signal.aborted) MessagePlugin.error(error.response?.data?.detail || '更早的消息加载失败，请重试')
    } finally {
      if (!controller.signal.aborted) setLoadingOlder(false)
    }
  }

  const scrollToLatest = () => {
    stickToBottom.current = true
    setAwayFromBottom(false)
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }

  const usePrompt = (text) => {
    setInput(text)
    composerRef.current?.focus()
  }

  const handleStop = async () => {
    const pending = readPendingRequest(pendingStorageKey)
    if (!pending) return
    try {
      const { data } = await stopRequest(pending.requestId)
      receiveResponse(data, pending)
      MessagePlugin.info('已请求停止后续步骤，正在等待当前操作返回。')
    } catch (error) {
      MessagePlugin.error(error.response?.data?.detail || '暂时无法停止，请查询状态')
    }
  }

  const submitRequest = async (text, requestId, appendUserMessage) => {
    if (busy) return
    const pending = { text, requestId }
    localStorage.setItem(pendingStorageKey, JSON.stringify(pending))
    stickToBottom.current = true
    setAwayFromBottom(false)
    setMessages((prev) => {
      const withoutRetryPrompt = clearPendingPrompts(prev)
      return appendUserMessage
        ? mergeMessages(withoutRetryPrompt, [makeMessage('user', text, requestId)])
        : withoutRetryPrompt
    })
    setSending(true)

    try {
      const res = await sendMessage(text, requestId)
      receiveResponse(res.data, pending)
    } catch (err) {
      const detail = err.response?.data?.detail || '请求超时或网络异常'
      if (appendUserMessage && [400, 403, 409, 422].includes(err.response?.status)) {
        // 明确拒绝受理时没有后台任务，不把一个不存在的 request_id 永久挂起。
        localStorage.removeItem(pendingStorageKey)
        setInput(text)
        setMessages((previous) => [...previous, makeMessage('assistant', detail, requestId)])
        MessagePlugin.error(detail)
        return
      }
      setMessages((prev) => mergeMessages(clearPendingPrompts(prev), [
        makeMessage('assistant', `${detail}。可以查询状态或继续原任务。`, requestId, {
          key: `${requestId}:pending`,
          retry: pending,
        }),
      ]))
      MessagePlugin.error(detail)
    } finally {
      setSending(false)
    }
  }

  const handleSend = async () => {
    if (busy) return
    if (readPendingRequest(pendingStorageKey)) {
      MessagePlugin.warning('当前任务尚未结束，请先查询、重试或处理当前交互。')
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
    if (busy) return
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
    if (busy) return
    setSending(true)
    try {
      const res = await getRequestStatus(pending.requestId)
      receiveResponse(res.data, pending)
    } catch (err) {
      const detail = err.response?.data?.detail || '暂时无法查询任务状态'
      showStatusCheck(pending, `${detail}。后端恢复后可再次查询。`)
      MessagePlugin.error(detail)
    } finally {
      setSending(false)
    }
  }

  const submitResume = async (interaction, payload, visibleAnswer) => {
    if (busy) return
    const pending = readPendingRequest(pendingStorageKey)
    if (pending?.interaction?.interrupt_id !== interaction.interrupt_id) {
      setMessages((prev) => [
        ...clearInteractions(prev),
        makeMessage('assistant', '交互信息已失效，请直接输入新问题。'),
      ])
      return
    }
    setSending(true)
    if (visibleAnswer) {
      setMessages((prev) => [
        ...clearInteractions(prev),
        makeMessage('user', visibleAnswer, pending.requestId, {
          key: `${pending.requestId}:answer:${interaction.interrupt_id}`,
        }),
      ])
    } else {
      setMessages((prev) => clearInteractions(prev))
    }

    try {
      const res = await resumeInteraction(interaction.interrupt_id, payload)
      receiveResponse(res.data, pending)
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
          receiveResponse(latest, pending)
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
          makeMessage('assistant', `${detail}。请直接输入新问题。`),
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
    if (busy) return
    const pending = readPendingRequest(pendingStorageKey)
    if (pending?.interaction?.interrupt_id !== interaction.interrupt_id) {
      MessagePlugin.warning('这条交互已不是当前任务，请查询最新状态。')
      return
    }
    setSending(true)
    try {
      const res = await cancelInteraction(interaction.interrupt_id)
      receiveResponse(res.data, pending)
    } catch (err) {
      const detail = err.response?.data?.detail || '未收到取消任务的响应'
      const expired = err.response?.status === 409
        && detail === '交互信息已失效，请重新发起操作'
      try {
        const statusRes = await getRequestStatus(pending.requestId)
        const latest = statusRes.data
        const latestInteraction = latest.confirmation || latest.clarification
        // 检查点已失效而数据库仍留着旧交互时，解除本地卡住的输入框。
        if (!expired
          || latestInteraction?.interrupt_id !== interaction.interrupt_id) {
          receiveResponse(latest, pending)
          return
        }
      } catch {
        // 状态查询不可用时，保留 request_id，供稍后再查。
      }
      if (expired) {
        localStorage.removeItem(pendingStorageKey)
        setMessages((prev) => [
          ...clearPendingPrompts(prev),
          makeMessage('assistant', `${detail}。请重新发起新问题。`),
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
    streamRef.current?.abort()
    localStorage.removeItem('token')
    localStorage.removeItem('user')
    navigate('/login')
  }

  return <ChatLayout user={user} messages={messages}
    view={{ input, busy, sending, streaming, connection, progress, draft,
      historyLoading, historyError, historyPage, loadingOlder, awayFromBottom,
      pendingInteraction, pendingRequest: readPendingRequest(pendingStorageKey) }}
    scrollRef={scrollRef} bottomRef={bottomRef} composerRef={composerRef}
    actions={{ input: setInput, send: handleSend, clarify: handleSubmitClarification,
      retry: handleRetry, check: handleCheckStatus, resume: submitResume,
      cancel: submitCancel, stop: handleStop, logout: handleLogout,
      prompt: usePrompt, loadOlder, latest: scrollToLatest,
      reloadHistory: () => setHistoryReload((version) => version + 1),
      scroll: (event) => {
        const element = event.currentTarget
        const atBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 120
        stickToBottom.current = atBottom
        setAwayFromBottom(!atBottom)
      },
    }} />
}
