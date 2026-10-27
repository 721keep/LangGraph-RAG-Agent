"""Wire-level Mock NDJSON streaming, including a genuinely broken chunked response."""
import json
import time
from scenarios import scenario


def serve(handler, tid, data, anonymous, history, threads, counts):
    prompt = data.get('prompt', '')
    if '[ERROR]' in prompt:
        return handler.response(503, {'detail': 'Mock chat unavailable'})
    rows = history.setdefault(tid, [])
    rows.append({'role': 'human', 'content': prompt})
    kb = bool(threads.get(tid, {}).get('knowledge_base_id'))
    events, answer = scenario(prompt, kb, anonymous)
    broken = '[INTERRUPT]' in prompt
    handler.send_response(200)
    handler.send_header('Content-Type', 'application/x-ndjson; charset=utf-8')
    handler.send_header('Connection', 'close')
    if broken:
        handler.send_header('Transfer-Encoding', 'chunked')
    handler.end_headers()
    handler.close_connection = True
    try:
        for event in events:
            line = ((event if isinstance(event, str) else json.dumps(event, ensure_ascii=False)) + '\n').encode()
            handler.wfile.write((f'{len(line):X}\r\n'.encode() + line + b'\r\n') if broken else line)
            handler.wfile.flush()
            time.sleep(.06)
    except (BrokenPipeError, ConnectionResetError):
        counts['stream_interrupted'] += 1
        return
    if broken:
        counts['mock_broken_streams'] += 1
        return  # Deliberately omit the terminal HTTP chunk; httpx must raise.
    if answer:
        rows.append({'role': 'ai', 'content': answer})
