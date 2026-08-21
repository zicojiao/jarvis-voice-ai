import asyncio
import json
from types import SimpleNamespace

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
    assert names == ["create_task"]


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
    assert options["max_tokens"] == 256


def test_completion_options_cap_requested_tokens(fake_env, monkeypatch):
    monkeypatch.setenv("LLM_MAX_TOKENS", "128")
    request = custom_llm.ChatCompletionRequest(
        messages=[{"role": "user", "content": "Explain this"}], max_tokens=1024
    )

    options = custom_llm._completion_options(
        request, request.messages, custom_llm._tools_for_request(request)
    )

    assert options["max_tokens"] == 128


def test_direct_tool_confirmation_skips_second_model_pass(fake_env):
    success = custom_llm._direct_tool_confirmation(
        [
            {
                "name": "create_task",
                "content": json.dumps(
                    {"ok": True, "task": {"title": "Prepare the demo"}}
                ),
            }
        ]
    )
    failure = custom_llm._direct_tool_confirmation(
        [
            {
                "name": "create_task",
                "content": json.dumps({"ok": False, "error": "Not configured"}),
            }
        ]
    )

    assert success == 'Done — I added "Prepare the demo" to your tasks.'
    assert failure == "I couldn't create that task: Not configured"


def test_completion_chunk_is_openai_compatible(fake_env):
    event = custom_llm._completion_chunk(
        content="Done.", finish_reason=None, completion_id="chatcmpl-test"
    )
    payload = json.loads(event.removeprefix("data: "))

    assert payload["object"] == "chat.completion.chunk"
    assert payload["choices"][0]["delta"]["content"] == "Done."


def test_streaming_tool_result_returns_without_second_upstream_pass(
    fake_env, monkeypatch
):
    upstream_calls = []

    class FakeToolDelta:
        def model_dump(self):
            return {
                "index": 0,
                "id": "call-fast",
                "type": "function",
                "function": {
                    "name": "create_task",
                    "arguments": '{"title":"Fast task"}',
                },
            }

    class FakeStream:
        def __init__(self):
            self.chunks = iter(
                [
                    SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                delta=SimpleNamespace(
                                    tool_calls=[FakeToolDelta()], content=None
                                ),
                                finish_reason=None,
                            )
                        ],
                        model_dump=lambda: {"choices": []},
                    ),
                    SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                delta=SimpleNamespace(tool_calls=None, content=None),
                                finish_reason="tool_calls",
                            )
                        ],
                        model_dump=lambda: {"choices": []},
                    ),
                ]
            )

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self.chunks)
            except StopIteration as exc:
                raise StopAsyncIteration from exc

    class FakeCompletions:
        async def create(self, **kwargs):
            upstream_calls.append(kwargs)
            return FakeStream()

    async def fake_execute_tools(tool_calls, context):
        return [
            {
                "role": "tool",
                "tool_call_id": "call-fast",
                "name": "create_task",
                "content": json.dumps(
                    {"ok": True, "task": {"title": "Fast task"}}
                ),
            }
        ]

    monkeypatch.setattr(
        custom_llm,
        "_upstream_client",
        lambda: SimpleNamespace(
            chat=SimpleNamespace(completions=FakeCompletions())
        ),
    )
    monkeypatch.setattr(custom_llm, "_execute_tools", fake_execute_tools)

    async def run():
        response = await custom_llm.chat_completions(
            custom_llm.ChatCompletionRequest(
                messages=[{"role": "user", "content": "Add fast task"}]
            )
        )
        parts = []
        async for part in response.body_iterator:
            parts.append(part.decode() if isinstance(part, bytes) else part)
        return "".join(parts)

    body = asyncio.run(run())
    events = [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: {")
    ]
    response_events = [event for event in events if event.get("choices")]

    assert len(upstream_calls) == 1
    assert response_events[0]["choices"][0]["delta"]["content"] == (
        'Done — I added "Fast task" to your tasks.'
    )
    assert response_events[1]["choices"][0]["finish_reason"] == "stop"
    assert body.endswith("data: [DONE]\n\n")


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
                        "name": "create_task",
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


def test_legacy_notion_tool_name_remains_executable(fake_env, monkeypatch):
    async def fake_tool(*args):
        return json.dumps({"ok": True})

    monkeypatch.setattr(custom_llm.notion_tasks, "create_task_tool", fake_tool)
    results = asyncio.run(
        custom_llm._execute_tools(
            [
                {
                    "id": "legacy-call",
                    "function": {
                        "name": "create_notion_task",
                        "arguments": '{"title":"Legacy task"}',
                    },
                }
            ],
            {},
        )
    )
    assert json.loads(results[0]["content"])["ok"] is True
