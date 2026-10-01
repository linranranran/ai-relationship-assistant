import test from 'node:test'
import assert from 'node:assert/strict'

import {
  buildAuthPayload,
  buildChatPayload,
  validateAuthInput,
  validateChatMessage,
} from '../src/validation/requests.js'

test('auth validation accepts a mainland mobile number and valid password', () => {
  assert.deepEqual(validateAuthInput(' 13800138000 ', 'secret123'), {
    valid: true,
    error: '',
  })
  assert.deepEqual(buildAuthPayload(' 13800138000 ', 'secret123'), {
    phone_number: '13800138000',
    password: 'secret123',
  })
})

test('auth validation rejects malformed phone numbers and short passwords', () => {
  assert.equal(validateAuthInput('12800138000', 'secret123').valid, false)
  assert.equal(validateAuthInput('13800138000', '12345').valid, false)
  assert.throws(() => buildAuthPayload('12800138000', 'secret123'))
})

test('chat payload trims text, enforces the limit, and contains no identity fields', () => {
  assert.deepEqual(buildChatPayload('  你好  '), { message: '你好' })
  assert.equal(validateChatMessage('   ').valid, false)
  assert.equal(validateChatMessage('x'.repeat(4001)).valid, false)
  assert.throws(() => buildChatPayload('   '))
})
