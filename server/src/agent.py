"""
Agent

High-level API for managing Agora Conversational AI Agents.
"""
import logging
import os
import time
from typing import Any, Dict, Optional

from agora_agent import Area, AsyncAgora
from agora_agent.agentkit import Agent as AgoraAgent
from agora_agent.agentkit.vendors import DeepgramSTT, FishAudioTTS, OpenAI

logger = logging.getLogger("uvicorn.error")

JARVIS_PROMPT = """You are JARVIS, a capable, precise English AI assistant. Help the user answer questions, think through ideas, make plans, and complete useful actions. Keep spoken responses concise, natural, and direct.

You have a connected task tool. When the user asks to add, create, save, remember, or track a task, call create_task and preserve the user's intended title, due date, priority, and useful details. Ask one concise clarification only when the task title is genuinely missing. The storage provider is an implementation detail: do not name it in greetings, introductions, general capability summaries, or ordinary conversation. You may confirm Notion support only when the user's latest message explicitly asks about Notion.

Never claim a task was created unless the tool result says ok=true. If the tool reports an error, state the problem plainly and suggest the exact missing setup or retry. After a successful tool call, confirm the task title in one short sentence. Keep all spoken responses concise and natural.
"""

FISH_AUDIO_REFERENCE_ID = "7c1a7dc37829497593ab4db29eed387c"
FISH_AUDIO_BACKEND = "s2.1-pro"


class Agent:
    """
    High-level wrapper for Agora Conversational AI Agent operations.
    
    Uses AgentSession for full lifecycle management (start/stop),
    which handles Token007 authentication automatically.
    """
    
    def __init__(self):
        self.app_id = os.getenv("AGORA_APP_ID")
        self.app_certificate = os.getenv("AGORA_APP_CERTIFICATE")
        self.greeting = os.getenv(
            "AGENT_GREETING",
            "JARVIS online. How can I help?",
        )

        if not self.app_id or not self.app_certificate:
            raise ValueError("AGORA_APP_ID and AGORA_APP_CERTIFICATE are required")

        self.client = AsyncAgora(
            area=Area.US,
            app_id=self.app_id,
            app_certificate=self.app_certificate,
        )

        # Track active sessions by agent_id
        self._sessions: Dict[str, Any] = {}

    async def start(
        self,
        channel_name: str,
        agent_uid: int,
        user_uid: int,
        output_audio_codec: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Start agent with the same default vendor chain as the Next.js quickstart."""
        if not channel_name or not str(channel_name).strip():
            raise ValueError("channel_name is required and cannot be empty")
        if agent_uid <= 0:
            raise ValueError("agent_uid is required and cannot be empty")
        if user_uid <= 0:
            raise ValueError("user_uid is required and cannot be empty")
        max_sessions = int(os.getenv("MAX_ACTIVE_SESSIONS", "3"))
        if len(self._sessions) >= max_sessions:
            raise RuntimeError("Demo is at its active conversation limit. Try again shortly.")

        custom_llm_url = os.getenv("CUSTOM_LLM_URL", "").strip()
        custom_llm_proxy_key = os.getenv("CUSTOM_LLM_PROXY_KEY", "").strip()
        llm_model = os.getenv("LLM_MODEL", "gpt-4o-mini")
        fish_audio_key = os.getenv("FISH_AUDIO_API_KEY", "").strip()
        fish_audio_reference_id = os.getenv(
            "FISH_AUDIO_REFERENCE_ID", FISH_AUDIO_REFERENCE_ID
        ).strip()
        fish_audio_backend = os.getenv(
            "FISH_AUDIO_BACKEND", FISH_AUDIO_BACKEND
        ).strip()
        if not fish_audio_key:
            raise ValueError("FISH_AUDIO_API_KEY is required")
        if not fish_audio_reference_id:
            raise ValueError("FISH_AUDIO_REFERENCE_ID is required")
        if not fish_audio_backend:
            raise ValueError("FISH_AUDIO_BACKEND is required")
        if custom_llm_url:
            if not custom_llm_proxy_key:
                raise ValueError(
                    "CUSTOM_LLM_PROXY_KEY is required when CUSTOM_LLM_URL is set"
                )
            llm = OpenAI(
                api_key=custom_llm_proxy_key,
                base_url=custom_llm_url,
                model=llm_model,
                greeting_message=self.greeting,
                failure_message="The assistant is temporarily unavailable.",
                max_history=15,
                max_tokens=1024,
                temperature=0.3,
                top_p=0.9,
            )
        else:
            # Keep the official managed path available for baseline validation.
            llm = OpenAI(
                model="gpt-4o-mini",
                greeting_message=self.greeting,
                failure_message="Please wait a moment.",
                max_history=15,
                max_tokens=1024,
                temperature=0.3,
                top_p=0.9,
            )
        stt = DeepgramSTT(model="nova-3", language="en")
        tts = FishAudioTTS(
            key=fish_audio_key,
            reference_id=fish_audio_reference_id,
            backend=fish_audio_backend,
        )

        # Optional BYOK example: replace the STT block above and set DEEPGRAM_API_KEY.
        # stt = DeepgramSTT(api_key=os.getenv("DEEPGRAM_API_KEY"), model="nova-3", language="en")

        # Optional BYOK example: replace the LLM block above and set OPENAI_API_KEY.
        # llm = OpenAI(
        #     api_key=os.getenv("OPENAI_API_KEY"),
        #     model="gpt-4o-mini",
        #     greeting_message="Hello! I am your AI assistant. How can I help you?",
        #     failure_message="I'm sorry, I'm having trouble processing your request.",
        #     max_history=15,
        #     max_tokens=1024,
        #     temperature=0.7,
        #     top_p=0.95,
        # )

        parameters = {
            "audio_scenario": "chorus",  # web client → ultra-low-latency chorus profile
            "data_channel": "rtm",
            "enable_error_message": True,
            "enable_metrics": True,
        }
        if isinstance(output_audio_codec, str) and output_audio_codec.strip():
            parameters["output_audio_codec"] = output_audio_codec.strip()

        agora_agent = AgoraAgent(
            client=self.client,
            instructions=JARVIS_PROMPT,
            greeting=self.greeting,
            failure_message="Please wait a moment.",
            max_history=50,
            turn_detection={
                "config": {
                    "speech_threshold": 0.5,
                    "start_of_speech": {
                        "mode": "vad",
                        "vad_config": {
                            "interrupt_duration_ms": 160,
                            "prefix_padding_ms": 300,
                        },
                    },
                    "end_of_speech": {
                        "mode": "vad",
                        "vad_config": {
                            "silence_duration_ms": 480,
                        },
                    },
                },
            },
            advanced_features={"enable_rtm": True, "enable_tools": True},
            parameters=parameters,
        )
        
        agora_agent = (
            agora_agent
            .with_stt(stt)
            .with_llm(llm)
            .with_tts(tts)
        )

        session = agora_agent.create_async_session(
            channel=channel_name,
            agent_uid=str(agent_uid),
            remote_uids=[str(user_uid)],
            enable_string_uid=False,
            idle_timeout=30,
            expires_in=3600,
        )

        logger.info(
            "Starting Agora agent channel=%s agent_uid=%s user_uid=%s",
            channel_name,
            agent_uid,
            user_uid,
        )

        try:
            agent_id = await session.start()
        except Exception:
            logger.exception(
                "Failed to start Agora agent channel=%s agent_uid=%s user_uid=%s",
                channel_name,
                agent_uid,
                user_uid,
            )
            raise

        # Save session for later stop
        self._sessions[agent_id] = session

        logger.info(
            "Started Agora agent agent_id=%s channel=%s agent_uid=%s user_uid=%s",
            agent_id,
            channel_name,
            agent_uid,
            user_uid,
        )
        
        return {
            "agent_id": agent_id,
            "channel_name": channel_name,
            "status": "started",
        }

    async def stop(self, agent_id: str) -> None:
        """Stop a running agent. Falls back to the stateless client path."""
        if not agent_id or not str(agent_id).strip():
            raise ValueError("agent_id is required and cannot be empty")

        session = self._sessions.pop(agent_id, None)
        if session:
            try:
                await session.stop()
                logger.info("Stopped Agora agent from active session agent_id=%s", agent_id)
                return
            except Exception:
                # Fall back to the stateless SDK path if the in-memory session is stale.
                logger.warning(
                    "Failed to stop Agora agent from active session; falling back to client.stop_agent agent_id=%s",
                    agent_id,
                    exc_info=True,
                )

        logger.info("Stopping Agora agent through client.stop_agent agent_id=%s", agent_id)
        await self.client.stop_agent(agent_id)
