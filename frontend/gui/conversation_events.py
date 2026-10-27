"""Presentation state derived only from the existing NDJSON event fields."""
import hashlib
import json
import math
import re

HIDDEN = {'reasoning_content', 'reasoning', 'thinking', 'analysis'}
PRIVATE = ('password', 'secret', 'token', 'api_key', 'authorization')


def safe_details(value, depth=0):
    if depth > 8:
        return '[nested value omitted]'
    if isinstance(value, dict):
        return {str(k): ('[redacted]' if any(p in str(k).lower() for p in PRIVATE) else safe_details(v, depth + 1))
                for k, v in value.items() if str(k).lower() not in HIDDEN}
    if isinstance(value, list):
        return [safe_details(v, depth + 1) for v in value[:100]]
    if isinstance(value, str):
        return re.sub(r'\bsk-[A-Za-z0-9_-]{6,}', '[redacted]', value[:12000])
    return value


def number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def payload(value):
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return None


def new_turn():
    return {'answer': '', 'tools': [], 'delivery': 'waiting', 'ignored_events': 0, 'malformed_events': 0}


def _result(event):
    data = payload(event.get('content'))
    name = event.get('name', 'unknown')
    result = {'data': safe_details(data) if data is not None else None,
              'kind': 'rag' if name == 'retrieve_user_documents' else 'web' if name == 'web_search' else 'tool',
              'status': 'received'}
    if name == 'retrieve_user_documents':
        if not isinstance(data, dict) or not isinstance(data.get('sources'), list):
            result['status'] = 'invalid'
        elif data.get('status') == 'error':
            result['status'] = 'error'
        elif data.get('status') == 'no_evidence' or data.get('evidence_gate_passed') is False:
            result['status'] = 'no_evidence'
        elif data.get('status') == 'ok':
            result['status'] = 'complete' if data['sources'] else 'empty'
    elif event.get('tool_source') == 'mcp' or (isinstance(data, dict) and data.get('tool_source') == 'mcp'):
        result['kind'] = 'mcp'
        for key in ('server_name', 'status', 'success', 'latency_ms', 'error_reason'):
            value = event.get(key, data.get(key) if isinstance(data, dict) else None)
            if value is not None:
                result[key if key != 'status' else 'reported_status'] = safe_details(value)
        result['status'] = 'complete' if result.get('success') is True else 'error' if result.get('success') is False else 'received'
    elif isinstance(data, dict) and (data.get('status') in ('error', 'failed') or data.get('success') is False):
        result['status'] = 'error'
    return result


def ingest(turn, event):
    if not isinstance(event, dict):
        turn['malformed_events'] += 1
        return False
    kind = event.get('type')
    if kind == 'llm_chunk':
        if isinstance(event.get('content'), str):
            turn['answer'] += event['content']
            turn['delivery'] = 'streaming'
            return True
        turn['malformed_events'] += 1
    elif kind == 'tool_call':
        name = event.get('name')
        if not isinstance(name, str):
            turn['malformed_events'] += 1
            return False
        tool = {'name': name, 'kind': 'rag' if name == 'retrieve_user_documents' else 'web' if name == 'web_search' else 'tool',
                'status': 'running', 'args': safe_details(event.get('args', {}))}
        if event.get('tool_call_id') is not None:
            tool['tool_call_id'] = event['tool_call_id']
        turn['tools'].append(tool)
        return True
    elif kind == 'error':
        from runtime_errors import MESSAGES
        if isinstance(event.get('code'), str) and event['code'] in MESSAGES:
            turn['runtime_error'] = event['code']
            return True
        return False
    elif kind == 'tool_result':
        name = event.get('name')
        if not isinstance(name, str):
            turn['malformed_events'] += 1
            return False
        waiting = [t for t in turn['tools'] if t['name'] == name and t['status'] == 'running']
        ident = event.get('tool_call_id')
        matched = [t for t in waiting if t.get('tool_call_id') == ident] if ident is not None else waiting
        if len(matched) == 1:
            tool = matched[0]
        else:
            # The current backend omits call IDs. Never guess same-name parallel associations.
            tool = {'name': name, 'association': 'ambiguous' if waiting else 'result_only'}
            turn['tools'].append(tool)
            if waiting:
                for call in waiting:
                    call['association'] = 'ambiguous'
        tool.update(_result(event))
        return True
    else:
        # No reasoning payload, raw unknown event or fabricated "done" event is shown.
        turn['ignored_events'] += 1
    return False


def finish(turn, error=None):
    turn['delivery'] = error or ('http_error' if turn.get('runtime_error') else ('received' if turn['answer'].strip() else 'no_answer'))
    for tool in turn['tools']:
        if tool['status'] == 'running':
            tool['status'] = 'unconfirmed'
    return turn


def sources(tool):
    data = tool.get('data')
    if tool.get('kind') != 'rag' or tool.get('status') not in ('complete', 'received') or not isinstance(data, dict):
        return []
    return [s for s in data.get('sources', []) if isinstance(s, dict)]


def source_label(source):
    label = source.get('source_label')
    if isinstance(label, str) and label:
        return label
    if source.get('source_id') is not None:
        return '[Source ' + str(source['source_id']) + ']'
    return None


def citation_issues(turn):
    catalog = {}
    for tool in turn['tools']:
        for source in sources(tool):
            label = source_label(source)
            if label:
                identity = json.dumps({k: source.get(k) for k in ('file_name', 'page', 'chunk_index', 'content')}, sort_keys=True, ensure_ascii=False)
                catalog.setdefault(label, set()).add(identity)
    refs = set(re.findall(r'\[Source\s+\d+\]', turn['answer']))
    return {'unresolved': sorted(refs - catalog.keys()),
            'ambiguous': sorted(label for label, items in catalog.items() if len(items) > 1)}


def history_key(messages):
    normalized = [{'role': 'ai' if m.get('role') in ('ai', 'assistant') else 'human', 'content': m.get('content', '')} for m in messages]
    return hashlib.sha256(json.dumps(normalized, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
