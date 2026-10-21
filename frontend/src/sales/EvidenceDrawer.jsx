import { useEffect, useState } from 'react'
import { Drawer, Loading } from 'tdesign-react'
import { getSalesEvidence } from '../api/sales'
import { salesTime } from './format'

export default function EvidenceDrawer({ evidence, onClose }) {
  const [message, setMessage] = useState(null)
  const [error, setError] = useState('')
  // 父级按来源+引用挂载新抽屉，避免 effect 同步重置造成额外渲染/旧原文闪现。
  const [loading, setLoading] = useState(evidence.source.source_type === 'simulated_chat')

  useEffect(() => {
    if (evidence.source.source_type === 'profile_snapshot') return undefined
    const controller = new AbortController()
    getSalesEvidence(evidence.source.message_id, controller.signal).then(({ data }) => {
      // API 已检查 owner；再核对人物与消息，防止过期引用/异步响应展示错对象。
      if (data.message_id !== evidence.source.message_id || data.person_id !== evidence.personId) {
        throw new Error('来源与当前准备对象不一致')
      }
      if (!controller.signal.aborted) setMessage(data)
    }).catch((reason) => {
      if (!controller.signal.aborted) setError(reason.response?.data?.detail || '来源暂不可用，请稍后重试')
    }).finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [evidence])

  const source = evidence?.source
  const profile = source?.source_type === 'profile_snapshot'
  return <Drawer visible={Boolean(evidence)} onClose={onClose} header={profile ? '客户档案快照' : '模拟聊天原文'}
    size="min(480px, 100vw)" footer={false} closeOnEscKeydown closeOnOverlayClick destroyOnClose>
    {source && <div className="sales-evidence">
      <span className="sales-badge">{profile ? '档案快照' : '模拟数据'}</span>
      <p>{profile ? '取证时的档案' : message?.speaker === 'sales' ? '销售发言' : '客户发言'} · {salesTime(source.occurred_at)}</p>
      <h4>本条引用</h4><blockquote>{evidence.quote}</blockquote>
      {loading && <Loading text="读取原文…" size="small" />}
      {error && <p role="alert">{error}</p>}
      {!loading && !error && <><h4>{profile ? '已保存的档案快照' : '完整消息原文'}</h4>
        {/* 用 React 文本转义，原文里的 HTML/脚本不能成为可执行页面内容。 */}
        <pre>{profile ? source.excerpt : message?.text}</pre></>}
      {source.truncated && <p>生成时只提供了裁剪片段；本条引用只依据当时可见内容。</p>}
    </div>}
  </Drawer>
}
