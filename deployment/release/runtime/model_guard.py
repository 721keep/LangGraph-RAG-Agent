"""R2-only persistent HTTP budgets and incremental, fail-closed SSE accounting."""
import codecs
from contextvars import ContextVar
REQUEST_CONTEXT = ContextVar("release_r2_request", default=("blocked", "none"))
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys

from budget_core import Ledger as LockedLedger, OfficialCounter, StopRun, digest, signature
import httpx

ORIGINAL_SEND = httpx.AsyncHTTPTransport.handle_async_request
LIMITS = {'chat': 8, 'embedding': 6, 'reranker': 3}
MODELS = {'chat': 'deepseek-flash', 'embedding': 'BAAI/bge-m3', 'reranker': 'BAAI/bge-reranker-v2-m3'}


class Ledger(LockedLedger):
    def __init__(self, path, counter, create=False):
        path = Path(path)
        if create:
            with path.open('x') as file:
                json.dump({'mode': 'blocked', 'halted': False, 'requests': 0, 'counts': dict.fromkeys(LIMITS, 0),
                           'in_flight': False, 'chat_input_reserved': 0, 'chat_output_reserved': 0,
                           'chat_input_actual': 0, 'chat_output_actual': 0, 'events': []}, file)
                file.flush()
                import os
                os.fsync(file.fileno())
        super().__init__(path, counter)

    def halt(self, reason):
        self.update(lambda s: s.update(mode='halted', halted=True, stop_reason=reason, in_flight=False))
        raise StopRun(reason)

    def seal(self):
        self.update(lambda s: s.update(mode='sealed'))

    def reserve(self, request, scenario, thread_id):
        body = json.loads(request.content)
        if scenario not in ('upload', 'positive', 'followup'):
            self.halt('unscoped_model_request')
        def change(s):
            def reject(reason):
                s.update(mode='halted', halted=True, stop_reason=reason)
                raise StopRun(reason)
            if s['mode'] != 'armed' or s['halted'] or s['in_flight']:
                reject('not_armed_or_unresolved_request')
            url = request.url
            endpoints = {('api.deepseek.com', '/chat/completions'): 'chat',
                         ('api.siliconflow.cn', '/v1/embeddings'): 'embedding',
                         ('api.siliconflow.cn', '/v1/rerank'): 'reranker'}
            kind = endpoints.get((url.host, url.path))
            if (request.method != 'POST' or url.scheme != 'https' or url.port not in (None, 443)
                    or url.query or url.userinfo or kind is None):
                reject('unauthorized_endpoint')
            if body.get('model') != MODELS[kind]:
                reject('unauthorized_model')
            if s['counts'][kind] >= LIMITS[kind]:
                reject(kind + '_request_limit')
            event = {'number': s['requests']+1, 'kind': kind, 'model': MODELS[kind], 'scenario': scenario,
                     'thread_id': thread_id, 'started_utc': datetime.now(timezone.utc).isoformat(), 'http_status': None}
            if kind == 'chat':
                if (body.get('stream') is not True or body.get('max_tokens') != 2048
                        or 'max_completion_tokens' in body or body.get('n', 1) != 1
                        or body.get('stream_options',{}).get('include_usage') is not True
                        or body.get('thinking', {}).get('type') != 'disabled'):
                    reject('chat_limits_or_thinking_invalid')
                tools = [t['function']['name'] for t in body.get('tools') or []]
                if set(tools) != {'retrieve_user_documents','web_search','get_server_status'}:
                    reject('original_registry_missing')
                previous = [e for e in s['events'] if e['kind']=='chat' and e['thread_id']==thread_id and e.get('assistant')]
                assistants = [m for m in body['messages'] if m['role']=='assistant']
                if len(previous) != len(assistants):
                    reject('assistant_history_count_mismatch')
                for message, old in zip(assistants, previous):
                    info = old['assistant']; reason = message.get('reasoning_content')
                    if signature(message) != info['signature']:
                        reject('assistant_history_changed')
                    if info['reasoning_present'] and (not isinstance(reason,str) or digest(reason) != info['reasoning_sha256']):
                        reject('reasoning_replay_mismatch')
                pending=set()
                for message in body['messages']:
                    if message['role']=='assistant':
                        if pending:reject('tool_results_missing')
                        pending.update(c['id'] for c in message.get('tool_calls') or [])
                    elif message['role']=='tool':
                        if message['tool_call_id'] not in pending:reject('tool_call_id_mismatch')
                        pending.remove(message['tool_call_id'])
                if pending:reject('unresolved_tool_calls')
                local, upper = self.counter.count(body)
                if upper>8192 or s['chat_input_reserved']+upper>65536 or s['chat_output_reserved']+2048>16384:
                    reject('chat_token_limit')
                event.update(input_local_tokens=local,input_reserved=upper,output_limit=2048,
                             tools=tools,assistant_history_count=len(assistants),reasoning_replay_verified=True)
                s['chat_input_reserved']+=upper;s['chat_output_reserved']+=2048
            elif kind == 'embedding':
                texts=body.get('input');texts=[texts] if isinstance(texts,str) else texts
                if (not isinstance(texts,list) or not 1<=len(texts)<=2 or not all(isinstance(t,str) and 0<len(t)<=1600 for t in texts)
                        or 'dimensions' in body or body.get('encoding_format')!='float'):
                    reject('embedding_input_outside_query_scope')
                event.update(input_count=len(texts),input_lengths=[len(t) for t in texts])
            else:
                texts=body.get('documents')
                if (not isinstance(texts,list) or not 1<=len(texts)<=3
                        or not all(isinstance(t,str) and 0<len(t)<=1600 for t in texts)
                        or not isinstance(body.get('query'),str) or not 0<len(body['query'])<=300
                        or not 1<=body.get('top_n',0)<=len(texts)):
                    reject('reranker_input_outside_test_scope')
                event.update(input_count=len(texts),input_lengths=[len(t) for t in texts])
            s['events'].append(event);s['counts'][kind]+=1;s['requests']+=1;s['in_flight']=True
            return len(s['events'])-1,kind
        return self.update(change)

    def status(self,index,status):
        self.update(lambda s:s['events'][index].update(http_status=status))
        if status!=200:self.halt('provider_http_'+str(status))

    def chat_complete(self,index,message,usage,finish,frames):
        def change(s):
            event=s['events'][index]
            def reject(reason):
                s.update(mode='halted',halted=True,stop_reason=reason,in_flight=False);event['stop_reason']=reason
                raise StopRun(reason)
            inp=usage.get('prompt_tokens');out=usage.get('completion_tokens')
            if type(inp) is not int or type(out) is not int or inp<0 or out<0:reject('stream_usage_missing')
            event['usage']={'input':inp,'output':out,'reasoning':(usage.get('completion_tokens_details') or {}).get('reasoning_tokens'),
                            'cache_hit':usage.get('prompt_cache_hit_tokens'),'cache_miss':usage.get('prompt_cache_miss_tokens')}
            s['chat_input_actual']+=inp;s['chat_output_actual']+=out
            if inp>event['input_reserved'] or inp>8192 or out>2048 or s['chat_input_actual']>65536 or s['chat_output_actual']>16384:
                reject('provider_usage_exceeds_limit')
            reason=message.get('reasoning_content');calls=message.get('tool_calls') or []
            event.update(finish_reason=finish,sse_done=True,sse_frames=frames,
                         assistant={'signature':signature(message),'reasoning_present':isinstance(reason,str),
                                    'reasoning_characters':len(reason) if isinstance(reason,str) else 0,
                                    'reasoning_sha256':digest(reason) if isinstance(reason,str) else None,
                                    'tool_names':[c['function']['name'] for c in calls],
                                    'tool_call_ids':[c['id'] for c in calls]})
            # Thinking is explicitly disabled for the R2 cost-controlled baseline.
            if any(c['function']['name']!='retrieve_user_documents' for c in calls):reject('non_rag_tool_selected')
            if finish not in ('stop','tool_calls') or bool(calls)!=(finish=='tool_calls'):reject('stream_finish_invalid')
            s['in_flight']=False
        self.update(change)

    def json_complete(self,index,body):
        def change(s):
            event=s['events'][index]
            try:
                if event['kind']=='embedding':
                    data=body['data'];assert len(data)==event['input_count'] and body.get('model')==MODELS['embedding']
                    assert sorted(row['index'] for row in data)==list(range(event['input_count']))
                    for row in data:
                        vector=row['embedding'];assert isinstance(vector,list) and len(vector)==1024
                        assert all(type(v) in (int,float) and math.isfinite(v) for v in vector) and any(vector)
                    event['dimensions']=[1024]*len(data)
                else:
                    rows=body['results'];indices=[r['index'] for r in rows];scores=[r['relevance_score'] for r in rows]
                    assert rows and len(rows)<=event['input_count'] and len(set(indices))==len(indices)
                    assert all(type(i) is int and 0<=i<event['input_count'] for i in indices)
                    assert all(type(v) in (int,float) and math.isfinite(v) for v in scores) and scores==sorted(scores,reverse=True)
                    event['results']=[{'index':i,'relevance_score':v} for i,v in zip(indices,scores)]
                def numeric(value):
                    if isinstance(value,dict):return {k:numeric(v) for k,v in value.items() if isinstance(v,(int,float,dict))}
                    return value
                event['usage_metadata']=numeric(body.get('usage') or body.get('meta') or {})
            except Exception:
                s.update(mode='halted',halted=True,stop_reason='invalid_bge_response',in_flight=False)
                raise StopRun('invalid_bge_response') from None
            s['in_flight']=False
        self.update(change)


class SSEInspector:
    def __init__(self,ledger,index):
        self.ledger=ledger;self.index=index;self.decoder=codecs.getincrementaldecoder('utf-8')()
        self.buffer='';self.lines=[];self.message={'role':'assistant','content':''};self.calls={}
        self.usage={};self.finish=None;self.done=False;self.frames=0;self.reasoning_seen=False

    def feed(self,chunk):
        self.buffer+=self.decoder.decode(chunk)
        while '\n' in self.buffer:
            line,self.buffer=self.buffer.split('\n',1);line=line.rstrip('\r')
            if line.startswith('data:'):self.lines.append(line[5:].lstrip())
            elif not line and self.lines:
                data='\n'.join(self.lines);self.lines=[];self.frame(data)

    def frame(self,data):
        if data=='[DONE]':
            self.message['tool_calls']=[self.calls[k] for k in sorted(self.calls)]
            if not self.message['tool_calls']:self.message.pop('tool_calls')
            self.ledger.chat_complete(self.index,self.message,self.usage,self.finish,self.frames)
            self.done=True;return
        frame=json.loads(data);self.frames+=1
        if frame.get('error'):self.ledger.halt('provider_stream_error')
        if frame.get('usage'):self.usage=frame['usage']
        for choice in frame.get('choices') or []:
            if choice.get('finish_reason'):self.finish=choice['finish_reason']
            delta=choice.get('delta') or {}
            if delta.get('content'):self.message['content']+=delta['content']
            if isinstance(delta.get('reasoning_content'),str):
                self.reasoning_seen=True
                self.message['reasoning_content']=self.message.get('reasoning_content','')+delta['reasoning_content']
            for part in delta.get('tool_calls') or []:
                item=self.calls.setdefault(part['index'],{'id':'','type':'function','function':{'name':'','arguments':''}})
                if part.get('id'):item['id']+=part['id']
                function=part.get('function') or {}
                item['function']['name']+=function.get('name') or ''
                item['function']['arguments']+=function.get('arguments') or ''

    def close(self):
        if not self.done:self.ledger.halt('sse_interrupted_or_done_missing')


class Stream(httpx.AsyncByteStream):
    def __init__(self,upstream,inspector,transport):
        self.upstream=upstream;self.inspector=inspector;self.transport=transport

    async def __aiter__(self):
        try:
            async for chunk in self.upstream.aiter_bytes():
                self.inspector.feed(chunk)
                yield chunk
        except BaseException:
            if not self.inspector.done and not self.ledger_state_halted():self.inspector.ledger.halt('stream_transport_or_parse_failure')
            raise
        finally:
            if self.inspector.done:
                reason=self.inspector.message.get('reasoning_content')
                if reason:self.transport.private_reasoning.append(reason)

    def ledger_state_halted(self):
        return self.inspector.ledger.read()['halted']

    async def aclose(self):
        await self.upstream.aclose()
        self.inspector.close()
        reason=self.inspector.message.get('reasoning_content')
        if reason and reason not in self.transport.private_reasoning:self.transport.private_reasoning.append(reason)


class Transport(httpx.AsyncBaseTransport):
    def __init__(self,ledger):
        self.ledger=ledger;self.inner=httpx.AsyncHTTPTransport(retries=0,trust_env=False)
        self.keys={};self.private_reasoning=[]

    async def handle_async_request(self,request):
        scenario,thread_id=REQUEST_CONTEXT.get()
        index,kind=self.ledger.reserve(request,scenario,thread_id)
        try:
            key=self.keys.get(kind)
            if not key:self.ledger.halt('model_key_not_armed')
            request.headers['Authorization']='Bearer '+key
            response=await ORIGINAL_SEND(self.inner,request)
            if response.status_code!=200:
                await response.aread()
                try:text=str((response.json().get('error') or {})).lower()
                except Exception:text=''
                category=('authentication' if response.status_code==401 else
                          'quota_billing_or_free_eligibility' if response.status_code==402 or any(w in text for w in ['quota','balance','billing','free','allocation']) else
                          'rate_limit' if response.status_code==429 else 'provider_error')
                self.ledger.update(lambda s:s['events'][index].update(error_category=category))
            self.ledger.status(index,response.status_code)
            if kind=='chat':
                if 'text/event-stream' not in response.headers.get('content-type',''):
                    self.ledger.halt('chat_response_not_sse')
                headers=dict(response.headers)
                headers.pop('content-encoding',None);headers.pop('content-length',None)
                return httpx.Response(response.status_code,headers=headers,
                                      stream=Stream(response,SSEInspector(self.ledger,index),self),extensions=response.extensions)
            await response.aread();self.ledger.json_complete(index,response.json());return response
        except BaseException:
            if not self.ledger.read()['halted']:self.ledger.halt('http_transport_failure')
            raise

    async def aclose(self):await self.inner.aclose()


class DenySync(httpx.BaseTransport):
    def handle_request(self,request):raise StopRun('sync_model_http_forbidden')
