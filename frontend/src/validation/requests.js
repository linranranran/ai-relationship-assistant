export const PHONE_PATTERN = /^1[3-9]\d{9}$/
export const MIN_PASSWORD_LENGTH = 6
export const MAX_PASSWORD_LENGTH = 64
export const MAX_PASSWORD_BYTES = 72
export const MAX_CHAT_MESSAGE_LENGTH = 4000

const utf8Length = (value) => new TextEncoder().encode(value).length

export function validateAuthInput(phoneNumber, password) {
  const phone = String(phoneNumber ?? '').trim()
  const rawPassword = String(password ?? '')

  if (!PHONE_PATTERN.test(phone)) {
    return { valid: false, error: '请输入正确的 11 位大陆手机号' }
  }
  if (rawPassword.length < MIN_PASSWORD_LENGTH) {
    return { valid: false, error: '密码至少需要 6 位' }
  }
  if (rawPassword.length > MAX_PASSWORD_LENGTH || utf8Length(rawPassword) > MAX_PASSWORD_BYTES) {
    return { valid: false, error: '密码过长，请控制在 64 位且不超过 72 字节' }
  }
  return { valid: true, error: '' }
}

export function buildAuthPayload(phoneNumber, password) {
  const validation = validateAuthInput(phoneNumber, password)
  if (!validation.valid) throw new Error(validation.error)
  return {
    phone_number: String(phoneNumber).trim(),
    password: String(password),
  }
}

export function validateChatMessage(message) {
  const normalized = String(message ?? '').trim()
  if (!normalized) return { valid: false, error: '请输入消息' }
  if (normalized.length > MAX_CHAT_MESSAGE_LENGTH) {
    return { valid: false, error: `消息不能超过 ${MAX_CHAT_MESSAGE_LENGTH} 个字符` }
  }
  return { valid: true, error: '' }
}

export function buildChatPayload(message) {
  const validation = validateChatMessage(message)
  if (!validation.valid) throw new Error(validation.error)
  return { message: String(message).trim() }
}
