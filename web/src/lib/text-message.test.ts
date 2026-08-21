import { expect, test } from 'bun:test'
import { ChatMessagePriority, ChatMessageType } from 'agora-agent-client-toolkit'

import { createTextMessagePayload, shouldSubmitTextMessage } from './text-message'

test('createTextMessagePayload trims text and builds the Agora sendText shape', () => {
  expect(createTextMessagePayload('  Add the launch review task  ')).toEqual({
    messageType: ChatMessageType.TEXT,
    priority: ChatMessagePriority.INTERRUPTED,
    responseInterruptable: true,
    text: 'Add the launch review task',
  })
})

test('createTextMessagePayload rejects empty input', () => {
  expect(createTextMessagePayload('   ')).toBeNull()
})

test('Enter submits while Shift+Enter and IME composition do not', () => {
  expect(shouldSubmitTextMessage({ key: 'Enter', shiftKey: false, isComposing: false })).toBe(true)
  expect(shouldSubmitTextMessage({ key: 'Enter', shiftKey: true, isComposing: false })).toBe(false)
  expect(shouldSubmitTextMessage({ key: 'Enter', shiftKey: false, isComposing: true })).toBe(false)
})
