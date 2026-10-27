"""Offline configuration and adapter contracts; no real .env or live services."""

import asyncio
import importlib
import importlib.util
import json
import os
import socket
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
import requests
from loguru import logger
from openai import AsyncOpenAI
from pydantic import ValidationError
from pydantic_settings import BaseSettings
from pydantic_settings.sources import DotEnvSettingsSource


ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = {
    "CHAT_API_KEY": "mock-chat-key",
    "DASHSCOPE_API_KEY": "",
    "tavily_api_key": "mock-search-key",
    "model_provider": "openai",
    "model_names": ["deepseek-v4-flash"],
    "model_base_url": "https://api.deepseek.com",
    "EMBEDDINGS_API_KEY": "mock-embedding-key",
    "embeddings_provider": "openai",
    "embeddings_model_name": "mock-bge-m3",
    "embeddings_base_url": "https://embedding.invalid/v1",
    "RERANK_API_KEY": "mock-rerank-key",
    "rerank_base_url": "https://rerank.invalid/v1",
    "rerank_model_name": "mock-bge-reranker",
    "token_bearer_url": "/auth/login",
    "jwt_secret": "mock-jwt",
    "jwt_algorithm": "HS256",
    "access_token_expiry_mins": 60,
    "refresh_token_expiry_days": 1,
    "postgres_host": "unused.invalid",
    "postgres_port": 5432,
    "postgres_user": "mock-user",
    "postgres_password": "mock-password",
    "postgres_database": "mock-database",
    "pgvector_collection_name": "mock-existing-collection",
}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("Live network access is forbidden in model tests")

    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(requests.Session, "send", deny)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", deny)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", deny)


@pytest.fixture
def make_settings(monkeypatch):
    # Execute the actual config module with dummy init values. Disable dotenv
    # before module execution, including its global Settings() construction.
    monkeypatch.setattr(os, "environ", {})

    def no_dotenv(self):
        assert self.env_file is None, "Tests must never read the real .env"
        return {}

    monkeypatch.setattr(DotEnvSettingsSource, "_read_env_files", no_dotenv)
    spec = importlib.util.spec_from_file_location("phase2_real_config", ROOT / "backend/app/config.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    original_init = BaseSettings.__init__
    original_exists = Path.exists

    def isolated_init(self, **kwargs):
        original_init(self, _env_file=None, **(DEFAULTS | kwargs))

    with monkeypatch.context() as ctx:
        ctx.setattr(BaseSettings, "__init__", isolated_init)
        ctx.setattr(logger, "add", Mock())
        ctx.setattr(Path, "exists", lambda path: True if path == ROOT / "logs" else original_exists(path))
        spec.loader.exec_module(module)

    def build(_omit=(), **overrides):
        values = DEFAULTS | overrides
        for name in _omit:
            values.pop(name, None)
        return module.Settings(_env_file=None, **values)

    return build


@pytest.fixture
def models(monkeypatch, make_settings):
    monkeypatch.syspath_prepend(str(ROOT / "backend"))
    settings = make_settings(embeddings_provider="mock-embedding-adapter")

    def stub(name, **attrs):
        module = ModuleType(name)
        module.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, module)
        return module

    @dataclass
    class Document:
        page_content: str
        metadata: dict = field(default_factory=dict)

    def tool(function):
        function.name = function.__name__
        return function

    embeddings = Mock()
    init_embeddings = Mock(return_value=embeddings)
    store = SimpleNamespace(
        aadd_documents=AsyncMock(return_value=["mock-chunk"]),
        adelete=AsyncMock(),
        asimilarity_search_with_relevance_scores=AsyncMock(),
    )
    pgvector = Mock(return_value=store)
    init_chat = Mock(return_value=Mock())
    deepseek_chat = Mock(return_value=Mock())
    create_agent = Mock(return_value=object())
    rerank_client = SimpleNamespace(post=AsyncMock())
    rerank_factory = Mock(return_value=rerank_client)
    monkeypatch.setattr("openai.AsyncOpenAI", rerank_factory)
    stub("app.config", settings=settings)
    stub("langchain", __path__=[])
    stub("langchain.embeddings", init_embeddings=init_embeddings)
    stub("langchain.chat_models", init_chat_model=init_chat)
    stub("langchain.text_splitter", RecursiveCharacterTextSplitter=Mock())
    stub("langchain_core", __path__=[])
    stub("langchain_core.documents", Document=Document)
    stub("langchain_core.runnables", RunnableConfig=dict)
    stub("langchain_core.tools", tool=tool, BaseTool=object)
    stub("langchain_core.language_models", __path__=[])
    stub("langchain_core.language_models.chat_models", BaseChatModel=object)
    stub("langchain_community", __path__=[])
    stub("langchain_community.document_loaders", __path__=[], Docx2txtLoader=Mock(), PyPDFLoader=Mock(), TextLoader=Mock())
    stub("langchain_community.document_loaders.base", BaseLoader=object)
    stub("langchain_community.tools", __path__=[])
    stub("langchain_community.tools.tavily_search", TavilySearchResults=Mock(return_value=SimpleNamespace(name="web_search")))
    stub("langchain_postgres", PGVector=pgvector)
    stub("langgraph", __path__=[])
    stub("langgraph.checkpoint", __path__=[])
    stub("langgraph.checkpoint.base", BaseCheckpointSaver=object)
    stub("langgraph.graph", __path__=[])
    stub("langgraph.graph.state", CompiledStateGraph=object)
    stub("langgraph.prebuilt", create_react_agent=create_agent)
    stub("app.chat.prompts", dynamic_system_prompt=Mock())
    stub("app.chat.deepseek", DeepSeekChatOpenAI=deepseek_chat)
    mcp_tool = SimpleNamespace(name="get_server_status")
    mcp_client = SimpleNamespace(list_tools=AsyncMock(return_value=SimpleNamespace(tools=[mcp_tool])))
    context = SimpleNamespace(__aenter__=AsyncMock(return_value=mcp_client), __aexit__=AsyncMock(return_value=False))
    # Special methods are looked up on the class, not on a SimpleNamespace.
    context_type = type("MCPContext", (), {"__aenter__": context.__aenter__, "__aexit__": context.__aexit__})
    stub("mcp", Client=Mock(return_value=context_type()))
    adapter = Mock(return_value=mcp_tool)
    stub("app.mcp.adapter", create_langchain_tool_from_mcp=adapter)
    stub("app.mcp.server", mcp=object())

    names = ["app.db.pgvector_utils", "app.rag.reranker", "app.chat.tools", "app.tools.registry", "app.chat.langgraph_agent"]
    previous = {name: sys.modules.pop(name) for name in names if name in sys.modules}
    try:
        loaded = [importlib.import_module(name) for name in names]
        yield SimpleNamespace(
            **dict(zip(("pg", "reranker", "tools", "registry", "agent"), loaded)),
            settings=settings, init_embeddings=init_embeddings, embeddings=embeddings,
            deepseek_chat=deepseek_chat,
            pgvector=pgvector, store=store, init_chat=init_chat, create_agent=create_agent,
            rerank_client=rerank_client, rerank_factory=rerank_factory,
            mcp_client=mcp_client, adapter=adapter, Document=Document,
        )
    finally:
        for name in names:
            sys.modules.pop(name, None)
        sys.modules.update(previous)


def test_independent_credentials_without_dashscope(make_settings):
    settings = make_settings(DASHSCOPE_API_KEY=None)
    assert settings.chat_api_key.get_secret_value() == "mock-chat-key"
    assert settings.embeddings_api_key.get_secret_value() == "mock-embedding-key"
    assert settings.rerank_api_key.get_secret_value() == "mock-rerank-key"
    for secret in ("mock-chat-key", "mock-embedding-key", "mock-rerank-key"):
        assert secret not in repr(settings)


def test_independent_embedding_provider(make_settings):
    settings = make_settings(_omit=("embeddings_provider",), model_provider="chat-only-provider")
    assert settings.embeddings_provider == "openai"


def test_new_configuration_environment_variables(make_settings, monkeypatch):
    variables = {
        "EMBEDDINGS_API_KEY": "mock-env-embedding-key",
        "EMBEDDINGS_PROVIDER": "mock-env-provider",
        "EMBEDDINGS_BASE_URL": "https://env-embedding.invalid/v1",
        "EMBEDDINGS_MODEL_NAME": "mock-env-embedding-model",
        "RERANK_API_KEY": "mock-env-rerank-key",
        "RERANK_BASE_URL": "https://env-rerank.invalid/v1",
        "RERANK_MODEL_NAME": "mock-env-reranker-model",
        "RERANK_PATH": "/rerank",
    }
    for name, value in variables.items():
        monkeypatch.setenv(name, value)
    omitted = [name for name in DEFAULTS if name.upper() in variables]
    settings = make_settings(_omit=omitted)
    for name, value in variables.items():
        actual = getattr(settings, name.lower())
        if name.endswith("API_KEY"):
            actual = actual.get_secret_value()
        assert actual == value
    assert settings.chat_api_key.get_secret_value() == "mock-chat-key"
    assert settings.model_base_url == "https://api.deepseek.com"


def test_original_configuration_uses_legacy_defaults(make_settings):
    settings = make_settings(
        _omit=("EMBEDDINGS_API_KEY", "embeddings_provider", "RERANK_API_KEY",
               "rerank_base_url", "rerank_model_name"),
        DASHSCOPE_API_KEY="mock-legacy-key",
        embeddings_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    assert settings.embeddings_provider == "openai"
    assert settings.embeddings_api_key.get_secret_value() == "mock-legacy-key"
    assert settings.rerank_api_key.get_secret_value() == "mock-legacy-key"
    assert settings.rerank_base_url == "https://dashscope.aliyuncs.com/compatible-api/v1"
    assert settings.rerank_model_name == "qwen3-rerank"
    assert settings.rerank_path == "/reranks"


def test_legacy_dashscope_configuration(make_settings):
    settings = make_settings(
        DASHSCOPE_API_KEY="mock-legacy-key", EMBEDDINGS_API_KEY="", RERANK_API_KEY="",
        embeddings_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        rerank_base_url="https://dashscope.aliyuncs.com/compatible-api/v1",
    )
    assert settings.embeddings_api_key.get_secret_value() == "mock-legacy-key"
    assert settings.rerank_api_key.get_secret_value() == "mock-legacy-key"
    assert settings.chat_api_key.get_secret_value() == "mock-chat-key"
    assert settings.rerank_model_name == "mock-bge-reranker"  # explicit model wins


def test_independent_keys_override_legacy(make_settings):
    settings = make_settings(
        DASHSCOPE_API_KEY="mock-legacy-key",
        embeddings_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        rerank_base_url="https://dashscope.aliyuncs.com/compatible-api/v1",
    )
    assert settings.embeddings_api_key.get_secret_value() == "mock-embedding-key"
    assert settings.rerank_api_key.get_secret_value() == "mock-rerank-key"


@pytest.mark.parametrize("key,url_field", [("EMBEDDINGS_API_KEY", "embeddings_base_url"), ("RERANK_API_KEY", "rerank_base_url")])
@pytest.mark.parametrize("endpoint", [
    "https://other-provider.invalid/v1",
    "https://api.deepseek.com",
    "https://dashscope.aliyuncs.com.evil.invalid/v1",
    "https://dashscope.aliyuncs.com@evil.invalid/v1",
    "http://dashscope.aliyuncs.com/v1",
    "https://dashscope.aliyuncs.com:8443/v1",
    "",
])
def test_legacy_key_never_sent_to_other_endpoints(make_settings, key, url_field, endpoint):
    with pytest.raises(ValidationError, match=key) as caught:
        make_settings(**{key: "", url_field: endpoint, "DASHSCOPE_API_KEY": "mock-legacy-key"})
    for secret in ("mock-chat-key", "mock-embedding-key", "mock-rerank-key", "mock-legacy-key"):
        assert secret not in str(caught.value)


@pytest.mark.parametrize("key", ["EMBEDDINGS_API_KEY", "RERANK_API_KEY"])
@pytest.mark.parametrize("missing", ["", "   "])
def test_missing_keys_do_not_reuse_other_model_keys(make_settings, key, missing):
    with pytest.raises(ValidationError, match=key):
        make_settings(**{key: missing})


def test_legacy_embedding_fallback_requires_openai_adapter(make_settings):
    with pytest.raises(ValidationError, match="EMBEDDINGS_API_KEY"):
        make_settings(
            EMBEDDINGS_API_KEY="", DASHSCOPE_API_KEY="mock-legacy-key",
            embeddings_provider="other-provider",
            embeddings_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )


@pytest.mark.parametrize("path", ["https://other.invalid/rerank", "//other.invalid/rerank", "rerank", "/rerank?key=mock", "/rerank#fragment"])
def test_reranker_path_is_relative_to_configured_endpoint(make_settings, path):
    with pytest.raises(ValidationError, match="rerank_path"):
        make_settings(rerank_path=path)


def test_embedding_factory_uses_only_embedding_configuration(models):
    args = models.init_embeddings.call_args.kwargs
    assert args == {
        "model": "mock-bge-m3", "provider": "mock-embedding-adapter",
        "base_url": "https://embedding.invalid/v1",
        "api_key": models.settings.embeddings_api_key,
        "check_embedding_ctx_length": False, "chunk_size": 10,
    }
    args = models.pgvector.call_args.kwargs
    assert args["embeddings"] is models.embeddings
    assert args["collection_name"] == "mock-existing-collection"
    assert args["async_mode"] is True
    assert "embedding_length" not in args
    assert not args.get("pre_delete_collection")


@pytest.mark.parametrize("streaming", [False, True])
def test_chat_keeps_deepseek_factory_contract(models, streaming):
    models.agent.create_model("deepseek-v4-flash", streaming=streaming)
    assert models.deepseek_chat.call_args.kwargs == {
        "model": "deepseek-v4-flash",
        "api_key": models.settings.chat_api_key,
        "base_url": "https://api.deepseek.com", "streaming": streaming,
    }


def test_graph_keeps_native_mcp_tools_and_checkpointer(models):
    checkpointer = object()
    result = asyncio.run(models.agent.build_retrival_graph(
        checkpointer, "deepseek-v4-flash", interrupt_before=["tools"],
    ))
    assert result is models.create_agent.return_value
    args = models.create_agent.call_args.kwargs
    assert args["model"] is models.deepseek_chat.return_value
    assert args["checkpointer"] is checkpointer
    assert args["interrupt_before"] == ["tools"]
    assert args["prompt"] is models.agent.dynamic_system_prompt
    assert {tool.name for tool in args["tools"]} == {"retrieve_user_documents", "web_search", "get_server_status"}
    models.mcp_client.list_tools.assert_awaited_once()
    models.adapter.assert_called_once()


def test_reranker_factory_uses_only_reranker_credentials(models):
    assert models.rerank_factory.call_args.kwargs == {
        "api_key": "mock-rerank-key", "base_url": "https://rerank.invalid/v1", "timeout": 30.0,
    }


@pytest.mark.parametrize("path", ["/reranks", "/rerank"])
def test_reranker_http_contract_with_real_sdk_and_mock_transport(models, monkeypatch, path):
    captured = []

    def handle(request):
        captured.append(request)
        return httpx.Response(200, json={"results": [
            {"index": 1, "relevance_score": 0.95}, {"index": 0, "relevance_score": 0.8},
        ]})

    async def exercise():
        async with AsyncOpenAI(
            api_key=models.settings.rerank_api_key.get_secret_value(),
            base_url=models.settings.rerank_base_url,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
            max_retries=0,
        ) as client:
            monkeypatch.setattr(models.reranker, "rerank_client", client)
            monkeypatch.setattr(models.settings, "rerank_path", path)
            return await models.reranker.rerank_documents("query", ["first", "second"], 5)

    assert asyncio.run(exercise()) == [{"index": 1, "relevance_score": 0.95}, {"index": 0, "relevance_score": 0.8}]
    assert len(captured) == 1
    request = captured[0]
    assert str(request.url) == "https://rerank.invalid/v1" + path
    assert request.headers["authorization"] == "Bearer mock-rerank-key"
    assert json.loads(request.content) == {
        "model": "mock-bge-reranker", "query": "query", "documents": ["first", "second"], "top_n": 2,
    }


@pytest.mark.parametrize("response", [[], {}, {"results": None}, {"results": {}}])
def test_reranker_rejects_invalid_response_contract(models, response):
    models.rerank_client.post.return_value = response
    with pytest.raises(RuntimeError):
        asyncio.run(models.reranker.rerank_documents("query", ["first"], 1))


def test_reranker_preserves_empty_and_validation_behavior(models):
    assert asyncio.run(models.reranker.rerank_documents("query", [], 1)) == []
    for query, top_n in [(" ", 1), ("query", 0)]:
        with pytest.raises(ValueError):
            asyncio.run(models.reranker.rerank_documents(query, ["first"], top_n))
    models.rerank_client.post.assert_not_awaited()


def test_rag_preserves_scope_citations_and_rerank_mapping(models):
    documents = [
        models.Document("first", {"file_name": "first.pdf", "page": 0, "chunk_index": 0}),
        models.Document("second", {"file_name": "second.pdf", "page": 2, "chunk_index": 1}),
    ]
    models.store.asimilarity_search_with_relevance_scores.return_value = [(doc, 0.9) for doc in documents]
    models.rerank_client.post.return_value = {"results": [{"index": 1, "relevance_score": 0.95}]}
    result = json.loads(asyncio.run(models.tools.retrieve_user_documents("query", {
        "configurable": {"user_id": "mock-user", "thread_id": "mock-thread", "knowledge_base_id": "mock-kb"},
    })))
    assert result["status"] == "ok"
    assert result["evidence_gate_passed"] is True
    assert result["sources"][0]["content"] == "second"
    assert result["sources"][0]["source_label"] == "[Source 1]"
    assert result["sources"][0]["page"] == 3
    assert result["sources"][0]["rerank_score"] == 0.95
    assert models.store.asimilarity_search_with_relevance_scores.call_args.kwargs["filter"] == {
        "user_id": "mock-user", "knowledge_base_id": "mock-kb",
    }
    assert models.rerank_client.post.call_args.kwargs["body"]["documents"] == ["first", "second"]


@pytest.mark.parametrize("component,reason", [("vector", "vector_search_failed"), ("reranker", "reranker_failed")])
def test_rag_service_failures_keep_error_semantics(models, component, reason):
    models.store.asimilarity_search_with_relevance_scores.return_value = [(models.Document("first"), 0.9)]
    if component == "vector":
        models.store.asimilarity_search_with_relevance_scores.side_effect = RuntimeError("mock failure")
    else:
        models.rerank_client.post.side_effect = RuntimeError("mock failure")
    result = json.loads(asyncio.run(models.tools.retrieve_user_documents("query", {
        "configurable": {"user_id": "mock-user", "knowledge_base_id": "mock-kb"},
    })))
    assert (result["status"], result["reason"], result["sources"]) == ("error", reason, [])
