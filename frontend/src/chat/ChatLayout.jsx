import { Button, Loading } from 'tdesign-react'
import ChatIcon from './ChatIcon'
import ChatMessage from './ChatMessage'
import { QUICK_PROMPTS } from './messages'
import { MAX_CHAT_MESSAGE_LENGTH } from '../validation/requests'

// 页面布局不执行请求。Chat.jsx 负责恢复/订阅，消息组件只负责展示和用户操作。
export default function ChatLayout({ user, messages, view, scrollRef, bottomRef, composerRef, actions }) {
  const clarification = view.pendingInteraction?.clarification_type === 'FREE_TEXT'
  const active = view.sending || view.streaming
  const progressLabel = view.connection === 'reconnecting'
    ? '正在恢复进度连接，任务继续执行中'
    : view.progress.at(-1)?.message || '任务已提交，等待执行'
  const composerAction = clarification ? () => actions.clarify(view.pendingInteraction) : actions.send
  return (
    <div className="chat-app">
      <aside className="chat-sidebar">
        <a className="chat-brand" href="/chat" aria-label="关系助手首页">
          <span className="chat-brand-icon"><ChatIcon size={24} /></span>
          <span><strong>关系助手</strong><small>让每一次连接更有温度</small></span>
        </a>
        <div className="chat-sidebar-label">我的工作空间</div>
        <div className="chat-session"><ChatIcon name="chat" /><span>日常关系对话<small>默认会话 · 历史自动保存</small></span>
          <span className="chat-session-dot" />
        </div>
        <div className="chat-sidebar-label chat-sidebar-label--prompts">快捷提问</div>
        <div className="chat-sidebar-prompts">
          {QUICK_PROMPTS.map((prompt) => <button key={prompt.title} onClick={() => actions.prompt(prompt.text)}
            disabled={view.busy || Boolean(view.pendingRequest)}>
            <ChatIcon name={prompt.icon} size={18} /><span>{prompt.title}</span><span className="chat-prompt-arrow">↗</span>
          </button>)}
        </div>
        <div className="chat-sidebar-note"><ChatIcon name="history" size={19} />
          <strong>对话不会因刷新丢失</strong><p>历史问答会保存在你的账号下，随时回来继续梳理。</p>
        </div>
        <div className="chat-account">
          <div className="chat-account-avatar">{(user.user_name || user.phone || '我').slice(0, 1)}</div>
          <div><strong>{user.user_name || '我的账号'}</strong><small>{user.phone || '已登录'}</small></div>
          <button className="chat-icon-button" onClick={actions.logout} aria-label="退出登录" title="退出登录">
            <ChatIcon name="logout" size={18} />
          </button>
        </div>
      </aside>
      <section className="chat-workspace">
        <header className="chat-header">
          <div><div className="chat-header-eyebrow">RELATIONSHIP ASSISTANT</div>
            <h1>人际关系助手</h1><p>记住重要的人，理清彼此的关系。</p>
          </div>
          <div className="chat-header-right">
            <span className={`chat-connection ${view.connection === 'reconnecting' ? 'chat-connection--waiting' : ''}`}>
              <i />{view.streaming ? (view.connection === 'reconnecting' ? '连接恢复中' : '正在处理') : '智能对话'}
            </span>
            <button className="chat-mobile-logout" onClick={actions.logout} aria-label="退出登录"><ChatIcon name="logout" size={18} /></button>
          </div>
        </header>
        <main className="chat-scroll" ref={scrollRef} onScroll={actions.scroll} aria-label="聊天记录">
          <div className="chat-timeline">
            {view.historyLoading && <div className="chat-history-loading"><Loading size="small" text="正在加载你的聊天记录…" /></div>}
            {view.historyError && <div className="chat-history-error" role="alert">
              <ChatIcon name="alert" size={17} /><span>{view.historyError}</span>
              <Button variant="text" size="small" disabled={view.busy} onClick={actions.reloadHistory}>重新加载</Button>
            </div>}
            {!view.historyLoading && view.historyPage.hasMore && <div className="chat-history-more">
              <Button variant="text" size="small" loading={view.loadingOlder} onClick={actions.loadOlder}>
                <ChatIcon name="history" size={14} />&nbsp;加载更早的消息
              </Button>
            </div>}
            {!view.historyLoading && messages.length > 0 && <div className="chat-history-divider">
              <span>{view.historyPage.hasMore ? '最近的对话' : '在这里，整理你的关系与记忆'}</span>
            </div>}
            {!view.historyLoading && messages.length === 0 && !view.historyError && <div className="chat-welcome">
              <div className="chat-welcome-icon"><ChatIcon size={32} /></div>
              <span className="chat-welcome-eyebrow">你的关系与记忆，都有迹可循</span>
              <h2>从一个人，开始一段对话。</h2>
              <p>告诉我你认识的人，了解他们的资料，<br />或一起梳理你们之间的关系。</p>
              <div className="chat-welcome-prompts">{QUICK_PROMPTS.map((prompt) => <button key={prompt.title}
                onClick={() => actions.prompt(prompt.text)} disabled={view.busy}>
                <ChatIcon name={prompt.icon} size={21} /><strong>{prompt.title}</strong><span>{prompt.text}</span>
                <span className="chat-welcome-arrow">↗</span>
              </button>)}</div>
            </div>}
            <div role="log" aria-label="对话消息">
              {messages.map((message) => <ChatMessage key={message.key} message={message} busy={view.busy} actions={actions} />)}
            </div>
            {(active || view.progress.length > 0) && <div className={`chat-progress ${active ? 'chat-progress--active' : ''}`}>
              <div className="chat-progress-heading">
                {active ? <span className="chat-spinner" /> : <ChatIcon name="check" size={16} />}
                <span>{active ? progressLabel : '本轮执行记录'}</span>
                {view.streaming && <button onClick={actions.stop}>停止</button>}
              </div>
              {view.progress.length > 0 && <details><summary>查看执行步骤 · {view.progress.length}</summary>
                <ol>{view.progress.map((item) => <li key={item.seq}>{item.message}</li>)}</ol>
              </details>}
            </div>}
            {view.draft?.text && <ChatMessage message={{ role: 'assistant', content: view.draft.text,
              key: view.draft.id }} busy={true} actions={actions} />}
            <div ref={bottomRef} className="chat-bottom-anchor" />
          </div>
        </main>
        {view.awayFromBottom && <button className="chat-jump" onClick={actions.latest}>
          <ChatIcon name="arrow" size={15} />回到最新消息
        </button>}
        <footer className="chat-composer-area">
          <div className="chat-composer-container">
            {view.pendingRequest && !active && !view.historyLoading && <div className="chat-composer-notice">
              <ChatIcon name="chat" size={15} />{clarification ? '助手需要更多信息，请补充后继续。'
                : view.pendingInteraction ? '请先回应上方的确认或选择，再开启新问题。' : '还有一项待处理任务，请查询状态、继续或结束它。'}
            </div>}
            <div className={`chat-composer ${view.busy ? 'chat-composer--busy' : ''}`}>
              <textarea ref={composerRef} value={view.input} onChange={(event) => actions.input(event.target.value)}
                aria-label={clarification ? '补充信息' : '输入消息'} maxLength={MAX_CHAT_MESSAGE_LENGTH}
                placeholder={clarification ? '补充这次任务需要的信息…' : '聊聊你认识的人，或问一个关于关系的问题…'}
                disabled={view.busy} rows={2} onKeyDown={(event) => {
                  // 中文输入法的确认回车不能误发消息；Shift + Enter 留作换行。
                  if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing && event.keyCode !== 229) {
                    event.preventDefault()
                    if (!view.busy && (!view.pendingRequest || clarification)) composerAction()
                  }
                }} />
              <div className="chat-composer-bottom"><span className="chat-input-hint">Enter 发送 · Shift + Enter 换行</span>
                <div><span className="chat-input-count">{view.input.length} / {MAX_CHAT_MESSAGE_LENGTH}</span>
                  <Button theme="primary" onClick={composerAction} loading={view.sending}
                    disabled={view.busy || !view.input.trim() || (Boolean(view.pendingRequest) && !clarification)}>
                    {clarification ? '提交补充信息' : '发送'}&nbsp;<ChatIcon name="send" size={15} />
                  </Button>
                </div>
              </div>
            </div>
            <p className="chat-composer-footnote">重要关系信息，请结合实际情况核实。需要执行的操作会在对话中说明。</p>
          </div>
        </footer>
      </section>
    </div>
  )
}
