# 06 Interfaces

> Boundary contracts: FastAPI routes, Next rewrites, environment variables, and managed agent payload.

## Python Backend Routes

`server/src/server.py` registers these on an `APIRouter`:

| Path           | Method | Request                                                                              | Success (200)                                                                                | Errors                                                          |
| -------------- | ------ | ------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------- | --------------------------------------------------------------- |
| `/get_config`  | GET    | Query: optional `channel`, optional `uid`                                            | `{ "code": 0, "msg": "success", "data": { app_id, token, uid, channel_name, agent_uid } }`   | `500` if `agent is None`; exceptions via `_to_http_error`        |
| `/startAgent`  | POST   | JSON `StartAgentRequest`: `channelName`, `rtcUid`, `userUid`, optional `parameters`  | `{ "code": 0, "msg": "success", "data": { agent_id, channel_name, status } }`                | `400` validation (`ValueError`); `500` runtime / generic        |
| `/stopAgent`   | POST   | JSON `StopAgentRequest`: `agentId`                                                   | `{ "code": 0, "msg": "success" }`                                                            | Same shape                                                       |
| `/health`      | GET    | None                                                                                 | Non-secret Agora, Custom LLM, and Notion readiness                                               | —                                                                |
| `/tasks/recent`| GET    | None                                                                                 | `{ code, msg, data: { configured, tasks } }`                                                     | —                                                                |
| `/chat/completions` | POST | OpenAI-compatible body + bearer proxy key                                        | JSON or SSE response with server-side tool execution                                              | `401`, `422`, `503`                                              |

CORS middleware reads comma-separated `CORS_ORIGINS` and disables credentials.

`StartAgentRequest.parameters` is optional — the handler only reads `output_audio_codec` from it today.

`get_config` treats missing, zero, and negative UIDs as "generate a random user UID" and returns the generated value. This keeps the single RTC+RTM token usable for RTM, where `0` is not a valid login subject.

## Next.js Rewrites

`web/next.config.ts` registers these only when `AGENT_BACKEND_URL` is set:

| Source             | Destination                                  |
| ------------------ | -------------------------------------------- |
| `/api/get_config`  | `${AGENT_BACKEND_URL}/get_config`             |
| `/api/startAgent`  | `${AGENT_BACKEND_URL}/startAgent`             |
| `/api/stopAgent`   | `${AGENT_BACKEND_URL}/stopAgent`              |
| `/api/health`      | `${AGENT_BACKEND_URL}/health`                 |
| `/api/tasks/recent`| `${AGENT_BACKEND_URL}/tasks/recent`           |

`verify-api-contracts.ts` asserts that no `web/app/api/**/route.ts` files exist. Adding one would create a competing handler in front of the rewrite — don't.

## Environment Variables

| Scope                  | Variable                                  |
| ---------------------- | ----------------------------------------- |
| Python server (required) | `AGORA_APP_ID`, `AGORA_APP_CERTIFICATE` |
| Python server (TTS) | `FISH_AUDIO_API_KEY`, `FISH_AUDIO_REFERENCE_ID`, `FISH_AUDIO_BACKEND` |
| Python server (optional) | `AGENT_GREETING`, `PORT`                 |
| Python server (tool path) | `CUSTOM_LLM_URL`, `CUSTOM_LLM_PROXY_KEY`, `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`, `LLM_THINKING_TYPE`, `LLM_MAX_HISTORY`, `LLM_MAX_TOKENS`, `NOTION_API_KEY`, `NOTION_DATA_SOURCE_ID`, `NOTION_TITLE_PROPERTY` |
| Next build             | `AGENT_BACKEND_URL`                       |
| Browser                | `NEXT_PUBLIC_AGENT_UID` (optional)        |

`AGENT_BACKEND_URL` is a Next **server**-time env var (used inside `next.config.ts`), not a `NEXT_PUBLIC_*` value — do not prefix it.

## Token Shape

`get_config` returns:

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "app_id": "string",
    "token": "string",          // built by generate_convo_ai_token, token_expire=3600
    "uid": "string",            // serialized number
    "channel_name": "string",
    "agent_uid": "string"
  }
}
```

The same token grants RTC and RTM privileges.

## Managed Agent Payload

`server/src/agent.py` does not POST a hand-written JSON payload to Agora — it uses the SDK builder chain:

```python
agent = (
    AgoraAgent(...)
    .with_stt(DeepgramSTT(model="nova-3", language="en"))
    .with_llm(OpenAI(model="gpt-4o-mini", ...))
    .with_tts(FishAudioTTS(key=os.environ["FISH_AUDIO_API_KEY"],
                           reference_id="7c1a7dc37829497593ab4db29eed387c",
                           backend="s2.1-pro"))
)

session = agora_agent.create_async_session(
    client=self.client,
    channel=channel_name,
    agent_uid=str(agent_uid),
    remote_uids=[str(user_uid)],
    enable_string_uid=False,
    idle_timeout=30,
    expires_in=3600,
)
agent_id = await session.start()
```

## RTM Event Shapes (Client-Side)

Typed input uses the existing authenticated RTM connection rather than an HTTP chat route:

```ts
AgoraVoiceAI.sendText(agentUID, {
  messageType: ChatMessageType.TEXT,
  priority: ChatMessagePriority.INTERRUPTED,
  responseInterruptable: true,
  text,
})
```

Messages are limited to 1,000 characters in the client, trimmed before sending, and share the active agent's transcript, tools, and spoken response path.

`AgoraVoiceAI` emits the same toolkit events as the other quickstarts:

- `TRANSCRIPT_UPDATED` — `{ uid, text, status, timestamp }[]`
- `AGENT_STATE_CHANGED` — `AgentState`
- `AGENT_METRICS` — `{ type, name, value, timestamp }`
- `MESSAGE_ERROR` — `{ module, code, message, send_ts }`
- `MESSAGE_SAL_STATUS` — `{ status, timestamp }`
- `AGENT_ERROR` — SDK error info

`ConversationComponent.tsx` also attaches a raw RTM `message` listener as a fallback for the same `message.error` / `message.sal_status` payloads.

## Internal Types

| Type                          | Lives in                                       | Notes                                            |
| ----------------------------- | ---------------------------------------------- | ------------------------------------------------ |
| `StartAgentRequest`           | `server/src/server.py`                         | pydantic, camelCase fields                       |
| `StopAgentRequest`            | `server/src/server.py`                         | pydantic, `agentId`                              |
| `AgoraTokenData`              | `web/src/types/conversation.ts`                | Used by `LandingPage` + `ConversationComponent`  |
| `AgoraRenewalTokens`          | `web/src/types/conversation.ts`                | Renewal handler payload                          |
| `ConversationComponentProps`  | `web/src/types/conversation.ts`                | Includes RTM client + data                       |
| `GetConfigResponse`           | `web/src/services/api.ts`                      | Browser shape for `data` field                   |

## Related Deep Dives

- [Managed Agent Config](L2/managed_agent_config.md) — Detailed field reference.
- [Verification Scripts](L2/verification_scripts.md) — How the contracts above are enforced by local pre-ship checks.
