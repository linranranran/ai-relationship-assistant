import { useEffect, useRef, useState } from 'react'
import { Button, MessagePlugin } from 'tdesign-react'
import { getSalesCapabilities, importCustomerChat, searchSalesCustomers } from '../api/sales'
import './Sales.css'
// 单一示例来源，和调试文档共用这份虚构 JSON，避免维护两份相互漂移的数据。
import demoChat from '../../../docs/evaluations/sales-preparation/demo-chat.json'

// 输入是项目定义的模拟 JSON，不是假装读取微信设备/企业微信聊天。
// 前端先检查基本体积和结构，后端仍负责时间、长度、重复 ID、归属等完整校验。
export default function ChatImportPanel({ busy, onPrepare }) {
  const [query, setQuery] = useState('')
  const [customers, setCustomers] = useState([])
  const [customerId, setCustomerId] = useState('')
  const [payload, setPayload] = useState(null)
  const [filename, setFilename] = useState('')
  const [working, setWorking] = useState(false)
  const [result, setResult] = useState(null)
  const [available, setAvailable] = useState(false)
  const [capabilityError, setCapabilityError] = useState('')
  const searchAbort = useRef(null)

  useEffect(() => {
    const controller = new AbortController()
    getSalesCapabilities(controller.signal).then(({ data }) => setAvailable(data.preparation_available))
      .catch(() => { if (!controller.signal.aborted) setCapabilityError('暂时无法确认服务能力，请稍后重新打开。') })
    return () => { controller.abort(); searchAbort.current?.abort() }
  }, [])

  const search = async () => {
    if (!query.trim() || query.trim().length > 128) return MessagePlugin.warning('请输入 1–128 字的客户姓名')
    searchAbort.current?.abort()
    const controller = new AbortController()
    searchAbort.current = controller
    setWorking(true)
    setCustomerId('')
    setResult(null)
    try {
      const { data } = await searchSalesCustomers(query.trim(), controller.signal)
      if (controller.signal.aborted) return
      setCustomers(data.customers)
      if (data.customers.length === 0) MessagePlugin.info('未找到客户，请先在普通对话中建立资料。')
    } catch (error) {
      if (!controller.signal.aborted) MessagePlugin.error(error.response?.data?.detail || '客户查询失败')
    } finally { if (!controller.signal.aborted) setWorking(false) }
  }

  const chooseFile = async (event) => {
    const file = event.target.files?.[0]
    setPayload(null); setFilename(''); setResult(null)
    if (!file) return
    if (file.size > 512 * 1024) return MessagePlugin.warning('模拟 JSON 文件不能超过 512 KB')
    try {
      const data = JSON.parse(await file.text())
      if (!data || typeof data.source_conversation_id !== 'string'
        || !Array.isArray(data.messages) || !data.messages.length || data.messages.length > 100) {
        throw new Error('请使用包含 source_conversation_id 和 1–100 条 messages 的模拟 JSON')
      }
      setPayload(data); setFilename(file.name)
    } catch (error) { MessagePlugin.warning(error.message || '无法读取 JSON') }
  }

  const save = async () => {
    setWorking(true)
    try {
      const { data } = await importCustomerChat(customerId, payload)
      setResult(data)
      MessagePlugin.success('模拟聊天已导入')
    } catch (error) {
      const detail = error.response?.data?.detail
      MessagePlugin.error(typeof detail === 'string' ? detail : '导入失败，请检查时间、消息 ID 和内容长度')
    } finally { setWorking(false) }
  }

  const customer = customers.find((item) => item.id === customerId)
  const disabled = busy || working
  return <section className="sales-import" aria-label="模拟客户聊天导入">
    <div className="sales-import-heading"><span className="sales-badge">模拟数据</span><strong>客户沟通准备</strong></div>
    <p>选择已有客户，导入模拟聊天，再为下一次联系做准备。同名时助手会请你确认。</p>
    <div className="sales-search">
      <input value={query} onChange={(event) => setQuery(event.target.value)} maxLength={128}
        aria-label="搜索客户姓名" placeholder="搜索客户姓名，如杨鑫" disabled={disabled} />
      <Button size="small" variant="outline" onClick={search} disabled={disabled}>查询客户</Button>
    </div>
    {customers.length > 0 && <select value={customerId} aria-label="选择客户" disabled={disabled}
      onChange={(event) => { setCustomerId(event.target.value); setResult(null) }}>
      <option value="">请选择客户</option>
      {customers.map((item) => <option key={item.id} value={item.id}>{item.name}{item.occupation ? ` · ${item.occupation}` : ''}</option>)}
    </select>}
    <label className="sales-file-label">导入模拟 JSON
      <input type="file" accept=".json,application/json" onChange={chooseFile} disabled={disabled} />
    </label>
    <Button size="small" variant="text" disabled={disabled} onClick={() => {
      setPayload(demoChat); setFilename('虚构示例聊天'); setResult(null)
    }}>载入虚构示例聊天</Button>
    {payload && <small>{filename} · {payload.messages.length} 条消息</small>}
    <div className="sales-import-actions">
      <Button size="small" variant="outline" onClick={save} disabled={disabled || !customerId || !payload}>导入聊天</Button>
      <Button size="small" theme="primary" onClick={() => onPrepare(customer)}
        disabled={disabled || !customer || !available}>生成沟通准备</Button>
    </div>
    {result && <p role="status">新增 {result.inserted_count} 条，重复 {result.duplicate_count} 条。原文已保存。</p>}
    {!available && <small>{capabilityError || '当前模式不支持沟通准备；此功能需要服务端使用 Direct 模式。'}</small>}
    <small>仅导入虚构模拟聊天，尚未接入企业微信。</small>
  </section>
}
