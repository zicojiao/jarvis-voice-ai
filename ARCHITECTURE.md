# JARVIS Voice AI Assistant — Architecture

## Topology

```text
Browser
  ├─ Agora RTC (voice) ─────────> Agora Conversational AI
  ├─ Agora RTM (text/events) ────> Agora Conversational AI
  └─ Next.js /api/* ─────────────> FastAPI on Railway
                                        ├─ agent lifecycle and tokens
Agora Conversational AI ────────────────> private /chat/completions
                                        ├─ Zhipu GLM API
                                        ├─ Notion REST API
                                        └─ Fish Audio TTS
```

The Vercel-hosted Next.js app owns presentation and browser-facing `/api/*` URLs. Its rewrites forward lifecycle, health, and recent-task requests to FastAPI through `AGENT_BACKEND_URL`.

FastAPI owns all secrets, Agora session creation, the OpenAI-compatible streaming endpoint, tool execution, and the recent-task view. The production endpoint uses Zhipu GLM with thinking disabled for lower voice latency. The browser never receives provider credentials.

## Conversation lifecycle

1. `GET /api/get_config` returns short-lived Agora RTC/RTM connection data.
2. The browser joins the channel and calls `POST /api/startAgent`.
3. The user can speak over RTC or type through `AgoraVoiceAI.sendText`; typed messages are sent directly to the agent UID over the authenticated RTM connection.
4. The agent uses `CUSTOM_LLM_URL` as its LLM endpoint and authenticates with `CUSTOM_LLM_PROXY_KEY`.
5. The custom LLM streams model output and executes the provider-neutral `create_task` tool when requested.
6. `NotionTaskService` creates a page under `NOTION_DATA_SOURCE_ID` using Notion API version `2025-09-03`.
7. The result is spoken over Agora and exposed by `GET /api/tasks/recent` for the UI.
   Speech synthesis uses Fish Audio voice model `7c1a7dc37829497593ab4db29eed387c`.
8. `POST /api/stopAgent` stops the cloud agent session.

If `CUSTOM_LLM_URL` is absent, the original Agora-managed LLM remains available. If Notion is unconfigured or rejects a write, the tool returns an explicit failure and the assistant must not claim success.

For task writes, the custom LLM performs one model pass to extract the structured tool arguments, executes the task service, then streams a deterministic confirmation directly. Avoiding a second model pass reduces tool-response latency and removes a provider-specific failure point. Spoken output is capped by `LLM_MAX_TOKENS`, and request timing logs separate upstream first-content latency from tool latency.

## Backend endpoints

| Endpoint | Method | Responsibility |
| --- | --- | --- |
| `/get_config` | GET | Generate Agora connection config |
| `/startAgent` | POST | Start an Agora agent session |
| `/stopAgent` | POST | Stop an Agora agent session |
| `/chat/completions` | POST | Authenticated OpenAI-compatible LLM and tool loop |
| `/tasks/recent` | GET | Show recent real task results |
| `/health` | GET | Non-secret deployment readiness |

## Security and correctness invariants

- Provider keys and the Agora certificate remain backend-only.
- `/chat/completions` requires a separate proxy bearer key; the upstream model key is never given to Agora.
- Notion tool executions are idempotent by `tool_call_id` within the server process.
- The Notion page title is the only schema-dependent property; details, due date, and priority are written as page content.
- CORS is restricted by `CORS_ORIGINS` when configured.
- Health responses reveal configuration state but never credential values.

More detail lives in [docs/ai/L1/02_architecture.md](./docs/ai/L1/02_architecture.md), [docs/ai/L1/06_interfaces.md](./docs/ai/L1/06_interfaces.md), and [docs/ai/L1/08_security.md](./docs/ai/L1/08_security.md).
