"""Notion task creation through the current REST API.

The service intentionally writes only the data source title property. Optional
task metadata is stored in the page body so the demo works with any task schema.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections import deque
from datetime import datetime, timezone
from typing import Any

import httpx

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2025-09-03"


class NotionConfigurationError(RuntimeError):
    """Raised when the runtime is missing required Notion settings."""


class NotionTaskService:
    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._http_client = http_client
        self._recent: deque[dict[str, Any]] = deque(maxlen=12)
        self._completed_calls: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        return bool(self._token and self._data_source_id)

    @property
    def _token(self) -> str:
        return os.getenv("NOTION_API_KEY") or os.getenv("NOTION_TOKEN") or ""

    @property
    def _data_source_id(self) -> str:
        return os.getenv("NOTION_DATA_SOURCE_ID", "")

    @property
    def _title_property(self) -> str:
        return os.getenv("NOTION_TITLE_PROPERTY", "Name")

    def status(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "api_version": NOTION_VERSION,
            "title_property": self._title_property,
        }

    def recent_tasks(self) -> list[dict[str, Any]]:
        return list(self._recent)

    async def create_task(
        self,
        *,
        title: str,
        details: str | None = None,
        due_date: str | None = None,
        priority: str | None = None,
        tool_call_id: str | None = None,
    ) -> dict[str, Any]:
        title = title.strip()
        if not title:
            raise ValueError("Task title cannot be empty")
        if not self.configured:
            raise NotionConfigurationError(
                "Notion is not configured. Set NOTION_API_KEY and "
                "NOTION_DATA_SOURCE_ID on the backend."
            )

        async with self._lock:
            if tool_call_id and tool_call_id in self._completed_calls:
                return self._completed_calls[tool_call_id]

            body_lines = [line for line in [details, f"Due: {due_date}" if due_date else None, f"Priority: {priority}" if priority else None] if line]
            payload: dict[str, Any] = {
                "parent": {
                    "type": "data_source_id",
                    "data_source_id": self._data_source_id,
                },
                "properties": {
                    self._title_property: {
                        "type": "title",
                        "title": [{"type": "text", "text": {"content": title}}],
                    }
                },
            }
            if body_lines:
                payload["children"] = [
                    {
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [
                                {
                                    "type": "text",
                                    "text": {"content": "\n".join(body_lines)},
                                }
                            ]
                        },
                    }
                ]

            headers = {
                "Authorization": f"Bearer {self._token}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            }

            owns_client = self._http_client is None
            client = self._http_client or httpx.AsyncClient(timeout=15.0)
            try:
                response = await client.post(
                    f"{NOTION_API_BASE}/pages", headers=headers, json=payload
                )
                response.raise_for_status()
                page = response.json()
            except httpx.HTTPStatusError as exc:
                try:
                    detail = exc.response.json().get("message", exc.response.text)
                except (ValueError, AttributeError):
                    detail = exc.response.text
                raise RuntimeError(f"Notion rejected the task: {detail}") from exc
            except httpx.HTTPError as exc:
                raise RuntimeError(f"Notion request failed: {exc}") from exc
            finally:
                if owns_client:
                    await client.aclose()

            record = {
                "id": page.get("id", ""),
                "title": title,
                "url": page.get("url", ""),
                "due_date": due_date,
                "priority": priority,
                "status": "created",
                "created_at": page.get("created_time")
                or datetime.now(timezone.utc).isoformat(),
            }
            self._recent.appendleft(record)
            if tool_call_id:
                self._completed_calls[tool_call_id] = record
            return record

    async def create_task_tool(
        self,
        app_id: str,
        user_id: str,
        channel: str,
        args: dict[str, Any],
        tool_call_id: str,
    ) -> str:
        del app_id, user_id, channel
        try:
            task = await self.create_task(
                title=str(args.get("title", "")),
                details=args.get("details"),
                due_date=args.get("due_date"),
                priority=args.get("priority"),
                tool_call_id=tool_call_id,
            )
            return json.dumps({"ok": True, "task": task})
        except (ValueError, NotionConfigurationError, RuntimeError) as exc:
            return json.dumps({"ok": False, "error": str(exc)})


notion_tasks = NotionTaskService()
