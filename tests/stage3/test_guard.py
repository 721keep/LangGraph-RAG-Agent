import json
from types import SimpleNamespace
import httpx
import pytest
from guard import Budget, verify_isolation


@pytest.mark.parametrize('field', ['database_uri', 'pgvector_connection', 'checkpointer_uri'])
def test_each_connection_must_target_test_database(field):
    values = {name: 'postgresql://stage3_rag_user:unused@stage3-postgres:5432/langgraph_rag_stage3_test'
              for name in ('database_uri', 'pgvector_connection', 'checkpointer_uri')}
    values[field] = 'postgresql://postgres:unused@postgres:5432/langgraph_db'
    with pytest.raises(RuntimeError, match=field):
        verify_isolation(SimpleNamespace(**values))


def request(model='BAAI/bge-m3', host='api.siliconflow.cn', texts=None):
    return httpx.Request('POST', 'https://' + host + '/v1/embeddings',
                         json={'model': model, 'input': texts or ['synthetic PDF text']})


def test_offline_phase_blocks_all_external_requests(tmp_path):
    budget = Budget(tmp_path / 'budget.json')
    budget.mode('fake')
    with pytest.raises(RuntimeError):
        budget.consume(request())
    assert budget.read()['embedding'] == 0


@pytest.mark.parametrize('override', [dict(model='Pro/BAAI/bge-m3'), dict(host='api.deepseek.com'), dict(texts=['x'] * 4), dict(texts=['x' * 1601])])
def test_model_host_and_input_allowlists(tmp_path, override):
    budget = Budget(tmp_path / 'budget.json')
    budget.mode('live')
    with pytest.raises(RuntimeError):
        budget.consume(request(**override))
    assert budget.read()['embedding'] == 0


def test_budget_survives_reopening_and_blocks_seventh_attempt(tmp_path):
    path = tmp_path / 'budget.json'
    budget = Budget(path)
    budget.mode('live')
    for _ in range(6):
        budget.consume(request())
    reopened = Budget(path)
    with pytest.raises(RuntimeError, match='exhausted'):
        reopened.consume(request())
    assert reopened.read()['embedding'] == 6 and reopened.read()['halted']


def test_provider_error_halts_next_request(tmp_path):
    budget = Budget(tmp_path / 'budget.json')
    budget.mode('live')
    index = budget.consume(request())
    with pytest.raises(RuntimeError, match='429'):
        budget.response(index, httpx.Response(429))
    with pytest.raises(RuntimeError):
        budget.consume(request())
    assert budget.read()['embedding'] == 1


def test_reranker_budget_blocks_third_request(tmp_path):
    budget = Budget(tmp_path / 'budget.json')
    budget.mode('live')
    rerank = httpx.Request('POST', 'https://api.siliconflow.cn/v1/rerank', json={
        'model': 'BAAI/bge-reranker-v2-m3', 'query': 'query', 'documents': ['first', 'second'], 'top_n': 2,
    })
    for _ in range(2):
        budget.consume(rerank)
    with pytest.raises(RuntimeError, match='exhausted'):
        budget.consume(rerank)
    assert budget.read()['reranker'] == 2 and budget.read()['halted']


def test_wrong_dimension_halts_before_sdk_or_database_receives_it(tmp_path):
    budget = Budget(tmp_path / 'budget.json')
    budget.mode('live')
    index = budget.consume(request())
    response = httpx.Response(200, json={'model': 'BAAI/bge-m3', 'data': [
        {'index': 0, 'embedding': [0.1] * 1023},
    ]})
    with pytest.raises(RuntimeError, match='Invalid provider response'):
        budget.response(index, response)
    assert budget.read()['halted']
