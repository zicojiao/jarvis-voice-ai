import asyncio

import httpx
import pytest

from notion_service import NOTION_VERSION, NotionConfigurationError, NotionTaskService


def test_requires_notion_configuration(fake_env):
    service = NotionTaskService()
    with pytest.raises(NotionConfigurationError):
        asyncio.run(service.create_task(title="Prepare demo"))


def test_create_task_uses_data_source_api_and_is_idempotent(fake_env, monkeypatch):
    monkeypatch.setenv("NOTION_API_KEY", "notion-secret")
    monkeypatch.setenv("NOTION_DATA_SOURCE_ID", "source-123")
    monkeypatch.setenv("NOTION_TITLE_PROPERTY", "Task")
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "page-1",
                "url": "https://notion.so/page-1",
                "created_time": "2026-08-21T00:00:00.000Z",
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = NotionTaskService(client)

    async def run():
        first = await service.create_task(
            title="Prepare KOL demo",
            details="Show the voice workflow",
            due_date="2026-08-22",
            priority="high",
            tool_call_id="call-1",
        )
        second = await service.create_task(
            title="Prepare KOL demo", tool_call_id="call-1"
        )
        await client.aclose()
        return first, second

    first, second = asyncio.run(run())
    assert first == second
    assert len(requests) == 1
    request = requests[0]
    assert request.headers["authorization"] == "Bearer notion-secret"
    assert request.headers["notion-version"] == NOTION_VERSION
    payload = __import__("json").loads(request.content)
    assert payload["parent"] == {
        "type": "data_source_id",
        "data_source_id": "source-123",
    }
    assert payload["properties"]["Task"]["title"][0]["text"]["content"] == "Prepare KOL demo"
    assert service.recent_tasks()[0]["url"] == "https://notion.so/page-1"
