"""OpenAI-compatible Custom LLM endpoint derived from Agora's official sample."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field

from notion_service import notion_tasks

logger = logging.getLogger("uvicorn.error")
router = APIRouter()

CREATE_NOTION_TASK_TOOL = {
    "type": "function",
    "function": {
        "name": "create_notion_task",
        "description": (
            "Create a real task in the user's configured Notion task data source. "
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
    if "create_notion_task" not in names:
        tools.append(CREATE_NOTION_TASK_TOOL)
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

        if name == "create_notion_task":
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
    options: dict[str, Any] = {
        "model": os.getenv("LLM_MODEL", "gpt-4o-mini"),
        "messages": messages,
        "tools": tools,
        "tool_choice": request.tool_choice or "auto",
        "parallel_tool_calls": request.parallel_tool_calls,
    }
    if request.temperature is not None:
        options["temperature"] = request.temperature
    if request.max_tokens is not None:
        options["max_tokens"] = request.max_tokens
    thinking_type = os.getenv("LLM_THINKING_TYPE", "").strip()
    if thinking_type:
        options["extra_body"] = {"thinking": {"type": thinking_type}}
    return options


@router.post("/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    authorization: str | None = Header(default=None),
):
    _require_proxy_auth(authorization)
    client = _upstream_client()
    tools = _tools_for_request(request)
    context = request.context or {}
    messages = list(request.messages)

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
        for _ in range(5):
            stream = await client.chat.completions.create(
                **_completion_options(request, current_messages, tools), stream=True
            )
            accumulated_calls: list[dict[str, Any]] = []
            accumulated_content = ""
            finish_reason = None

            try:
                async for chunk in stream:
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
                current_messages.extend(await _execute_tools(accumulated_calls, context))
                continue

            yield "data: [DONE]\n\n"
            return
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
