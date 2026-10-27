"""Knowledge and document management, built on M1 components and existing APIs."""
import html
from pathlib import Path

import api_utils
import resource_state as resources
import streamlit as st
import ui


def _styles():
    upload_icon = '.st-key-m2_upload_panel [data-testid="stFileUploaderDropzoneInstructions"] > span:first-child'
    extra = f'{upload_icon}{{background:var(--color-accent);mask:url("{ui.data_uri("upload")}") center/contain no-repeat;width:28px;height:28px;}}{upload_icon} svg{{visibility:hidden;}}'
    st.markdown('<style>' + (Path(__file__).parent / 'assets/resources.css').read_text() + extra + '</style>', unsafe_allow_html=True)


def _notice(kind, message):
    getattr(st, kind if kind in ('success', 'warning', 'error', 'info') else 'info')(message)


def _feedback(scope):
    if notice := resources.state().get('notice'):
        if len(notice) == 3 and notice[2] == scope:
            _notice(notice[0], notice[1])


def _error(result):
    status = result.get('status_code')
    _notice('warning' if result.get('error_type') in ('timeout', 'network') else 'error',
            (f'HTTP {status} · ' if status else '') + result['message'])


def _empty(title, description, name='book-open'):
    st.markdown(f'<section class="ws-resource-empty">{ui.icon(name, size=30)}'
                f'<h3>{html.escape(title)}</h3><p>{html.escape(description)}</p></section>', unsafe_allow_html=True)


def _heading(title, description='', name='book-open', current=False):
    badge = '<span class="ws-resource-badge current">Current / 当前</span>' if current else ''
    st.markdown(f'<div class="ws-resource-title">{ui.icon(name, size=22)}<div><h3>{html.escape(str(title))}</h3>'
                f'<p>{html.escape(description)}</p></div>{badge}</div>', unsafe_allow_html=True)


def _status(status):
    labels = {'ready': ('Ready / 就绪', 'circle-check'), 'processing': ('Processing / 处理中', 'refresh-cw'),
              'failed': ('Failed / 失败', 'circle-alert')}
    label, name = labels.get(status, ('Unconfirmed / 待核验', 'circle-help'))
    cls = status if status in labels else 'unknown'
    st.markdown(f'<span class="ws-resource-badge {cls}">{ui.icon(name, size=14)}{label}</span>', unsafe_allow_html=True)


def knowledge_base_component():
    _styles()
    current = resources.ensure_loaded()
    title, action = st.columns([4, 1])
    with title:
        st.caption('YOUR KNOWLEDGE / 知识空间')
    with action:
        if ui.button('Refresh knowledge bases', key='m2_refresh_kbs', name='refresh-cw', use_container_width=True):
            current['notice'] = None
            resources.load_knowledge_bases()
    _feedback('knowledge')
    if current['kb_error']:
        _error(current['kb_error'])
        st.caption('保留上次成功加载的列表；不会将请求失败当作空知识库。')

    ui.restore_draft('new_knowledge_base_name')
    ui.restore_draft('new_knowledge_base_description')
    with st.expander('Create Knowledge Base', expanded=not st.session_state['user'].knowledge_bases):
        st.caption('A clear name. A little context. / 为资料建立一个清晰的归属。')
        name = st.text_input('Name', key='new_knowledge_base_name', max_chars=100,
                             placeholder='e.g. Product research / 产品研究',
                             on_change=ui.remember_draft, args=('new_knowledge_base_name',))
        description = st.text_area('Description', key='new_knowledge_base_description', max_chars=500,
                                   placeholder='What belongs here? / 简要说明知识库用途',
                                   on_change=ui.remember_draft, args=('new_knowledge_base_description',))
        if ui.button('Create', key='create_knowledge_base_button', name='plus', variant='primary'):
            if not name.strip():
                st.error('Knowledge base name cannot be empty. 请输入名称。')
            else:
                result = api_utils.create_knowledge_base(name.strip(), description.strip() or None, detailed=True)
                if result['ok']:
                    refreshed = resources.load_knowledge_bases()
                    resources.set_notice('success' if refreshed else 'warning',
                        'Knowledge base created successfully. 已创建，可在列表中选择。' if refreshed
                        else '知识库创建已获确认，但列表刷新失败。请刷新，勿重复创建。')
                    st.rerun()
                else:
                    _error(result)

    kbs = st.session_state['user'].knowledge_bases
    if not kbs:
        _empty('Knowledge list unavailable' if current['kb_error'] else 'Your knowledge starts here',
               '请检查连接或权限，然后刷新。' if current['kb_error'] else '创建第一个知识库，再添加 PDF、DOCX 或 TXT 资料。')
        return
    ui.restore_draft('m2_kb_search')
    query = st.text_input('Search knowledge bases', key='m2_kb_search', placeholder='Search names or descriptions / 搜索名称或描述',
                          on_change=ui.remember_draft, args=('m2_kb_search',))
    visible = [k for k in kbs if query.casefold() in (k['name'] + ' ' + (k.get('description') or '')).casefold()]
    st.caption(f'{len(visible)} of {len(kbs)} knowledge bases · 文档数量来自成功加载的服务端列表')
    if not visible:
        _empty('No matching knowledge bases', '请调整搜索内容；也可以在上方创建新的知识库。')
    selected = str(st.session_state['thread'].knowledge_base_id)
    for kb in visible:
        key = str(kb['id'])
        with st.container(key='kb_card_' + key):
            _heading(kb['name'], kb.get('description') or 'No description / 暂无描述', current=key == selected)
            documents = current['documents'].get(key)
            date = str(kb.get('created_at') or '')[:10]
            st.caption(('Created ' + date + ' · ' if date else '') +
                       (f'{len(documents)} documents' if documents is not None else 'Document count not loaded / 数量待加载'))
            if current['doc_errors'].get(key):
                _error(current['doc_errors'][key])
                st.caption('数量为上次快照，不代表最新状态。' if documents is not None else '未将加载失败显示为 0。')
            use, view, inspect, danger = st.columns([1.3, 1.2, 1.2, .35])
            with use:
                if ui.button('Current knowledge base' if key == selected else 'Use knowledge base', key='m2_use_kb_' + key,
                             name='circle-check' if key == selected else 'book-open', disabled=key == selected or key in current['unavailable'], use_container_width=True):
                    result = resources.select_knowledge_base(key)
                    if result['ok']:
                        st.rerun()
                    else:
                        _error(result)
            with view:
                if ui.button('View documents', key='m2_view_docs_' + key, name='file-text', use_container_width=True,
                             disabled=key in current['unavailable']):
                    result = resources.select_knowledge_base(key)
                    if result['ok']:
                        ui.set_view('documents')
                        st.rerun()
                    else:
                        _error(result)
            with inspect:
                if ui.button('Refresh count' if documents is not None else 'Load document count', key='m2_count_' + key,
                             name='refresh-cw', use_container_width=True, disabled=key in current['unavailable']):
                    resources.load_documents(key)
                    st.rerun()
            with danger:
                with ui.popover('Knowledge base actions: ' + kb['name'], key='m2_kb_actions_' + key, icon_only=True):
                    st.caption(kb['name'])
                    ui.danger_note('Delete this knowledge base, its documents and indexed chunks? 此操作无法撤销。')
                    confirmed = st.checkbox('Confirm knowledge base deletion', key='confirm_delete_kb_' + key)
                    if ui.button('Delete Knowledge Base', key='delete_knowledge_base_' + key, name='trash-2',
                                 variant='danger', disabled=not confirmed or key in current['unavailable']):
                        result = resources.delete_knowledge_base(key)
                        if result['ok']:
                            st.rerun()
                        else:
                            _error(result)


def _upload_panel(kb_id, current):
    with st.container(key='m2_upload_panel'):
        ui.icon_label('Add a source / 添加资料', 'upload')
        st.caption('PDF · DOCX · TXT · One file at a time / 每次一个文件')
        file = st.file_uploader('Upload document', type=['pdf', 'docx', 'txt'], key='kb_upload_' + str(kb_id))
        if file is None:
            return
        receipt = current['receipts'].get(resources.file_key(kb_id, file))
        processing = any(d.get('status') == 'processing' for d in current['documents'].get(str(kb_id), []))
        retry = False
        if receipt:
            status = receipt['status']
            _notice('success' if status == 'ready' else 'error' if status == 'failed' else 'warning', receipt['message'])
            if status in ('submitting', 'unknown'):
                st.caption('未自动重试。请等待或刷新核验；没有可核验的文档 ID 时，本会话继续阻止相同文件重复提交。')
            if status == 'failed':
                retry = st.checkbox('已核验失败，允许重新提交一次 / Retry confirmed failure',
                                    key='m2_retry_' + resources.file_key(kb_id, file) + '_' + str(receipt.get('attempt', 1)))
        blocked = bool(receipt and not (receipt['status'] == 'failed' and retry))
        if ui.button('Upload', key='upload_button_' + str(kb_id), name='upload', variant='primary',
                     disabled=blocked or processing or bool(current['doc_errors'].get(str(kb_id))), use_container_width=True):
            with st.spinner('Indexing document… / 正在处理资料'):
                receipt = resources.upload(kb_id, file, retry_failed=retry)
            _notice('success' if receipt['status'] == 'ready' else 'error' if receipt['status'] == 'failed' else 'warning', receipt['message'])
        if processing:
            st.caption('此知识库仍有文档处理中，上传暂不可用。请刷新状态，不要重复提交。')


def document_list_component():
    _styles()
    current = resources.ensure_loaded()
    kb_id = st.session_state['thread'].knowledge_base_id
    if kb_id is None:
        _empty('Choose a knowledge base', '先在顶部选择知识库，或前往知识库页面创建资料空间。', 'folder-open')
        if ui.button('Browse knowledge bases', key='m2_browse_kbs', name='book-open'):
            ui.set_view('knowledge')
            st.rerun()
        return
    key = str(kb_id)
    kb = next((k for k in st.session_state['user'].knowledge_bases if str(k['id']) == key), None)
    if kb is None or key in current['unavailable']:
        st.warning('知识库已不可用，请刷新知识库列表或重新选择。')
        return
    with st.container(key='m2_document_context'):
        _heading(kb['name'], 'CURRENT KNOWLEDGE BASE / 当前知识库 · 使用顶部选择器切换')
        if ui.button('Refresh documents', key='refresh_documents_' + key, name='refresh-cw'):
            current['notice'] = None
            resources.load_documents(kb_id)
        if current['fetched'].get(key):
            st.caption('Last successful refresh / 上次成功刷新 ' + current['fetched'][key])
    _feedback('documents:' + key)
    if error := current['doc_errors'].get(key):
        _error(error)
        st.caption('以下为上次成功加载的快照，可能不是最新状态。' if key in current['documents']
                   else '列表尚未成功加载，不能确认是否已有文档。')
    _upload_panel(kb_id, current)
    documents = current['documents'].get(key)
    if documents is None:
        _empty('Document list unavailable', '请检查权限或网络，并手动刷新。', 'circle-alert')
        return
    total = len(documents)
    counts = {s: sum(d.get('status', 'ready') == s for d in documents) for s in ['ready', 'processing', 'failed']}
    st.markdown('<div class="ws-resource-summary">' + ''.join(
        f'<span><strong>{n}</strong> {label}</span>' for n, label in [(total, 'documents / 文档'),
        (counts['ready'], 'ready / 就绪'), (counts['processing'], 'processing / 处理中'), (counts['failed'], 'failed / 失败')]) + '</div>', unsafe_allow_html=True)
    search, filtering = st.columns([3, 1])
    with search:
        query = st.text_input('Search documents', key='m2_doc_search_' + key, placeholder='Search filenames / 搜索文件名')
    with filtering:
        selected = st.selectbox('Document status', ['All statuses', 'ready', 'processing', 'failed'], key='m2_doc_status_' + key)
    filtered = [d for d in documents if query.casefold() in d['file_name'].casefold()
                and (selected == 'All statuses' or d.get('status', 'ready') == selected)]
    if not filtered:
        _empty('No matching documents' if documents else 'A place for your sources',
               '请调整文件名或状态筛选。' if documents else '上传第一份小型资料；索引完成后即可用于对话。', 'file-text')
    for doc in filtered:
        ident = str(doc['id'])
        status = doc.get('status', 'ready')
        with st.container(key='document_card_' + ident):
            content, actions = st.columns([.86, .14])
            with content:
                _heading(doc['file_name'], name='file-text')
                _status(status)
                st.caption(f"{kb['name']} · ID {ident[:8]}" + (' · ' + str(doc['uploaded_at'])[:10] if doc.get('uploaded_at') else ''))
                if status == 'processing':
                    st.caption('文档仍在处理中，请稍后刷新；此状态不表示上传失败。')
                elif status == 'failed':
                    st.error(api_utils.document_failure_message(doc))
            with actions:
                with ui.popover('Document actions: ' + doc['file_name'], key='doc_actions_' + ident, icon_only=True):
                    st.caption(doc['file_name'])
                    ui.danger_note('Delete this document and its indexed chunks? 此操作无法撤销。')
                    confirmed = st.checkbox('Confirm document deletion', key='confirm_delete_document_' + ident, disabled=status == 'processing')
                    if ui.button('Delete document', key='delete_document_' + ident, name='trash-2', variant='danger',
                                 disabled=status == 'processing' or not confirmed):
                        result = resources.delete_document(kb_id, ident)
                        if result['ok']:
                            st.rerun()
                        else:
                            _error(result)
