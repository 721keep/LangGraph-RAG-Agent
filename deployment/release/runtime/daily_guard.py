"""Daily deployment boundary: durable reservation before HTTP, no prompt/key logs."""
from contextvars import ContextVar
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
import uuid

import httpx
from model_guard import SSEInspector

SCOPE = ContextVar('daily_operation', default=None)
ORIGINAL_SEND = httpx.AsyncHTTPTransport.handle_async_request
MODELS = {'chat': 'deepseek-flash', 'embedding': 'BAAI/bge-m3', 'reranker': 'BAAI/bge-reranker-v2-m3'}
ENDPOINTS = {('api.deepseek.com', '/chat/completions'): 'chat',
             ('api.siliconflow.cn', '/v1/embeddings'): 'embedding',
             ('api.siliconflow.cn', '/v1/rerank'): 'reranker'}
MESSAGES = {
    'daily_limit_reached': '日常模型额度已达上限；请查看 daily-status，调整配置或等待下一 UTC 日期。不会自动重试。',
    'daily_input_limit': '输入、批量或单次工具调用次数超过日常安全上限；请缩小文件或缩短会话。',
    'daily_paused': '日常模型调用已暂停；请查看 daily-status 排查后显式 resume-daily，不要重复已成功的操作。',
    'daily_provider_error': '模型服务返回错误或连接中断；已记录并暂停调用，请核验结果后再继续。',
    'daily_login_required': '请登录并使用已保存的会话进行日常模型问答。',
    'daily_busy': '当前模型并发数已达上限；请等待正在执行的操作结束，不会自动重试。',
}


class DailyError(RuntimeError):
    def __init__(self, code):
        self.code = code
        state = SCOPE.get()
        if state is not None:
            state['error'] = code
        super().__init__(code)


def policy(values):
    expected = {'version', 'ledger_id', 'requests_per_day', 'input_tokens_per_day', 'chat_output_tokens_per_day',
                'chat_max_input_tokens', 'chat_max_output_tokens', 'requests_per_operation',
                'max_batch_texts', 'max_text_characters', 'max_batch_characters', 'max_parallel_requests'}
    if set(values) != expected or values['version'] != 1:
        raise ValueError('Invalid daily policy schema')
    for field in ('requests_per_day', 'input_tokens_per_day', 'requests_per_operation'):
        if set(values[field]) != set(MODELS):
            raise ValueError('Invalid daily model limits')
    uuid.UUID(values['ledger_id'])
    numbers = [n for k,v in values.items() if k != 'ledger_id' for n in (v.values() if isinstance(v, dict) else [v])]
    if any(type(n) is not int or not 1 <= n <= 1000000000 for n in numbers):
        raise ValueError('Daily limits must be positive bounded integers')
    if (values['chat_max_input_tokens'] > 65536 or values['chat_max_output_tokens'] > 8192
            or values['max_batch_texts'] > 64 or values['max_text_characters'] > 8192
            or values['max_batch_characters'] > 100000 or values['max_parallel_requests'] > 8
            or values['requests_per_operation']['chat'] > 24):
        raise ValueError('Daily hard safety ceiling exceeded')
    return values


class Ledger:
    def __init__(self, path, limits, counter, clock=None, create=False, recover=True):
        self.path = Path(path)
        self.limits = policy(limits)
        self.counter = counter
        self.clock = clock or (lambda: datetime.now(timezone.utc).date().isoformat())
        if create:
            # Reserve a new file exclusively; normal startups cannot regenerate it.
            with self.path.open('xb'):
                pass
        elif not self.path.is_file():
            raise ValueError('Daily ledger missing; refusing automatic regeneration')
        with self.connect() as db:
            if create:
                db.executescript('''
                CREATE TABLE control (id INTEGER PRIMARY KEY CHECK(id=1), paused TEXT, ledger_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS requests (
                    id INTEGER PRIMARY KEY, day TEXT NOT NULL, operation TEXT NOT NULL, kind TEXT NOT NULL,
                    input_reserved INTEGER NOT NULL, output_reserved INTEGER NOT NULL,
                    http_status INTEGER, status TEXT NOT NULL DEFAULT 'pending', metadata TEXT NOT NULL DEFAULT '{}');
                CREATE INDEX IF NOT EXISTS daily_kind ON requests(day,kind);
                CREATE INDEX IF NOT EXISTS operation_kind ON requests(operation,kind);
                ''')
                db.execute('INSERT INTO control VALUES(1,NULL,?)', (limits['ledger_id'],))
            if db.execute('SELECT ledger_id FROM control WHERE id=1').fetchone()[0] != limits['ledger_id']:
                raise ValueError('Daily ledger identity mismatch')
            # A crash may have sent a billable request. Never repeat or discard it.
            if recover and db.execute("SELECT 1 FROM requests WHERE status='pending' LIMIT 1").fetchone():
                db.execute("UPDATE control SET paused='unresolved_request_after_restart' WHERE id=1")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level='IMMEDIATE')
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        try:
            with db:
                yield db
        finally:
            db.close()

    def status(self):
        with self.connect() as db:
            paused = db.execute('SELECT paused FROM control WHERE id=1').fetchone()[0]
            rows = [dict(r) for r in db.execute('SELECT kind,COUNT(*) requests,SUM(input_reserved) input_reserved,'
                     'SUM(output_reserved) output_reserved FROM requests WHERE day=? GROUP BY kind', (self.clock(),))]
            return {'day_utc': self.clock(), 'paused': paused, 'usage': rows,
                    'lifetime_requests': db.execute('SELECT COUNT(*) FROM requests').fetchone()[0], 'limits': self.limits}

    def available(self, kind):
        state = self.status()
        if state['paused']:
            raise DailyError('daily_paused')
        row = next((r for r in state['usage'] if r['kind'] == kind), {})
        if (row.get('requests', 0) >= self.limits['requests_per_day'][kind]
                or row.get('input_reserved', 0) >= self.limits['input_tokens_per_day'][kind]
                or (kind == 'chat' and row.get('output_reserved', 0) >= self.limits['chat_output_tokens_per_day'])):
            raise DailyError('daily_limit_reached')

    def reserve(self, request):
        scope = SCOPE.get()
        if scope is None:
            raise DailyError('daily_login_required')
        if scope.get('error'):
            raise DailyError(scope['error'])
        url = request.url
        kind = ENDPOINTS.get((url.host, url.path))
        if (request.method != 'POST' or url.scheme != 'https' or url.port not in (None, 443)
                or url.query or url.fragment or url.userinfo or kind is None):
            raise DailyError('daily_input_limit')
        body = json.loads(request.content)
        if body.get('model') != MODELS[kind]:
            raise DailyError('daily_input_limit')
        output = 0
        meta = {}
        if kind == 'chat':
            output = self.limits['chat_max_output_tokens']
            if (body.get('stream') is not True or body.get('max_tokens') != output
                    or body.get('n', 1) != 1 or 'max_completion_tokens' in body
                    or body.get('thinking') != {'type': 'disabled'}
                    or body.get('stream_options', {}).get('include_usage') is not True):
                raise DailyError('daily_input_limit')
            _, reserve = self.counter.count(body)
            if reserve > self.limits['chat_max_input_tokens']:
                raise DailyError('daily_input_limit')
        else:
            texts = body.get('input') if kind == 'embedding' else body.get('documents')
            texts = [texts] if isinstance(texts, str) else texts
            if (not isinstance(texts, list) or not 1 <= len(texts) <= self.limits['max_batch_texts']
                    or not all(isinstance(t, str) and 0 < len(t) <= self.limits['max_text_characters'] for t in texts)
                    or sum(map(len, texts)) > self.limits['max_batch_characters']):
                raise DailyError('daily_input_limit')
            if kind == 'embedding':
                if 'dimensions' in body or body.get('encoding_format') != 'float':
                    raise DailyError('daily_input_limit')
                counted = texts
            else:
                query = body.get('query')
                if (not isinstance(query, str) or not 0 < len(query) <= self.limits['max_text_characters']
                        or type(body.get('top_n')) is not int or not 1 <= body['top_n'] <= len(texts)):
                    raise DailyError('daily_input_limit')
                counted = [query + t for t in texts]
            # Conservative UTF-8 byte reservation, NOT an exact BGE tokenizer count.
            reserve = sum(len(t.encode('utf-8')) + 32 for t in counted)
            meta = {'input_count': len(texts), 'input_characters': sum(map(len, texts)),
                    'accounting': 'utf8_bytes_plus_special_token_margin'}
        day = self.clock()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT paused FROM control WHERE id=1').fetchone()[0]:
                raise DailyError('daily_paused')
            row = db.execute('SELECT COUNT(*),COALESCE(SUM(input_reserved),0),COALESCE(SUM(output_reserved),0) '
                             'FROM requests WHERE day=? AND kind=?', (day, kind)).fetchone()
            if (row[0] >= self.limits['requests_per_day'][kind]
                    or row[1] + reserve > self.limits['input_tokens_per_day'][kind]
                    or (kind == 'chat' and row[2] + output > self.limits['chat_output_tokens_per_day'])):
                raise DailyError('daily_limit_reached')
            count = db.execute('SELECT COUNT(*) FROM requests WHERE operation=? AND kind=?', (scope['id'], kind)).fetchone()[0]
            if count >= self.limits['requests_per_operation'][kind]:
                raise DailyError('daily_input_limit')
            if db.execute("SELECT COUNT(*) FROM requests WHERE status='pending'").fetchone()[0] >= self.limits['max_parallel_requests']:
                raise DailyError('daily_busy')
            cur = db.execute('INSERT INTO requests(day,operation,kind,input_reserved,output_reserved,metadata) '
                             'VALUES(?,?,?,?,?,?)', (day, scope['id'], kind, reserve, output, json.dumps(meta)))
            return cur.lastrowid, kind

    def halt(self, reason):
        # Error categories are fixed constants, never provider messages or bodies.
        with self.connect() as db:
            db.execute('UPDATE control SET paused=? WHERE id=1', (reason,))
        raise DailyError('daily_provider_error')

    def fail(self, index, reason):
        with self.connect() as db:
            db.execute("UPDATE requests SET status='error',metadata=? WHERE id=? AND status='pending'",
                       (json.dumps({'error_category': reason}), index))
        self.halt(reason)

    def status_code(self, index, code):
        with self.connect() as db:
            db.execute('UPDATE requests SET http_status=? WHERE id=?', (code, index))

    def finish(self, index, meta):
        with self.connect() as db:
            row = db.execute('SELECT * FROM requests WHERE id=?', (index,)).fetchone()
            if row['status'] != 'pending':
                raise DailyError('daily_paused')
            data = {**json.loads(row['metadata']), **meta}
            db.execute("UPDATE requests SET status='complete',metadata=? WHERE id=?", (json.dumps(data), index))

    def chat_complete(self, index, message, usage, finish, frames):
        inp, out = usage.get('prompt_tokens'), usage.get('completion_tokens')
        with self.connect() as db:
            row = db.execute('SELECT * FROM requests WHERE id=?', (index,)).fetchone()
        if (type(inp) is not int or type(out) is not int or inp < 0 or out < 0
                or inp > row['input_reserved'] or out > row['output_reserved']
                or finish not in ('stop', 'tool_calls')):
            self.fail(index, 'invalid_or_truncated_chat_usage')
        self.finish(index, {'usage': {'input': inp, 'output': out}, 'finish_reason': finish, 'sse_frames': frames,
                           'reasoning_present': isinstance(message.get('reasoning_content'), str),
                           'tool_names': [t['function']['name'] for t in message.get('tool_calls', [])]})

    def json_complete(self, index, body):
        with self.connect() as db:
            event = db.execute('SELECT * FROM requests WHERE id=?', (index,)).fetchone()
        count = json.loads(event['metadata'])['input_count']
        try:
            if event['kind'] == 'embedding':
                rows = body['data']
                assert body.get('model') == MODELS['embedding'] and len(rows) == count
                assert sorted(r['index'] for r in rows) == list(range(count))
                for row in rows:
                    v = row['embedding']
                    assert len(v) == 1024 and any(v) and all(type(x) in (int, float) and math.isfinite(x) for x in v)
                meta = {'dimensions': [1024] * count}
            else:
                rows = body['results']; ids = [r['index'] for r in rows]; scores = [r['relevance_score'] for r in rows]
                assert rows and len(rows) <= count and len(set(ids)) == len(ids)
                assert all(type(i) is int and 0 <= i < count for i in ids)
                assert all(type(x) in (int, float) and math.isfinite(x) for x in scores) and scores == sorted(scores, reverse=True)
                meta = {'results': [{'index': i, 'relevance_score': score} for i, score in zip(ids, scores)]}
            usage = body.get('usage') or ((body.get('meta') or {}).get('tokens')) or {}
            numeric = {k: v for k, v in usage.items() if type(v) is int and v >= 0}
            measured = numeric.get('total_tokens', numeric.get('input_tokens', numeric.get('prompt_tokens', 0)))
            if measured > event['input_reserved']:
                raise ValueError('BGE usage exceeds reservation')
            meta['usage'] = numeric
        except Exception:
            self.fail(index, 'invalid_bge_response')
        self.finish(index, meta)

    def resume(self):
        # Explicit operator action: retain every request/reservation/error.
        with self.connect() as db:
            db.execute("UPDATE requests SET status='uncertain' WHERE status='pending'")
            db.execute('UPDATE control SET paused=NULL WHERE id=1')


class Stream(httpx.AsyncByteStream):
    def __init__(self, response, ledger, index):
        self.response, self.ledger, self.index = response, ledger, index
        self.inspector = SSEInspector(ledger, index)

    async def __aiter__(self):
        try:
            async for data in self.response.aiter_bytes():
                self.inspector.feed(data)
                yield data
            self.inspector.close()
        except BaseException:
            if not self.inspector.done:
                self.ledger.fail(self.index, 'stream_interrupted')
            raise

    async def aclose(self):
        await self.response.aclose()
        if not self.inspector.done:
            self.ledger.fail(self.index, 'stream_closed_early')


class Transport(httpx.AsyncBaseTransport):
    def __init__(self, ledger, keys, send=None):
        self.ledger, self.keys = ledger, keys
        self.inner = httpx.AsyncHTTPTransport(retries=0, trust_env=False)
        self.send = send or ORIGINAL_SEND

    async def handle_async_request(self, request):
        index, kind = self.ledger.reserve(request)
        try:
            request.headers['Authorization'] = 'Bearer ' + self.keys[kind]
            response = await self.send(self.inner, request)
            self.ledger.status_code(index, response.status_code)
            if response.status_code != 200:
                await response.aread()
                await response.aclose()
                self.ledger.fail(index, 'provider_http_' + str(response.status_code))
            if kind == 'chat':
                if 'text/event-stream' not in response.headers.get('content-type', ''):
                    self.ledger.fail(index, 'chat_response_not_sse')
                headers = {k:v for k,v in response.headers.items() if k not in ('content-encoding', 'content-length')}
                return httpx.Response(200, headers=headers, stream=Stream(response, self.ledger, index))
            await response.aread()
            self.ledger.json_complete(index, response.json())
            return response
        except DailyError:
            raise
        except BaseException:
            self.ledger.fail(index, 'transport_or_response_failure')

    async def aclose(self):
        await self.inner.aclose()
