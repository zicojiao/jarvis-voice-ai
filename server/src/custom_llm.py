"""OpenAI-compatible Custom LLM endpoint derived from Agora's official sample."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import time
import uuid
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field

from notion_service import notion_tasks

logger = logging.getLogger("uvicorn.error")
router = APIRouter()

CREATE_TASK_TOOL = {
    "type": "function",
    "function": {
        "name": "create_task",
        "description": (
            "Create a real task in the user's connected task service. "
            "Use this whenever the user asks to add, save, remember, or create a task."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Concise task title without conversational filler",
                },
                "details": {
                    "type": "string",
                    "description": "Optional supporting notes",
                },
                "due_date": {
                    "type": "string",
                    "description": "Optional due date in ISO 8601 form when known",
                },
                "priority": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                },
            },
            "required": ["title"],
            "additionalProperties": False,
        },
    },
}


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    context: dict[str, Any] | None = None
    model: str | None = None
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = "auto"
    parallel_tool_calls: bool = True
    stream: bool = True
    stream_options: dict[str, Any] | None = None
    temperature: float | None = None
    max_tokens: int | None = Field(default=None, gt=0)


def _require_proxy_auth(authorization: str | None) -> None:
    expected = os.getenv("CUSTOM_LLM_PROXY_KEY", "")
    if not expected:
        return
    provided = authorization or ""
    candidates = {expected, f"Bearer {expected}"}
    if not any(hmac.compare_digest(provided, candidate) for candidate in candidates):
        raise HTTPException(status_code=401, detail="Invalid custom LLM credential")


def _tools_for_request(request: ChatCompletionRequest) -> list[dict[str, Any]]:
    tools = list(request.tools or [])
    names = {
        tool.get("function", {}).get("name")
        for tool in tools
        if isinstance(tool, dict)
    }
    if "create_task" not in names:
        tools.append(CREATE_TASK_TOOL)
    return tools


def _accumulate_tool_calls(
    accumulated: list[dict[str, Any]], deltas: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    for delta in deltas:
        index = delta.get("index", 0)
        while len(accumulated) <= index:
            accumulated.append({"type": "function", "function": {}})
        item = accumulated[index]
        if delta.get("id"):
            item["id"] = delta["id"]
        function = delta.get("function") or {}
        if function.get("name"):
            item["function"]["name"] = function["name"]
        if function.get("arguments") is not None:
            item["function"]["arguments"] = (
                item["function"].get("arguments", "") + function["arguments"]
            )
    return accumulated


async def _execute_tools(
    tool_calls: list[dict[str, Any]], context: dict[str, Any]
) -> list[dict[str, Any]]:
    results = []
    for tool_call in tool_calls:
        function = tool_call.get("function") or {}
        name = function.get("name", "")
        tool_call_id = tool_call.get("id", "")
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except json.JSONDecodeError:
            arguments = {}

        if name in {"create_task", "create_notion_task"}:
            content = await notion_tasks.create_task_tool(
                str(context.get("appId", "")),
                str(context.get("userId", "")),
                str(context.get("channel", "default")),
                arguments,
                tool_call_id,
            )
        else:
            content = json.dumps({"ok": False, "error": f"Unknown tool: {name}"})

        results.append(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "name": name,
                "content": content,
            }
        )
    return results


def _upstream_client() -> AsyncOpenAI:
    api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="LLM_API_KEY is not configured")
    return AsyncOpenAI(
        api_key=api_key,
        base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
    )


def _completion_options(
    request: ChatCompletionRequest, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
) -> dict[str, Any]:
    configured_max_tokens = max(32, int(os.getenv("LLM_MAX_TOKENS", "256")))
    requested_max_tokens = request.max_tokens or configured_max_tokens
    options: dict[str, Any] = {
        "model": os.getenv("LLM_MODEL", "gpt-4o-mini"),
        "messages": messages,
        "tools": tools,
        "tool_choice": request.tool_choice or "auto",
        "parallel_tool_calls": request.parallel_tool_calls,
        "max_tokens": min(requested_max_tokens, configured_max_tokens),
    }
    if request.temperature is not None:
        options["temperature"] = request.temperature
    thinking_type = os.getenv("LLM_THINKING_TYPE", "").strip()
    if thinking_type:
        options["extra_body"] = {"thinking": {"type": thinking_type}}
    return options


def _direct_tool_confirmation(tool_results: list[dict[str, Any]]) -> str | None:
    """Turn our single task result into the short spoken answer without a second LLM call."""
    if len(tool_results) != 1:
        return None
    result = tool_results[0]
    if result.get("name") not in {"create_task", "create_notion_task"}:
        return None
    try:
        payload = json.loads(result.get("content") or "{}")
    except (TypeError, json.JSONDecodeError):
        return None

    if payload.get("ok") is True:
        title = str((payload.get("task") or {}).get("title") or "the task")
        return f'Done — I added "{title}" to your tasks.'
    error = str(payload.get("error") or "the task service rejected the request")
    return f"I couldn't create that task: {error}"


def _completion_chunk(
    *, content: str | None, finish_reason: str | None, completion_id: str
) -> str:
    delta: dict[str, Any] = {}
    if content is not None:
        delta = {"role": "assistant", "content": content}
    payload = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": os.getenv("LLM_MODEL", "gpt-4o-mini"),
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
    }
    return f"data: {json.dumps(payload)}\n\n"


@router.post("/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    authorization: str | None = Header(default=None),
):
    request_id = uuid.uuid4().hex[:8]
    request_started = time.perf_counter()
    _require_proxy_auth(authorization)
    client = _upstream_client()
    tools = _tools_for_request(request)
    context = request.context or {}
    messages = list(request.messages)
    logger.info(
        "Custom LLM request id=%s model=%s messages=%s tools=%s stream=%s",
        request_id,
        os.getenv("LLM_MODEL", "gpt-4o-mini"),
        len(messages),
        len(tools),
        request.stream,
    )

    if not request.stream:
        response = None
        for _ in range(5):
            response = await client.chat.completions.create(
                **_completion_options(request, messages, tools)
            )
            message = response.choices[0].message
            if not message.tool_calls:
                return JSONResponse(response.model_dump())
            calls = [call.model_dump() for call in message.tool_calls]
            messages.append(
                {
                    "role": "assistant",
                    "content": message.content or "",
                    "tool_calls": calls,
                }
            )
            messages.extend(await _execute_tools(calls, context))
        return JSONResponse(response.model_dump() if response else {})

    async def generate():
        current_messages = list(messages)
        for pass_index in range(5):
            pass_started = time.perf_counter()
            stream = await client.chat.completions.create(
                **_completion_options(request, current_messages, tools), stream=True
            )
            logger.info(
                "Custom LLM upstream open id=%s pass=%s elapsed_ms=%s",
                request_id,
                pass_index + 1,
                round((time.perf_counter() - pass_started) * 1000),
            )
            accumulated_calls: list[dict[str, Any]] = []
            accumulated_content = ""
            finish_reason = None
            first_chunk_logged = False
            first_content_logged = False

            try:
                async for chunk in stream:
                    if not first_chunk_logged:
                        first_chunk_logged = True
                        logger.info(
                            "Custom LLM first chunk id=%s pass=%s elapsed_ms=%s",
                            request_id,
                            pass_index + 1,
                            round((time.perf_counter() - request_started) * 1000),
                        )
                    choice = chunk.choices[0] if chunk.choices else None
                    delta = choice.delta if choice else None
                    finish_reason = choice.finish_reason if choice else finish_reason
                    if delta and delta.tool_calls:
                        _accumulate_tool_calls(
                            accumulated_calls,
                            [call.model_dump() for call in delta.tool_calls],
                        )
                        continue
                    if delta and delta.content:
                        if not first_content_logged:
                            first_content_logged = True
                            logger.info(
                                "Custom LLM first content id=%s pass=%s elapsed_ms=%s",
                                request_id,
                                pass_index + 1,
                                round((time.perf_counter() - request_started) * 1000),
                            )
                        accumulated_content += delta.content
                    yield f"data: {json.dumps(chunk.model_dump())}\n\n"
            except asyncio.CancelledError:
                logger.info("Custom LLM stream cancelled")
                raise

            if finish_reason == "tool_calls" and accumulated_calls:
                current_messages.append(
                    {
                        "role": "assistant",
                        "content": accumulated_content,
                        "tool_calls": accumulated_calls,
                    }
                )
                tool_started = time.perf_counter()
                tool_results = await _execute_tools(accumulated_calls, context)
                logger.info(
                    "Custom LLM tools complete id=%s count=%s elapsed_ms=%s total_ms=%s",
                    request_id,
                    len(tool_results),
                    round((time.perf_counter() - tool_started) * 1000),
                    round((time.perf_counter() - request_started) * 1000),
                )
                confirmation = _direct_tool_confirmation(tool_results)
                if confirmation is not None:
                    completion_id = f"chatcmpl-tool-{request_id}"
                    yield _completion_chunk(
                        content=confirmation,
                        finish_reason=None,
                        completion_id=completion_id,
                    )
                    yield _completion_chunk(
                        content=None,
                        finish_reason="stop",
                        completion_id=completion_id,
                    )
                    yield "data: [DONE]\n\n"
                    logger.info(
                        "Custom LLM direct tool response id=%s total_ms=%s",
                        request_id,
                        round((time.perf_counter() - request_started) * 1000),
                    )
                    return
                current_messages.extend(tool_results)
                continue

            yield "data: [DONE]\n\n"
            logger.info(
                "Custom LLM response complete id=%s total_ms=%s",
                request_id,
                round((time.perf_counter() - request_started) * 1000),
            )
            return
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
