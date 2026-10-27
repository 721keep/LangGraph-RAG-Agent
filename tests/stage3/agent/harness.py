"""Script only Chat/embedding/rerank boundaries; graph, tools and MCP stay real."""
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import httpx
from langchain_core.documents import Document
from langchain_core.messages import AIMessage, ToolMessage
from bootstrap import ARTIFACTS
from app.chat import deepseek, langgraph_agent, tools

MODEL_CLASS = deepseek.DeepSeekChatOpenAI
# Portable offline regressions use fictional data; local PostgreSQL acceptance
# keeps its original private run snapshot and resource IDs unchanged.
SNAPSHOT_PATH = (Path(__file__).with_name('fixture.json')
                 if os.environ.get('STAGE3D_MODE') == 'offline'
                 else ARTIFACTS / 'run_live.json')
SNAPSHOT = json.loads(SNAPSHOT_PATH.read_text())
ANSWER = '向量模型为 BGE-M3，维度为 1024。[Source 1]\n\nSources:\n- [Source 1] `stage3_rag_test.pdf`, page 1, chunk 0'


def call(name='retrieve_user_documents', ident='call_rag_1', **arguments):
    return {'id': ident, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(arguments, ensure_ascii=False)}}


def reply(content='', calls=None, reasoning='mock reasoning only'):
    data = {'role': 'assistant', 'content': content}
    if calls:
        data['tool_calls'] = calls
    if reasoning is not None:
        data['reasoning_content'] = reasoning
    return data


class ScriptedAPI:
    def __init__(self, replies):
        self.replies = copy.deepcopy(replies)
        self.requests = []
        self.clients = []

    def handle(self, request):
        assert request.url.host == 'api.deepseek.com' and request.url.path == '/chat/completions'
        body = json.loads(request.content)
        assert body['model'] == 'deepseek-flash'
        assert len(self.requests) < len(self.replies), 'Mock request plan exhausted; no retry allowed'
        message = self.replies[len(self.requests)]
        self.requests.append(body)
        finish = 'tool_calls' if message.get('tool_calls') else 'stop'
        common = {'id': 'mock-completion', 'created': 1, 'model': 'deepseek-flash'}
        if not body.get('stream'):
            return httpx.Response(200, json={**common, 'object': 'chat.completion',
                'choices': [{'index': 0, 'message': message, 'finish_reason': finish}],
                'usage': {'prompt_tokens': 8, 'completion_tokens': 5, 'total_tokens': 13}})
        deltas = [{'role': 'assistant', 'content': ''}]
        if 'reasoning_content' in message:
            reason = message['reasoning_content']
            midpoint = len(reason) // 2
            deltas += [{'reasoning_content': reason[:midpoint]}, {'reasoning_content': reason[midpoint:]}]
        for index, tool in enumerate(message.get('tool_calls', [])):
            function = tool['function']; arguments = function['arguments']; midpoint = len(arguments) // 2
            deltas += [{'tool_calls': [{'index': index, 'id': tool['id'], 'type': 'function',
                        'function': {'name': function['name'], 'arguments': arguments[:midpoint]}}]},
                       {'tool_calls': [{'index': index, 'function': {'arguments': arguments[midpoint:]}}]}]
        content = message.get('content') or ''
        if content:
            midpoint = len(content) // 2
            deltas += [{'content': content[:midpoint]}, {'content': content[midpoint:]}]
        chunks = [{**common, 'object': 'chat.completion.chunk',
                   'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]} for delta in deltas]
        chunks.append({**common, 'object': 'chat.completion.chunk', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}]})
        wire = ''.join('data: ' + json.dumps(item, ensure_ascii=False) + '\n\n' for item in chunks) + 'data: [DONE]\n\n'
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, content=wire.encode())

    def model(self, thinking='enabled', **kwargs):
        kwargs.setdefault('model', 'deepseek-flash')
        kwargs.setdefault('api_key', 'stage3d-dummy-chat')
        kwargs.setdefault('base_url', 'https://api.deepseek.com')
        sync = httpx.Client(transport=httpx.MockTransport(self.handle), trust_env=False)
        asynchronous = httpx.AsyncClient(transport=httpx.MockTransport(self.handle), trust_env=False)
        self.clients += [sync, asynchronous]
        return MODEL_CLASS(**kwargs, max_retries=0, max_tokens=256,
                           extra_body={'thinking': {'type': thinking}},
                           http_client=sync, http_async_client=asynchronous)

    async def close(self):
        for client in self.clients:
            if isinstance(client, httpx.AsyncClient):
                await client.aclose()
            else:
                client.close()


def install(monkeypatch, api, thinking='enabled', evidence=True, error=False):
    monkeypatch.setattr(deepseek, 'DeepSeekChatOpenAI', lambda **kwargs: api.model(thinking, **kwargs))
    vector_calls = []
    rerank_calls = []

    async def search(query, **kwargs):
        vector_calls.append({'query': query, **kwargs})
        if error:
            raise RuntimeError('synthetic vector boundary failure')
        return [(Document(page_content=item['content'], metadata=copy.deepcopy(item['metadata'])),
                 item['score'] if evidence else 0.1) for item in SNAPSHOT['pgvector_search']['results']]

    async def rerank(**kwargs):
        rerank_calls.append(kwargs)
        event = next(e for e in SNAPSHOT['budget']['events'] if e['kind'] == 'reranker')
        return copy.deepcopy(event['results'])

    monkeypatch.setattr(tools, 'vector_store', SimpleNamespace(asimilarity_search_with_relevance_scores=search))
    monkeypatch.setattr(tools, 'rerank_documents', rerank)
    return vector_calls, rerank_calls


def config(thread_id='stage3d-offline-thread'):
    return {'recursion_limit': 12, 'configurable': {**SNAPSHOT['scope'], 'thread_id': thread_id,
            'top_k': 3, 'similarity_threshold': 0.5, 'rerank_top_n': 2}}


def assert_message_pairs(messages):
    pending = set()
    for message in messages:
        if isinstance(message, AIMessage):
            assert not pending, 'Model continued before all tool results returned'
            pending.update(call['id'] for call in message.tool_calls)
        if isinstance(message, ToolMessage):
            assert message.tool_call_id in pending
            pending.remove(message.tool_call_id)
    assert not pending


def assert_wire_history(requests, thinking):
    for payload in requests:
        assert payload['thinking']['type'] == thinking
        assert payload['max_tokens'] == 256 and 'max_completion_tokens' not in payload
        pending = set()
        for message in payload['messages']:
            if message['role'] == 'assistant':
                assert not pending
                if thinking == 'enabled':
                    assert isinstance(message['reasoning_content'], str)
                pending.update(t['id'] for t in message.get('tool_calls', []))
            elif message['role'] == 'tool':
                assert message['tool_call_id'] in pending
                pending.remove(message['tool_call_id'])
        assert not pending
