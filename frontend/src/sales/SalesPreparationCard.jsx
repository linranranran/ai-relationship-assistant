import { useState } from 'react'
import EvidenceDrawer from './EvidenceDrawer'
import { salesTime } from './format'
import './Sales.css'

const statusLabels = { OPEN: '记录明确未完成', DONE: '记录明确已完成', UNKNOWN: '状态未确认' }

export default function SalesPreparationCard({ result }) {
  const [evidence, setEvidence] = useState(null)
  const { brief, sources, coverage } = result
  const sourceMap = new Map(sources.map((source) => [source.source_id, source]))

  const references = (refs = []) => refs.length > 0 && <div className="sales-citations">
    {refs.map((ref, index) => <button type="button" key={`${ref.source_id}:${index}`}
      onClick={() => {
        const source = sourceMap.get(ref.source_id)
        if (source) setEvidence({ source, quote: ref.quote, personId: brief.person_id })
      }} disabled={!sourceMap.has(ref.source_id)}>查看依据 {index + 1}</button>)}
  </div>

  const section = (label, items, recommended = false) => <section className="sales-brief-section" key={label}>
    <h4>{label}{recommended && <span>沟通建议</span>}</h4>
    {items.length === 0 ? <p className="sales-empty">当前提供资料中暂无可确认内容。</p> : <ul>
      {items.map((item, index) => <li key={index}>
        <p>{item.text}</p>
        {item.completion_state && <span className={`sales-status sales-status--${item.completion_state.toLowerCase()}`}>{statusLabels[item.completion_state]}</span>}
        {references(item.evidence_refs || item.basis_refs)}
        {item.status_evidence_refs?.length > 0 && <div><small>状态依据</small>{references(item.status_evidence_refs)}</div>}
      </li>)}
    </ul>}
  </section>

  return <div className="sales-brief">
    <header><span className="sales-badge">模拟聊天</span><h3>下一次沟通准备</h3>
      <p>准备于 {salesTime(result.as_of)} · 参考 {coverage.chat_source_count} 条模拟消息</p></header>
    {section('客户背景', brief.customer_summary)}
    {section('近期沟通', brief.recent_communications)}
    {section('近期需求', brief.customer_needs)}
    {section('上次约定', brief.previous_commitments)}
    {section('建议询问的问题', brief.suggested_questions, true)}
    {brief.suggested_opening && section('建议开场白', [brief.suggested_opening], true)}
    {brief.missing_information.length > 0 && <section className="sales-brief-section"><h4>需要补充的信息</h4>
      <ul>{brief.missing_information.map((item, index) => <li key={index}>{item}</li>)}</ul></section>}
    <aside className="sales-limitations"><strong>资料范围与限制</strong>
      <ul>{brief.limitations.map((item, index) => <li key={index}>{item}</li>)}</ul></aside>
    {/* 保留父级 result 的来源快照，历史卡片不能拿新档案替换旧引用。 */}
    {evidence && <EvidenceDrawer key={`${evidence.source.source_id}:${evidence.quote}`}
      evidence={evidence} onClose={() => setEvidence(null)} />}
  </div>
}
