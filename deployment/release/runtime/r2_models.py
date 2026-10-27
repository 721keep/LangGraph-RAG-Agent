"""Explicit bounded model configuration for the original factories, default off."""
import json
from pathlib import Path
import httpx
from model_guard import Ledger, OfficialCounter, Transport, DenySync, StopRun

ART = Path('/run/r2')
PLAN = json.loads(Path('/run/secrets/r2_plan').read_text())
ledger = Ledger(ART / 'model_budget.json', OfficialCounter('/run/tokenizer'))
if ledger.read()['in_flight']:
    raise StopRun('unresolved_request_requires_manual_review_before_restart')
transport = Transport(ledger)
original_async_client = httpx.AsyncClient
clients = []


def asynchronous_client():
    client = original_async_client(transport=transport, trust_env=False, follow_redirects=False,
                                   timeout=httpx.Timeout(90, connect=15))
    clients.append(client)
    return client


def configure(runtime):
    transport.keys.update({k: runtime[n] for k, n in [('chat', 'CHAT_API_KEY'),
                          ('embedding', 'EMBEDDINGS_API_KEY'), ('reranker', 'RERANK_API_KEY')]})
    import langchain.embeddings
    native_embeddings = langchain.embeddings.init_embeddings

    def embeddings(**kwargs):
        sync = httpx.Client(transport=DenySync(), trust_env=False)
        clients.append(sync)
        return native_embeddings(**kwargs, max_retries=0, model_kwargs={'encoding_format': 'float'},
                                 http_client=sync, http_async_client=asynchronous_client())

    langchain.embeddings.init_embeddings = embeddings
    import openai
    native_openai = openai.AsyncOpenAI

    def bounded_openai(**kwargs):
        kwargs['max_retries'] = 0
        kwargs.setdefault('http_client', asynchronous_client())
        return native_openai(**kwargs)

    openai.AsyncOpenAI = bounded_openai
    from app.chat import deepseek
    native_chat = deepseek.DeepSeekChatOpenAI

    def chat(**kwargs):
        sync = httpx.Client(transport=DenySync(), trust_env=False)
        clients.append(sync)
        kwargs.update(max_retries=0, max_tokens=2048, stream_usage=True,
                      extra_body={'thinking': {'type': 'disabled'}},
                      http_client=sync, http_async_client=asynchronous_client())
        return native_chat(**kwargs)

    deepseek.DeepSeekChatOpenAI = chat
