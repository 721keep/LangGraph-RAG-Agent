import asyncio
import json
from unittest.mock import Mock

import pytest
from bootstrap import configure
configure()

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langchain_openai.chat_models.base import _convert_dict_to_message, _convert_message_to_dict, _convert_delta_to_message_chunk
from app.chat import langgraph_agent
from app.chat.schemas import ChatStreamResponse
from app.tools.registry import get_agent_tools
from harness import ANSWER, MODEL_CLASS, SNAPSHOT, ScriptedAPI, assert_message_pairs, assert_wire_history, call, config, install, reply


def test_pinned_openai_adapter_reproduces_reasoning_loss():
    raw = reply(calls=[call(query='fixture')], reasoning='mock preserved value')
    assert 'reasoning_content' not in _convert_dict_to_message(raw).additional_kwargs
    assert 'reasoning_content' not in _convert_delta_to_message_chunk(raw, AIMessageChunk).additional_kwargs
    assert 'reasoning_content' not in _convert_message_to_dict(AIMessage(content='', additional_kwargs={'reasoning_content': 'mock'}))


def test_factory_preserves_provider_thinking_default():
    model = langgraph_agent.create_model('deepseek-flash')
    try:
        assert isinstance(model, MODEL_CLASS)
        assert model.extra_body is None
        assert model.model_name == 'deepseek-flash'
    finally:
        model.root_client.close()
        asyncio.run(model.root_async_client.close())


def test_external_http_is_blocked_without_mock_transport():
    import httpx
    with httpx.Client(trust_env=False) as client:
        with pytest.raises(AssertionError, match='External HTTP is forbidden'):
            client.get('https://forbidden.invalid')


@pytest.mark.parametrize('reasoning', [None, '', 'mock preserved value'])
@pytest.mark.parametrize('with_tool', [False, True])
@pytest.mark.parametrize('streaming', [False, True])
def test_reasoning_and_tool_roundtrip_with_real_sdk(reasoning, with_tool, streaming):
    async def exercise():
        api = ScriptedAPI([reply(content='' if with_tool else 'answer', calls=[call(query='fixture')] if with_tool else None, reasoning=reasoning)])
        mode = 'disabled' if reasoning is None else 'enabled'
        model = api.model(mode)
        try:
            if streaming:
                chunks = [item async for item in model.astream([HumanMessage(content='question')])]
                message = sum(chunks[1:], chunks[0])
            else:
                message = await model.ainvoke([HumanMessage(content='question')])
            assert message.additional_kwargs.get('reasoning_content') == reasoning
            assert bool(message.tool_calls) == with_tool
            if with_tool:
                assert message.tool_calls[0]['id'] == 'call_rag_1'
                assert message.tool_calls[0]['args'] == {'query': 'fixture'}
            payload = model._get_request_payload([HumanMessage(content='question'), message], tools=[{'type': 'function', 'function': {'name': 'unused'}}])
            assert payload['messages'][1].get('reasoning_content') == reasoning
            if with_tool:
                assert payload['messages'][1]['content'] == ''
        finally:
            await api.close()
    asyncio.run(exercise())


def test_thinking_refuses_history_without_original_reasoning():
    api = ScriptedAPI([])
    model = api.model()
    with pytest.raises(ValueError, match='missing reasoning_content'):
        model._get_request_payload([AIMessage(content='old final answer')], tools=[{'type': 'function'}])
    assert api.requests == []
    asyncio.run(api.close())


@pytest.mark.parametrize('tool_choice', ['required', {'type': 'function', 'function': {'name': 'unused'}}])
def test_thinking_rejects_forced_tool_choice(tool_choice):
    api = ScriptedAPI([])
    model = api.model()
    with pytest.raises(ValueError, match='forced tool_choice'):
        model._get_request_payload([HumanMessage(content='question')], tools=[{'type': 'function'}], tool_choice=tool_choice)
    asyncio.run(api.close())


@pytest.mark.parametrize('url', ['https://proxy.invalid', 'http://api.deepseek.com', 'https://api.deepseek.com.evil.invalid', 'https://api.deepseek.com:8443'])
def test_other_endpoints_keep_original_factory(monkeypatch, url):
    fallback = Mock(return_value=object())
    monkeypatch.setattr(langgraph_agent.settings, 'model_base_url', url)
    monkeypatch.setattr(langgraph_agent, 'init_chat_model', fallback)
    assert langgraph_agent.create_model('deepseek-flash') is fallback.return_value
    assert fallback.call_args.kwargs['base_url'] == url


@pytest.mark.parametrize('thinking', ['disabled', 'enabled'])
@pytest.mark.parametrize('streaming', [False, True])
def test_original_agent_rag_citation_and_stream(monkeypatch, thinking, streaming):
    async def exercise():
        reasoning = None if thinking == 'disabled' else 'mock preserved value'
        api = ScriptedAPI([reply(calls=[call(query='向量模型与维度')], reasoning=reasoning), reply(ANSWER, reasoning=reasoning)])
        vectors, reranks = install(monkeypatch, api, thinking)
        graph = await langgraph_agent.build_retrival_graph(None, 'deepseek-flash')
        try:
            if streaming:
                stream = graph.astream({'messages': [HumanMessage(content='文档使用什么向量模型？')]}, config=config(), stream_mode=['updates', 'messages'])
                response = ChatStreamResponse(stream)
                events = [json.loads(item) async for item in response.body_iterator if item.strip()]
                assert ''.join(e['content'] for e in events if e['type'] == 'llm_chunk') == ANSWER
                assert any(e['type'] == 'tool_call' and e['name'] == 'retrieve_user_documents' for e in events)
                data = json.loads(next(e['content'] for e in events if e['type'] == 'tool_result'))
                assert all('reasoning_content' not in e for e in events)
                assert 'mock preserved value' not in json.dumps(events)
            else:
                result = await graph.ainvoke({'messages': [HumanMessage(content='文档使用什么向量模型？')]}, config=config())
                assert result['messages'][-1].content == ANSWER
                assert_message_pairs(result['messages'])
                data = json.loads(next(m.content for m in result['messages'] if isinstance(m, ToolMessage)))
            assert data['sources'] == SNAPSHOT['positive_rag']['sources']
            assert len(vectors) == len(reranks) == 1
            assert vectors[0]['filter'] == {k: SNAPSHOT['scope'][k] for k in ['user_id', 'knowledge_base_id']}
            assert '[Source 1]' in ANSWER and 'page 1, chunk 0' in ANSWER
            assert set(t['function']['name'] for t in api.requests[0]['tools']) == {'retrieve_user_documents', 'web_search', 'get_server_status'}
            assert 'RAG Citation Rules' in api.requests[0]['messages'][0]['content']
            assert_wire_history(api.requests, thinking)
            assert len(api.requests) == 2
        finally:
            await api.close()
    asyncio.run(exercise())


def test_continuous_calls_then_no_evidence_refusal(monkeypatch):
    async def exercise():
        refusal = '当前检索结果未达到相似度阈值，无法提供可靠的文档证据。'
        api = ScriptedAPI([reply(calls=[call(query='采购价格')]), reply(calls=[call(ident='call_rag_2', query='采购预算')]), reply(refusal)])
        vectors, reranks = install(monkeypatch, api, evidence=False)
        graph = await langgraph_agent.build_retrival_graph(None, 'deepseek-flash')
        try:
            result = await graph.ainvoke({'messages': [HumanMessage(content='文档的采购价格？')]}, config=config())
            messages = result['messages']; assert_message_pairs(messages)
            results = [json.loads(m.content) for m in messages if isinstance(m, ToolMessage)]
            assert len(vectors) == len(results) == 2 and not reranks
            assert all(d['status'] == 'no_evidence' and d['sources'] == [] for d in results)
            assert messages[-1].content == refusal and '[Source' not in messages[-1].content
            assert all(c['function']['name'] != 'web_search' for p in api.requests for m in p['messages'] for c in m.get('tool_calls', []))
            assert_wire_history(api.requests, 'enabled')
        finally:
            await api.close()
    asyncio.run(exercise())


def test_technical_failure_remains_error_without_retry(monkeypatch):
    async def exercise():
        api = ScriptedAPI([reply(calls=[call(query='模型')]), reply('文档检索遇到技术问题，请稍后重试。')])
        vectors, reranks = install(monkeypatch, api, error=True)
        graph = await langgraph_agent.build_retrival_graph(None, 'deepseek-flash')
        try:
            result = await graph.ainvoke({'messages': [HumanMessage(content='模型？')]}, config=config())
            data = json.loads(next(m.content for m in result['messages'] if isinstance(m, ToolMessage)))
            assert data['status'] == 'error' and data['reason'] == 'vector_search_failed'
            assert len(vectors) == 1 and not reranks
            assert '[Source' not in result['messages'][-1].content
        finally:
            await api.close()
    asyncio.run(exercise())


def test_real_mcp_registry_tool_and_parallel_events(monkeypatch):
    async def exercise():
        api = ScriptedAPI([reply(calls=[call(query='向量模型'), call('get_server_status', 'call_mcp_1', component='stage3d')]), reply(ANSWER)])
        install(monkeypatch, api)
        registered = await get_agent_tools()
        assert {tool.name for tool in registered} == {'retrieve_user_documents', 'web_search', 'get_server_status'}
        graph = await langgraph_agent.build_retrival_graph(None, 'deepseek-flash')
        try:
            stream = graph.astream({'messages': [HumanMessage(content='模型及 MCP 状态？')]}, config=config(), stream_mode=['updates', 'messages'])
            events = [json.loads(item) async for item in ChatStreamResponse(stream).body_iterator if item.strip()]
            results = [e for e in events if e['type'] == 'tool_result']
            assert {e['name'] for e in results} == {'retrieve_user_documents', 'get_server_status'}
            mcp = next(e for e in results if e['name'] == 'get_server_status')
            assert mcp['tool_source'] == 'mcp' and mcp['success'] is True
            assert json.loads(mcp['content'])['result'] == {'result': 'stage3d is available'}
            assert_wire_history(api.requests, 'enabled')
        finally:
            await api.close()
    asyncio.run(exercise())
