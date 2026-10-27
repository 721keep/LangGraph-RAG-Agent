"""M3 presentation: real event summaries, traceable metadata, ordinary Markdown."""
import html
from pathlib import Path
from urllib.parse import urlsplit

import streamlit as st
import ui
from conversation_copy import copy_answer
from conversation_events import citation_issues, number, source_label, sources


def styles():
    st.markdown('<style>' + (Path(__file__).parent / 'assets/conversation.css').read_text() + '</style>', unsafe_allow_html=True)


def label(role, status=None):
    text = 'Agent' if role in ('ai', 'assistant') else 'You / 你'
    note = f'<span>{html.escape(status)}</span>' if status else ''
    st.markdown(f'<div class="ws-conversation-label"><strong>{text}</strong>{note}</div>', unsafe_allow_html=True)


def tools(turn):
    if not turn['tools']:
        return
    rows = []
    names = {'rag': ('book-open', 'Knowledge retrieval / 知识检索'), 'web': ('globe', 'Web Search'), 'mcp': ('workflow', 'MCP'), 'tool': ('workflow', 'Tool')}
    states = {'running': '执行中', 'complete': '已返回', 'received': '结果已收到', 'error': '执行失败',
              'no_evidence': '无有效证据', 'empty': '未返回来源', 'invalid': '结果格式异常', 'unconfirmed': '结果未确认'}
    for tool in turn['tools']:
        icon, title = names.get(tool.get('kind'), names['tool'])
        state = tool.get('status', 'received')
        cls = state if state in states else 'received'
        name = tool['name'] if tool.get('kind') in ('mcp', 'tool') else title
        rows.append(f'<div class="ws-tool-summary">{ui.icon(icon, size=16)}<span>{html.escape(name)}</span>'
                    f'<small class="ws-tool-state {cls}">{html.escape(states.get(state, state))}</small></div>')
    st.markdown('<div class="ws-tools">' + ''.join(rows) + '</div>', unsafe_allow_html=True)
    with st.expander(f"Execution details / 执行详情 · {len(turn['tools'])} records", expanded=False):
        for index, tool in enumerate(turn['tools'], 1):
            st.caption(f"{index}. {tool['name']} · {states.get(tool.get('status'), tool.get('status', ''))}")
            if tool.get('association') == 'ambiguous':
                st.warning('同名调用缺少关联 ID，未推断参数与结果的对应关系。')
            if 'args' in tool:
                st.json(tool['args'], expanded=False)
            if tool.get('kind') == 'rag' and isinstance(tool.get('data'), dict):
                detail = {k: v for k, v in tool['data'].items() if k != 'sources'}
                counts = [f'{title}: {detail[k]}' for k, title in [('candidate_count', 'Candidates'), ('retrieved_count', 'Returned sources'),
                          ('reranked_count', 'Reranked')] if k in detail]
                if counts:
                    st.caption(' · '.join(counts))
                st.json(detail, expanded=False)
            elif tool.get('kind') == 'mcp':
                detail = {k: tool[k] for k in ('server_name', 'reported_status', 'success', 'latency_ms', 'error_reason') if k in tool}
                if detail:
                    parts = [f'{title}: {detail[k]}' for k, title in [('server_name', 'Server'), ('reported_status', 'Reported status'),
                             ('latency_ms', 'Latency (ms)'), ('error_reason', 'Reason')] if k in detail]
                    if parts:
                        st.caption(' · '.join(parts))
                    st.json(detail, expanded=False)
                data = tool.get('data')
                if isinstance(data, dict) and 'result' in data:
                    st.json(data['result'], expanded=False)
            elif tool.get('data') is not None:
                st.json(tool['data'], expanded=False)


def evidence(turn):
    for index, tool in enumerate(turn['tools'], 1):
        if tool.get('kind') == 'rag':
            if not isinstance(tool.get('data'), dict):
                continue
            data = tool.get('data') or {}
            gate = data.get('evidence_gate_passed') if isinstance(data, dict) else None
            if tool['status'] in ('error', 'invalid'):
                st.error('RAG retrieval failed / 检索技术错误，不能将其视为无匹配文档。')
            elif tool['status'] == 'no_evidence':
                st.info('No reliable evidence / 未获得足够证据，不能据此编造文档结论。')
            elif tool['status'] == 'empty':
                st.warning('检索未返回可展示的来源，不标记为有据回答。')
            st.caption('Evidence Gate · ' + ('PASS / 证据通过' if gate is True else 'REJECT / 证据未通过' if gate is False else '未返回判定'))
            for position, source in enumerate(sources(tool), 1):
                name = source.get('file_name') or '文档名未返回'
                tag = source_label(source) or '引用编号未返回'
                with st.expander(f'{tag} · {name} · retrieval {index}', expanded=False):
                    metadata = []
                    for key, title in [('page', 'Page'), ('chunk_index', 'Chunk'), ('document_id', 'Document ID'), ('chunk_id', 'Chunk ID')]:
                        if source.get(key) is not None:
                            metadata.append(f'{title}: {source[key]}')
                    st.caption(' · '.join(metadata) if metadata else '接口未返回页码或 Chunk 定位信息。')
                    for key, title in [('relevance_score', 'Vector score'), ('rerank_score', 'Reranker score')]:
                        value = number(source.get(key))
                        if value is not None:
                            st.caption(f'{title}: {value:.4f}')
                    if source.get('content'):
                        st.text(source['content'])
        elif tool.get('kind') == 'web':
            data = tool.get('data')
            results = data.get('results', []) if isinstance(data, dict) else data if isinstance(data, list) else []
            for result in results:
                if not isinstance(result, dict):
                    continue
                with st.expander('Web · ' + str(result.get('title') or 'Title not returned'), expanded=False):
                    url = result.get('url')
                    if isinstance(url, str):
                        try:
                            parsed = urlsplit(url)
                        except ValueError:
                            parsed = urlsplit('')
                        if parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.username and not parsed.password:
                            st.link_button('Open web source', url)
                    if result.get('content'):
                        st.text(str(result['content']))
                    score = number(result.get('score'))
                    if score is not None:
                        st.caption(f'Web relevance score: {score:.4f}')
        if tool.get('kind') != 'rag' and tool.get('status') == 'error':
            reason = {'tool_timeout': '工具响应超时', 'server_unavailable': 'MCP 服务不可用',
                      'invalid_arguments': '工具参数被拒绝', 'tool_execution_error': '工具执行错误'}.get(tool.get('error_reason'), '工具未能完成请求')
            st.warning(tool['name'] + ' · ' + reason)
    issues = citation_issues(turn)
    if issues['unresolved']:
        st.warning('引用缺少对应来源元数据：' + ', '.join(issues['unresolved']))
    if issues['ambiguous']:
        st.warning('不同检索轮次复用了引用编号，未推断唯一归属：' + ', '.join(issues['ambiguous']))


def delivery(turn):
    from runtime_errors import MESSAGES
    if turn.get('runtime_error') in MESSAGES:
        st.error(MESSAGES[turn['runtime_error']])
        return
    status = turn['delivery']
    if status in ('network_error', 'http_error', 'interrupted', 'setup_error'):
        st.error({'network_error': '网络连接中断，服务器处理结果待核验。', 'http_error': 'Chat 服务拒绝或未能完成请求。',
                  'interrupted': '输出中断，已收到的内容保留为未确认的局部结果。',
                  'setup_error': '会话初始化或知识库绑定未确认；保留 Thread，不发送 Chat 请求。'}[status])
    elif status == 'no_answer':
        st.warning('流已结束，但未收到最终回答；没有补造模型回复。')
    if turn['malformed_events']:
        st.warning('部分流式事件格式无法识别，未显示其原始载荷。')


def answer(text, key, turn=None, saved=False):
    with ui.chat_message('ai', key):
        label('ai', '历史消息' if saved and turn is None else '响应已接收' if turn and turn['delivery'] == 'received' else None)
        if turn:
            tools(turn)
        if text:
            st.markdown(text)
        if turn:
            delivery(turn)
            evidence(turn)
        elif '[Source ' in text:
            st.caption('历史接口只提供正文；没有补造本次未返回的工具或来源面板。')
        if text:
            copy_answer(text, st.session_state.get('ui_theme', 'light'))
