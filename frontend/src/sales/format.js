// 所有销售证据统一按北京时间显示；原始带时区时间仍由后端保存。
export const salesTime = (value) => value && !Number.isNaN(Date.parse(value))
  ? new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
  : '时间未知'
