"""Agent env validation + managed-OpenAI wiring (SDK session monkeypatched)."""
import asyncio
import sys

import pytest


def _fresh_agent_module():
    sys.modules.pop("agent", None)
    import agent

    return agent


@pytest.mark.parametrize("missing", ["AGORA_APP_ID", "AGORA_APP_CERTIFICATE"])
def test_agent_requires_env(fake_env, monkeypatch, missing):
    monkeypatch.delenv(missing, raising=False)
    agent = _fresh_agent_module()
    with pytest.raises(ValueError):
        agent.Agent()


def test_agent_constructs_with_full_env(fake_env):
    agent = _fresh_agent_module()
    instance = agent.Agent()
    assert instance.app_id == "0123456789abcdef0123456789abcdef"
    assert instance.client is not None


def test_start_wires_managed_openai_and_returns_shape(fake_env, monkeypatch):
    agent = _fresh_agent_module()
    captured = {}

    class FakeSession:
        async def start(self):
            return "test-agent-id"

        async def stop(self):
            captured["stopped"] = True

    def fake_create_async_session(self, **kwargs):
        captured["llm"] = self.llm
        captured["tts"] = self.tts
        captured["channel"] = kwargs.get("channel")
        captured["remote_uids"] = kwargs.get("remote_uids")
        return FakeSession()

    from agora_agent.agentkit import Agent as AgoraAgent

    monkeypatch.setattr(AgoraAgent, "create_async_session", fake_create_async_session)

    instance = agent.Agent()
    result = asyncio.run(instance.start(channel_name="ch", agent_uid=111, user_uid=222))

    assert result == {
        "agent_id": "test-agent-id",
        "channel_name": "ch",
        "status": "started",
    }
    # The LLM stage is the managed OpenAI vendor (gpt-4o-mini), NOT CustomLLM.
    assert captured["llm"]["url"] == "https://api.openai.com/v1/chat/completions"
    assert captured["llm"]["params"]["model"] == "gpt-4o-mini"
    assert captured["llm"]["style"] == "openai"
    assert "vendor" not in captured["llm"]  # managed OpenAI has no custom vendor key
    assert captured["tts"] == {
        "vendor": "fishaudio",
        "params": {
            "api_key": "fish-test-key",
            "reference_id": "7c1a7dc37829497593ab4db29eed387c",
            "backend": "s2.1-pro",
        },
    }
    assert captured["channel"] == "ch"
    assert captured["remote_uids"] == ["222"]


def test_start_wires_custom_llm_without_exposing_upstream_key(fake_env, monkeypatch):
    monkeypatch.setenv("CUSTOM_LLM_URL", "https://backend.example/chat/completions")
    monkeypatch.setenv("CUSTOM_LLM_PROXY_KEY", "proxy-secret")
    monkeypatch.setenv("LLM_API_KEY", "upstream-secret")
    agent = _fresh_agent_module()
    captured = {}

    class FakeSession:
        async def start(self):
            return "custom-agent-id"

    def fake_create_async_session(self, **kwargs):
        captured["llm"] = self.llm
        return FakeSession()

    from agora_agent.agentkit import Agent as AgoraAgent

    monkeypatch.setattr(AgoraAgent, "create_async_session", fake_create_async_session)
    asyncio.run(agent.Agent().start(channel_name="ch", agent_uid=111, user_uid=222))

    assert captured["llm"]["url"] == "https://backend.example/chat/completions"
    assert captured["llm"]["api_key"] == "proxy-secret"
    assert "upstream-secret" not in str(captured["llm"])


def test_start_uses_fish_audio_env_overrides(fake_env, monkeypatch):
    monkeypatch.setenv("FISH_AUDIO_REFERENCE_ID", "custom-reference")
    monkeypatch.setenv("FISH_AUDIO_BACKEND", "speech-1.5")
    agent = _fresh_agent_module()
    captured = {}

    class FakeSession:
        async def start(self):
            return "fish-agent-id"

    def fake_create_async_session(self, **kwargs):
        captured["tts"] = self.tts
        return FakeSession()

    from agora_agent.agentkit import Agent as AgoraAgent

    monkeypatch.setattr(AgoraAgent, "create_async_session", fake_create_async_session)
    asyncio.run(agent.Agent().start(channel_name="ch", agent_uid=111, user_uid=222))

    assert captured["tts"]["vendor"] == "fishaudio"
    assert captured["tts"]["params"]["reference_id"] == "custom-reference"
    assert captured["tts"]["params"]["backend"] == "speech-1.5"


def test_start_validates_arguments(fake_env, monkeypatch):
    agent = _fresh_agent_module()
    from agora_agent.agentkit import Agent as AgoraAgent

    monkeypatch.setattr(AgoraAgent, "create_async_session", lambda self, **k: None)
    instance = agent.Agent()
    with pytest.raises(ValueError):
        asyncio.run(instance.start(channel_name="", agent_uid=1, user_uid=2))
    with pytest.raises(ValueError):
        asyncio.run(instance.start(channel_name="c", agent_uid=0, user_uid=2))


def test_stop_uses_active_session_then_falls_back(fake_env, monkeypatch):
    agent = _fresh_agent_module()

    class FakeSession:
        def __init__(self):
            self.stopped = False

        async def start(self):
            return "agent-xyz"

        async def stop(self):
            self.stopped = True

    session = FakeSession()
    from agora_agent.agentkit import Agent as AgoraAgent

    monkeypatch.setattr(AgoraAgent, "create_async_session", lambda self, **k: session)
    instance = agent.Agent()

    fallback_calls = []

    async def fake_stop_agent(agent_id):
        fallback_calls.append(agent_id)

    monkeypatch.setattr(instance.client, "stop_agent", fake_stop_agent)

    asyncio.run(instance.start(channel_name="ch", agent_uid=111, user_uid=222))
    asyncio.run(instance.stop("agent-xyz"))
    assert session.stopped is True
    assert fallback_calls == []

    asyncio.run(instance.stop("unknown-id"))
    assert fallback_calls == ["unknown-id"]
