"""Isolated upload tests: no application startup, .env reads or live clients.

FastAPI, OpenAI error types and SQLAlchemy are real. The LangChain/PGVector
construction, authentication, configuration and Streamlit surface are mocked
before importing production modules. SQL tests use a disposable SQLite :memory:
database, never the configured PostgreSQL database.
"""

import importlib
import socket
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Annotated
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
import httpx
import requests
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root / "backend"))
    monkeypatch.syspath_prepend(str(root / "frontend" / "gui"))

    def deny_network(*args, **kwargs):
        raise AssertionError("Live network access is forbidden in upload tests")

    monkeypatch.setattr(socket, "create_connection", deny_network)
    # Windows asyncio uses socketpair internally; block outbound HTTP transports
    # without blocking the event loop's own local wake-up socket.
    monkeypatch.setattr(requests.Session, "send", deny_network)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", deny_network)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", deny_network)

    def module(name, **attrs):
        result = ModuleType(name)
        result.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, result)
        return result

    settings = SimpleNamespace(
        embeddings_api_key="mock-embedding-only",
        embeddings_provider="openai",
        embeddings_model_name="mock-existing-model",
        embeddings_base_url="https://unused.invalid",
        model_provider="openai",
        dashscope_api_key="mock-only",
        pgvector_connection="unused",
        pgvector_collection_name="mock-only-collection",
        backend_base_url="http://unused.invalid/api/v1",
        document_upload_connect_timeout=10,
        document_upload_read_timeout=300,
    )
    module("app.config", settings=settings, BASE_DIR=tmp_path)
    module("config", settings=settings)

    user = SimpleNamespace(id=uuid4(), access_token="mock-token")
    session = AsyncMock(spec=AsyncSession)

    async def get_user():
        return user

    async def get_session():
        yield session

    module("app.auth.dependencies", CurrentUserDep=Annotated[object, Depends(get_user)])
    module("app.db.main", SessionDep=Annotated[AsyncSession, Depends(get_session)])
    for scope, function in (("knowledge_bases", "get_knowledge_base"), ("threads", "get_thread")):
        module(f"app.{scope}.service", **{function: AsyncMock()})

    @dataclass
    class Chunk:
        page_content: str
        metadata: dict = field(default_factory=dict)

    loader = Mock()
    loader.return_value.aload = AsyncMock(return_value=[Chunk("PDF mock text")])
    splitter = Mock()
    splitter.return_value.split_documents.side_effect = lambda documents: documents
    store = SimpleNamespace(aadd_documents=AsyncMock(return_value=["own-chunk"]), adelete=AsyncMock())
    module("langchain", __path__=[])
    module("langchain.embeddings", init_embeddings=Mock(return_value=object()))
    module("langchain.text_splitter", RecursiveCharacterTextSplitter=splitter)
    module("langchain_core", __path__=[])
    module("langchain_core.documents", Document=Chunk)
    module("langchain_community", __path__=[])
    module("langchain_community.document_loaders", __path__=[], Docx2txtLoader=loader, PyPDFLoader=loader, TextLoader=loader)
    module("langchain_community.document_loaders.base", BaseLoader=object)
    module("langchain_postgres", PGVector=Mock(return_value=store))

    st = module("streamlit", session_state={"user": user})
    module("streamlit.runtime", __path__=[])
    module("streamlit.runtime.uploaded_file_manager", UploadedFile=object)

    # Reload only the tested modules; no main/lifespan or real Settings imports.
    names = [
        "app.documents.errors", "app.db.pgvector_utils", "app.documents.schemas",
        "app.documents.service", "app.documents.routes", "api_utils", "state_management", "sidebar",
    ]
    previous = {name: sys.modules.pop(name) for name in names if name in sys.modules}
    try:
        loaded = [importlib.import_module(name) for name in names]
        yield SimpleNamespace(
            **dict(zip(("errors", "pg", "schemas", "service", "routes", "api", "state", "sidebar"), loaded)),
            user=user, session=session, loader=loader, store=store, st=st,
            Chunk=Chunk, tmp_path=tmp_path,
        )
    finally:
        for name in names:
            sys.modules.pop(name, None)
        sys.modules.update(previous)
