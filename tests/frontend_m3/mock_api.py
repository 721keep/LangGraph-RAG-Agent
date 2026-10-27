"""In-memory UI-only API. No database, provider credentials or model clients."""
from collections import Counter
from datetime import datetime, timezone
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

USER = '10000000-0000-0000-0000-000000000001'
KB = '20000000-0000-0000-0000-000000000001'
KB2 = '20000000-0000-0000-0000-000000000002'
THREAD = '30000000-0000-0000-0000-000000000001'
DOC = '40000000-0000-0000-0000-000000000001'
TOKEN = 'm3-local-mock-session-only'
LOCK = threading.RLock()
COUNTS = Counter()
MODES = {}
PROTECTED = '20000000-0000-0000-0000-000000000003'


def stamp():
    return datetime.now(timezone.utc).isoformat()


KBS = {KB: {'id': KB, 'name': 'Product notes / 产品笔记', 'description': 'Project context, decisions, and source material.', 'user_id': USER, 'created_at': stamp(), 'updated_at': stamp()},
       KB2: {'id': KB2, 'name': 'Engineering handbook', 'description': 'Architecture and delivery notes.', 'user_id': USER, 'created_at': stamp(), 'updated_at': stamp()}}
DOCS = {DOC: {'id': DOC, 'file_name': 'workspace-guide.pdf', 'knowledge_base_id': KB, 'thread_id': None, 'status': 'ready', 'error_message': None, 'uploaded_at': stamp()},
        '40000000-0000-0000-0000-000000000002': {'id': '40000000-0000-0000-0000-000000000002', 'file_name': '架构草案.pdf', 'knowledge_base_id': KB2, 'thread_id': None, 'status': 'processing', 'error_message': None, 'uploaded_at': stamp()},
        '40000000-0000-0000-0000-000000000003': {'id': '40000000-0000-0000-0000-000000000003', 'file_name': 'sample-failed.pdf', 'knowledge_base_id': KB2, 'thread_id': None, 'status': 'failed', 'error_message': 'pdf_parse_failed: Mock only', 'uploaded_at': stamp()}}
KBS[PROTECTED] = {'id': PROTECTED, 'name': 'Protected workspace / 删除受限示例', 'description': 'Mock 403: demonstrates the unchanged F1.5 deletion policy.', 'user_id': USER, 'created_at': stamp()}
KBS['20000000-0000-0000-0000-000000000004'] = {'id': '20000000-0000-0000-0000-000000000004', 'name': '跨团队产品研究与架构决策记录 / Long knowledge base name for layout verification ' + 'Research' * 4, 'description': '长名称、中文与英文混排测试。此知识库暂无文档。', 'user_id': USER, 'created_at': stamp()}
THREADS = {THREAD: {'id': THREAD, 'title': 'RAG architecture review / 检索方案', 'user_id': USER, 'knowledge_base_id': KB, 'created_at': stamp()}}
DOCS['40000000-0000-0000-0000-000000000004'] = {'id': '40000000-0000-0000-0000-000000000004', 'file_name': 'architecture-notes.docx', 'knowledge_base_id': KB, 'status': 'ready', 'uploaded_at': stamp()}
HISTORY = {THREAD: [{'role': 'human', 'content': '这个工作区如何组织知识？'},
                    {'role': 'ai', 'content': '这是本地 Mock 历史。知识库组织资料，会话保留上下文。 [Source 1]\n\nSources:\n- [Source 1] `workspace-guide.pdf`, page 1, chunk 0'}]}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def response(self, status, data=None):
        body = b'' if data is None else json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_api(self):
        path = urlsplit(self.path).path.rstrip('/')
        count = int(self.headers.get('Content-Length', '0'))
        body = self.rfile.read(count) if count else b''
        key = self.command + ' ' + path
        with LOCK:
            if path != '/m1/metrics':
                COUNTS[key] += 1
        if path == '/m2/control' and self.command == 'POST':
            MODES.clear(); MODES.update(json.loads(body or b'{}'))
            if MODES.get('ready_id') in DOCS:
                DOCS[MODES['ready_id']]['status'] = 'ready'
            return self.response(200, {'ok': True})
        if path == '/m1/metrics':
            return self.response(200, {'requests': dict(COUNTS), 'real_model_requests': 0})
        if path == '/api/v1/chat/config':
            return self.response(200, {'model_provider': 'openai', 'model_names': ['deepseek-flash', 'preview-model'], 'default_model': 'deepseek-flash'})
        if path == '/api/v1/auth/login':
            form = parse_qs(body.decode())
            if form.get('username') != ['demo@example.org'] or form.get('password') != ['workspace-demo']:
                return self.response(401, {'detail': 'Invalid credentials'})
            return self.response(200, {'message': 'Login successful', 'access_token': TOKEN, 'refresh_token': TOKEN,
                                       'user': {'username': 'alex', 'email': 'demo@example.org', 'id': USER}})
        if path == '/api/v1/auth/signup':
            return self.response(201, {'message': 'Mock account created', 'user': {'id': USER}})
        anonymous_chat = path == '/api/v1/chat'
        if not anonymous_chat and self.headers.get('Authorization') != 'Bearer ' + TOKEN:
            return self.response(401, {'detail': 'Preview login required'})
        data = json.loads(body) if body and self.headers.get('Content-Type', '').startswith('application/json') else {}
        if path == '/api/v1/knowledge-bases' and self.command == 'GET' and MODES.get('empty_kbs'):
            return self.response(200, [])
        mode = ('kb_list' if path == '/api/v1/knowledge-bases' and self.command == 'GET' else
                'create' if path == '/api/v1/knowledge-bases' and self.command == 'POST' else
                'doc_list' if path.startswith('/api/v1/documents/knowledge-bases/') and self.command == 'GET' else
                'delete_doc' if path.startswith('/api/v1/documents/') and self.command == 'DELETE' else
                'delete_kb' if path.startswith('/api/v1/knowledge-bases/') and self.command == 'DELETE' else
                'binding' if path.endswith('/knowledge-base') and self.command == 'PATCH' else None)
        if mode and MODES.get(mode):
            return self.response(MODES[mode], {'detail': MODES.get('detail', 'not safe: sk-mock-secret postgresql://mock:password@host/db')})
        if path == '/api/v1/knowledge-bases/' + PROTECTED and self.command == 'DELETE':
            return self.response(403, {'detail': 'F1.5 preserves existing test data; DELETE disabled.'})

        if path == '/api/v1/knowledge-bases':
            if self.command == 'GET':
                return self.response(200, list(KBS.values()))
            if self.command == 'POST':
                ident = str(uuid4()); row = {'id': ident, 'name': data['name'], 'description': data.get('description'), 'user_id': USER, 'created_at': stamp(), 'updated_at': stamp()}
                KBS[ident] = row
                return self.response(201, row)
        if path.startswith('/api/v1/knowledge-bases/') and self.command == 'DELETE':
            ident = path.split('/')[-1]
            if ident not in KBS:
                return self.response(404, {'detail': 'Knowledge base not found'})
            del KBS[ident]
            for doc in list(DOCS):
                if DOCS[doc]['knowledge_base_id'] == ident:
                    del DOCS[doc]
            for t in THREADS.values():
                if t['knowledge_base_id'] == ident:
                    t['knowledge_base_id'] = None
            return self.response(204)
        if path == '/api/v1/threads' and self.command == 'POST' and MODES.get('create_thread'):
            return self.response(MODES['create_thread'], {'detail': 'Mock create unavailable'})
        if path.startswith('/api/v1/threads/') and self.command == 'GET' and MODES.get('thread'):
            return self.response(MODES['thread'], {'detail': 'Mock thread denied'})
        if path == '/api/v1/threads':
            if self.command == 'GET':
                return self.response(200, list(reversed(THREADS.values())))
            ident = str(uuid4()); row = {'id': ident, 'title': 'New conversation', 'user_id': USER, 'knowledge_base_id': None, 'created_at': stamp()}
            THREADS[ident] = row; HISTORY[ident] = []
            return self.response(201, row)
        if path.startswith('/api/v1/threads/'):
            bits = path.split('/'); ident = bits[4]
            if ident not in THREADS:
                return self.response(404, {'detail': 'Thread not found'})
            if self.command == 'GET':
                return self.response(200, THREADS[ident])
            if self.command == 'PATCH':
                THREADS[ident].update(data)
                return self.response(200, THREADS[ident])
            if self.command == 'DELETE':
                del THREADS[ident]; HISTORY.pop(ident, None)
                return self.response(204)
        if path.startswith('/api/v1/documents/knowledge-bases/'):
            bits = path.split('/'); kb = bits[5]
            if kb not in KBS:
                return self.response(404, {'detail': 'Knowledge base not found'})
            if self.command == 'GET':
                return self.response(200, [d for d in DOCS.values() if d['knowledge_base_id'] == kb])
            if self.command == 'POST' and bits[-1] == 'upload':
                mime = BytesParser(policy=default).parsebytes(('Content-Type: ' + self.headers['Content-Type'] + '\r\nMIME-Version: 1.0\r\n\r\n').encode() + body)
                file = next(p for p in mime.iter_parts() if p.get_param('name', header='content-disposition') == 'file')
                name = file.get_filename(); ident = str(uuid4())
                if 'uncertain' in name:
                    return self.response(504, {'detail': {'status': 'unknown', 'error_code': 'upload_timeout'}})
                status = 'processing' if 'processing' in name else 'failed' if 'failed' in name else 'ready'
                DOCS[ident] = {'id': ident, 'file_name': name, 'knowledge_base_id': kb, 'thread_id': None, 'status': status,
                               'error_message': 'pdf_parse_failed: Mock only' if status == 'failed' else None, 'uploaded_at': stamp()}
                if status != 'ready':
                    return self.response(504 if status == 'processing' else 422,
                                         {'detail': {'status': 'unknown' if status == 'processing' else 'failed', 'document_id': ident,
                                                     'error_code': 'upload_timeout' if status == 'processing' else 'pdf_parse_failed'}})
                return self.response(200, {'status': 'ready', 'document_id': ident, 'message': 'Mock upload completed'})
        if path.startswith('/api/v1/documents/') and self.command == 'DELETE':
            ident = path.split('/')[-1]
            if ident not in DOCS:
                return self.response(404, {'detail': 'Document not found'})
            del DOCS[ident]
            return self.response(200, {'message': 'Document deleted'})
        if path.startswith('/api/v1/chat'):
            tid = path.split('/')[-1] if not anonymous_chat else 'guest'
            if self.command == 'GET':
                if MODES.get('history'):
                    return self.response(MODES['history'], {'detail': 'Mock history unavailable'})
                return self.response(200, HISTORY.get(tid, []))
            from mock_stream import serve
            return serve(self, tid, data, anonymous_chat, HISTORY, THREADS, COUNTS)
        return self.response(404, {'detail': 'Mock route not implemented'})

    do_GET = handle_api
    do_POST = handle_api
    do_PATCH = handle_api
    do_DELETE = handle_api


if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0', 8000), Handler).serve_forever()
