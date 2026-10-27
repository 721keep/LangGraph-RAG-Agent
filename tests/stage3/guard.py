"""Test-only isolation and persistent, fail-closed model request budgets."""
import fcntl
import json
import math
from pathlib import Path
from urllib.parse import urlsplit

DATABASE = 'langgraph_rag_stage3_test'
COLLECTION = 'bge_m3_1024_v1_test'
HOST = 'stage3-postgres'
USER = 'stage3_rag_user'
MODELS = {'embedding': 'BAAI/bge-m3', 'reranker': 'BAAI/bge-reranker-v2-m3'}
LIMITS = {'embedding': 6, 'reranker': 2}


def verify_isolation(settings):
    checks = {}
    for name in ('database_uri', 'pgvector_connection', 'checkpointer_uri'):
        url = urlsplit(getattr(settings, name))
        if url.hostname != HOST or url.port != 5432 or url.path != '/' + DATABASE or url.username != USER:
            raise RuntimeError('Isolation rejected: ' + name)
        checks[name] = {'host': HOST, 'port': 5432, 'database': DATABASE, 'user': USER}
    if settings.pgvector_collection_name != COLLECTION:
        raise RuntimeError('Isolation rejected: collection')
    for name, expected in {
        'embeddings_provider': 'openai', 'embeddings_model_name': MODELS['embedding'],
        'embeddings_base_url': 'https://api.siliconflow.cn/v1',
        'rerank_model_name': MODELS['reranker'], 'rerank_base_url': 'https://api.siliconflow.cn/v1',
        'rerank_path': '/rerank',
    }.items():
        if getattr(settings, name) != expected:
            raise RuntimeError('Isolation rejected: ' + name)
    return checks


class Budget:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            with self.path.open('x') as file:
                json.dump({'mode': 'blocked', 'halted': False, 'embedding': 0, 'reranker': 0, 'events': []}, file)

    def update(self, callback):
        with self.path.open('r+') as file:
            fcntl.flock(file, fcntl.LOCK_EX)
            state = json.load(file)
            try:
                result = callback(state)
            finally:
                file.seek(0)
                json.dump(state, file, ensure_ascii=False)
                file.truncate()
                file.flush()
                fcntl.flock(file, fcntl.LOCK_UN)
            return result

    def read(self):
        return self.update(lambda state: json.loads(json.dumps(state)))

    def mode(self, mode):
        def change(state):
            if mode not in ('fake', 'fake_error', 'live', 'sealed') or (state['halted'] and mode != 'sealed'):
                raise RuntimeError('Budget is halted or mode is invalid')
            state['mode'] = mode
        self.update(change)

    def consume(self, request):
        def consume(state):
            url = request.url
            kind = {'/v1/embeddings': 'embedding', '/v1/rerank': 'reranker'}.get(url.path)
            if state['mode'] != 'live' or state['halted']:
                raise RuntimeError('External HTTP blocked in this test phase')
            if (request.method != 'POST' or url.scheme != 'https' or url.host != 'api.siliconflow.cn'
                    or url.port not in (None, 443) or url.query or url.userinfo or kind is None):
                raise RuntimeError('External endpoint outside authorized scope')
            body = json.loads(request.content)
            if body.get('model') != MODELS[kind]:
                raise RuntimeError('Model outside non-Pro allowlist')
            if kind == 'embedding':
                texts = body.get('input')
                texts = [texts] if isinstance(texts, str) else texts
                if (not isinstance(texts, list) or not 1 <= len(texts) <= 3
                        or not all(isinstance(t, str) and 0 < len(t) <= 1600 for t in texts)
                        or 'dimensions' in body):
                    raise RuntimeError('Embedding input outside small-PDF budget')
            else:
                texts = body.get('documents')
                if (not isinstance(texts, list) or not 1 <= len(texts) <= 3
                        or not all(isinstance(t, str) and 0 < len(t) <= 1600 for t in texts)
                        or not isinstance(body.get('query'), str) or len(body['query']) > 200
                        or not 1 <= body.get('top_n', 0) <= len(texts)):
                    raise RuntimeError('Reranker input outside small-PDF budget')
            if state[kind] >= LIMITS[kind]:
                state['halted'] = True
                raise RuntimeError('Model budget exhausted')
            state[kind] += 1
            state['events'].append({'kind': kind, 'model': MODELS[kind], 'input_count': len(texts),
                                    'input_lengths': [len(t) for t in texts], 'http_status': None})
            return len(state['events']) - 1
        return self.update(consume)

    def response(self, event_index, response):
        body = response.json() if response.status_code == 200 else None
        def record(state):
            event = state['events'][event_index]
            event['http_status'] = response.status_code
            if response.status_code != 200:
                state['halted'] = True
                event['stop_reason'] = 'provider_http_error'
                return
            try:
                if event['kind'] == 'embedding':
                    data = body['data']
                    assert len(data) == event['input_count']
                    assert sorted(item['index'] for item in data) == list(range(len(data)))
                    assert body.get('model') == MODELS['embedding']
                    for item in data:
                        vector = item['embedding']
                        # The SDK may request base64; require float for this test.
                        assert isinstance(vector, list) and len(vector) == 1024
                        assert all(type(v) in (int, float) and math.isfinite(v) for v in vector)
                        assert sum(v * v for v in vector) > 0
                    event['dimensions'] = [len(item['embedding']) for item in data]
                else:
                    results = body['results']
                    assert 1 <= len(results) <= event['input_count']
                    indices = [item['index'] for item in results]
                    scores = [item['relevance_score'] for item in results]
                    assert all(type(i) is int and 0 <= i < event['input_count'] for i in indices)
                    assert len(set(indices)) == len(indices)
                    assert all(type(s) in (int, float) and math.isfinite(s) for s in scores)
                    assert scores == sorted(scores, reverse=True)
                    event['results'] = [{'index': i, 'relevance_score': s} for i, s in zip(indices, scores)]
            except (AssertionError, KeyError, TypeError):
                state['halted'] = True
                event['stop_reason'] = 'invalid_provider_response'
                raise RuntimeError('Invalid provider response; test halted') from None
        self.update(record)
        if response.status_code != 200:
            raise RuntimeError('Provider returned HTTP ' + str(response.status_code) + '; test halted')
