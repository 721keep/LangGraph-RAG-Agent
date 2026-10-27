"""Persistent limits at the HTTP transport boundary; no prompt/CoT in the ledger."""
import copy
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path

MODEL = 'deepseek-flash'
MAX_REQUESTS = 6
MAX_INPUT = 8192
MAX_OUTPUT = 2048
MAX_TOTAL_INPUT = 49152
MAX_TOTAL_OUTPUT = 12288
SAFETY_TOKENS = 1024


class StopRun(RuntimeError):
    pass


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def signature(message):
    calls = []
    for call in message.get('tool_calls') or []:
        function = call['function']
        calls.append({'id': call['id'], 'name': function['name'], 'arguments': json.loads(function['arguments'])})
    return digest(json.dumps({'content': message.get('content') or '', 'calls': calls}, sort_keys=True, ensure_ascii=False))


class OfficialCounter:
    def __init__(self, directory):
        directory = Path(directory)
        manifest = json.loads((directory / 'manifest.json').read_text())
        for name, metadata in manifest['files'].items():
            if hashlib.sha256((directory / name).read_bytes()).hexdigest() != metadata['sha256']:
                raise StopRun('official_tokenizer_integrity_failed')
        from tokenizers import Tokenizer
        self.tokenizer = Tokenizer.from_file(str(directory / 'tokenizer.json'))
        spec = importlib.util.spec_from_file_location('release_official_encoding', directory / 'encoding/encoding.py')
        self.encoding = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.encoding)
        self.manifest = manifest

    def count(self, body):
        messages = copy.deepcopy(body['messages'])
        if not messages or messages[0]['role'] != 'system':
            raise StopRun('system_message_missing')
        if not all(isinstance(m.get('content'), (str, type(None))) for m in messages):
            raise StopRun('non_text_input_forbidden')
        messages[0]['tools'] = copy.deepcopy(body.get('tools') or [])
        mode = 'thinking' if body['thinking']['type'] == 'enabled' else 'chat'
        rendered = self.encoding.encode_messages(messages, thinking_mode=mode, drop_thinking=False,
                                                reasoning_effort=body.get('reasoning_effort'))
        count = len(self.tokenizer.encode(rendered, add_special_tokens=False).ids)
        # Account conservatively for possible provider wrapper differences as well.
        return count, count + SAFETY_TOKENS


class Ledger:
    def __init__(self, path, counter, create=False):
        self.path = Path(path)
        self.counter = counter
        if create:
            with self.path.open('x') as file:
                json.dump({'mode': 'blocked', 'requests': 0, 'input_reserved': 0, 'output_reserved': 0,
                           'actual_input': 0, 'actual_output': 0, 'events': [], 'in_flight': False}, file)
                file.flush(); os.fsync(file.fileno())
        elif not self.path.is_file():
            raise StopRun('ledger_missing')

    def update(self, callback):
        with self.path.open('r+') as file:
            fcntl.flock(file, fcntl.LOCK_EX)
            state = json.load(file)
            try:
                result = callback(state)
            finally:
                file.seek(0); json.dump(state, file, ensure_ascii=False); file.truncate()
                file.flush(); os.fsync(file.fileno()); fcntl.flock(file, fcntl.LOCK_UN)
            return result

    def read(self):
        return self.update(lambda s: copy.deepcopy(s))

    def halt(self, reason):
        self.update(lambda s: s.update(mode='halted', stop_reason=reason, in_flight=False))
        raise StopRun(reason)

    def arm(self):
        def change(s):
            if s['mode'] != 'blocked' or s['requests'] or s['in_flight']:
                raise StopRun('refusing_ledger_rearm')
            s['mode'] = 'armed'
        self.update(change)

    def seal(self):
        self.update(lambda s: s.update(mode='sealed') if s['mode'] != 'halted' else None)
