"""Explicitly fictional fixtures shaped like the existing backend events."""
import copy
import json

SOURCE1 = {'source_id': 1, 'source_label': '[Source 1]', 'file_name': 'workspace-guide.pdf', 'page': 1,
           'chunk_index': 0, 'relevance_score': .88, 'rerank_score': .96,
           'content': 'Mock fixture: the workspace uses BGE-M3 with 1024 dimensions.'}
SOURCE2 = {'source_id': 2, 'source_label': '[Source 2]', 'file_name': 'architecture-notes.docx', 'page': 2,
           'chunk_index': 3, 'relevance_score': .82, 'rerank_score': .91,
           'content': 'Mock fixture: PostgreSQL and pgvector store knowledge vectors; BGE Reranker orders evidence.'}


def rag(sources=None):
    return {'status': 'ok', 'query': 'Mock query', 'knowledge_base_id': '20000000-0000-0000-0000-000000000001',
            'candidate_count': 4, 'retrieved_count': 2, 'threshold_passed_count': 3, 'threshold_filtered_count': 1,
            'reranked_count': 2, 'top_k': 3, 'similarity_threshold': .5, 'rerank_top_n': 3,
            'evidence_gate_passed': True, 'rerank_evidence_threshold': .5, 'top_rerank_score': .96,
            'sources': copy.deepcopy([SOURCE1, SOURCE2] if sources is None else sources)}


def result(name, data, **meta):
    return {'type': 'tool_result', 'name': name, 'content': json.dumps(data, ensure_ascii=False), **meta}


def scenario(prompt, kb=True, anonymous=False):
    call = {'type': 'tool_call', 'name': 'retrieve_user_documents', 'args': {'query': prompt}}
    data = rag()
    answer = '这是本地 Mock 回答。测试资料使用 **BGE-M3**，向量维度为 **1024**。[Source 1]\n\nPostgreSQL 与 pgvector 存储向量，BGE Reranker 排序证据。[Source 2]'
    events = [call]
    if not kb or '[NO_EVIDENCE]' in prompt or 'no evidence' in prompt.lower():
        data.update(status='no_evidence', reason='no_candidates' if kb else 'knowledge_base_not_selected',
                    evidence_gate_passed=False, sources=[], retrieved_count=0)
        answer = '本地 Mock：资料不足，无法从当前文档确认该结论。' if kb else '本地 Mock：请先选择知识库，再询问文档内容。'
    if '[TOOL_ERROR]' in prompt:
        data.update(status='error', reason='vector_search_failed', sources=[], retrieved_count=0, evidence_gate_passed=False)
        answer = '本地 Mock：检索服务暂时不可用，无法据此给出文档结论。'
    if '[MISSING]' in prompt:
        data = {'status': 'ok', 'sources': [{'source_label': '[Source 1]', 'file_name': SOURCE1['file_name'], 'content': SOURCE1['content']}]}
        answer = '本地 Mock：来源未提供页码、分数或 Gate 判定。[Source 1]'
    if '[EMPTY]' in prompt:
        data.update(sources=[], retrieved_count=0)
        answer = ''
    events.append(result('retrieve_user_documents', data))
    if '[MULTI]' in prompt:
        events = [call, {'type': 'tool_call', 'name': 'web_search', 'args': {'query': 'Mock public reference'}},
                  {'type': 'tool_call', 'name': 'get_server_status', 'args': {}},
                  {'type': 'tool_call', 'name': 'lookup_inventory', 'args': {'item': 'test'}}, events[-1]]
        events += [result('web_search', {'results': [{'title': 'Public reference / Mock only', 'url': 'https://example.org/mock-reference',
                       'content': 'Local fixture only. No web request was made.', 'score': .7}]}),
                   result('get_server_status', {'tool_source': 'mcp', 'server_name': 'mock-mcp', 'success': True,
                       'status': 'ok', 'latency_ms': 18.4, 'result': {'healthy': True, 'api_key': 'sk-M3_FAKE_SECRET', 'reasoning_content': 'M3_HIDDEN_REASONING'}},
                       tool_source='mcp', server_name='mock-mcp', success=True, status='ok', latency_ms=18.4),
                   result('lookup_inventory', {'tool_source': 'mcp', 'success': False, 'status': 'error', 'error_reason': 'server_unavailable'},
                       tool_source='mcp', server_name='mock-mcp', success=False, status='error', error_reason='server_unavailable')]
        answer += '\n\nMock MCP 状态已返回；另一项 MCP 查询不可用。'
    if '[PARALLEL]' in prompt:
        other = copy.deepcopy(SOURCE2)
        other.update(source_id=1, source_label='[Source 1]')
        events = [call, {'type': 'tool_call', 'name': 'retrieve_user_documents', 'args': {'query': 'second query'}},
                  result('retrieve_user_documents', rag([SOURCE1])), result('retrieve_user_documents', rag([other]))]
        answer = 'Mock：两轮检索复用同一来源编号，无法仅凭编号确定唯一来源。[Source 1]'
    if '[CITATION_MISSING]' in prompt:
        answer = 'Mock：这个来源标签未在工具结果中出现。[Source 99]'
    if '[MALFORMED]' in prompt:
        events.insert(0, 'not-json')
        events.insert(1, {'type': 'reasoning_content', 'content': 'M3_HIDDEN_REASONING'})
    if '[LONG]' in prompt:
        answer += '\n\n## Architecture / 架构\n\n- **Evidence first**，保留原始来源。\n- Use Markdown and code.\n\n```python\nvector_dimensions = 1024\nprint("<script>not executable</script>")\n```\n\n| Component | Role |\n| --- | --- |\n| BGE-M3 | Embedding |\n| pgvector | Storage |\n\n> This is a local Mock fixture.\n\n' + ('中英文长文本 / A longer explanation with traceable sources. ' * 45)
    if anonymous:
        events = []
        answer = '本地 Mock 普通对话。\n\n```python\nprint("Hello workspace")\n```'
    if '[INTERRUPT]' in prompt:
        answer = answer[:30]
    events += [{'type': 'llm_chunk', 'content': answer[i:i+12]} for i in range(0, len(answer), 12)]
    return events, answer
