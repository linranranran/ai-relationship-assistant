import { Button, MessagePlugin } from 'tdesign-react'
import ChatIcon from './ChatIcon'

const timeLabel = (value) => value && !Number.isNaN(Date.parse(value))
  ? new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' }).format(new Date(value))
  : ''

export default function ChatMessage({ message: msg, busy, actions }) {
  const isUser = msg.role === 'user'
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(msg.content)
      MessagePlugin.success('已复制回复')
    } catch { MessagePlugin.warning('暂时无法复制，请选中文字复制') }
  }
  return (
    <article className={`chat-message ${isUser ? 'chat-message--user' : 'chat-message--assistant'}`}>
      <div className={`chat-avatar ${isUser ? 'chat-avatar--user' : ''}`}>
        <ChatIcon name={isUser ? 'person' : 'sparkle'} size={18} />
      </div>
      <div className="chat-message-body">
        <div className="chat-message-meta">
          <span>{isUser ? '你' : '关系助手'}</span><time dateTime={msg.createdAt}>{timeLabel(msg.createdAt)}</time>
          {msg.interaction && <span className="chat-pill">需要你的回应</span>}
        </div>
        <div className={`chat-bubble ${msg.retry || msg.statusCheck ? 'chat-bubble--notice' : ''}`}>
          <div className="chat-message-text">{msg.content}</div>
          {(msg.retry || msg.statusCheck) && <div className="chat-message-actions">
            {msg.retry && <Button size="small" variant="outline" disabled={busy}
              onClick={() => actions.retry(msg.retry)}>继续原任务</Button>}
            <Button size="small" variant="text" disabled={busy}
              onClick={() => actions.check(msg.retry || msg.statusCheck)}>查询当前状态</Button>
            <Button size="small" variant="text" disabled={busy}
              onClick={actions.stop}>结束当前任务</Button>
          </div>}
          {msg.interaction && <div className="chat-interaction">
            {msg.interaction.tool_names && <>
              <p className="chat-help">确认后，助手将执行这项操作。</p>
              <Button size="small" theme="primary" disabled={busy}
                onClick={() => actions.resume(msg.interaction, { confirmed: true }, '确认执行')}>确认执行</Button>
            </>}
            {msg.interaction.clarification_type === 'CANDIDATE_SELECTION' && <div className="chat-candidates">
              {msg.interaction.candidates.map((candidate) => <button key={candidate.id}
                className="chat-candidate" disabled={busy} onClick={() => actions.resume(
                  msg.interaction, { candidate_id: candidate.id }, `选择：${candidate.label}`)}>
                <ChatIcon name="person" size={17} />
                <span><strong>{candidate.label}</strong>{candidate.description && <small>{candidate.description}</small>}</span>
              </button>)}
            </div>}
            {msg.interaction.clarification_type === 'FREE_TEXT' && <p className="chat-help">
              请在下方输入补充信息，再点击「提交补充信息」。
            </p>}
            <Button size="small" variant="text" disabled={busy}
              onClick={() => actions.cancel(msg.interaction)}>取消当前任务</Button>
          </div>}
        </div>
        {!isUser && !msg.interaction && !msg.retry && !msg.statusCheck && <button
          type="button" className="chat-copy" onClick={copy} aria-label="复制回复">
          <ChatIcon name="copy" size={13} />复制
        </button>}
      </div>
    </article>
  )
}
