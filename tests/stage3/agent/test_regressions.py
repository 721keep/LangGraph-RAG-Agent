"""Regression evidence uses production code and no external services."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from bootstrap import configure
configure()

import httpx
from fastapi import FastAPI, HTTPException
from langchain_core.messages import AIMessage, ToolMessage
from app.chat.schemas import ChatStreamResponse


def test_parallel_tool_results_are_all_streamed():
    async def exercise():
        response = ChatStreamResponse(None)
        messages = [ToolMessage(content='first', name='retrieve_user_documents', tool_call_id='call_a'),
                    ToolMessage(content='second', name='get_server_status', tool_call_id='call_b')]
        return [json.loads(line) async for line in response._handle_updates_stream({'tools': {'messages': messages}})]
    events = asyncio.run(exercise())
    assert [e['content'] for e in events] == ['first', 'second']


def test_closed_checkpointer_is_not_reused(monkeypatch):
    from app.db import checkpointer as module
    connection = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(module, 'connection', connection)
    monkeypatch.setattr(module, 'checkpointer', object())
    asyncio.run(module.close_connection())
    connection.close.assert_awaited_once()
    assert module.connection is None and module.checkpointer is None


def test_history_checks_ownership_before_checkpoint_read(monkeypatch):
    from app.auth.dependencies import get_current_user
    from app.db.main import get_session
    from app.chat.routes import chat_router
    from app.chat import service
    saver = SimpleNamespace(aget=AsyncMock(return_value={'channel_values': {'messages': [AIMessage(content='fictional other user history')]}}))
    get_saver = AsyncMock(return_value=saver)
    denied = AsyncMock(side_effect=HTTPException(403, 'Wrong test owner'))
    monkeypatch.setattr(service, 'get_checkpointer', get_saver)
    monkeypatch.setattr(service.thread_service, 'get_thread', denied)
    app = FastAPI()
    app.include_router(chat_router, prefix='/api/v1/chat')
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uuid4())
    app.dependency_overrides[get_session] = lambda: object()

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://asgi.invalid') as client:
            return await client.get('/api/v1/chat/' + str(uuid4()))
    response = asyncio.run(exercise())
    assert response.status_code == 403
    get_saver.assert_not_awaited()
    saver.aget.assert_not_awaited()
