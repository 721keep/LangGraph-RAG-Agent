"""Native FastAPI + real PostgreSQL/pgvector; no model, auth or delete mocks."""
import asyncio
from collections import Counter
import hashlib
import json
import logging
import os
from pathlib import Path
import sys
import traceback
from urllib.parse import urlsplit
from uuid import UUID, uuid4

if os.environ.get('DELETE_ACCEPTANCE_MODE') != 'isolated':
    raise RuntimeError('Explicit isolated delete-test mode required')
sys.path.insert(0, '/workspace/backend')
CFG = json.loads(Path('/run/secrets/delete_runtime').read_text())
assert CFG['host'] == 'delete-postgres' and CFG['database'] == 'langgraph_rag_delete_20261026_' + CFG['run_id']
assert CFG['collection'] == 'delete_acceptance_' + CFG['run_id']
assert not Path('/workspace/.env').exists()
os.environ.update({
    'CHAT_API_KEY': 'delete-only-dummy-chat', 'MODEL_PROVIDER': 'openai', 'MODEL_NAMES': '["delete-only-offline"]',
    'MODEL_BASE_URL': 'https://unused.invalid', 'TAVILY_API_KEY': 'delete-only-dummy-search',
    'DASHSCOPE_API_KEY': '', 'EMBEDDINGS_API_KEY': 'delete-only-dummy-embedding', 'EMBEDDINGS_PROVIDER': 'openai',
    'EMBEDDINGS_MODEL_NAME': 'local-synthetic-1024', 'EMBEDDINGS_BASE_URL': 'https://unused.invalid',
    'RERANK_API_KEY': 'delete-only-dummy-rerank', 'RERANK_BASE_URL': 'https://unused.invalid',
    'RERANK_MODEL_NAME': 'delete-only-offline', 'RERANK_PATH': '/rerank',
    'TOKEN_BEARER_URL': '/api/v1/auth/login', 'JWT_SECRET': CFG['jwt_secret'], 'JWT_ALGORITHM': 'HS256',
    'ACCESS_TOKEN_EXPIRY_MINS': '60', 'REFRESH_TOKEN_EXPIRY_DAYS': '1',
    'POSTGRES_HOST': CFG['host'], 'POSTGRES_PORT': str(CFG['port']), 'POSTGRES_USER': CFG['user'],
    'POSTGRES_PASSWORD': CFG['password'], 'POSTGRES_DATABASE': CFG['database'],
    'PGVECTOR_COLLECTION_NAME': CFG['collection'], 'LANGCHAIN_TRACING_V2': 'false', 'LANGSMITH_TRACING': 'false'})
logging.disable(logging.CRITICAL)
import httpx
import httpx2
import requests
BLOCKED_HTTP = []


def deny(*args, **kwargs):
    BLOCKED_HTTP.append('blocked before network send')
    raise AssertionError('All model/external HTTP is forbidden in deletion acceptance')


httpx.HTTPTransport.handle_request = deny
httpx.AsyncHTTPTransport.handle_async_request = deny
httpx2.HTTPTransport.handle_request = deny
httpx2.AsyncHTTPTransport.handle_async_request = deny
requests.Session.send = deny
from app.config import settings
from loguru import logger
logger.remove()
from app.main import app
from app.auth.utils import create_jwt_token
from app.db.main import engine, async_session
from app.db.models import Base, User, KnowledgeBase, Document, Thread
from app.db import pgvector_utils as pg
from fastapi.responses import JSONResponse
from sqlalchemy import text

ART = Path('/delete-artifacts')
ERRORS = []
REPORT = {'mode': 'native app.main ASGI routes/auth/services + real dedicated PostgreSQL/pgvector',
          'database': CFG['database'], 'collection': CFG['collection'], 'cases': [],
          'targets_checked_before_delete': [], 'external_model_requests': 0}


@app.exception_handler(Exception)
async def observe_exception(request, error):
    # Observability only: actual SQL/ORM errors still produce HTTP 500.
    ERRORS.append({'type': type(error).__name__, 'message': str(error)[:1500],
                   'frames': [{'file': f.filename, 'line': f.lineno, 'function': f.name}
                              for f in traceback.extract_tb(error.__traceback__) if '/app/' in f.filename]})
    return JSONResponse({'detail': 'Database exception recorded by delete acceptance'}, status_code=500)


def save():
    REPORT['errors'] = ERRORS
    REPORT['unexpected_network_attempts'] = len(BLOCKED_HTTP)
    (ART / 'delete_acceptance.json').write_text(json.dumps(REPORT, ensure_ascii=False, indent=2))


async def identity(session, collection=True):
    row = (await session.execute(text('SELECT current_database(),current_user'))).one()
    assert tuple(row) == (CFG['database'], CFG['user'])
    for uri in [settings.database_uri, settings.pgvector_connection, settings.checkpointer_uri]:
        u = urlsplit(uri)
        assert (u.hostname, u.port, u.path) == (CFG['host'], 5432, '/' + CFG['database'])
    assert settings.pgvector_collection_name == CFG['collection']
    if collection:
        ident = (await session.execute(text('SELECT uuid FROM langchain_pg_collection WHERE name=:n'), {'n': CFG['collection']})).scalar_one()
        assert str(ident) == REPORT['collection_uuid']


class Harness:
    def __init__(self):
        self.allowed = {'kb': set(), 'doc': set(), 'thread': set()}
        self.datasets = []

    async def init(self):
        async with async_session() as s:
            await identity(s, collection=False)  # Before any DDL or write.
            tables = set((await s.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))).scalars())
            allowed_tables = {'delete_acceptance_meta', 'users', 'knowledge_bases', 'documents', 'threads',
                              'langchain_pg_collection', 'langchain_pg_embedding'}
            assert tables <= allowed_tables
            if tables:
                assert 'delete_acceptance_meta' in tables
                assert (await s.execute(text('SELECT run_id FROM delete_acceptance_meta'))).scalar_one() == CFG['run_id']
        async with engine.begin() as c:
            await c.execute(text('CREATE TABLE IF NOT EXISTS delete_acceptance_meta(run_id text PRIMARY KEY)'))
            await c.execute(text('INSERT INTO delete_acceptance_meta(run_id) VALUES (:run) ON CONFLICT DO NOTHING'), {'run': CFG['run_id']})
            await c.run_sync(Base.metadata.create_all)
        await pg.vector_store.__apost_init__()  # Original PGVector physical tables/collection, no embedding request.
        async with async_session() as s:
            ident = (await s.execute(text('SELECT uuid FROM langchain_pg_collection WHERE name=:n'), {'n': CFG['collection']})).scalar_one()
            REPORT['collection_uuid'] = str(ident)
            await identity(s)
            REPORT['postgres_version'] = (await s.execute(text('SHOW server_version'))).scalar_one()
            REPORT['pgvector_version'] = (await s.execute(text("SELECT extversion FROM pg_extension WHERE extname='vector'"))).scalar_one()
            REPORT['foreign_keys'] = (await s.execute(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid IN ('documents'::regclass,'threads'::regclass) AND contype='f' ORDER BY conname"))).scalars().all()
            a, b = uuid4(), uuid4()
            self.users = (a, b)
            for num, ident in enumerate(self.users):
                s.add(User(id=ident, username='del_' + str(ident)[:8], email=f'del{num}_{CFG["run_id"]}@example.org',
                           password_hash='fictional-delete-test-unused-password', first_name='', last_name='', is_active=True, is_verified=True))
            await s.commit()
        self.headers = [{'Authorization': 'Bearer ' + create_jwt_token({'id': str(ident), 'email': f'del{num}_{CFG["run_id"]}@example.org'})}
                        for num, ident in enumerate(self.users)]
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url='http://delete-test')
        REPORT['original_router_modules'] = sorted({r.endpoint.__module__ for r in app.routes if getattr(r, 'path', '').startswith(('/api/v1/documents', '/api/v1/knowledge-bases'))})
        save()

    async def seed(self, label):
        kbs, docs, threads = [uuid4() for _ in range(3)], [uuid4() for _ in range(4)], [uuid4(), uuid4()]
        self.allowed['kb'].update(kbs); self.allowed['doc'].update(docs); self.allowed['thread'].update(threads)
        data = {'label': label, 'kb': kbs, 'doc': docs, 'thread': threads, 'chunks': {}}
        async with async_session() as s:
            await identity(s)  # DB/collection and all target UUIDs known before inserts.
            for i, kb in enumerate(kbs):
                s.add(KnowledgeBase(id=kb, name='delete-' + label + '-' + str(i), user_id=self.users[1 if i == 2 else 0]))
            await s.flush()
            for i, doc in enumerate(docs):
                kb = kbs[0] if i < 2 else kbs[i - 1]
                s.add(Document(id=doc, file_name=f'{label}_{i}.pdf', knowledge_base_id=kb, status='ready'))
            s.add_all([Thread(id=threads[0], user_id=self.users[0], knowledge_base_id=kbs[0]),
                       Thread(id=threads[1], user_id=self.users[0], knowledge_base_id=kbs[1])])
            await s.flush()
            for i, doc in enumerate(docs):
                kb = kbs[0] if i < 2 else kbs[i - 1]
                actor = self.users[1 if i == 3 else 0]
                ids = []
                for part in range(2):
                    ident = str(uuid4()); ids.append(ident)
                    vector = [0.] * 1024; vector[(i * 2 + part) % 1024] = 1.
                    metadata = {'id': ident, 'user_id': str(actor), 'knowledge_base_id': str(kb),
                                'document_id': str(doc), 'file_name': f'{label}_{i}.pdf', 'page': 0,
                                'chunk_index': part, 'delete_acceptance_run': CFG['run_id']}
                    await s.execute(text('''INSERT INTO langchain_pg_embedding(id,collection_id,embedding,document,cmetadata)
                        VALUES (:id,:collection,CAST(:vector AS vector),:content,CAST(:metadata AS jsonb))'''),
                                    {'id': ident, 'collection': UUID(REPORT['collection_uuid']), 'vector': json.dumps(vector),
                                     'content': f'Fictional local PDF chunk {label}-{i}-{part}', 'metadata': json.dumps(metadata)})
                data['chunks'][doc] = ids
            await s.commit()
        self.datasets.append(data)
        return data

    async def delete(self, kind, ident, actor=0, missing=False):
        assert ident in self.allowed[kind]
        async with async_session() as s:
            await identity(s)
            table = 'knowledge_bases' if kind == 'kb' else 'documents'
            exists = (await s.execute(text('SELECT count(*) FROM ' + table + ' WHERE id=:id'), {'id': ident})).scalar_one()
            assert exists == (0 if missing else 1)
        REPORT['targets_checked_before_delete'].append({'kind': kind, 'id': str(ident), 'existing_rows': exists, 'actor': actor})
        save()
        path = '/api/v1/' + ('knowledge-bases/' if kind == 'kb' else 'documents/') + str(ident)
        return await self.client.delete(path, headers={} if actor is None else self.headers[actor])

    async def rows(self, data):
        async with async_session() as s:
            await identity(s)
            out = {}
            for table, ids in [('knowledge_bases', data['kb']), ('documents', data['doc']), ('threads', data['thread'])]:
                result = (await s.execute(text('SELECT id::text,md5(row_to_json(t)::text) FROM ' + table + ' t WHERE id=ANY(:ids) ORDER BY id'), {'ids': ids})).all()
                out[table] = dict(result)
            ids = [i for v in data['chunks'].values() for i in v]
            out['vectors'] = dict((await s.execute(text('SELECT id,md5(row_to_json(t)::text) FROM langchain_pg_embedding t WHERE id=ANY(:ids) ORDER BY id'), {'ids': ids})).all())
            return out

    async def exists(self, kind, ident):
        table = {'doc': 'documents', 'kb': 'knowledge_bases', 'thread': 'threads'}[kind]
        async with async_session() as s:
            return (await s.execute(text('SELECT count(*) FROM ' + table + ' WHERE id=:id'), {'id': ident})).scalar_one()

    async def fault(self, kind, ident):
        assert ident in self.allowed[kind]
        table = 'knowledge_bases' if kind == 'kb' else 'documents'
        key = 'knowledge_base_id' if kind == 'kb' else 'document_id'
        name = 'fault_' + str(ident).replace('-', '')
        async with engine.begin() as c:
            await identity(c)
            # Real DB exception after the original vector DELETE has already run.
            await c.execute(text(f'''CREATE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    IF EXISTS (SELECT 1 FROM langchain_pg_embedding WHERE cmetadata->>'{key}'='{ident}') THEN
                        RAISE EXCEPTION 'fault_injection_order_incorrect' USING ERRCODE='23514';
                    END IF;
                    RAISE EXCEPTION 'delete_acceptance_injected_after_vector_delete' USING ERRCODE='23514';
                END; $$'''))
            await c.execute(text(f'''CREATE TRIGGER {name} BEFORE DELETE ON {table}
                FOR EACH ROW WHEN (OLD.id='{ident}'::uuid) EXECUTE FUNCTION {name}()'''))

    async def close(self):
        assert not BLOCKED_HTTP
        save()
        await self.client.aclose()
        await engine.dispose()
        await pg.vector_store._async_engine.dispose()
