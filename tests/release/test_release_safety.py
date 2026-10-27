"""Offline startup gates; no app initialization, environment read or network."""
import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
import pytest

_path = Path('/release/safety.py')
if not _path.exists():
    _path = Path(__file__).resolve().parents[2] / 'deployment/release/runtime/safety.py'
spec = importlib.util.spec_from_file_location('r1_safety_test', _path)
safety = importlib.util.module_from_spec(spec)
spec.loader.exec_module(safety)


def fixture():
    values = dict(postgres_host=safety.HOST, postgres_port=5432, postgres_user=safety.USER,
                  postgres_database=safety.DATABASE, postgres_password='test-only-' * 8,
                  pgvector_collection_name=safety.COLLECTION, jwt_secret='offline-fixture-' * 8,
                  model_provider='openai', model_base_url='https://api.deepseek.com', model_names=['deepseek-flash'],
                  embeddings_provider='openai', embeddings_model_name='BAAI/bge-m3', embeddings_base_url='https://api.siliconflow.cn/v1',
                  rerank_model_name='BAAI/bge-reranker-v2-m3', rerank_base_url='https://api.siliconflow.cn/v1', rerank_path='/rerank')
    for k in ['database_uri', 'pgvector_connection', 'checkpointer_uri']:
        values[k] = f'postgresql://{safety.USER}:offline-only@{safety.HOST}:5432/{safety.DATABASE}'
    return SimpleNamespace(**values), dict(attested=True, volume=safety.VOLUME, database=safety.DATABASE,
                                         user=safety.USER, system_identifier='1234567890123456789')


def test_exact_release_profile_accepts_fresh_isolated_database():
    settings, identity = fixture()
    safety.validate(settings, identity)
    safety.verify_actual((safety.DATABASE, safety.USER, identity['system_identifier']), identity)


@pytest.mark.parametrize('field,value', [('pgvector_collection_name', 'my_collection'),
    ('postgres_host', 'postgres'), ('postgres_database', 'langgraph_db'), ('postgres_user', 'postgres'),
    ('embeddings_model_name', 'Pro/BAAI/bge-m3'), ('rerank_path', '/reranks'),
    ('model_names', ['other-model']), ('jwt_secret', 'short')])
def test_configuration_mismatch_stops_before_initializers(field, value):
    settings, identity = fixture()
    setattr(settings, field, value)
    with pytest.raises(safety.IsolationError):
        safety.validate(settings, identity)


@pytest.mark.parametrize('field', ['database_uri', 'pgvector_connection', 'checkpointer_uri'])
def test_each_actual_connection_uri_is_independently_checked(field):
    settings, identity = fixture()
    setattr(settings, field, 'postgresql://postgres:unused@postgres:5432/langgraph_db')
    with pytest.raises(safety.IsolationError, match='database_uri_not_isolated'):
        safety.validate(settings, identity)


@pytest.mark.parametrize('override', [{'attested': False}, {'volume': 'langgraph-rag-agent_postgres_data'},
    {'database': 'langgraph_rag_stage3_test'}, {'system_identifier': ''}])
def test_missing_attestation_and_wrong_volume_rejected(override):
    settings, identity = fixture()
    identity.update(override)
    with pytest.raises(safety.IsolationError):
        safety.validate(settings, identity)


def test_same_database_name_on_another_cluster_is_rejected():
    settings, identity = fixture()
    with pytest.raises(safety.IsolationError, match='actual_database_identity'):
        safety.verify_actual((safety.DATABASE, safety.USER, '987654321'), identity)


@pytest.mark.parametrize('rows', [[('my_collection', safety.PROFILE)], [(safety.COLLECTION, None)],
    [(safety.COLLECTION, {**safety.PROFILE, 'model': 'unknown-old-model'})],
    [(safety.COLLECTION, {**safety.PROFILE, 'dimensions': 768})]])
def test_same_dimension_is_not_enough_to_accept_old_model(rows):
    with pytest.raises(safety.IsolationError, match='collection_model_profile'):
        safety.verify_profile(rows)


def test_known_collection_profile_is_valid_and_empty_database_allowed():
    safety.verify_profile([])
    safety.verify_profile([(safety.COLLECTION, dict(safety.PROFILE))])


@pytest.mark.parametrize('path', ['/api/v1/chat/', '/api/v1/chat/thread-id',
    '/api/v1/documents/upload/thread-id', '/api/v1/documents/knowledge-bases/kb/upload'])
def test_r1_model_entry_routes_blocked_before_sdk(path):
    assert safety.block_model_route('POST', path)


def test_read_auth_and_resource_routes_remain_available():
    for method, path in [('GET', '/api/v1/chat/config'), ('GET', '/api/v1/chat/thread-id'),
                         ('POST', '/api/v1/auth/login'), ('POST', '/api/v1/knowledge-bases/'),
                         ('DELETE', '/api/v1/documents/doc-id')]:
        assert not safety.block_model_route(method, path)


def test_sync_and_async_external_transports_fail_closed():
    with pytest.raises(safety.IsolationError, match='external_HTTP_disabled'):
        safety.deny_http(None)
    with pytest.raises(safety.IsolationError, match='external_HTTP_disabled'):
        asyncio.run(safety.deny_async_http(None))
