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
  }))

export const mergeMessages = (current, incoming, prepend = false) => {
  // Map 保持插入顺序；补收的同一条消息只更新内容，不改变位置。
  const ordered = prepend ? [...incoming, ...current] : [...current, ...incoming]
  const result = new Map()
  for (const item of ordered) result.set(item.key, { ...result.get(item.key), ...item })
  return [...result.values()]
}

export const QUICK_PROMPTS = [
  { title: '记录一段关系', text: '我有个朋友叫张三，喜欢打篮球。', icon: 'people' },
  { title: '了解一个人', text: '帮我查一下张建国的个人资料。', icon: 'person' },
  { title: '梳理亲属称谓', text: '张建国和我是什么关系？', icon: 'relations' },
]
