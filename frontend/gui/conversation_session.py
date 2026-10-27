"""Local request receipts and exact-history presentation cache; no model clients."""
import copy
import hashlib
from uuid import uuid4

import streamlit as st
from conversation_events import history_key, new_turn


def store():
    user = st.session_state['user']
    owner = hashlib.sha256((getattr(user, 'access_token', None) or 'guest').encode()).hexdigest()
    value = st.session_state.get('conversation_workspace')
    if value is None or value['owner'] != owner:
        value = {'owner': owner, 'receipts': {}, 'presentations': {}}
        st.session_state['conversation_workspace'] = value
    return value


def thread_key(thread):
    return str(thread.id) if thread.id is not None else 'draft:' + str(id(thread))


def pending(thread):
    return next((r for r in reversed(list(store()['receipts'].values()))
                 if r['thread_key'] == thread_key(thread) and r['needs_reconcile']), None)


def begin(thread, prompt):
    if pending(thread):
        return None
    record = {'id': uuid4().hex, 'thread_key': thread_key(thread), 'thread_id': str(thread.id) if thread.id else None,
              'before': copy.deepcopy(thread.messages), 'prompt': prompt, 'turn': new_turn(),
              'needs_reconcile': True, 'snapshot_read': False, 'request_sent': False}
    store()['receipts'][record['id']] = record  # Before any create/bind/chat HTTP request.
    return record


def bind_record(record, thread):
    record['thread_key'] = thread_key(thread)
    record['thread_id'] = str(thread.id) if thread.id is not None else None


def save_presentation(thread, turn):
    key = thread_key(thread) + ':' + history_key(thread.messages)
    store()['presentations'][key] = copy.deepcopy(turn)


def presentation(thread, prefix):
    return store()['presentations'].get(thread_key(thread) + ':' + history_key(prefix))


def reconcile(thread):
    for receipt in store()['receipts'].values():
        if receipt['thread_key'] != thread_key(thread) or not receipt['needs_reconcile']:
            continue
        receipt['snapshot_read'] = True
        index = len(receipt['before'])
        rows = thread.messages
        if (len(rows) >= index + 2 and history_key(rows[:index]) == history_key(receipt['before'])
                and rows[index].get('content') == receipt['prompt']
                and rows[index + 1].get('role') in ('ai', 'assistant')):
            receipt['needs_reconcile'] = False
            receipt['turn']['delivery'] = 'history_loaded'


def load_thread(thread_id):
    """Use existing endpoints; replace UI state only after both reads succeed."""
    import api_utils
    from state_management import Thread, update_knowledge_base_document_list
    from uuid import UUID
    metadata = api_utils.get_thread(thread_id, detailed=True)
    history = api_utils.get_chat_history(thread_id, detailed=True) if metadata['ok'] else None
    if metadata['ok'] and history.get('error_code') == 'chat_history_absent':
        current = st.session_state['thread']
        cache = store()
        known = (str(current.id) == str(thread_id) and bool(current.messages)) or any(
            key.startswith(str(thread_id) + ':') for key in cache['presentations'])
        uncertain = any(r['thread_id'] == str(thread_id) and r['needs_reconcile']
                        for r in cache['receipts'].values())
        if not known and not uncertain:
            history = {'ok': True, 'data': []}
    error = metadata if not metadata['ok'] else history if not history['ok'] else None
    if error:
        if metadata['ok'] and error.get('status_code') == 404:
            for receipt in store()['receipts'].values():
                if receipt['thread_id'] == str(thread_id):
                    receipt['snapshot_read'] = True
        st.session_state['chat_history_error'] = error['message']
        return False
    row = metadata['data']
    messages = history['data']
    if any(not isinstance(m, dict) or not isinstance(m.get('content'), str)
           or m.get('role') not in ('human', 'user', 'ai', 'assistant') for m in messages):
        st.session_state['chat_history_error'] = '历史消息格式异常，保留当前会话。'
        return False
    kb = row.get('knowledge_base_id')
    try:
        kb_uuid = UUID(kb) if kb else None
    except (ValueError, TypeError, AttributeError):
        kb_uuid = None
        st.session_state['chat_history_error'] = '会话知识库标识格式异常，保留当前会话。'
        return False
    if str(row['id']) != str(thread_id):
        st.session_state['chat_history_error'] = '会话标识不匹配，保留当前会话。'
        return False
    thread = Thread(id=thread_id, title=row.get('title') or '', user_id=row.get('user_id'),
                    knowledge_base_id=kb_uuid, messages=messages)
    st.session_state['thread'] = thread
    st.session_state['ui_reset_kb'] = True
    st.session_state.pop('chat_history_error', None)
    if kb:
        update_knowledge_base_document_list(thread.knowledge_base_id)
    reconcile(thread)
    return True
