"""R2 original Router/Service observation and acceptance-resource guards. No replay."""
from collections import Counter
import hashlib,json,secrets
from pathlib import Path
from fastapi import HTTPException,Request
from fastapi.routing import APIRoute
from fastapi.responses import JSONResponse
from app.main import app
from app.db import pgvector_utils as pg,checkpointer
from app.documents import service as document_service
from app.threads import service as thread_service
from app.knowledge_bases import service as kb_service
from app.chat import tools
from r2_models import ledger,transport,PLAN,ART
from model_guard import StopRun,REQUEST_CONTEXT
CONTROL = Path('/run/secrets/r2_control').read_text().strip()
STATE = ART / 'operations.json'
RUN = json.loads(STATE.read_text()) if STATE.exists() else {
    'chats': [], 'searches': [], 'reranks': [], 'document_timeline': [], 'http': []}


def save():
    STATE.write_text(json.dumps(RUN, ensure_ascii=False, indent=2))


def armed():
    state = ledger.read()
    if state['mode'] != 'armed' or state['halted']:
        raise HTTPException(409, 'R2 model budget blocked, sealed or halted; no request sent.')


async def scope(kb_id, user, session):
    kb = await kb_service.get_knowledge_base(kb_id, user.id, session)
    if user.email != PLAN['email'] or kb.name not in PLAN['kb_names']:
        raise HTTPException(403, 'R2 only permits this run\'s test resources.')
    return kb


real_create_document = document_service.create_knowledge_base_document


async def observed_create_document(*args, **kwargs):
    doc = await real_create_document(*args, **kwargs)
    RUN.update(document_id=str(doc.id))
    RUN['document_timeline'].append({'status': doc.status, 'stage': 'original_service_committed'})
    save()
    return doc


document_service.create_knowledge_base_document = observed_create_document
real_search = pg.vector_store.asimilarity_search_with_relevance_scores


async def observed_search(query, **kwargs):
    expected = {'user_id': RUN['user_id'], 'knowledge_base_id': RUN['kb_id']}
    if kwargs.get('filter') != expected:
        ledger.halt('vector_filter_scope_mismatch')
    result = await real_search(query, **kwargs)
    rows = []
    for doc, score in result:
        metadata = doc.metadata
        if any(str(metadata.get(k)) != v for k, v in {**expected, 'document_id': RUN['document_id']}.items()):
            ledger.halt('retrieved_chunk_scope_mismatch')
        rows.append({'id': metadata['id'], 'score': float(score), 'metadata': metadata,
                     'content_sha256': hashlib.sha256(doc.page_content.encode()).hexdigest()})
    RUN['searches'].append({'scenario': REQUEST_CONTEXT.get()[0], 'filter': expected,
                            'native_module': real_search.__func__.__module__, 'results': rows})
    save()
    return result


pg.vector_store.asimilarity_search_with_relevance_scores = observed_search
real_rerank = tools.rerank_documents


async def observed_rerank(**kwargs):
    result = await real_rerank(**kwargs)
    RUN['reranks'].append({'scenario': REQUEST_CONTEXT.get()[0], 'input_count': len(kwargs['documents']),
                          'results': result})
    save()
    return result


tools.rerank_documents = observed_rerank


def protect_upload(native):
    async def upload(**kw):
        armed()
        await scope(kw['knowledge_base_id'], kw['current_user'], kw['session'])
        if RUN.get('upload_started'):
            raise HTTPException(409, 'R2 one small PDF only; no repeated upload.')
        data = await kw['file'].read(65537)
        await kw['file'].seek(0)
        if kw['file'].filename != PLAN['file_name'] or hashlib.sha256(data).hexdigest() != PLAN['file_sha256']:
            raise HTTPException(403, 'Only the approved small R2 PDF is allowed.')
        RUN.update(upload_started=True, kb_id=str(kw['knowledge_base_id']), user_id=str(kw['current_user'].id))
        save()
        token = REQUEST_CONTEXT.set(('upload', 'none'))
        try:
            result = await native(**kw)
            RUN['upload_ready'] = result['status'] == 'ready'
            RUN['document_timeline'].append({'status': result['status'], 'stage': 'original_router_response'})
            save()
            return result
        except BaseException as error:
            RUN['upload_error'] = type(error).__name__
            save()
            raise
        finally:
            REQUEST_CONTEXT.reset(token)
    return upload


def protect_chat(native):
    async def chat(**kw):
        armed()
        thread = await thread_service.get_thread(kw['thread_id'], kw['current_user'].id, kw['session'])
        await scope(thread.knowledge_base_id, kw['current_user'], kw['session'])
        if not RUN.get('upload_ready') or len(RUN['chats']) >= 2:
            raise HTTPException(409, 'R2 requires ready PDF; at most two explicit user turns.')
        prompt = kw['prompt_input'].prompt
        digest = hashlib.sha256(prompt.encode()).hexdigest()
        if prompt not in PLAN['prompts'] or any(c.get('prompt_sha256') == digest for c in RUN['chats']):
            raise HTTPException(409, 'Only the two new R2 questions are allowed, once each.')
        item = {'prompt_sha256': digest, 'scenario': ['positive', 'followup', 'no_evidence'][len(RUN['chats'])],
                'thread_id': str(thread.id), 'events': {}, 'complete': False}
        RUN['chats'].append(item)
        save()
        response = await native(**kw)
        iterator = response.body_iterator

        async def stream():
            token = REQUEST_CONTEXT.set((item['scenario'], item['thread_id']))
            counts = Counter()
            answer = ''
            results = []
            try:
                async for part in iterator:
                    value = part.decode() if isinstance(part, bytes) else part
                    for line in value.splitlines():
                        if not line.strip():
                            continue
                        event = json.loads(line)
                        counts[event['type']] += 1
                        if event['type'] == 'llm_chunk':
                            answer += event['content']
                        elif event['type'] == 'tool_result':
                            result = json.loads(event['content'])
                            results.append(result)
                    yield part
                item.update(complete=True, events=dict(counts), answer=answer, tool_results=results)
                save()
            except BaseException as error:
                item.update(events=dict(counts), error_type=type(error).__name__)
                save()
                raise
            finally:
                REQUEST_CONTEXT.reset(token)
        response.body_iterator = stream()
        return response
    return chat


def protect_delete(native, kind):
    async def delete(**kw):
        user = kw['current_user']
        session = kw['session']
        if user.email != PLAN['email']:
            raise HTTPException(403, 'R2 test user only.')
        if kind == 'knowledge_base':
            ident = str(kw['knowledge_base_id'])
            await scope(kw['knowledge_base_id'], user, session)
            expected = RUN.get('created_kbs', [])
        else:
            ident = str(kw['document_id'])
            expected = RUN.get('document_id')
            await scope(__import__('uuid').UUID(RUN['kb_id']), user, session)
        if not (ident in expected if kind == 'knowledge_base' else ident == expected):
            raise HTTPException(403, 'R2 deletion restricted to newly recorded resource IDs.')
        result = await native(**kw)
        RUN.setdefault(kind + '_deleted', []).append(ident)
        save()
        return result
    return delete


def observe_kb_create(native):
    async def create(**kw):
        if kw['current_user'].email != PLAN['email'] or kw['knowledge_base_data'].name not in PLAN['kb_names']:
            raise HTTPException(403, 'R2 acceptance user and KB names only.')
        kb = await native(**kw)
        RUN.setdefault('created_kbs', []).append(str(kb.id))
        save()
        return kb
    return create


for route in app.routes:
    if not isinstance(route, APIRoute):
        continue
    if route.path == '/api/v1/knowledge-bases/' and 'POST' in route.methods:
        route.dependant.call = observe_kb_create(route.dependant.call)
    if route.path == '/api/v1/documents/knowledge-bases/{knowledge_base_id}/upload' and 'POST' in route.methods:
        route.dependant.call = protect_upload(route.dependant.call)
    if route.path == '/api/v1/chat/{thread_id}' and 'POST' in route.methods:
        route.dependant.call = protect_chat(route.dependant.call)
    if 'DELETE' in route.methods:
        if route.path == '/api/v1/knowledge-bases/{knowledge_base_id}':
            route.dependant.call = protect_delete(route.dependant.call, 'knowledge_base')
        elif route.path == '/api/v1/documents/{document_id}':
            route.dependant.call = protect_delete(route.dependant.call, 'document')


@app.middleware('http')
async def protect(request, call_next):
    if ((request.method == 'POST' and (request.url.path == '/api/v1/chat/' or request.url.path.startswith('/api/v1/documents/upload/')))
            or (request.method == 'DELETE' and not request.url.path.startswith(('/api/v1/documents/', '/api/v1/knowledge-bases/')))):
        return JSONResponse({'detail': 'R2 authenticated test resources only.'}, status_code=403)
    response = await call_next(request)
    if request.url.path.startswith('/api/v1/'):
        RUN['http'].append({'method': request.method, 'path': request.url.path, 'status': response.status_code})
        save()
    return response


@app.exception_handler(StopRun)
async def stopped(request, error):
    return JSONResponse({'detail': 'R2 request guard stopped this operation; inspect sanitized ledger.'}, status_code=409)


@app.get('/release/r2/status')
async def status():
    state = ledger.read()
    return {k: state[k] for k in ['mode', 'halted', 'counts', 'requests', 'in_flight']} | {
        'stop_reason': state.get('stop_reason'), 'upload_ready': RUN.get('upload_ready', False),
        'document_id': RUN.get('document_id'), 'kb_id': RUN.get('kb_id'),
        'chats': [{k: v for k, v in c.items() if k not in ['answer', 'tool_results']} for c in RUN['chats']]}


@app.post('/release/r2/control')
async def control(request: Request):
    if request.client.host != '127.0.0.1' or not secrets.compare_digest(request.headers.get('X-R2-Control', ''), CONTROL):
        raise HTTPException(403, 'Private container-loopback control only')
    payload = await request.json()
    if payload.get('action') == 'arm':
        if not app.state.release_ready():
            raise HTTPException(409, 'Isolation not verified')
        ledger.arm()
    elif payload.get('action') == 'seal':
        if ledger.read()['in_flight']:
            raise HTTPException(409, 'Unresolved HTTP request')
        ledger.seal()
        transport.keys.clear()
        transport.private_reasoning.clear()
    else:
        raise HTTPException(400, 'Unknown action')
    return await status()
