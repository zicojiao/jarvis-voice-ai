# -*- coding: utf-8 -*-
"""
Agora Agent & Token Service

HTTP APIs:
- GET  /get_config     -> Agent.generate_config()
- POST /startAgent     -> Agent.start()
- POST /stopAgent      -> Agent.stop()
"""
import logging
import os
import random
import time
from typing import Any, Dict, Optional
from dotenv import load_dotenv

# Load environment variables from .env.local or .env
_base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_base_dir, '.env.local'), override=True)
load_dotenv(os.path.join(_base_dir, '.env'), override=True)

from fastapi import APIRouter, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from agora_agent.agentkit.token import generate_convo_ai_token
from agent import Agent
from custom_llm import router as custom_llm_router
from notion_service import notion_tasks

logger = logging.getLogger("uvicorn.error")


# 路由层日志只记录定位问题所需的非敏感上下文，禁止写入 Token、证书或上游 API Key。
def _log_route_error(route: str, exc: Exception, **context) -> None:
    """Log route failures with safe request context and a traceback."""
    safe_context = {key: value for key, value in context.items() if value is not None}
    logger.exception(
        "Request failed route=%s context=%s error_type=%s error=%s",
        route,
        safe_context,
        type(exc).__name__,
        exc,
    )


# 将内部异常收敛为稳定的 HTTP 状态码，避免 SDK 异常类型直接泄漏到前端协议。
def _to_http_error(exc: Exception) -> HTTPException:
    """Convert SDK exceptions to HTTP errors"""
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, RuntimeError):
        return HTTPException(status_code=500, detail=str(exc))
    return HTTPException(status_code=500, detail=f"Internal error: {exc}")

# Agent 在进程启动阶段初始化，以便复用底层 SDK Client 并统一维护活动会话。
# 配置缺失时保留 FastAPI 进程，使 /health 仍可返回可观测的未配置状态。
try:
    agent = Agent()
except ValueError as e:
    logger.exception(
        "Failed to initialize Agora Agent SDK. Service will fail if endpoints are called without proper configuration: %s",
        e,
    )
    agent = None


# FastAPI 应用仅承载鉴权配置签发、Agent 生命周期和自定义 LLM 代理接口。
app = FastAPI(
    title="Agora Agent & Token Service",
    version="2.0.0",
    description="Agora Conversational AI service",
)

app.add_middleware(
    CORSMiddleware,
    # 允许来源由部署环境显式配置，默认只允许本地 Next.js 开发服务器。
    allow_origins=[
        origin.strip()
        for origin in os.getenv(
            "CORS_ORIGINS", "http://localhost:3000"
        ).split(",")
        if origin.strip()
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

router = APIRouter()


# 请求模型在进入业务逻辑前完成结构校验，减少 SDK 层处理无效输入的分支。
class StartAgentRequest(BaseModel):
    """Request body for POST /startAgent"""
    channelName: str
    rtcUid: int
    userUid: int
    parameters: Optional[Dict[str, Any]] = None


class StopAgentRequest(BaseModel):
    """Request body for POST /stopAgent"""
    agentId: str


# 时间戳与随机后缀共同降低并发创建会话时的频道标识碰撞概率。
def _generate_channel_name() -> str:
    return f"ai-conversation-{int(time.time())}-{random.randint(1000, 9999)}"


@router.get("/get_config")
async def get_config(
    channel: Optional[str] = Query(default=None),
    uid: Optional[int] = Query(default=None),
):
    """Generate connection configuration"""
    # 未初始化 Agent 表示 Agora 环境变量不完整，此时不能安全签发会话配置。
    if agent is None:
        raise HTTPException(
            status_code=500,
            detail="Service not properly configured. Please check environment variables.",
        )

    try:
        # 每次新会话生成独立频道与参与者身份，隔离不同浏览器会话的媒体和信令数据。
        # RTC 可将 uid=0 解释为自动分配，但 RTM Token subject 必须是非零确定值；
        # 因此缺失、零值或负值统一替换为服务器生成的正整数 UID。
        user_uid = random.randint(1000, 9999999) if uid is None or uid <= 0 else uid
        # Agent UID 与用户 UID 使用不同数值区间，便于前端识别远端 Agent 参与者。
        agent_uid = str(random.randint(10000000, 99999999))
        # Token 续期传入既有 channel；首次创建则生成新的频道标识。
        channel_name = channel or _generate_channel_name()

        # 长期凭证只从服务端环境变量读取，不接受客户端请求覆盖。
        app_id = os.getenv("AGORA_APP_ID")
        app_certificate = os.getenv("AGORA_APP_CERTIFICATE")

        # Conversational AI Token 同时授予 RTC 媒体权限与 RTM 数据消息权限，有效期一小时。
        # App Certificate 仅参与服务端签名过程，响应中只返回短期 Token，不暴露长期凭证。
        token = generate_convo_ai_token(
            app_id=app_id,
            app_certificate=app_certificate,
            channel_name=channel_name,
            uid=user_uid,
            token_expire=3600,
        )

        # 前端使用同一组身份信息初始化 RTC、RTM 和云端 Agent。
        config_data = {
            "app_id": app_id,
            "token": token,
            "uid": str(user_uid),
            "channel_name": channel_name,
            "agent_uid": agent_uid,
        }

        return {
            "code": 0,
            "data": config_data,
            "msg": "success",
        }
    except Exception as e:
        _log_route_error("/get_config", e, channel=channel, uid=uid)
        raise _to_http_error(e)


@router.post("/startAgent")
async def start_agent(request: StartAgentRequest):
    """Start agent in a channel"""
    if agent is None:
        raise HTTPException(
            status_code=500,
            detail="Service not properly configured. Please check environment variables.",
        )

    try:
        # output_audio_codec 为可选透传参数；未设置时由 Agora 服务选择默认编码。
        output_audio_codec = None
        if request.parameters:
            output_audio_codec = request.parameters.get("output_audio_codec")

        # 客户端只提供频道与双方 UID；STT、LLM、TTS 供应商配置由可信后端统一组装。
        # 该边界防止浏览器修改系统提示词、供应商密钥或工具执行策略。
        result = await agent.start(
            channel_name=request.channelName,
            agent_uid=request.rtcUid,
            user_uid=request.userUid,
            output_audio_codec=output_audio_codec,
        )
        return {"code": 0, "msg": "success", "data": result}
    except Exception as e:
        _log_route_error(
            "/startAgent",
            e,
            channelName=request.channelName,
            rtcUid=request.rtcUid,
            userUid=request.userUid,
        )
        raise _to_http_error(e)


@router.post("/stopAgent")
async def stop_agent(request: StopAgentRequest):
    """Stop agent by ID"""
    if agent is None:
        raise HTTPException(
            status_code=500,
            detail="Service not properly configured. Please check environment variables.",
        )

    try:
        # agentId 是云端会话的唯一标识，停止操作据此释放 Agent 及关联服务资源。
        await agent.stop(request.agentId)
        return {"code": 0, "msg": "success"}
    except Exception as e:
        _log_route_error("/stopAgent", e, agentId=request.agentId)
        raise _to_http_error(e)


@router.get("/health")
async def health():
    """Deployment and UI readiness without exposing secrets."""
    # 健康检查只返回布尔配置状态和非敏感供应商元数据，不返回任何凭证内容。
    return {
        "status": "ok",
        "services": {
            "agora": {"configured": agent is not None},
            "custom_llm": {
                "configured": bool(
                    os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
                ),
                "enabled_for_agent": bool(os.getenv("CUSTOM_LLM_URL")),
            },
            "tts": {
                "configured": True,
                "vendor": (
                    "fishaudio" if os.getenv("FISH_AUDIO_API_KEY") else "minimax"
                ),
                "managed": not bool(os.getenv("FISH_AUDIO_API_KEY")),
                "reference_id": os.getenv(
                    "FISH_AUDIO_REFERENCE_ID",
                    "7c1a7dc37829497593ab4db29eed387c",
                )
                if os.getenv("FISH_AUDIO_API_KEY")
                else None,
                "backend": (
                    os.getenv("FISH_AUDIO_BACKEND", "s2.1-pro")
                    if os.getenv("FISH_AUDIO_API_KEY")
                    else "speech_2_6_turbo"
                ),
            },
            "notion": notion_tasks.status(),
        },
    }


@router.get("/tasks/recent")
async def recent_tasks():
    # 最近任务来自当前后端实例的有界内存队列，仅用于 Demo 界面即时反馈。
    return {
        "code": 0,
        "msg": "success",
        "data": {
            "configured": notion_tasks.configured,
            "tasks": notion_tasks.recent_tasks(),
        },
    }


# 业务路由与 OpenAI 兼容代理路由挂载到同一应用，便于 Agora 从公网访问 Custom LLM。
app.include_router(router)
app.include_router(custom_llm_router)


if __name__ == "__main__":
    import uvicorn

    # Railway 等托管平台通过 PORT 注入监听端口；本地开发默认使用 8000。
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
