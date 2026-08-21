# JARVIS Voice AI Assistant

An English voice assistant for natural conversation and useful actions. The demo uses Agora Conversational AI for the real-time voice session, a Python OpenAI-compatible tool loop, a Notion task capability, and a Next.js control surface.

## Stack

- Next.js 16, React 19, and Agora RTC/RTM in `web/`
- FastAPI and Agora Agent Server SDK in `server/`
- Zhipu GLM tool calling through the private OpenAI-compatible `/chat/completions` endpoint
- Notion REST API `2025-09-03` with a data-source parent
- Railway backend and Vercel frontend

## Local setup

```bash
agora login
agora project use <your-project>
bun run setup
agora project env write server/.env.local
```

Add the remaining server-only values to `server/.env.local`:

```bash
LLM_API_KEY=...
LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
LLM_MODEL=glm-4.5-flash
LLM_THINKING_TYPE=disabled
CUSTOM_LLM_PROXY_KEY=...
CUSTOM_LLM_URL=http://localhost:8000/chat/completions
FISH_AUDIO_API_KEY=...
FISH_AUDIO_REFERENCE_ID=7c1a7dc37829497593ab4db29eed387c
FISH_AUDIO_BACKEND=s2.1-pro
NOTION_API_KEY=...
NOTION_DATA_SOURCE_ID=...
```

Then run:

```bash
bun run doctor:local
bun run dev
```

Open `http://localhost:3000`, start the voice link, and say: “Create a task in Notion to prepare the launch brief by Friday, high priority.”

## Verification

```bash
bun run verify:backend
bun run verify:web:api
bun run verify:web:build
```

The backend tests mock Agora, OpenAI, and Notion boundaries; no paid cloud call is required.

## Deployment

Railway builds the root `Dockerfile`. Configure these variables on the backend service:

- `AGORA_APP_ID` and `AGORA_APP_CERTIFICATE`
- `LLM_API_KEY`, `LLM_MODEL`, `LLM_BASE_URL`, and optional `LLM_THINKING_TYPE`
- `CUSTOM_LLM_PROXY_KEY`
- `CUSTOM_LLM_URL=https://<railway-domain>/chat/completions`
- `FISH_AUDIO_API_KEY`
- `FISH_AUDIO_REFERENCE_ID=7c1a7dc37829497593ab4db29eed387c`
- `FISH_AUDIO_BACKEND=s2.1-pro`
- `NOTION_API_KEY` and `NOTION_DATA_SOURCE_ID`
- optional `NOTION_TITLE_PROPERTY` (default: `Name`)

Deploy `web/` to Vercel with `AGENT_BACKEND_URL=https://<railway-domain>`.

## Runtime flow

1. The browser joins Agora RTC/RTM and asks FastAPI to start an agent.
2. Agora streams recognized speech to the private OpenAI-compatible endpoint.
3. The model calls the provider-neutral `create_task` tool when the user requests a task.
4. FastAPI creates the Notion page and returns a truthful success or failure result.
5. The assistant speaks the result and the UI displays the recent task with its Notion URL.

The OpenAI key, Notion key, Agora certificate, and custom-LLM proxy key are server-only. Tool calls are idempotent by call ID, and Notion failures are never presented as successful writes.

See [ARCHITECTURE.md](./ARCHITECTURE.md), [AGENTS.md](./AGENTS.md), and [server/.env.example](./server/.env.example) for details.
