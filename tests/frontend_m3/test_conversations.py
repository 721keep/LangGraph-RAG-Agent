"""Original frontend Chat API, local HTTP streaming, actual Streamlit AppTest."""
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.request

sys.path[:0] = ['/workspace/frontend/gui', '/m3']
from streamlit.testing.v1 import AppTest
import conversation_events as events
import conversation_session as session
from scenarios import scenario, SOURCE1, SOURCE2

KB = '20000000-0000-0000-0000-000000000001'
OLD = '30000000-0000-0000-0000-000000000001'


def control(**modes):
    urllib.request.urlopen(urllib.request.Request('http://m3-api:8000/m2/control', data=json.dumps(modes).encode())).close()


def metrics():
    return json.load(urllib.request.urlopen('http://m3-api:8000/m1/metrics'))['requests']


def chats():
    return sum(v for k, v in metrics().items() if k.startswith('POST /api/v1/chat'))


def deletes():
    return sum(v for k, v in metrics().items() if k.startswith('DELETE /api/v1/threads/'))


def text(at):
    return '\n'.join(str(e.value) for kind in ('markdown', 'caption', 'error', 'info', 'warning') for e in getattr(at, kind))


def clean(at):
    assert not at.exception, [e.message for e in at.exception]
    return at


def start(login=True):
    at = clean(AppTest.from_file('/m3/preview.py', default_timeout=40).run())
    if login:
        at.button(key='ui_login').click().run()
        at.text_input[0].set_value('demo@example.org')
        at.text_input[1].set_value('workspace-demo')
        at.button(key='FormSubmitter:login_form-Login').click().run()
        choose(at, KB)
    return clean(at)


def choose(at, kb):
    next(s for s in at.selectbox if str(s.key).startswith('knowledge_base_selector_')).set_value(kb).run()
    return clean(at)


def ask(at, prompt):
    at.chat_input[0].set_value(prompt).run()
    return clean(at)


def latest(at):
    return list(at.session_state['conversation_workspace']['receipts'].values())[-1]


def collect(prompt):
    result = events.new_turn()
    for event in scenario(prompt)[0]:
        if isinstance(event, dict):
            events.ingest(result, event)
    return events.finish(result)


class EventContract(unittest.TestCase):
    def test_01_sources_are_exactly_returned_metadata(self):
        turn = collect('[RAG]')
        self.assertEqual(events.sources(turn['tools'][0]), [SOURCE1, SOURCE2])
        self.assertEqual(events.citation_issues(turn), {'unresolved': [], 'ambiguous': []})

    def test_02_missing_fields_are_not_filled_with_scores_or_gate(self):
        tool = collect('[MISSING]')['tools'][0]
        self.assertNotIn('evidence_gate_passed', tool['data'])
        self.assertNotIn('page', events.sources(tool)[0])
        self.assertIsNone(events.number(events.sources(tool)[0].get('relevance_score')))

    def test_03_refusal_and_technical_failure_stay_distinct(self):
        self.assertEqual(collect('[NO_EVIDENCE]')['tools'][0]['status'], 'no_evidence')
        self.assertEqual(collect('[TOOL_ERROR]')['tools'][0]['status'], 'error')
        self.assertEqual(events.sources(collect('[NO_EVIDENCE]')['tools'][0]), [])

    def test_04_multi_tool_results_and_private_fields(self):
        turn = collect('[MULTI]')
        self.assertEqual([t['kind'] for t in turn['tools']], ['rag', 'web', 'mcp', 'mcp'])
        self.assertEqual([t['status'] for t in turn['tools']], ['complete', 'received', 'complete', 'error'])
        self.assertNotIn('M3_HIDDEN_REASONING', json.dumps(turn))
        self.assertNotIn('sk-M3_FAKE_SECRET', json.dumps(turn))

    def test_05_parallel_same_name_without_ids_is_not_guessed(self):
        turn = collect('[PARALLEL]')
        self.assertEqual(len(turn['tools']), 4)
        self.assertEqual([t['status'] for t in turn['tools'][:2]], ['unconfirmed', 'unconfirmed'])
        self.assertEqual(events.citation_issues(turn)['ambiguous'], ['[Source 1]'])
        self.assertNotIn('args', turn['tools'][2])

    def test_06_explicit_ids_support_out_of_order_results(self):
        turn = events.new_turn()
        for ident in ['one', 'two']:
            events.ingest(turn, {'type': 'tool_call', 'name': 'custom', 'args': {'id': ident}, 'tool_call_id': ident})
        events.ingest(turn, {'type': 'tool_result', 'name': 'custom', 'content': '{}', 'tool_call_id': 'two'})
        self.assertEqual(turn['tools'][0]['status'], 'running')
        self.assertEqual(turn['tools'][1]['status'], 'received')

    def test_07_unresolved_citation_does_not_create_a_source(self):
        turn = collect('[CITATION_MISSING]')
        self.assertEqual(events.citation_issues(turn)['unresolved'], ['[Source 99]'])
        self.assertNotIn('[Source 99]', [events.source_label(s) for s in events.sources(turn['tools'][0])])

    def test_08_unknown_reasoning_events_are_not_retained(self):
        turn = events.new_turn()
        self.assertFalse(events.ingest(turn, {'type': 'reasoning_content', 'content': 'M3_HIDDEN_REASONING'}))
        self.assertFalse(events.ingest(turn, ['bad']))
        self.assertNotIn('M3_HIDDEN_REASONING', json.dumps(turn))

    def test_09_numbers_are_not_normalized_or_fabricated(self):
        for value in [None, True, 'nan', 'inf', {}, []]:
            self.assertIsNone(events.number(value))
        self.assertEqual(events.number(2.7), 2.7)
        self.assertEqual(events.number(-.3), -.3)

    def test_10_presentation_cache_requires_exact_history_and_owner(self):
        row = [{'role': 'human', 'content': 'Q'}, {'role': 'ai', 'content': 'A'}]
        self.assertNotEqual(events.history_key(row), events.history_key(row + [{'role': 'human', 'content': 'Q'}]))
        state = {'user': SimpleNamespace(access_token='first')}
        with patch('streamlit.session_state', state):
            session.store()['presentations']['x'] = {'answer': 'private fixture'}
            state['user'] = SimpleNamespace(access_token='second')
            self.assertEqual(session.store()['presentations'], {})


class ConversationUI(unittest.TestCase):
    def setUp(self):
        control()

    def tearDown(self):
        control()

    def test_11_normal_stream_sources_copy_and_rerun_single_send(self):
        at = start()
        count = chats()
        ask(at, '[RAG] 测试模型与维度')
        self.assertEqual(chats() - count, 1)
        self.assertIn('BGE-M3', text(at))
        self.assertIn('Vector score: 0.8800', text(at))
        self.assertIn('Reranker score: 0.9600', text(at))
        self.assertTrue(any('architecture-notes.docx' in e.label for e in at.expander))
        self.assertEqual([m['role'] for m in at.session_state['thread'].messages], ['human', 'ai'])
        self.assertFalse(latest(at)['needs_reconcile'])
        at.button(key='ui_theme_toggle').click().run()
        at.run()
        self.assertEqual(chats() - count, 1)
        self.assertIn('Page: 1 · Chunk: 0', text(at))

    def test_12_multi_tools_show_success_failure_without_reasoning(self):
        at = ask(start(), '[MULTI] 工作区检查')
        self.assertEqual(len(latest(at)['turn']['tools']), 4)
        self.assertIn('get_server_status', text(at))
        self.assertIn('lookup_inventory', text(at))
        self.assertIn('MCP 服务不可用', text(at))
        self.assertIn('Server: mock-mcp', text(at))
        self.assertNotIn('M3_HIDDEN_REASONING', text(at))

    def test_13_no_evidence_is_not_a_network_error(self):
        at = ask(start(), '[NO_EVIDENCE] 未提供的主题')
        self.assertIn('No reliable evidence', text(at))
        self.assertIn('Evidence Gate · REJECT', text(at))
        self.assertFalse(at.error)
        self.assertFalse(any('workspace-guide.pdf' in e.label for e in at.expander))

    def test_14_rag_technical_error_is_separate(self):
        at = ask(start(), '[TOOL_ERROR]')
        self.assertIn('RAG retrieval failed', text(at))
        self.assertEqual(latest(at)['turn']['tools'][0]['status'], 'error')

    def test_15_http_failure_retains_thread_and_blocks_automatic_resend(self):
        at = start()
        count, deleted = chats(), deletes()
        ask(at, '[ERROR]')
        tid = at.session_state['thread'].id
        self.assertIsNotNone(tid)
        self.assertTrue(at.chat_input[0].disabled)
        self.assertTrue(at.error)
        at.button(key='ui_theme_toggle').click().run()
        at.run()
        self.assertEqual(chats() - count, 1)
        self.assertEqual(deletes(), deleted)
        self.assertEqual(at.session_state['thread'].id, tid)

    def test_16_interrupted_wire_stream_preserves_partial_and_explicit_recovery(self):
        at = start()
        count = chats()
        ask(at, '[INTERRUPT]')
        receipt = latest(at)
        self.assertTrue(receipt['turn']['answer'])
        self.assertEqual(receipt['turn']['delivery'], 'interrupted')
        self.assertEqual(len(at.session_state['thread'].messages), 1)
        at.button(key='chat_refresh_history').click().run()
        clean(at)
        self.assertTrue(at.chat_input[0].disabled)
        self.assertTrue(latest(at)['snapshot_read'])
        at.checkbox(key='chat_unlock_' + receipt['id']).check().run()
        at.button(key='chat_unlock_button_' + receipt['id']).click().run()
        clean(at)
        self.assertFalse(at.chat_input[0].disabled)
        self.assertEqual(chats() - count, 1)

    def test_17_history_error_does_not_clear_current_messages(self):
        at = ask(start(), '[RAG] 历史保留')
        before = copy.deepcopy(at.session_state['thread'].messages)
        control(history=403)
        at.button(key='chat_refresh_history').click().run()
        clean(at)
        self.assertEqual(at.session_state['thread'].messages, before)
        self.assertIn('当前会话内容未清空', text(at))
        control()
        at.button(key='chat_refresh_history').click().run()
        self.assertTrue(any('workspace-guide.pdf' in e.label for e in at.expander))

    def test_18_thread_switch_restores_only_matching_cached_sources(self):
        at = ask(start(), '[RAG] 可恢复来源')
        tid = str(at.session_state['thread'].id)
        at.button(key='select_thread_' + OLD).click().run()
        clean(at)
        self.assertIn('历史接口只提供正文', text(at))
        self.assertFalse(any('workspace-guide.pdf' in e.label for e in at.expander))
        at.button(key='select_thread_' + tid).click().run()
        clean(at)
        self.assertIn('可恢复来源', text(at))
        self.assertTrue(any('workspace-guide.pdf' in e.label for e in at.expander))

    def test_19_binding_denial_does_not_delete_created_thread_or_send_chat(self):
        at = start()
        control(binding=403)
        count, deleted = chats(), deletes()
        ask(at, '绑定未确认')
        self.assertIsNotNone(at.session_state['thread'].id)
        self.assertEqual(chats(), count)
        self.assertEqual(deletes(), deleted)
        self.assertEqual(latest(at)['turn']['delivery'], 'setup_error')

    def test_20_missing_fields_do_not_gain_page_or_scores(self):
        at = ask(start(), '[MISSING]')
        self.assertIn('Evidence Gate · 未返回判定', text(at))
        self.assertNotIn('Vector score:', text(at))
        self.assertNotIn('Reranker score:', text(at))
        self.assertNotIn('Page: 0', text(at))

    def test_21_malformed_and_hidden_events_do_not_leak_payload(self):
        at = ask(start(), '[MALFORMED]')
        self.assertIn('部分流式事件格式无法识别', text(at))
        self.assertNotIn('M3_HIDDEN_REASONING', text(at))
        self.assertIn('BGE-M3', text(at))

    def test_22_unknown_citation_is_flagged_not_filled(self):
        at = ask(start(), '[CITATION_MISSING]')
        self.assertIn('引用缺少对应来源元数据', text(at))
        self.assertFalse(any('[Source 99]' in e.label for e in at.expander))

    def test_23_parallel_associations_stay_explicitly_unconfirmed(self):
        at = ask(start(), '[PARALLEL]')
        self.assertIn('同名调用缺少关联 ID', text(at))
        self.assertIn('未推断唯一归属', text(at))

    def test_24_empty_stream_does_not_invent_a_final_answer(self):
        at = ask(start(), '[EMPTY]')
        self.assertIn('未收到最终回答', text(at))
        self.assertEqual(len(at.session_state['thread'].messages), 1)
        self.assertTrue(at.chat_input[0].disabled)

    def test_25_anonymous_stream_keeps_plain_chat_compatible(self):
        at = ask(start(False), 'Hello')
        self.assertIn('普通对话', text(at))
        self.assertEqual(latest(at)['turn']['tools'], [])
        self.assertIsNone(at.session_state['thread'].id)


if __name__ == '__main__':
    result = unittest.main(verbosity=2, exit=False).result
    Path('/m3-artifacts/conversation_tests.json').write_text(json.dumps({'tests': result.testsRun, 'passed': result.wasSuccessful(),
        'failed_cases': [t.id() for t, _ in result.failures + result.errors]}, indent=2))
    raise SystemExit(0 if result.wasSuccessful() else 1)
