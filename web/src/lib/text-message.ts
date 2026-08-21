import { ChatMessagePriority, type ChatMessageText, ChatMessageType } from 'agora-agent-client-toolkit'

export const MAX_TEXT_MESSAGE_LENGTH = 1000

export type TextSubmitKey = {
  key: string
  shiftKey: boolean
  isComposing: boolean
}

export function shouldSubmitTextMessage(event: TextSubmitKey): boolean {
  return event.key === 'Enter' && !event.shiftKey && !event.isComposing
}

export function createTextMessagePayload(input: string): ChatMessageText | null {
  const text = input.trim()
  if (!text) return null

  return {
    messageType: ChatMessageType.TEXT,
    priority: ChatMessagePriority.INTERRUPTED,
    responseInterruptable: true,
    text,
  }
}
