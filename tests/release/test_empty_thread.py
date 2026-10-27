"""Regression for the real owned-Thread/no-checkpoint 404 observed during R2."""
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock
import pytest
import requests

ROOT=Path(__file__).resolve().parents[2]
TARGET='00000000-0000-0000-0000-000000000002'


@pytest.fixture
def front(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT/'frontend/gui'))
    st=ModuleType('streamlit')
    old=SimpleNamespace(id='00000000-0000-0000-0000-000000000001',messages=[{'role':'ai','content':'saved earlier answer'}])
    st.session_state={'thread':old,'user':SimpleNamespace(access_token='offline-fake-token')}
    monkeypatch.setitem(sys.modules,'streamlit',st)
    api=ModuleType('api_utils');api.BASE_URL='http://unused.invalid/api/v1';api.TIMEOUT=1
    api.get_thread=Mock(return_value={'ok':True,'data':{'id':TARGET,'title':'Empty','user_id':'owned-user','knowledge_base_id':None}})
    api.get_chat_history=Mock(return_value={'ok':False,'status_code':404,'error_code':'chat_history_absent','message':'not found'})
    monkeypatch.setitem(sys.modules,'api_utils',api)
    state=ModuleType('state_management');state.Thread=lambda **kw:SimpleNamespace(**kw)
    state.update_knowledge_base_document_list=Mock()
    monkeypatch.setitem(sys.modules,'state_management',state)
    spec=importlib.util.spec_from_file_location('empty_thread_session',ROOT/'frontend/gui/conversation_session.py')
    session=importlib.util.module_from_spec(spec);spec.loader.exec_module(session)
    return st,api,session,old


def test_owned_empty_thread_is_selectable(front):
    st,api,session,old=front
    assert session.load_thread(TARGET) is True
    assert st.session_state['thread'].id==TARGET and st.session_state['thread'].messages==[]
    assert old.messages==[{'role':'ai','content':'saved earlier answer'}]


@pytest.mark.parametrize('history',[
 {'ok':False,'status_code':404,'message':'generic not found'},
 {'ok':False,'status_code':403,'message':'forbidden'},
 {'ok':False,'status_code':None,'message':'network failure'}])
def test_unknown_failures_keep_current_conversation(front,history):
    st,api,session,old=front;api.get_chat_history.return_value=history
    assert session.load_thread(TARGET) is False
    assert st.session_state['thread'] is old


def test_missing_history_never_clears_known_current_messages(front):
    st,api,session,old=front;old.id=TARGET
    assert session.load_thread(TARGET) is False and st.session_state['thread'] is old


def test_pending_request_must_be_reconciled_before_empty_switch(front):
    st,api,session,old=front
    session.store()['receipts']['pending']={'thread_id':TARGET,'needs_reconcile':True}
    assert session.load_thread(TARGET) is False and st.session_state['thread'] is old
    assert session.store()['receipts']['pending']['snapshot_read'] is True


def test_unowned_thread_never_requests_or_installs_history(front):
    st,api,session,old=front;api.get_thread.return_value={'ok':False,'status_code':403,'message':'forbidden'}
    assert session.load_thread(TARGET) is False and st.session_state['thread'] is old
    api.get_chat_history.assert_not_called()


@pytest.mark.parametrize('path,status,detail,classified',[
 ('/chat/id',404,'Chat history not found',True),('/chat/id',404,'Not Found',False),
 ('/chat/id',403,'Chat history not found',False),('/documents/id',404,'Chat history not found',False)])
def test_only_precise_existing_backend_response_is_classified(front,monkeypatch,path,status,detail,classified):
    spec=importlib.util.spec_from_file_location('empty_thread_api',ROOT/'frontend/gui/resource_api.py')
    resource=importlib.util.module_from_spec(spec);spec.loader.exec_module(resource)
    response=SimpleNamespace(status_code=status,json=lambda:{'detail':detail})
    def fail():raise requests.HTTPError(response=response)
    response.raise_for_status=fail
    monkeypatch.setattr(requests,'get',lambda *a,**kw:response)
    result=resource.request('GET',path)
    assert (result.get('error_code')=='chat_history_absent') is classified
