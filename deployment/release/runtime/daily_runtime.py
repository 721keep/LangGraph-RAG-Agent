"""Wire original model factories into the daily transport; original routes remain."""
import json
from pathlib import Path
import uuid

import httpx
from budget_core import OfficialCounter
from daily_guard import DailyError, Ledger, MESSAGES, SCOPE, Transport
from model_guard import DenySync
from safety import block_model_route


def configure(runtime):
    limits = json.loads(Path('/run/secrets/daily_policy').read_text())
    ledger = Ledger('/run/daily/usage.sqlite3', limits, OfficialCounter('/run/daily-tokenizer'))
    transport = Transport(ledger, {k:runtime[n] for k,n in [('chat','CHAT_API_KEY'),
                          ('embedding','EMBEDDINGS_API_KEY'), ('reranker','RERANK_API_KEY')]})
    native_client = httpx.AsyncClient

    def client():
        return native_client(transport=transport, trust_env=False, follow_redirects=False,
                             timeout=httpx.Timeout(120, connect=15))

    import langchain.embeddings
    native_embeddings = langchain.embeddings.init_embeddings

    def embeddings(**kwargs):
        kwargs.update(max_retries=0, chunk_size=limits['max_batch_texts'],
                      check_embedding_ctx_length=False, model_kwargs={'encoding_format':'float'},
                      http_client=httpx.Client(transport=DenySync(), trust_env=False), http_async_client=client())
        return native_embeddings(**kwargs)

    langchain.embeddings.init_embeddings = embeddings
    import openai
    native_openai = openai.AsyncOpenAI

    def openai_client(**kwargs):
        kwargs.update(max_retries=0, http_client=client())
        return native_openai(**kwargs)

    openai.AsyncOpenAI = openai_client
    from app.chat import deepseek
    native_chat = deepseek.DeepSeekChatOpenAI

    def chat(**kwargs):
        kwargs.update(max_retries=0, max_tokens=limits['chat_max_output_tokens'], stream_usage=True,
                      extra_body={'thinking':{'type':'disabled'}},
                      http_client=httpx.Client(transport=DenySync(), trust_env=False), http_async_client=client())
        return native_chat(**kwargs)

    deepseek.DeepSeekChatOpenAI = chat
    return ledger


class Boundary:
    """Preserve normal JSON/NDJSON contracts, expose only fixed safe error codes."""
    def __init__(self, app, ledger):
        self.app, self.ledger = app, ledger

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or not block_model_route(scope['method'], scope['path']):
            return await self.app(scope, receive, send)
        chat = scope['path'].startswith('/api/v1/chat')
        state = {'id':str(uuid.uuid4()), 'error':None}
        token = SCOPE.set(state)
        started = ended = False
        buffered = []
        start = None

        async def json_error(code, status=429):
            data = json.dumps({'detail':{'error_code':code, 'message':MESSAGES[code], 'status':'failed'}}, ensure_ascii=False).encode()
            await send({'type':'http.response.start','status':status,'headers':[(b'content-type',b'application/json')]})
            await send({'type':'http.response.body','body':data})

        async def guarded(message):
            nonlocal started, ended, start
            if message['type'] == 'http.response.start':
                start = message
                if chat:
                    started = True
                    await send(message)
                return
            if message['type'] != 'http.response.body' or ended:
                return
            if chat:
                if state['error']:
                    message = {'type':'http.response.body','body':(json.dumps({'type':'error','code':state['error']})+'\n').encode(), 'more_body':False}
                ended = not message.get('more_body',False)
                await send(message)
            else:
                buffered.append(message.get('body',b''))
                if not message.get('more_body',False):
                    ended = True
                    if state['error']:
                        try:
                            previous = json.loads(b''.join(buffered)).get('detail',{})
                        except ValueError:
                            previous = {}
                        detail = dict(previous) if isinstance(previous,dict) else {}
                        detail.update(error_code=state['error'], message=MESSAGES[state['error']])
                        await send({'type':'http.response.start','status':429 if state['error'] in ('daily_limit_reached','daily_busy') else 503,
                                    'headers':[(b'content-type',b'application/json')]})
                        await send({'type':'http.response.body','body':json.dumps({'detail':detail}).encode()})
                    else:
                        await send(start)
                        await send({'type':'http.response.body','body':b''.join(buffered)})
        try:
            if chat and scope['path'].rstrip('/') == '/api/v1/chat':
                await json_error('daily_login_required',401)
                return
            try:
                self.ledger.available('chat' if chat else 'embedding')
            except DailyError as error:
                await json_error(error.code,429 if error.code=='daily_limit_reached' else 503)
                return
            try:
                await self.app(scope, receive, guarded)
            except Exception:
                if not state['error']:
                    raise
                if not ended:
                    if started:
                        await guarded({'type':'http.response.body','body':b''})
                    else:
                        await json_error(state['error'],503)
            if not ended and state['error'] and started:
                await guarded({'type':'http.response.body','body':b''})
        finally:
            SCOPE.reset(token)
