"""R2 independent budget tests; provider responses are Mock, never network."""
import asyncio
import json
import os
from types import SimpleNamespace

import pytest
if os.environ.get('RELEASE_R2_TESTS') != '1':
    pytest.skip('R2 guard tests require the explicit isolated test mode', allow_module_level=True)
from model_guard import Ledger, SSEInspector, Transport, StopRun, REQUEST_CONTEXT
import model_guard as guard
import httpx


def state(tmp_path, counter=None):
    ledger = Ledger(tmp_path / 'budget.json', counter or SimpleNamespace(count=lambda b: (100, 1124)), create=True)
    ledger.arm()
    return ledger


def body(kind):
    if kind == 'chat':
        return {'model': 'deepseek-flash', 'stream': True, 'max_tokens': 2048,
                'stream_options': {'include_usage': True}, 'thinking': {'type': 'disabled'},
                'messages': [{'role': 'system', 'content': 'fictional system'}, {'role': 'user', 'content': 'fixture'}],
                'tools': [{'function': {'name': n}} for n in ['retrieve_user_documents', 'web_search', 'get_server_status']]}
    if kind == 'embedding':
        return {'model': 'BAAI/bge-m3', 'input': ['fictional PDF chunk ' * 35, 'second chunk'], 'encoding_format': 'float'}
    return {'model': 'BAAI/bge-reranker-v2-m3', 'query': 'fixture', 'documents': ['one', 'two'], 'top_n': 2}


def request(kind, payload=None):
    endpoint = {'chat': 'https://api.deepseek.com/chat/completions',
                'embedding': 'https://api.siliconflow.cn/v1/embeddings',
                'reranker': 'https://api.siliconflow.cn/v1/rerank'}[kind]
    return httpx.Request('POST', endpoint, json=payload or body(kind))


def finish(ledger, index, kind):
    ledger.status(index, 200)
    if kind == 'chat':
        ledger.chat_complete(index, {'role': 'assistant', 'content': 'fictional answer'},
                             {'prompt_tokens': 100, 'completion_tokens': 20}, 'stop', 2)
    elif kind == 'embedding':
        ledger.json_complete(index, {'model': 'BAAI/bge-m3', 'data': [{'index': i, 'embedding': [.1] * 1024} for i in range(2)]})
    else:
        ledger.json_complete(index, {'results': [{'index': 1, 'relevance_score': .9}, {'index': 0, 'relevance_score': .2}]})


@pytest.mark.parametrize('kind,limit', [('chat', 8), ('embedding', 6), ('reranker', 3)])
def test_separate_persistent_caps(tmp_path, kind, limit):
    ledger = state(tmp_path)
    for i in range(limit):
        index, _ = ledger.reserve(request(kind), 'positive', str(i))
        finish(ledger, index, kind)
    reopened = Ledger(ledger.path, ledger.counter)
    with pytest.raises(StopRun, match=kind + '_request_limit'):
        reopened.reserve(request(kind), 'positive', 'extra')
    assert reopened.read()['counts'][kind] == limit


def test_two_pdf_chunks_are_bounded_and_1024_each(tmp_path):
    ledger = state(tmp_path)
    index, _ = ledger.reserve(request('embedding'), 'upload', 'none')
    finish(ledger, index, 'embedding')
    assert ledger.read()['events'][index]['dimensions'] == [1024, 1024]


@pytest.mark.parametrize('override', [{'input': ['x'] * 3}, {'input': ['x' * 1601]}, {'model': 'Pro/BAAI/bge-m3'}])
def test_oversize_batch_text_and_pro_block_before_count(tmp_path, override):
    ledger = state(tmp_path)
    payload = {**body('embedding'), **override}
    with pytest.raises(StopRun):
        ledger.reserve(request('embedding', payload), 'positive', 'none')
    assert ledger.read()['requests'] == 0


def test_wrong_vector_dimension_stops_all_models(tmp_path):
    ledger = state(tmp_path)
    payload = {**body('embedding'), 'input': ['one']}
    index, _ = ledger.reserve(request('embedding', payload), 'upload', 'none')
    with pytest.raises(StopRun):
        ledger.json_complete(index, {'model': 'BAAI/bge-m3', 'data': [{'index': 0, 'embedding': [.1] * 1023}]})
    with pytest.raises(StopRun):
        ledger.reserve(request('chat'), 'positive', 'none')


def test_disabled_thinking_stream_accepts_absent_reasoning(tmp_path):
    ledger = state(tmp_path)
    index, _ = ledger.reserve(request('chat'), 'positive', 'thread1')
    stream = SSEInspector(ledger, index)
    frame = {'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': '虚构答案'}, 'finish_reason': 'stop'}],
             'usage': {'prompt_tokens': 100, 'completion_tokens': 20}}
    raw = ('data: ' + json.dumps(frame, ensure_ascii=False) + '\n\ndata: [DONE]\n\n').encode()
    for value in raw:
        stream.feed(bytes([value]))
    stream.close()
    assert ledger.read()['events'][index]['assistant']['reasoning_present'] is False


def test_missing_done_halts_without_automatic_retry(tmp_path):
    ledger = state(tmp_path)
    index, _ = ledger.reserve(request('chat'), 'positive', 'thread1')
    with pytest.raises(StopRun):
        SSEInspector(ledger, index).close()
    assert ledger.read()['counts']['chat'] == 1


def test_token_upper_bound_blocks_before_send(tmp_path):
    ledger = state(tmp_path, SimpleNamespace(count=lambda b: (8000, 9024)))
    with pytest.raises(StopRun, match='token_limit'):
        ledger.reserve(request('chat'), 'positive', 'thread1')
    assert ledger.read()['requests'] == 0


def test_unresolved_request_and_sealed_ledger_do_not_rearm(tmp_path):
    ledger = state(tmp_path)
    ledger.reserve(request('embedding'), 'upload', 'none')
    with pytest.raises(StopRun):
        ledger.reserve(request('chat'), 'positive', 'thread1')
    ledger.seal()
    with pytest.raises(StopRun):
        ledger.arm()


def test_http_failure_is_counted_before_send_and_stops_other_kinds(tmp_path, monkeypatch):
    ledger = state(tmp_path)
    transport = Transport(ledger)
    transport.keys = {'embedding': 'fictional-test-key'}
    attempts = []

    async def mock_send(inner, req):
        assert ledger.read()['counts']['embedding'] == 1
        attempts.append(req.url.path)
        return httpx.Response(401, json={'error': {'message': 'fictional authentication error'}})
    monkeypatch.setattr(guard, 'ORIGINAL_SEND', mock_send)
    async def run():
        token = REQUEST_CONTEXT.set(('positive', 'thread1'))
        try:
            with pytest.raises(StopRun):
                await transport.handle_async_request(request('embedding'))
            await transport.aclose()
        finally:
            REQUEST_CONTEXT.reset(token)
    asyncio.run(run())
    assert attempts == ['/v1/embeddings']
    assert ledger.read()['events'][0]['http_status'] == 401
    with pytest.raises(StopRun):
        ledger.reserve(request('chat'), 'positive', 'thread1')


def test_unscoped_request_stops_before_any_http_count(tmp_path):
    ledger = state(tmp_path)
    with pytest.raises(StopRun, match='unscoped_model_request'):
        ledger.reserve(request('embedding'), 'blocked', 'none')
    assert ledger.read()['requests'] == 0


def test_non_rag_tool_selection_halts_before_tool_execution(tmp_path):
    ledger = state(tmp_path)
    index, _ = ledger.reserve(request('chat'), 'positive', 'thread1')
    message = {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'call1', 'type': 'function',
                'function': {'name': 'web_search', 'arguments': '{"query":"fictional"}'}}]}
    with pytest.raises(StopRun, match='non_rag_tool_selected'):
        ledger.chat_complete(index, message, {'prompt_tokens': 100, 'completion_tokens': 20}, 'tool_calls', 2)
