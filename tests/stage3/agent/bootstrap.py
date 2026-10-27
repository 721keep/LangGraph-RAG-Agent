"""Dummy model configuration; the optional database target is stage3 only."""
import json
import logging
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
ARTIFACTS = ROOT / 'tests/stage3/artifacts'
sys.path.insert(0, str(ROOT / 'backend'))


def configure(integration=False):
    expected_mode = 'postgres' if integration else 'offline'
    if os.environ.get('STAGE3D_MODE') != expected_mode:
        import pytest
        pytest.skip('Run stage3d with its explicit isolated test mode', allow_module_level=True)
    values = {
        'CHAT_API_KEY': 'stage3d-dummy-chat', 'MODEL_PROVIDER': 'openai',
        'MODEL_NAMES': '["deepseek-flash"]', 'MODEL_BASE_URL': 'https://api.deepseek.com',
        'DASHSCOPE_API_KEY': '', 'TAVILY_API_KEY': 'stage3d-dummy-search',
        'EMBEDDINGS_API_KEY': 'stage3d-dummy-embedding', 'EMBEDDINGS_PROVIDER': 'openai',
        'EMBEDDINGS_MODEL_NAME': 'BAAI/bge-m3', 'EMBEDDINGS_BASE_URL': 'https://api.siliconflow.cn/v1',
        'RERANK_API_KEY': 'stage3d-dummy-rerank', 'RERANK_MODEL_NAME': 'BAAI/bge-reranker-v2-m3',
        'RERANK_BASE_URL': 'https://api.siliconflow.cn/v1', 'RERANK_PATH': '/rerank',
        'TOKEN_BEARER_URL': '/api/v1/auth/login', 'JWT_SECRET': 'stage3d-fictional-jwt-secret-for-tests-only',
        'JWT_ALGORITHM': 'HS256', 'ACCESS_TOKEN_EXPIRY_MINS': '60', 'REFRESH_TOKEN_EXPIRY_DAYS': '1',
        'POSTGRES_HOST': 'stage3-postgres', 'POSTGRES_PORT': '5432', 'POSTGRES_USER': 'stage3_rag_user',
        'POSTGRES_PASSWORD': 'not-connected-offline', 'POSTGRES_DATABASE': 'langgraph_rag_stage3_test',
        'PGVECTOR_COLLECTION_NAME': 'bge_m3_1024_v1_test', 'APP_TIMEZONE': 'Asia/Shanghai',
        'LANGCHAIN_TRACING_V2': 'false', 'LANGSMITH_TRACING': 'false',
    }
    if integration:
        private = json.loads(Path('/run/secrets/runtime').read_text())['settings']
        expected = {'POSTGRES_HOST': 'stage3-postgres', 'POSTGRES_PORT': '5432',
                    'POSTGRES_USER': 'stage3_rag_user', 'POSTGRES_DATABASE': 'langgraph_rag_stage3_test',
                    'PGVECTOR_COLLECTION_NAME': 'bge_m3_1024_v1_test'}
        if any(str(private.get(k)) != v for k, v in expected.items()):
            raise RuntimeError('Stage3 private database target is not isolated')
        values['POSTGRES_PASSWORD'] = private['POSTGRES_PASSWORD']
    for key, value in values.items():
        os.environ[key] = value
    if (ROOT / '.env').exists():
        raise RuntimeError('Tests must run from a staged tree without production dotenv')
    logging.disable(logging.CRITICAL)
    import httpx
    import requests
    from loguru import logger

    def deny(*args, **kwargs):
        raise AssertionError('External HTTP is forbidden in stage3d')

    httpx.HTTPTransport.handle_request = deny
    httpx.AsyncHTTPTransport.handle_async_request = deny
    requests.Session.send = deny
    import httpx2
    httpx2.HTTPTransport.handle_request = deny
    httpx2.AsyncHTTPTransport.handle_async_request = deny
    from app.config import settings
    logger.remove()
    from guard import verify_isolation
    verify_isolation(settings)
    if integration:
        import psycopg
        with psycopg.connect(settings.checkpointer_uri, options='-c default_transaction_read_only=on -c statement_timeout=5000') as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT current_database(), current_user')
                if cursor.fetchone() != ('langgraph_rag_stage3_test', 'stage3_rag_user'):
                    raise RuntimeError('Actual database failed stage3 isolation proof')
    return settings
