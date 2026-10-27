"""Original frontend Chat API flow, with local receipts and M3 rendering."""
import asyncio
import json

import httpx
import streamlit as st
import api_utils
import ui
import conversation_events as events
import conversation_session as session
import conversation_view as view
from state_management import update_thread, update_user_threads


def _error_kind(error, partial):
    current = error
    for _ in range(6):
        if isinstance(current, httpx.HTTPStatusError):
            return 'http_error'
        if isinstance(current, httpx.RequestError):
            return 'interrupted' if partial else 'network_error'
        current = getattr(current, '__cause__', None)
        if current is None:
            break
    return 'interrupted' if partial else 'network_error'


def _recovery(thread, record):
    if record is None:
        return
    st.info('Conversation retained / 会话已保留。先核对已保存历史，不会自动重新发送。')
    if record.get('snapshot_read'):
        st.caption('历史已读取，但无法确认旧请求已在服务端结束；解锁不会重发，只允许下一次手动输入。')
        checked = st.checkbox('已核对历史，允许输入新消息', key='chat_unlock_' + record['id'])
        if ui.button('Allow a new message', key='chat_unlock_button_' + record['id'], name='message-square', disabled=not checked):
            record['needs_reconcile'] = False
            st.rerun()


def _refresh_view(record):
    # Render the saved result / pending receipt and refresh the sidebar once.
    # The one-shot guard also consumes a stale ChatInput trigger if a rerun replays it.
    session.store()['consumed_input'] = {'thread_key': record['thread_key'], 'prompt': record['prompt']}
    st.rerun()


def chat_interface(authenticated=True):
    view.styles()
    thread = st.session_state['thread']
    with st.container(key='conversation_actions'):
        left, right = st.columns([3, 1])
        with left:
            st.caption('CONVERSATION / Sources and tool results come from returned events')
        with right:
            if authenticated and thread.id is not None:
                ui.button('Refresh saved conversation', key='chat_refresh_history', name='refresh-cw', use_container_width=True,
                          on_click=session.load_thread, args=(thread.id,))
        if message := st.session_state.get('chat_history_error'):
            st.error(message + ' 当前会话内容未清空。')

    for index, message in enumerate(thread.messages):
        if message['role'] in ('ai', 'assistant'):
            turn = session.presentation(thread, thread.messages[:index + 1])
            view.answer(message['content'], str(index), turn, saved=True)
        else:
            with ui.chat_message(message['role'], str(index)):
                view.label('human')
                st.markdown(message['content'])
    pending = session.pending(thread)
    if pending:
        index = len(pending['before'])
        if len(thread.messages) <= index or thread.messages[index].get('content') != pending['prompt']:
            with ui.chat_message('human', 'pending_' + pending['id']):
                view.label('human', '本地待核验请求')
                st.markdown(pending['prompt'])
        view.answer(pending['turn']['answer'], 'pending_' + pending['id'], pending['turn'])
        _recovery(thread, pending)
    prompt = st.chat_input('Ask your agent / 向 Agent 提问…', key='prompt', disabled=pending is not None)
    consumed = session.store().pop('consumed_input', None)
    if prompt and consumed and consumed['thread_key'] == session.thread_key(thread) and consumed['prompt'] == prompt:
        return
    if not prompt:
        return
    record = session.begin(thread, prompt)
    if record is None:
        return
    record['owner'] = session.store()['owner']
    turn = record['turn']
    try:
        first = thread.id is None
        if first and authenticated:
            selected_kb = thread.knowledge_base_id
            created = api_utils.create_new_thread()
            if not created.get('id'):
                events.finish(turn, 'setup_error')
                view.delivery(turn)
                st.info('未收到 Thread ID，没有发送 Chat 请求；未删除任何远端记录。')
                _refresh_view(record)
            thread.id = created['id']
            session.bind_record(record, thread)
            if selected_kb is not None:
                bound = api_utils.set_thread_knowledge_base(thread.id, selected_kb, detailed=True)
                if not bound['ok']:
                    events.finish(turn, 'setup_error')
                    view.delivery(turn)
                    st.error(bound['message'])
                    st.info('Thread 已保留，请刷新已保存会话后再决定下一步。')
                    update_user_threads()
                    _refresh_view(record)
        thread.messages.append({'role': 'human', 'content': prompt})
        if first and authenticated:
            update_thread(thread.id, prompt[:30])
            update_user_threads()
        with ui.chat_message('human', 'live'):
            view.label('human')
            st.markdown(prompt)
        with ui.chat_message('ai', 'live'):
            view.label('ai', '正在接收响应…')
            status = st.empty()
            tool_area = st.empty()
            answer_area = st.empty()
            source_area = st.empty()
            status.caption('Waiting for Agent / 等待 Agent 返回事件')

            async def receive():
                data = {'prompt': prompt, 'model_name': st.session_state['model_name']}
                if authenticated:
                    data.update(top_k=st.session_state.get('top_k', 3), similarity_threshold=st.session_state.get('similarity_threshold', .50),
                                rerank_top_n=st.session_state.get('rerank_top_n', 3))
                record['request_sent'] = True
                stream = api_utils.chat_stream(data, thread.id) if authenticated else api_utils.simple_chat_stream(data)
                try:
                    async for line in stream:
                        try:
                            event = json.loads(line)
                        except (ValueError, TypeError):
                            turn['malformed_events'] += 1
                            continue
                        changed = events.ingest(turn, event)
                        if not changed:
                            continue
                        if event.get('type') == 'llm_chunk':
                            status.caption('Receiving response / 正在接收回答')
                            answer_area.markdown(turn['answer'] + '▌')
                        else:
                            status.caption('Tool events received / 工具执行记录已更新')
                            with tool_area.container():
                                view.tools(turn)
                            with source_area.container():
                                view.evidence(turn)
                    events.finish(turn)
                    if turn['answer']:
                        thread.messages.append({'role': 'ai', 'content': turn['answer']})
                        if session.store()['owner'] == record['owner']:
                            session.save_presentation(thread, turn)
                        record['needs_reconcile'] = False
                except Exception as error:
                    events.finish(turn, _error_kind(error, bool(turn['answer'])))
                finally:
                    if turn['delivery'] in ('waiting', 'streaming'):
                        events.finish(turn, 'interrupted')
            asyncio.run(receive())
            status.empty()
            answer_area.markdown(turn['answer'])
            with tool_area.container():
                view.tools(turn)
            with source_area.container():
                view.delivery(turn)
                view.evidence(turn)
            if turn['answer']:
                from conversation_copy import copy_answer
                copy_answer(turn['answer'], st.session_state.get('ui_theme', 'light'))
            if record['needs_reconcile']:
                _recovery(thread, record)
    finally:
        if turn['delivery'] in ('waiting', 'streaming'):
            events.finish(turn, 'interrupted')
    _refresh_view(record)
