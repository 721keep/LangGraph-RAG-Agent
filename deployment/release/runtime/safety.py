"""Fail-closed deployment identity checks; no app import, DDL or model call."""
from urllib.parse import urlsplit

DATABASE = 'langgraph_rag_bge_prod'
HOST = 'r1-postgres'
USER = 'r1_rag_user'
COLLECTION = 'bge_m3_1024_v1'
VOLUME = 'rag_release_r1_pgdata_v1'
PROFILE = {'provider': 'siliconflow', 'model': 'BAAI/bge-m3', 'dimensions': 1024,
           'version': 'bge-m3-dense-v1', 'purpose': 'release-r1'}


class IsolationError(RuntimeError):
    pass


def validate(settings, identity):
    expected = {'postgres_host': HOST, 'postgres_port': 5432, 'postgres_user': USER,
                'postgres_database': DATABASE, 'pgvector_collection_name': COLLECTION,
                'model_provider': 'openai', 'model_base_url': 'https://api.deepseek.com',
                'model_names': ['deepseek-flash'], 'embeddings_provider': 'openai',
                'embeddings_model_name': 'BAAI/bge-m3', 'embeddings_base_url': 'https://api.siliconflow.cn/v1',
                'rerank_model_name': 'BAAI/bge-reranker-v2-m3',
                'rerank_base_url': 'https://api.siliconflow.cn/v1', 'rerank_path': '/rerank'}
    if any(getattr(settings, k, None) != v for k, v in expected.items()):
        raise IsolationError('release_configuration_mismatch')
    if len(settings.jwt_secret) < 64 or len(settings.postgres_password) < 32:
        raise IsolationError('release_secret_strength_invalid')
    if not identity.get('attested') or identity.get('volume') != VOLUME or identity.get('database') != DATABASE or identity.get('user') != USER:
        raise IsolationError('database_volume_not_attested')
    if not str(identity.get('system_identifier', '')).isdigit():
        raise IsolationError('database_identity_missing')
    for name in ['database_uri', 'pgvector_connection', 'checkpointer_uri']:
        uri = urlsplit(getattr(settings, name))
        if (uri.hostname, uri.port, uri.path, uri.username) != (HOST, 5432, '/' + DATABASE, USER):
            raise IsolationError('database_uri_not_isolated_' + name)


def verify_actual(row, identity):
    if tuple(row) != (DATABASE, USER, identity['system_identifier']):
        raise IsolationError('actual_database_identity_mismatch')


def verify_profile(rows):
    for name, profile in rows:
        if name != COLLECTION or not isinstance(profile, dict) or any(profile.get(k) != v for k, v in PROFILE.items()):
            raise IsolationError('collection_model_profile_mismatch')


def block_model_route(method, path):
    return method == 'POST' and (path.startswith('/api/v1/chat') or
                                (path.startswith('/api/v1/documents/') and '/upload' in path))


def deny_http(*args, **kwargs):
    raise IsolationError('R1_external_HTTP_disabled')


async def deny_async_http(*args, **kwargs):
    raise IsolationError('R1_external_HTTP_disabled')
