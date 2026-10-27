"""Run the original full application with R1-only deployment and network guards."""
from contextlib import asynccontextmanager
import json
import logging
import os
from pathlib import Path
import sys

from safety import (COLLECTION, PROFILE, IsolationError, block_model_route,
                    deny_http, deny_async_http, validate, verify_actual, verify_profile)

PHASE = os.environ.get('RELEASE_PHASE', 'R1')
MODE = os.environ.get('RELEASE_MODEL_MODE', 'off')
if (PHASE, MODE) not in {('R1','off'), ('R2','off'), ('R2','bounded'), ('DAILY','daily')} or Path('/app/.env').exists():
    raise IsolationError('R1_requires_private_configuration_without_dotenv')
runtime = json.loads(Path('/run/secrets/runtime').read_text())
identity = json.loads(Path('/run/secrets/database_identity').read_text())
os.environ.update({k: str(v) for k, v in runtime.items()})

# Defense in depth: Backend has only internal Docker networks and all ordinary
# HTTP transports fail closed. No SDK factory or business tool is replaced.
import httpx
import httpx2
import requests
if MODE == 'bounded':
    # Capture the actual send transport before the ordinary transports are denied.
    import r2_models
elif MODE == 'daily':
    import daily_runtime
httpx.HTTPTransport.handle_request = deny_http
httpx.AsyncHTTPTransport.handle_async_request = deny_async_http
httpx2.HTTPTransport.handle_request = deny_http
httpx2.AsyncHTTPTransport.handle_async_request = deny_async_http
requests.Session.send = deny_http

from app.config import settings
from loguru import logger
logger.remove()
logger.add(sys.stdout, level='INFO', diagnose=False, backtrace=False)
logging.getLogger('httpx').setLevel(logging.WARNING)
validate(settings, identity)
if MODE == 'bounded':
    r2_models.configure(runtime)
elif MODE == 'daily':
    daily_ledger = daily_runtime.configure(runtime)

from app.main import app
from app.db.main import engine
from app.db import pgvector_utils as pg, checkpointer
from fastapi.responses import JSONResponse
from sqlalchemy import text
import psycopg
from psycopg.rows import tuple_row

pg.vector_store.collection_metadata = dict(PROFILE)
original_lifespan = app.router.lifespan_context
QUERY = 'SELECT current_database(),current_user,(SELECT system_identifier::text FROM pg_control_system())'
STATE = {'ready': False, 'connections': {}, 'blocked_model_routes': 0}
app.state.release_ready = lambda: STATE['ready']
if MODE == 'bounded':
    import r2_live
elif MODE == 'daily':
    app.add_middleware(daily_runtime.Boundary, ledger=daily_ledger)


async def precheck():
    proof = {}
    for name, target in [('business', engine), ('pgvector', pg.vector_store._async_engine)]:
        async with target.connect() as conn:
            await conn.execute(text('SET TRANSACTION READ ONLY'))
            row = (await conn.execute(text(QUERY))).one()
            verify_actual(row, identity)
            proof[name] = {'database': row[0], 'user': row[1], 'system_identifier': row[2]}
            exists = (await conn.execute(text("SELECT to_regclass('public.langchain_pg_collection') IS NOT NULL"))).scalar_one()
            if exists:
                rows = (await conn.execute(text('SELECT name,cmetadata FROM langchain_pg_collection'))).all()
                verify_profile(rows)
                invalid = (await conn.execute(text('SELECT COUNT(*) FROM langchain_pg_embedding WHERE vector_dims(embedding)<>1024'))).scalar_one()
                if invalid:
                    raise IsolationError('existing_vector_dimensions_mismatch')
    async with await psycopg.AsyncConnection.connect(settings.checkpointer_uri,
                options='-c default_transaction_read_only=on', row_factory=tuple_row) as conn:
        row = await (await conn.execute(QUERY)).fetchone()
        verify_actual(row, identity)
        proof['checkpointer'] = {'database': row[0], 'user': row[1], 'system_identifier': row[2]}
    return proof


@asynccontextmanager
async def release_lifespan(application):
    # All three actual connections and any existing vector profile checked
    # before business create_all(), Checkpointer setup(), or PGVector DDL.
    proof = await precheck()
    async with original_lifespan(application):
        await pg.vector_store.__apost_init__()
        async with engine.connect() as conn:
            rows = (await conn.execute(text('SELECT name,cmetadata FROM langchain_pg_collection'))).all()
            verify_profile(rows)
            if len(rows) != 1:
                raise IsolationError('release_collection_initialization_failed')
        saver = await checkpointer.get_checkpointer()
        async with saver.conn.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute(QUERY)
            verify_actual(await cursor.fetchone(), identity)
        STATE.update(ready=True, connections=proof)
        logger.info('Isolated business/PGVector/Checkpointer initialized; release mode={}.', MODE)
        try:
            yield
        finally:
            STATE['ready'] = False


app.router.lifespan_context = release_lifespan


@app.middleware('http')
async def r1_model_lock(request, call_next):
    enabled = MODE == 'daily' or (MODE == 'bounded' and r2_models.ledger.read()['mode'] == 'armed' and not r2_models.ledger.read()['halted'])
    if block_model_route(request.method, request.url.path) and not enabled:
        STATE['blocked_model_routes'] += 1
        return JSONResponse({'detail': 'R1 startup validation only: live model requests are disabled until R2 authorization.'}, status_code=503)
    return await call_next(request)


@app.get('/health/ready', include_in_schema=False)
async def ready():
    if not STATE['ready']:
        return JSONResponse({'status': 'starting'}, status_code=503)
    async with engine.connect() as conn:
        row = (await conn.execute(text(QUERY))).one()
        verify_actual(row, identity)
    return {'status': 'ready', 'release': PHASE, 'model_mode': MODE,
            'model_api_enabled': (MODE == 'daily' and not daily_ledger.status()['paused']) or (MODE == 'bounded' and r2_models.ledger.read()['mode'] == 'armed'),
            'collection': COLLECTION, 'connections': STATE['connections'],
            'blocked_model_routes': STATE['blocked_model_routes']}


@app.exception_handler(IsolationError)
async def isolation_failure(request, error):
    return JSONResponse({'detail': 'Release isolation check failed.'}, status_code=503)


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8000, access_log=False)
