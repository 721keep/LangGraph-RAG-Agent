"""Session-scoped resource snapshots and upload receipts, using existing endpoints."""
from datetime import datetime
import hashlib
from uuid import UUID

import api_utils
import streamlit as st


def state():
    user = st.session_state['user']
    owner = hashlib.sha256((user.access_token or '').encode()).hexdigest()
    current = st.session_state.get('resource_workspace')
    if current is None or current['owner'] != owner:
        current = {'owner': owner, 'loaded': False, 'kb_error': None, 'documents': {},
                   'doc_errors': {}, 'fetched': {}, 'receipts': {}, 'notice': None,
                   'active_kb': None, 'unavailable': set(), 'deletion_acknowledged': set()}
        st.session_state['resource_workspace'] = current
    return current


def load_knowledge_bases():
    current = state()
    result = api_utils.get_knowledge_bases(detailed=True)
    if result['ok'] and any(not isinstance(k, dict) or not isinstance(k.get('name'), str)
                            or not isinstance(k.get('id'), str) for k in result['data']):
        result = {'ok': False, 'message': '知识库列表格式异常，未更新本地状态。', 'status_code': None}
    current['loaded'] = True
    current['kb_error'] = None if result['ok'] else result
    if result['ok']:
        st.session_state['user'].knowledge_bases = result['data']
        ids = {str(k['id']) for k in result['data']}
        selected = st.session_state['thread'].knowledge_base_id
        if selected and str(selected) not in ids:
            clear_selection()
    return result['ok']


def clear_selection():
    thread = st.session_state['thread']
    thread.knowledge_base_id = None
    thread.documents = []
    st.session_state['ui_reset_kb'] = True


def load_documents(kb_id):
    current = state()
    key = str(kb_id)
    result = api_utils.list_knowledge_base_documents(kb_id, detailed=True)
    if result['ok'] and any(not isinstance(d, dict) or not d.get('id') or not isinstance(d.get('file_name'), str)
                            or str(d.get('knowledge_base_id', key)) != key for d in result['data']):
        result = {'ok': False, 'message': '文档归属或列表格式异常，未更新本地状态。', 'status_code': None}
    current['doc_errors'][key] = None if result['ok'] else result
    if not result['ok']:
        return False
    current['documents'][key] = result['data']
    current['fetched'][key] = datetime.now().strftime('%H:%M:%S')
    if str(st.session_state['thread'].knowledge_base_id) == key:
        st.session_state['thread'].documents = result['data']
    rows = {str(d['id']): d for d in result['data']}
    for receipt_key, receipt in list(current['receipts'].items()):
        ident = receipt.get('document_id')
        if receipt['kb_id'] == key and ident in current.get('deletion_acknowledged', set()) and ident not in rows:
            del current['receipts'][receipt_key]  # A confirmed deletion permits a new explicit upload.
            continue
        if receipt['kb_id'] == key and receipt.get('document_id') in rows:
            status = rows[receipt['document_id']].get('status')
            if status in ('ready', 'failed'):
                receipt['status'] = status
                receipt['message'] = ('服务端已确认文档就绪。' if status == 'ready'
                                      else api_utils.document_failure_message(rows[receipt['document_id']]))
    return True


def ensure_loaded():
    current = state()
    if not current['loaded']:
        load_knowledge_bases()
    kb = st.session_state['thread'].knowledge_base_id
    if kb and str(kb) not in current['unavailable']:
        key = str(kb)
        if current['active_kb'] != key or (key not in current['documents'] and key not in current['doc_errors']):
            load_documents(kb)
        current['active_kb'] = key
    return current


def select_knowledge_base(kb_id):
    key = str(kb_id)
    current = state()
    if key in current['unavailable']:
        return {'ok': False, 'message': '此知识库的删除已获服务器确认，请刷新列表。'}
    thread = st.session_state['thread']
    if thread.id is not None:
        result = api_utils.set_thread_knowledge_base(thread.id, UUID(key), detailed=True)
        if not result['ok']:
            return result
    thread.knowledge_base_id = UUID(key)
    thread.documents = []
    st.session_state['ui_reset_kb'] = True
    load_documents(kb_id)
    current['active_kb'] = key
    return {'ok': True}


def set_notice(kind, message, scope='knowledge'):
    state()['notice'] = (kind, message, scope)


def delete_knowledge_base(kb_id):
    result = api_utils.delete_knowledge_base(UUID(str(kb_id)), detailed=True)
    if not result['ok']:
        return result
    current = state()
    key = str(kb_id)
    current['unavailable'].add(key)
    if str(st.session_state['thread'].knowledge_base_id) == key:
        clear_selection()
    if load_knowledge_bases():
        if any(str(k['id']) == key for k in st.session_state['user'].knowledge_bases):
            set_notice('warning', '删除已获服务器确认，但列表仍包含该知识库，请刷新核验。')
        else:
            set_notice('success', 'Knowledge base deleted. 知识库及文档列表已刷新。')
    else:
        set_notice('warning', '删除已获服务器确认，但列表刷新失败。当前为上次快照，请手动刷新。')
    return result


def delete_document(kb_id, document_id):
    result = api_utils.delete_document(str(document_id), detailed=True)
    if not result['ok']:
        return result
    state().setdefault('deletion_acknowledged', set()).add(str(document_id))
    key = str(kb_id)
    if not load_documents(kb_id):
        set_notice('warning', '文档删除已获服务器确认，但列表刷新失败；尚未确认最新列表，请刷新核验。', 'documents:' + key)
    elif any(str(d['id']) == str(document_id) for d in state()['documents'][key]):
        set_notice('warning', '删除响应已收到，但服务器列表仍包含此文档，请刷新核验。', 'documents:' + key)
    else:
        set_notice('success', 'Document deleted successfully. 列表已按服务端结果更新。', 'documents:' + key)
    return result


def file_key(kb_id, file):
    digest = hashlib.sha256(file.getvalue()).hexdigest()
    return hashlib.sha256((str(kb_id) + '\0' + file.name + '\0' + digest).encode()).hexdigest()


def upload(kb_id, file, *, retry_failed=False):
    current = state()
    key = file_key(kb_id, file)
    previous = current['receipts'].get(key)
    if previous and not (previous['status'] == 'failed' and retry_failed):
        return previous
    receipt = {'kb_id': str(kb_id), 'file_name': file.name, 'status': 'submitting',
               'attempt': (previous or {}).get('attempt', 0) + 1,
               'document_id': None, 'message': '请求已提交，结果待确认。请勿重复上传。'}
    current['receipts'][key] = receipt  # Persist before sending, including interrupted reruns.
    result = api_utils.upload_knowledge_base_document(kb_id, file)
    receipt.update(status=result.get('status', 'unknown'), document_id=result.get('document_id'),
                   message=result.get('message', '上传结果待确认，请刷新列表。'))
    load_documents(kb_id)  # Refresh after every outcome, never resend here.
    return receipt
