"""No app startup, credentials, real database connection or external network."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'deployment/release/runtime'))
sys.path.insert(0, str(ROOT / 'frontend/gui'))
from daily_guard import DailyError, ENDPOINTS, Ledger, MODELS, SCOPE, Transport, policy
from daily_runtime import Boundary


@pytest.fixture
def limits():
    data = json.loads((ROOT / 'deployment/release/daily.example.json').read_text())
    data['ledger_id'] = '10000000-0000-0000-0000-000000000001'
    return data


@pytest.fixture
def ledger(tmp_path, limits):
    return Ledger(tmp_path/'daily.sqlite3', limits, SimpleNamespace(count=lambda b:(40,1064)), create=True)


@pytest.fixture(autouse=True)
def operation():
    token = SCOPE.set({'id':'fictional-operation','error':None})
    yield
    SCOPE.reset(token)


def body(kind):
    return {'chat':{'model':MODELS['chat'], 'stream':True, 'max_tokens':2048,
                    'thinking':{'type':'disabled'}, 'stream_options':{'include_usage':True},
                    'messages':[{'role':'system','content':'fictional system'}]},
            'embedding':{'model':MODELS['embedding'],'input':['虚构测试'],'encoding_format':'float'},
            'reranker':{'model':MODELS['reranker'],'query':'fixture','documents':['first','second'],'top_n':2}}[kind]


def request(kind='chat', payload=None, url=None):
    endpoint = next((host,path) for (host,path),k in ENDPOINTS.items() if k==kind)
    return httpx.Request('POST',url or 'https://'+''.join(endpoint),json=payload or body(kind))


def complete(ledger,index,kind):
    ledger.status_code(index,200)
    if kind=='chat':
        ledger.chat_complete(index,{'content':'fictional'}, {'prompt_tokens':100,'completion_tokens':20}, 'stop',2)
    else:
        value = {'model':MODELS['embedding'],'data':[{'index':0,'embedding':[.1]*1024}], 'usage':{'prompt_tokens':4}} if kind=='embedding' else {'results':[{'index':0,'relevance_score':.9},{'index':1,'relevance_score':.4}]}
        ledger.json_complete(index,value)


@pytest.mark.parametrize('kind',list(MODELS))
def test_original_endpoints_and_models_work_without_r2_fixed_resources(ledger,kind):
    index,actual=ledger.reserve(request(kind));complete(ledger,index,kind)
    assert actual==kind and ledger.status()['lifetime_requests']==1


@pytest.mark.parametrize('url',['http://api.deepseek.com/chat/completions','https://other.invalid/chat/completions',
    'https://api.deepseek.com/chat/completions?x=1','https://user:pass@api.deepseek.com/chat/completions',
    'https://api.deepseek.com:444/chat/completions','https://api.siliconflow.cn/v1/reranks'])
def test_endpoint_rejected_before_reservation(ledger,url):
    with pytest.raises(DailyError):ledger.reserve(request(url=url))
    assert ledger.status()['lifetime_requests']==0


@pytest.mark.parametrize('kind',list(MODELS))
def test_wrong_or_pro_model_never_sent(ledger,kind):
    b=body(kind);b['model']='Pro/'+b['model']
    with pytest.raises(DailyError):ledger.reserve(request(kind,b))
    assert ledger.status()['lifetime_requests']==0


def test_missing_http_operation_is_denied(ledger):
    SCOPE.set(None)
    with pytest.raises(DailyError):ledger.reserve(request())


def test_persistent_counts_and_no_automatic_recreation(ledger):
    i,k=ledger.reserve(request());complete(ledger,i,k)
    reopened=Ledger(ledger.path,ledger.limits,ledger.counter)
    assert reopened.status()['lifetime_requests']==1
    with pytest.raises(FileExistsError):Ledger(ledger.path,ledger.limits,None,create=True)
    with pytest.raises(ValueError):Ledger(ledger.path.with_name('missing.sqlite'),ledger.limits,None)


def test_restart_with_uncertain_http_pauses_and_resume_keeps_usage(ledger):
    ledger.reserve(request())
    reopened=Ledger(ledger.path,ledger.limits,ledger.counter)
    assert reopened.status()['paused']=='unresolved_request_after_restart'
    with pytest.raises(DailyError):reopened.available('chat')
    reopened.resume();SCOPE.set({'id':'new','error':None})
    assert reopened.status()['lifetime_requests']==1
    reopened.available('chat')


def test_ledger_uuid_cannot_be_adopted(ledger):
    other={**ledger.limits,'ledger_id':'20000000-0000-0000-0000-000000000002'}
    with pytest.raises(ValueError,match='identity'):Ledger(ledger.path,other,None)


def test_daily_bucket_preserves_prior_days(ledger):
    ledger.clock=lambda:'2026-10-27'
    i,k=ledger.reserve(request());complete(ledger,i,k)
    ledger.clock=lambda:'2026-10-28'
    assert ledger.status()['usage']==[] and ledger.status()['lifetime_requests']==1


def test_request_and_token_limit_stops_before_send(ledger):
    ledger.limits['requests_per_day']['chat']=1
    i,k=ledger.reserve(request());complete(ledger,i,k)
    with pytest.raises(DailyError,match='daily_limit_reached'):ledger.reserve(request())
    assert ledger.status()['lifetime_requests']==1


def test_output_budget_is_reserved_not_refunded(ledger):
    ledger.limits['chat_output_tokens_per_day']=2048
    i,k=ledger.reserve(request());complete(ledger,i,k)
    assert ledger.status()['usage'][0]['output_reserved']==2048
    with pytest.raises(DailyError):ledger.available('chat')


def test_parallel_reservation_cannot_overspend(ledger):
    ledger.limits['requests_per_day']['chat']=1
    def reserve(n):
        SCOPE.set({'id':str(n),'error':None})
        try:return ledger.reserve(request())
        except DailyError:return None
    with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(reserve,range(4)))
    assert sum(r is not None for r in results)==1
    assert ledger.status()['lifetime_requests']==1


def test_per_operation_loop_limit(ledger):
    ledger.limits['requests_per_operation']['chat']=1
    i,k=ledger.reserve(request());complete(ledger,i,k)
    with pytest.raises(DailyError,match='daily_input_limit'):ledger.reserve(request())


@pytest.mark.parametrize('override',[{'input':['x']*33},{'input':['x'*6001]}, {'dimensions':1024}, {'encoding_format':'base64'}])
def test_embedding_text_and_batch_limits(ledger,override):
    with pytest.raises(DailyError):ledger.reserve(request('embedding',{**body('embedding'),**override}))


def test_invalid_bge_response_pauses(ledger):
    i,_=ledger.reserve(request('embedding'))
    with pytest.raises(DailyError):ledger.json_complete(i,{'model':MODELS['embedding'],'data':[{'index':0,'embedding':[1]*768}]})
    assert ledger.status()['paused']=='invalid_bge_response'


@pytest.mark.parametrize('status',[401,402,403,429,500])
def test_http_failure_is_counted_once_without_sdk_retry(ledger,status):
    calls=[]
    async def send(inner,r):calls.append(r);return httpx.Response(status,json={'error':'fictional'})
    transport=Transport(ledger,dict.fromkeys(MODELS,'fictional-test-key'),send=send)
    async def run():
        with pytest.raises(DailyError):await transport.handle_async_request(request())
        await transport.aclose()
    asyncio.run(run())
    assert len(calls)==1 and ledger.status()['lifetime_requests']==1
    assert ledger.status()['paused']=='provider_http_'+str(status)


def test_stream_usage_is_persisted_without_content_or_reasoning(ledger):
    async def send(inner,r):
        frame={'choices':[{'delta':{'content':'fictional private answer','reasoning_content':'hidden fictional reasoning'},'finish_reason':'stop'}],
               'usage':{'prompt_tokens':100,'completion_tokens':20}}
        return httpx.Response(200,headers={'content-type':'text/event-stream'},content=('data: '+json.dumps(frame)+'\n\ndata: [DONE]\n\n').encode())
    transport=Transport(ledger,dict.fromkeys(MODELS,'fictional'),send)
    async def run():
        response=await transport.handle_async_request(request());await response.aread();await response.aclose();await transport.aclose()
    asyncio.run(run())
    with ledger.connect() as db:metadata=db.execute('SELECT metadata FROM requests').fetchone()[0]
    assert 'hidden fictional reasoning' not in metadata and 'fictional private answer' not in metadata
    assert json.loads(metadata)['usage']=={'input':100,'output':20}


async def boundary_call(ledger,app,path='/api/v1/chat/fictional'):
    sent=[]
    async def send(msg):sent.append(msg)
    await Boundary(app,ledger)({'type':'http','method':'POST','path':path},None,send)
    return sent


def test_boundary_quota_message_and_guest_auth(ledger):
    async def app(*args):raise AssertionError('must not enter app')
    result=asyncio.run(boundary_call(ledger,app,'/api/v1/chat/'))
    assert result[0]['status']==401
    ledger.limits['requests_per_day']['chat']=1
    i,k=ledger.reserve(request());complete(ledger,i,k)
    result=asyncio.run(boundary_call(ledger,app))
    assert result[0]['status']==429 and b'daily_limit_reached' in result[1]['body']


def test_stream_failure_has_safe_explicit_error_event(ledger):
    async def app(scope,receive,send):
        await send({'type':'http.response.start','status':200,'headers':[]})
        try:ledger.halt('fictional error')
        except DailyError:pass
        await send({'type':'http.response.body','body':b'raw upstream secrets'})
    result=asyncio.run(boundary_call(ledger,app))
    assert json.loads(result[-1]['body'])=={'type':'error','code':'daily_provider_error'}


def test_frontend_safe_runtime_error_is_not_a_model_answer():
    import conversation_events as events
    turn=events.new_turn();events.ingest(turn,{'type':'error','code':'daily_limit_reached','content':'untrusted payload'})
    events.finish(turn)
    assert turn['delivery']=='http_error' and turn['answer']==''
    assert 'untrusted payload' not in json.dumps(turn)


@pytest.mark.parametrize('override',[{'max_parallel_requests':0},{'chat_max_output_tokens':8193},{'requests_per_day':{'chat':1}}, {'unknown':1}])
def test_bad_policy_fails_closed(limits,override):
    with pytest.raises((ValueError,TypeError)):policy({**limits,**override})


def test_factory_preserves_original_call_signature_and_forces_zero_retries(monkeypatch,limits):
    import types
    import langchain.embeddings
    import openai
    import daily_runtime
    monkeypatch.syspath_prepend(str(ROOT/'backend'))
    original=Path.read_text
    monkeypatch.setattr(Path,'read_text',lambda p,*a,**kw:json.dumps(limits) if str(p)=='/run/secrets/daily_policy' else original(p,*a,**kw))
    monkeypatch.setattr(daily_runtime,'OfficialCounter',lambda p:None)
    monkeypatch.setattr(daily_runtime,'Ledger',lambda *a:SimpleNamespace())
    monkeypatch.setattr(langchain.embeddings,'init_embeddings',lambda **kw:kw)
    monkeypatch.setattr(openai,'AsyncOpenAI',lambda **kw:kw)
    fake=types.ModuleType('app.chat.deepseek')
    fake.DeepSeekChatOpenAI=lambda **kw:kw
    package=types.ModuleType('app');package.__path__=[str(ROOT/'backend/app')]
    chat_package=types.ModuleType('app.chat');chat_package.deepseek=fake
    monkeypatch.setitem(sys.modules,'app',package)
    monkeypatch.setitem(sys.modules,'app.chat',chat_package)
    monkeypatch.setitem(sys.modules,'app.chat.deepseek',fake)
    daily_runtime.configure({'CHAT_API_KEY':'fictional-chat','EMBEDDINGS_API_KEY':'fictional-embedding','RERANK_API_KEY':'fictional-reranker'})
    result=langchain.embeddings.init_embeddings(model='BAAI/bge-m3',chunk_size=32,max_retries=9)
    assert result['chunk_size']==limits['max_batch_texts'] and result['max_retries']==0
    assert result['check_embedding_ctx_length'] is False
    sdk=openai.AsyncOpenAI(max_retries=8)
    assert sdk['max_retries']==0
    chat=fake.DeepSeekChatOpenAI(model='deepseek-flash',max_retries=5)
    assert chat['max_retries']==0 and chat['max_tokens']==limits['chat_max_output_tokens']


def test_parallel_busy_is_not_daily_quota(ledger):
    ledger.limits['max_parallel_requests']=1
    ledger.reserve(request())
    SCOPE.set({'id':'another','error':None})
    with pytest.raises(DailyError,match='daily_busy'):ledger.reserve(request())
    assert not ledger.status()['paused']


def test_midnight_reservation_uses_one_calendar_bucket(ledger):
    days=iter(['2026-10-27','2026-10-28'])
    ledger.clock=lambda:next(days)
    ledger.reserve(request())
    with ledger.connect() as db:
        assert db.execute('SELECT day FROM requests').fetchone()[0]=='2026-10-27'
    assert next(days)=='2026-10-28'


def test_daily_host_initializer_passes_stdin_and_never_uses_r2(monkeypatch,tmp_path,limits):
    monkeypatch.syspath_prepend(str(ROOT/'deployment/release'))
    import daily_manage
    monkeypatch.setattr(daily_manage,'PRIVATE',tmp_path)
    (tmp_path/'daily_policy.json').write_text(json.dumps(limits))
    calls=[]
    def run(args,**kw):
        calls.append((args,kw))
        return SimpleNamespace(returncode=0,stdout='{}')
    monkeypatch.setattr(daily_manage.subprocess,'run',run)
    assert daily_manage.ledger('init')=={}
    args,kw=calls[0]
    assert '-i' in args and args[args.index('--network')+1]=='none'
    assert 'rag_release_daily_usage_v1' in ' '.join(args)
    assert 'r2' not in ' '.join(args)
    assert json.loads(kw['input'])==limits


def test_existing_daily_volume_never_reinitializes(monkeypatch,tmp_path):
    monkeypatch.syspath_prepend(str(ROOT/'deployment/release'))
    import daily_manage
    monkeypatch.setattr(daily_manage,'PRIVATE',tmp_path)
    (tmp_path/'daily_storage.json').write_text('{}')
    labels={'org.langgraph.daily':'usage-v1','com.docker.compose.project':'rag-release-r1'}
    monkeypatch.setattr(daily_manage.subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=0,stdout=json.dumps([{'Labels':labels}])))
    calls=[]
    monkeypatch.setattr(daily_manage,'ledger',lambda action='status':calls.append(action) or {'paused':None})
    daily_manage.storage()
    assert calls==['status']


def test_missing_initialized_volume_is_not_recreated(monkeypatch,tmp_path):
    monkeypatch.syspath_prepend(str(ROOT/'deployment/release'))
    import daily_manage
    monkeypatch.setattr(daily_manage,'PRIVATE',tmp_path)
    (tmp_path/'daily_storage.json').write_text('{}')
    monkeypatch.setattr(daily_manage.subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=1))
    with pytest.raises(RuntimeError,match='missing'):daily_manage.storage()
