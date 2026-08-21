import asyncio
import json

import pytest
from fastapi import HTTPException

import custom_llm


def test_proxy_auth_rejects_wrong_secret(fake_env, monkeypatch):
    monkeypatch.setenv("CUSTOM_LLM_PROXY_KEY", "expected")
    with pytest.raises(HTTPException) as exc:
        custom_llm._require_proxy_auth("Bearer wrong")
    assert exc.value.status_code == 401
    custom_llm._require_proxy_auth("Bearer expected")


def test_notion_tool_is_always_available(fake_env):
    request = custom_llm.ChatCompletionRequest(
        messages=[{"role": "user", "content": "Add a task"}]
    )
    names = [tool["function"]["name"] for tool in custom_llm._tools_for_request(request)]
    assert names == ["create_notion_task"]


def test_completion_options_forward_provider_thinking_mode(fake_env, monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "glm-4.5-flash")
    monkeypatch.setenv("LLM_THINKING_TYPE", "disabled")
    request = custom_llm.ChatCompletionRequest(
        messages=[{"role": "user", "content": "Add a task"}]
    )

    options = custom_llm._completion_options(
        request,
        request.messages,
        custom_llm._tools_for_request(request),
    )

    assert options["model"] == "glm-4.5-flash"
    assert options["extra_body"] == {"thinking": {"type": "disabled"}}


def test_execute_tool_passes_context_and_call_id(fake_env, monkeypatch):
    captured = {}

    async def fake_tool(app_id, user_id, channel, args, tool_call_id):
        captured.update(
            app_id=app_id,
            user_id=user_id,
            channel=channel,
            args=args,
            tool_call_id=tool_call_id,
        )
        return json.dumps({"ok": True})

    monkeypatch.setattr(custom_llm.notion_tasks, "create_task_tool", fake_tool)
    results = asyncio.run(
        custom_llm._execute_tools(
            [
                {
                    "id": "call-7",
                    "type": "function",
                    "function": {
                        "name": "create_notion_task",
                        "arguments": '{"title":"Ship demo"}',
                    },
                }
            ],
            {"appId": "app", "userId": "user", "channel": "room"},
        )
    )
    assert captured == {
        "app_id": "app",
        "user_id": "user",
        "channel": "room",
        "args": {"title": "Ship demo"},
        "tool_call_id": "call-7",
    }
    assert results[0]["role"] == "tool"
    assert results[0]["tool_call_id"] == "call-7"
