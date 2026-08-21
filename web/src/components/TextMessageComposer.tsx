'use client'

import { Loader2, SendHorizontal } from 'lucide-react'
import type { FormEvent, KeyboardEvent } from 'react'

import { MAX_TEXT_MESSAGE_LENGTH, shouldSubmitTextMessage } from '@/lib/text-message'

type TextMessageComposerProps = {
  value: string
  isSending: boolean
  isReady: boolean
  error: string | null
  onChange: (value: string) => void
  onSend: () => Promise<void>
}

export function TextMessageComposer({ value, isSending, isReady, error, onChange, onSend }: TextMessageComposerProps) {
  const canSend = isReady && !isSending && value.trim().length > 0

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (canSend) void onSend()
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (
      shouldSubmitTextMessage({
        key: event.key,
        shiftKey: event.shiftKey,
        isComposing: event.nativeEvent.isComposing,
      })
    ) {
      event.preventDefault()
      if (canSend) void onSend()
    }
  }

  return (
    <div className="jarvis-text-channel w-full max-w-3xl">
      <form className="jarvis-text-composer" onSubmit={handleSubmit}>
        <div className="min-w-0 flex-1">
          <label className="sr-only" htmlFor="jarvis-message">
            Message JARVIS
          </label>
          <textarea
            id="jarvis-message"
            value={value}
            onChange={(event) => onChange(event.target.value)}
            onKeyDown={handleKeyDown}
            maxLength={MAX_TEXT_MESSAGE_LENGTH}
            rows={1}
            disabled={!isReady || isSending}
            placeholder={isReady ? 'Message JARVIS' : 'Waiting for JARVIS to connect'}
            className="jarvis-text-input"
          />
          <p className="jarvis-text-hint">Enter to send · Shift + Enter for a new line</p>
        </div>
        <button
          type="submit"
          disabled={!canSend}
          className="jarvis-text-send"
          aria-label={isSending ? 'Sending message' : 'Send message'}
        >
          {isSending ? <Loader2 className="h-4 w-4 animate-spin" /> : <SendHorizontal className="h-4 w-4" />}
          <span>Send</span>
        </button>
      </form>
      {error ? (
        <p className="jarvis-text-error" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  )
}
