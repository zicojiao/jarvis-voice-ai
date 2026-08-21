# 02 Architecture

> Split frontend + backend in one repo: Next.js browser app proxies `/api/*` to a Python FastAPI service that drives the Agora Conversational AI managed agent.

## High-Level Topology

```
   Browser tab                       Next.js (web/)               Python (server/)
   ┌────────────────────┐    fetch  ┌──────────────────────┐ HTTP ┌───────────────────────┐
   │ LandingPage.tsx    │──────────▶│ /api/get_config      │─────▶│ GET  /get_config      │
   │ ConversationCmp.tsx│           │ /api/startAgent      │─────▶│ POST /startAgent      │
   │ AgoraVoiceAI       │◀──────────│ /api/stopAgent       │─────▶│ POST /stopAgent       │
   │ agora-rtc-react    │  JSON     │ next.config.ts       │      │ FastAPI + CORS        │
   │ agora-rtm          │           │  rewrites()          │      │ AsyncAgora + AgoraAgent│
   └─────┬──────┬───────┘           └──────────────────────┘      └──────────┬────────────┘
         │      │ RTM data                                                   │ HTTPS
         │ RTC  │                                                            ▼
         ▼      ▼                                                Agora Conversational AI
   Agora media + RTM cloud                                       (managed STT/LLM/TTS or optional custom providers)
```

## Live Session Lifecycle

1. `LandingPage` calls `getConfig()` → `GET /api/get_config` → FastAPI returns `data: { app_id, token, uid, channel_name, agent_uid }`.
2. `startAgent(channel_name, Number(agent_uid), Number(uid))` → `POST /api/startAgent` → FastAPI starts the managed agent.
3. After the agent start succeeds, `new AgoraRTM.RTM(appId, uid).login({ token })` → `subscribe(channel_name)`. If RTM setup fails, the browser stops the new agent and logs out the partial RTM client before showing a retryable error.
4. `ConversationComponent` mounts inside a dynamic `AgoraRTCProvider` (RTC client in `useRef` for StrictMode safety) and:
   - `useJoin` joins RTC.
   - `useLocalMicrophoneTrack` + `usePublish` start mic publishing.
   - `AgoraVoiceAI.init({ rtcEngine, rtmConfig: { rtmEngine: rtmClient } })` wires transcripts, state, metrics.
   - `subscribeMessage(channel_name)` opens the toolkit's RTM channel.
5. The user can speak over the published RTC microphone or type in `TextMessageComposer`. Typed messages call `AgoraVoiceAI.sendText(agent_uid, payload)` and travel directly to the agent over RTM with interrupted priority.
6. End: `stopAgent(agentId)` → `POST /api/stopAgent` → FastAPI stops the agent. `rtmClient.logout()` follows.
7. Renewal: on RTC `token-privilege-will-expire`, the client fetches `getConfig()` twice (once for RTC uid, once for the stored `agoraData.uid`) and renews RTC + RTM separately.

## How `/api/*` Reaches FastAPI

`web/next.config.ts`:

```ts
async rewrites() {
  const backendUrl = process.env.AGENT_BACKEND_URL?.replace(/\/$/, '');
  if (!backendUrl) return [];
  return [
    { source: '/api/get_config',  destination: `${backendUrl}/get_config` },
    { source: '/api/startAgent',  destination: `${backendUrl}/startAgent` },
    { source: '/api/stopAgent',   destination: `${backendUrl}/stopAgent` },
  ];
}
```

If `AGENT_BACKEND_URL` is unset/empty, **no rewrites register** — the client cannot reach the backend. `bun run dev` always exports `AGENT_BACKEND_URL=http://localhost:8000` before starting Next.

## FastAPI Shape

`server/src/server.py`:

- `FastAPI(title="...", version="2.0.0")`.
- `CORSMiddleware` with `allow_origins=["*"]`, `allow_credentials=True`.
- Reads `server/.env.local` then `server/.env` via `python-dotenv` at startup, resolved relative to `server/src/server.py`.
- Constructs a single `Agent` instance at import time (`agent = Agent()`).
- Routes registered on an `APIRouter`: `GET /get_config`, `POST /startAgent`, `POST /stopAgent`.
- All responses use the envelope `{ "code": 0, "msg": "success", "data": ... }`.
- Errors funnel through `_to_http_error` → `HTTPException` (`ValueError → 400`, `RuntimeError → 500`, else `500`).
- `uvicorn.run(app, host="0.0.0.0", port=PORT)` under `if __name__ == "__main__":`.

`server/src/agent.py`:

- `get_config` treats missing, zero, and negative UIDs as "generate a usable UID" before minting an RTC+RTM token.
- `Agent.start` prunes remotely stopped/failed tracked sessions before enforcing `MAX_ACTIVE_SESSIONS`, so abandoned browser tabs do not permanently consume the local demo limit.
- `Agent.start(channel_name, rtc_uid, user_uid)` uses the module-level `AsyncAgora` client, builds `AgoraAgent` from `agora-agents` (`agora_agent`), and creates an async session.
- `Agent.stop(agent_id)` ends the session.

## Managed Agent Defaults (server/src/agent.py)

| Stage | Vendor       | Config highlights                                                          |
| ----- | ------------ | -------------------------------------------------------------------------- |
| STT   | `DeepgramSTT`| `model="nova-3"`, `language="en"`                                           |
| LLM   | Custom OpenAI-compatible | Zhipu `glm-4.5-flash` through FastAPI, thinking disabled for latency |
| TTS   | `MiniMaxTTS` / `FishAudioTTS` | Managed MiniMax by default; Fish Audio when `FISH_AUDIO_API_KEY` is configured. |
| VAD   | Agora        | Tunable `turn_detection` dict with start/end mode and timing thresholds    |

Agent parameters: `data_channel="rtm"`, `enable_error_message=True`, `enable_metrics=True`. Advanced features: `{"enable_rtm": True, "enable_tools": True}`. Session options: `enable_string_uid=False`, `idle_timeout=30`, `expires_in=3600`.

## Why This Shape

- Python backend gives Python developers a familiar host for Conversational AI integration.
- Next.js rewrites hide backend placement from the browser — `/api/*` is the only URL the client knows.
- Single repo keeps web and backend changes reviewable together while preserving deploy separation.
- Task writes short-circuit after the tool result: FastAPI emits a brief OpenAI-compatible SSE confirmation instead of paying for a second upstream LLM pass.

## Related Deep Dives

- [Managed Agent Config](L2/managed_agent_config.md) — Full `agent.py` chain and tunable fields.
- [Session Lifecycle](L2/session_lifecycle.md) — Detailed client orchestration including renewal.
- [Verification Scripts](L2/verification_scripts.md) — How the contract harness asserts the proxy boundary.
