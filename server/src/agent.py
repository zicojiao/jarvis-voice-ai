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
from agora_agent.agentkit.vendors import DeepgramSTT, FishAudioTTS, MiniMaxTTS, OpenAI

logger = logging.getLogger("uvicorn.error")

# 系统提示词定义通用助手身份、工具触发条件、成功判定标准和语音回复长度约束。
# 存储供应商被视为实现细节，只有用户明确询问时才在对话中提及。
JARVIS_PROMPT = """You are JARVIS, a capable, precise English AI assistant. Help the user answer questions, think through ideas, make plans, and complete useful actions. Keep spoken responses concise, natural, and direct.

You have a connected task tool. When the user asks to add, create, save, remember, or track a task, call create_task and preserve the user's intended title, due date, priority, and useful details. Ask one concise clarification only when the task title is genuinely missing. The storage provider is an implementation detail: do not name it in greetings, introductions, general capability summaries, or ordinary conversation. You may confirm Notion support only when the user's latest message explicitly asks about Notion.

Never claim a task was created unless the tool result says ok=true. If the tool reports an error, state the problem plainly and suggest the exact missing setup or retry. After a successful tool call, confirm the task title in one short sentence. Keep all spoken responses concise and natural.
"""

# Fish Audio voice reference 与模型后端提供可部署的默认值，并允许环境变量覆盖。
FISH_AUDIO_REFERENCE_ID = "7c1a7dc37829497593ab4db29eed387c"
FISH_AUDIO_BACKEND = "s2.1-pro"


class Agent:
    """
    High-level wrapper for Agora Conversational AI Agent operations.
    
    Uses AgentSession for full lifecycle management (start/stop),
    which handles Token007 authentication automatically.
    """
    
    def __init__(self):
        # Agora App ID 与 Certificate 仅存在于服务端，用于 SDK 鉴权和短期 Token 签发。
        self.app_id = os.getenv("AGORA_APP_ID")
        self.app_certificate = os.getenv("AGORA_APP_CERTIFICATE")
        self.greeting = os.getenv(
            "AGENT_GREETING",
            "JARVIS online. How can I help?",
        )

        # 缺失 Agora 长期凭证时立即终止初始化，避免直到用户启动会话才产生模糊错误。
        if not self.app_id or not self.app_certificate:
            raise ValueError("AGORA_APP_ID and AGORA_APP_CERTIFICATE are required")

        # AsyncAgora 是进程级 SDK Client，可由多个会话复用；区域设置需与服务部署策略一致。
        self.client = AsyncAgora(
            area=Area.US,
            app_id=self.app_id,
            app_certificate=self.app_certificate,
        )

        # 按 agent_id 保存活动 AgentSession，以支持有状态停止和会话数量限制。
        self._sessions: Dict[str, Any] = {}

    async def _prune_inactive_sessions(self) -> None:
        """Remove sessions that Agora reports as no longer active.

        Browser tabs can disappear without calling ``/stopAgent``. In that case
        Agora eventually stops the agent via ``idle_timeout``, while the local
        in-memory session object still says ``running``. Querying the remote
        status before enforcing the demo limit prevents those stale handles
        from permanently blocking new conversations.
        """
        terminal_statuses = {"IDLE", "STOPPED", "FAILED"}
        stale_agent_ids = []

        for agent_id, session in list(self._sessions.items()):
            try:
                info = await session.get_info()
                status = str(getattr(info, "status", "")).upper()
                if status in terminal_statuses:
                    stale_agent_ids.append(agent_id)
            except Exception as exc:
                if getattr(exc, "status_code", None) == 404:
                    stale_agent_ids.append(agent_id)
                else:
                    logger.warning(
                        "Could not refresh Agora agent status agent_id=%s",
                        agent_id,
                        exc_info=True,
                    )

        for agent_id in stale_agent_ids:
            self._sessions.pop(agent_id, None)

        if stale_agent_ids:
            logger.info(
                "Pruned inactive Agora agent sessions count=%s",
                len(stale_agent_ids),
            )

    async def start(
        self,
        channel_name: str,
        agent_uid: int,
        user_uid: int,
        output_audio_codec: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Start agent with the same default vendor chain as the Next.js quickstart."""
        # 在调用 Agora SDK 前完成边界校验，使 API 层获得稳定、可解释的 ValueError。
        if not channel_name or not str(channel_name).strip():
            raise ValueError("channel_name is required and cannot be empty")
        if agent_uid <= 0:
            raise ValueError("agent_uid is required and cannot be empty")
        if user_uid <= 0:
            raise ValueError("user_uid is required and cannot be empty")
        # Demo 环境设置并发会话上限，防止异常客户端持续创建计费资源。
        await self._prune_inactive_sessions()
        max_sessions = int(os.getenv("MAX_ACTIVE_SESSIONS", "3"))
        if len(self._sessions) >= max_sessions:
            raise RuntimeError("Demo is at its active conversation limit. Try again shortly.")

        # 会话启动时读取供应商配置，使部署环境能够在不修改代码的情况下切换模型与端点。
        # 上下文轮数和生成 Token 上限用于控制推理延迟、语音输出长度及调用成本。
        custom_llm_url = os.getenv("CUSTOM_LLM_URL", "").strip()
        custom_llm_proxy_key = os.getenv("CUSTOM_LLM_PROXY_KEY", "").strip()
        llm_model = os.getenv("LLM_MODEL", "gpt-4o-mini")
        llm_max_history = max(2, int(os.getenv("LLM_MAX_HISTORY", "8")))
        llm_max_tokens = max(32, int(os.getenv("LLM_MAX_TOKENS", "256")))
        # Fish Audio Key、reference_id 与 backend 共同确定可选的自定义语音。
        # 未配置 Key 时回退到官方快速开始使用的 Agora 托管 MiniMax TTS。
        fish_audio_key = os.getenv("FISH_AUDIO_API_KEY", "").strip()
        fish_audio_reference_id = os.getenv(
            "FISH_AUDIO_REFERENCE_ID", FISH_AUDIO_REFERENCE_ID
        ).strip()
        fish_audio_backend = os.getenv(
            "FISH_AUDIO_BACKEND", FISH_AUDIO_BACKEND
        ).strip()
        # 只有选择 Fish Audio 时才校验其余配置；默认托管链路无需第三方密钥。
        if fish_audio_key and not fish_audio_reference_id:
            raise ValueError(
                "FISH_AUDIO_REFERENCE_ID is required when FISH_AUDIO_API_KEY is set"
            )
        if fish_audio_key and not fish_audio_backend:
            raise ValueError(
                "FISH_AUDIO_BACKEND is required when FISH_AUDIO_API_KEY is set"
            )
        if custom_llm_url:
            # 自定义 LLM URL 为公网代理端点时，必须配置独立代理密钥验证 Agora 请求。
            if not custom_llm_proxy_key:
                raise ValueError(
                    "CUSTOM_LLM_PROXY_KEY is required when CUSTOM_LLM_URL is set"
                )
            # OpenAI 类型在此表示 OpenAI-compatible 协议适配器，不限定实际模型供应商。
            # base_url 指向本项目 FastAPI 代理，由代理使用服务端上游凭证并执行工具调用。
            llm = OpenAI(
                api_key=custom_llm_proxy_key,
                base_url=custom_llm_url,
                model=llm_model,
                greeting_message=self.greeting,
                failure_message="The assistant is temporarily unavailable.",
                max_history=llm_max_history,
                max_tokens=llm_max_tokens,
                temperature=0.3,
                top_p=0.9,
            )
        else:
            # 未设置自定义端点时保留 Agora SDK 托管模型路径，用于基线验证与降级运行。
            llm = OpenAI(
                model="gpt-4o-mini",
                greeting_message=self.greeting,
                failure_message="Please wait a moment.",
                max_history=llm_max_history,
                max_tokens=llm_max_tokens,
                temperature=0.3,
                top_p=0.9,
            )
        # STT 模块使用 Deepgram Nova-3 将 RTC 中的英语音频转换为文本。
        # language 固定为 en，与当前英文 Demo 的系统提示词和交互界面保持一致。
        stt = DeepgramSTT(model="nova-3", language="en")
        # 配置 Fish Audio 时保留 JARVIS 自定义音色，否则使用官方托管默认音色。
        if fish_audio_key:
            tts = FishAudioTTS(
                key=fish_audio_key,
                reference_id=fish_audio_reference_id,
                backend=fish_audio_backend,
            )
        else:
            tts = MiniMaxTTS(
                model="speech_2_6_turbo",
                voice_id="English_captivating_female1",
            )

        # 可选 BYOK 配置示例：显式提供 Deepgram API Key，替换上方托管 STT 配置。
        # stt = DeepgramSTT(api_key=os.getenv("DEEPGRAM_API_KEY"), model="nova-3", language="en")

        # 可选 BYOK 配置示例：显式提供 OpenAI API Key，替换上方 LLM 配置。
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

        # data_channel=rtm 启用转录、Agent 状态、文本输入、指标和错误等数据消息通道。
        # chorus 音频场景针对实时互动配置低延迟音频处理参数。
        parameters = {
            "audio_scenario": "chorus",  # Web Client 使用低延迟 chorus 音频场景。
            "data_channel": "rtm",
            "enable_error_message": True,
            "enable_metrics": True,
        }
        # 仅允许非空字符串覆盖输出编码，空值继续使用 Agora 默认编码策略。
        if isinstance(output_audio_codec, str) and output_audio_codec.strip():
            parameters["output_audio_codec"] = output_audio_codec.strip()

        # AgoraAgent 聚合系统指令、欢迎语、上下文长度、轮次检测和高级功能开关。
        agora_agent = AgoraAgent(
            client=self.client,
            instructions=JARVIS_PROMPT,
            greeting=self.greeting,
            failure_message="Please wait a moment.",
            max_history=llm_max_history,
            # VAD 根据音频能量与持续时间判定说话开始、用户打断和说话结束。
            # 参数直接影响抢话响应速度、首字丢失概率以及误切分概率。
            turn_detection={
                "config": {
                    "speech_threshold": 0.5,
                    "start_of_speech": {
                        "mode": "vad",
                        "vad_config": {
                            # 连续语音达到 160ms 时触发新轮次，并允许中断 Agent 当前输出。
                            "interrupt_duration_ms": 160,
                            # 在检测点前保留 300ms 音频，降低句首音素被截断的风险。
                            "prefix_padding_ms": 300,
                        },
                    },
                    "end_of_speech": {
                        "mode": "vad",
                        "vad_config": {
                            # 连续静音 480ms 后结束当前用户轮次并提交至 LLM。
                            "silence_duration_ms": 480,
                        },
                    },
                },
            },
            # RTM 为文本与状态事件提供通道；enable_tools 允许 LLM 发起服务端函数调用。
            advanced_features={"enable_rtm": True, "enable_tools": True},
            parameters=parameters,
        )
        
        # 按 STT → LLM → TTS 的顺序组装级联式实时语音处理管线。
        # 每个 with_* 调用返回同一构建链，最终配置由 create_async_session 提交。
        agora_agent = (
            agora_agent
            .with_stt(stt)
            .with_llm(llm)
            .with_tts(tts)
        )

        # 创建云端异步会话，使 Agent 以独立 UID 加入与浏览器相同的 RTC 频道。
        # remote_uids 仅包含当前用户，避免 Agent 处理频道中其他参与者的音频。
        session = agora_agent.create_async_session(
            channel=channel_name,
            agent_uid=str(agent_uid),
            remote_uids=[str(user_uid)],
            # 前端和 Token 均使用整数 UID，因此关闭 string UID 模式。
            enable_string_uid=False,
            # 30 秒无活动自动结束会话；绝对有效期限制为一小时。
            idle_timeout=30,
            expires_in=3600,
        )

        # 启动前日志使用频道和 UID 关联分布式事件，但不记录任何鉴权凭证。
        logger.info(
            "Starting Agora agent channel=%s agent_uid=%s user_uid=%s",
            channel_name,
            agent_uid,
            user_uid,
        )

        try:
            # session.start() 在 Agora 云端完成 Agent 创建并返回唯一 agent_id。
            agent_id = await session.start()
        except Exception:
            logger.exception(
                "Failed to start Agora agent channel=%s agent_uid=%s user_uid=%s",
                channel_name,
                agent_uid,
                user_uid,
            )
            raise

        # 仅在云端启动成功后写入活动会话表，避免保存不可停止的半初始化对象。
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
        # agent_id 是停止操作的唯一定位键，空值不应下沉至 SDK。
        if not agent_id or not str(agent_id).strip():
            raise ValueError("agent_id is required and cannot be empty")

        # pop 先从本地活动表移除会话，使重复停止请求不会并发操作同一 Session 对象。
        session = self._sessions.pop(agent_id, None)
        if session:
            try:
                await session.stop()
                logger.info("Stopped Agora agent from active session agent_id=%s", agent_id)
                return
            except Exception:
                # Session 句柄失效时降级到无状态 SDK 停止接口，继续尝试释放云端资源。
                logger.warning(
                    "Failed to stop Agora agent from active session; falling back to client.stop_agent agent_id=%s",
                    agent_id,
                    exc_info=True,
                )

        # 服务重启后内存会话表为空，无状态 client.stop_agent 仍可通过 agent_id 停止会话。
        logger.info("Stopping Agora agent through client.stop_agent agent_id=%s", agent_id)
        await self.client.stop_agent(agent_id)
