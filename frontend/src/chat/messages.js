// 展示消息身份和执行身份分开：最终问答按 request_id + role 去重，
// 同一请求中的澄清/确认按 interrupt_id 标识，刷新和重复查询不会多一份回复。
export const makeMessage = (role, content, requestId, extra = {}) => ({
  role, content, requestId,
  key: extra.key || (requestId ? `${requestId}:${role}` : crypto.randomUUID()),
  createdAt: new Date().toISOString(),
  ...extra,
})

export const historyMessages = (items) => items.map((item) =>
  makeMessage(item.role, item.content, item.request_id, {
    messageId: item.message_id, createdAt: item.created_at,
    salesPreparation: item.sales_preparation || null,
  }))

export const mergeMessages = (current, incoming, prepend = false) => {
  // Map 保持插入顺序；补收的同一条消息只更新内容，不改变位置。
  const ordered = prepend ? [...incoming, ...current] : [...current, ...incoming]
  const result = new Map()
  for (const item of ordered) result.set(item.key, { ...result.get(item.key), ...item })
  return [...result.values()]
}

export const QUICK_PROMPTS = [
  { title: '记录客户资料', text: '我有个客户叫杨鑫，职业是销售负责人。', icon: 'people' },
  { title: '回顾客户信息', text: '帮我查一下杨鑫的个人资料。', icon: 'person' },
  { title: '了解客户关系', text: '查询我和杨鑫之间的关系。', icon: 'relations' },
]
