"""Real Streamlit widgets and original HTTP helpers against the local M2 Mock."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4
import urllib.request

sys.path.insert(0, '/workspace/frontend/gui')
from streamlit.testing.v1 import AppTest
import api_utils
import requests

KB = '20000000-0000-0000-0000-000000000001'
KB2 = '20000000-0000-0000-0000-000000000002'
PROTECTED = '20000000-0000-0000-0000-000000000003'
DOC = '40000000-0000-0000-0000-000000000001'


def control(**modes):
    data = json.dumps(modes).encode()
    return json.load(urllib.request.urlopen(urllib.request.Request('http://m2-api:8000/m2/control', data=data)))


def metrics():
    return json.load(urllib.request.urlopen('http://m2-api:8000/m1/metrics'))['requests']


def uploads():
    return sum(v for k, v in metrics().items() if k.startswith('POST ') and k.endswith('/upload'))


def text(at):
    return '\n'.join(str(e.value) for kind in ('markdown', 'caption', 'error', 'warning', 'success') for e in getattr(at, kind))


def clean(at):
    assert not at.exception, [e.message for e in at.exception]
    return at


def start(file=False):
    script = '''
from types import SimpleNamespace
from unittest.mock import patch
import runpy
import streamlit as st
file = SimpleNamespace(name=st.session_state.get('m2_test_file', 'm2-ready.pdf'),
                       type='application/pdf', getvalue=lambda: b'%PDF-M2-Mock')
with patch('streamlit.file_uploader', return_value=file):
    runpy.run_path('/m2/preview.py')
'''
    at = (AppTest.from_string(script, default_timeout=30) if file
          else AppTest.from_file('/m2/preview.py', default_timeout=30)).run()
    clean(at)
    at.button(key='ui_login').click().run()
    at.text_input[0].set_value('demo@example.org')
    at.text_input[1].set_value('workspace-demo')
    at.button(key='FormSubmitter:login_form-Login').click().run()
    return clean(at)


def nav(at, view):
    at.button(key='ui_nav_' + view).click().run()
    return clean(at)


def choose(at, key):
    next(s for s in at.selectbox if str(s.key).startswith('knowledge_base_selector_')).set_value(key).run()
    return clean(at)


def create(at):
    nav(at, 'knowledge')
    name = 'M2 fixture ' + uuid4().hex[:8]
    at.text_input(key='new_knowledge_base_name').set_value(name)
    at.button(key='create_knowledge_base_button').click().run()
    clean(at)
    return next(k['id'] for k in at.session_state['user'].knowledge_bases if k['name'] == name)


class ResourcePages(unittest.TestCase):
    def setUp(self):
        control()

    def tearDown(self):
        control()

    def test_01_kb_cards_and_real_document_counts(self):
        at = nav(start(), 'knowledge')
        self.assertIn('Document count not loaded', text(at))
        at.button(key='m2_count_' + KB2).click().run()
        clean(at)
        self.assertIn('2 documents', text(at))
        self.assertIn('数量待加载', text(at))

    def test_02_create_validation_and_success(self):
        at = nav(start(), 'knowledge')
        at.button(key='create_knowledge_base_button').click().run()
        self.assertIn('cannot be empty', text(at))
        kb = create(at)
        self.assertTrue(any(k['id'] == kb for k in at.session_state['user'].knowledge_bases))
        self.assertIn('created successfully', text(at))

    def test_03_card_selection_and_view_documents(self):
        at = nav(start(), 'knowledge')
        at.button(key='m2_use_kb_' + KB).click().run()
        clean(at)
        self.assertEqual(str(at.session_state['thread'].knowledge_base_id), KB)
        self.assertTrue(at.button(key='m2_use_kb_' + KB).disabled)
        at.button(key='m2_view_docs_' + KB2).click().run()
        clean(at)
        self.assertEqual(at.session_state['ui_view'], 'documents')
        self.assertEqual(str(at.session_state['thread'].knowledge_base_id), KB2)
        self.assertIn('Processing / 处理中', text(at))
        self.assertIn('PDF 解析失败', text(at))
        self.assertNotIn('workspace-guide.pdf', text(at))

    def test_04_kb_confirmed_delete_refreshes_selection(self):
        at = start()
        kb = create(at)
        at.button(key='m2_use_kb_' + kb).click().run()
        self.assertTrue(at.button(key='delete_knowledge_base_' + kb).disabled)
        at.checkbox(key='confirm_delete_kb_' + kb).check().run()
        at.button(key='delete_knowledge_base_' + kb).click().run()
        clean(at)
        self.assertNotIn(kb, [k['id'] for k in at.session_state['user'].knowledge_bases])
        self.assertIsNone(at.session_state['thread'].knowledge_base_id)
        self.assertIn('Knowledge base deleted.', text(at))

    def test_05_kb_403_preserves_safe_specific_reason(self):
        at = nav(start(), 'knowledge')
        at.checkbox(key='confirm_delete_kb_' + PROTECTED).check().run()
        at.button(key='delete_knowledge_base_' + PROTECTED).click().run()
        clean(at)
        self.assertIn('HTTP 403', text(at))
        self.assertIn('DELETE disabled', text(at))
        self.assertIn(PROTECTED, [k['id'] for k in at.session_state['user'].knowledge_bases])

    def test_06_kb_refresh_error_keeps_last_snapshot(self):
        at = nav(start(), 'knowledge')
        before = list(at.session_state['user'].knowledge_bases)
        control(kb_list=500)
        at.button(key='m2_refresh_kbs').click().run()
        clean(at)
        self.assertEqual(at.session_state['user'].knowledge_bases, before)
        self.assertIn('HTTP 500', text(at))
        self.assertNotIn('sk-mock-secret', text(at))
        self.assertNotIn('postgresql://', text(at))

    def test_07_document_refresh_failure_is_not_empty(self):
        at = nav(choose(start(), KB), 'documents')
        before = list(at.session_state['thread'].documents)
        control(doc_list=403)
        at.button(key='refresh_documents_' + KB).click().run()
        clean(at)
        self.assertEqual(at.session_state['thread'].documents, before)
        self.assertIn('上次成功加载的快照', text(at))
        self.assertIn('workspace-guide.pdf', text(at))
        self.assertNotIn('sk-mock-secret', text(at))

    def test_08_ready_upload_not_repeated_on_theme_or_rerun(self):
        at = start(True)
        kb = create(at)
        choose(at, kb)
        nav(at, 'documents')
        before = uploads()
        at.button(key='upload_button_' + kb).click().run()
        clean(at)
        self.assertEqual(at.session_state['thread'].documents[0]['status'], 'ready')
        self.assertIn('Ready / 就绪', text(at))
        at.button(key='ui_theme_toggle').click().run()
        at.run()
        self.assertEqual(uploads() - before, 1)
        self.assertTrue(at.button(key='upload_button_' + kb).disabled)
        self.assertEqual(str(at.session_state['thread'].knowledge_base_id), kb)

    def test_09_unknown_processing_refresh_never_reuploads(self):
        at = start(True)
        kb = create(at)
        choose(at, kb)
        at.session_state['m2_test_file'] = 'm2-processing.pdf'
        nav(at, 'documents')
        before = uploads()
        at.button(key='upload_button_' + kb).click().run()
        clean(at)
        self.assertIn('待确认', text(at))
        self.assertIn('Processing / 处理中', text(at))
        doc = at.session_state['thread'].documents[0]
        self.assertTrue(at.button(key='delete_document_' + doc['id']).disabled)
        at.button(key='ui_theme_toggle').click().run()
        at.button(key='refresh_documents_' + kb).click().run()
        self.assertEqual(uploads() - before, 1)
        self.assertTrue(at.button(key='upload_button_' + kb).disabled)

    def test_10_unknown_without_id_stays_locked_after_empty_refresh(self):
        at = start(True)
        kb = create(at)
        choose(at, kb)
        at.session_state['m2_test_file'] = 'm2-uncertain.pdf'
        nav(at, 'documents')
        before = uploads()
        at.button(key='upload_button_' + kb).click().run()
        at.button(key='refresh_documents_' + kb).click().run()
        at.button(key='ui_theme_toggle').click().run()
        clean(at)
        self.assertEqual(at.session_state['thread'].documents, [])
        self.assertTrue(at.button(key='upload_button_' + kb).disabled)
        self.assertEqual(uploads() - before, 1)
        self.assertIn('未自动重试', text(at))

    def test_11_failed_upload_reason_and_explicit_retry(self):
        at = start(True)
        kb = create(at)
        choose(at, kb)
        at.session_state['m2_test_file'] = 'm2-failed.pdf'
        nav(at, 'documents')
        before = uploads()
        at.button(key='upload_button_' + kb).click().run()
        at.run()
        clean(at)
        self.assertIn('PDF 解析失败', text(at))
        self.assertTrue(at.button(key='upload_button_' + kb).disabled)
        retry = next(c for c in at.checkbox if str(c.key).startswith('m2_retry_'))
        retry.check().run()
        self.assertFalse(at.button(key='upload_button_' + kb).disabled)

        self.assertEqual(uploads() - before, 1)  # Checking permission is not submission.
        at.button(key='upload_button_' + kb).click().run()
        at.run()
        self.assertEqual(uploads() - before, 2)
        self.assertTrue(at.button(key='upload_button_' + kb).disabled)
        self.assertFalse(next(c for c in at.checkbox if str(c.key).startswith('m2_retry_')).value)

    def test_12_document_delete_403_is_never_success(self):
        at = nav(choose(start(), KB), 'documents')
        control(delete_doc=403, detail='F1.5 preserves existing test data; DELETE disabled.')
        at.checkbox(key='confirm_delete_document_' + DOC).check().run()
        at.button(key='delete_document_' + DOC).click().run()
        clean(at)
        self.assertIn('DELETE disabled', text(at))
        self.assertIn(DOC, [d['id'] for d in at.session_state['thread'].documents])
        self.assertNotIn('Document deleted successfully', text(at))

    def test_13_document_delete_keeps_sibling_and_refreshes_server_list(self):
        at = start(True)
        kb = create(at)
        choose(at, kb)
        nav(at, 'documents')
        at.button(key='upload_button_' + kb).click().run()
        doc = at.session_state['thread'].documents[0]
        at.session_state['m2_test_file'] = 'sibling-ready.txt'
        at.run()
        at.button(key='upload_button_' + kb).click().run()
        self.assertEqual(len(at.session_state['thread'].documents), 2)
        self.assertTrue(at.button(key='delete_document_' + doc['id']).disabled)
        at.checkbox(key='confirm_delete_document_' + doc['id']).check().run()
        at.button(key='delete_document_' + doc['id']).click().run()
        clean(at)
        self.assertEqual([d['file_name'] for d in at.session_state['thread'].documents], ['sibling-ready.txt'])
        self.assertIn('Document deleted successfully', text(at))
        # AppTest retains a removed popover checkbox in its delta tree after
        # deletion. Check the confirmed receipt here; Chrome covers the next
        # actual file/KB interaction without substituting its widget lifecycle.
        self.assertFalse(any(r.get('document_id') == doc['id']
                             for r in at.session_state['resource_workspace']['receipts'].values()))

    def test_14_delete_ack_with_refresh_error_is_uncertain_list(self):
        at = start(True)
        kb = create(at)
        choose(at, kb)
        nav(at, 'documents')
        at.button(key='upload_button_' + kb).click().run()
        doc = at.session_state['thread'].documents[0]
        control(doc_list=500)
        at.checkbox(key='confirm_delete_document_' + doc['id']).check().run()
        at.button(key='delete_document_' + doc['id']).click().run()
        clean(at)
        self.assertIn('删除已获服务器确认，但列表刷新失败', text(at))
        self.assertNotIn('Document deleted successfully', text(at))
        control()
        at.button(key='refresh_documents_' + kb).click().run()
        self.assertEqual(at.session_state['thread'].documents, [])
        self.assertNotIn('列表刷新失败', text(at))

    def test_15_document_filters_and_empty_states(self):
        at = nav(start(), 'documents')
        self.assertIn('Choose a knowledge base', text(at))
        choose(at, KB2)
        at.selectbox(key='m2_doc_status_' + KB2).set_value('failed').run()
        self.assertIn('sample-failed.pdf', text(at))
        self.assertNotIn('架构草案.pdf', text(at))
        at.text_input(key='m2_doc_search_' + KB2).set_value('not-a-file').run()
        self.assertIn('No matching documents', text(at))

    def test_16_creation_permission_and_validation_errors_are_safe(self):
        at = nav(start(), 'knowledge')
        at.text_input(key='new_knowledge_base_name').set_value('Denied ' + uuid4().hex[:8])
        control(create=403)
        at.button(key='create_knowledge_base_button').click().run()
        clean(at)
        self.assertIn('HTTP 403', text(at))
        self.assertNotIn('sk-mock-secret', text(at))

    def test_19_empty_kb_list_is_distinct_from_load_failure(self):
        at = nav(start(), 'knowledge')
        control(empty_kbs=True)
        at.button(key='m2_refresh_kbs').click().run()
        clean(at)
        self.assertIn('Your knowledge starts here', text(at))
        self.assertNotIn('Knowledge list unavailable', text(at))
        self.assertTrue(any(i.key == 'new_knowledge_base_name' for i in at.text_input))

    def test_20_binding_403_keeps_original_thread_and_kb(self):
        at = start()
        tid = '30000000-0000-0000-0000-000000000001'
        at.button(key='select_thread_' + tid).click().run()
        nav(at, 'knowledge')
        before = list(at.session_state['thread'].messages)
        control(binding=403, detail='You do not have permission to access this knowledge base.')
        at.button(key='m2_use_kb_' + KB2).click().run()
        clean(at)
        self.assertEqual(str(at.session_state['thread'].knowledge_base_id), KB)
        self.assertEqual(at.session_state['thread'].messages, before)
        self.assertIn('没有权限访问此知识库', text(at))

    def test_21_processing_receipt_reconciles_to_ready_from_server(self):
        at = start(True)
        kb = create(at)
        choose(at, kb)
        at.session_state['m2_test_file'] = 'm2-processing.pdf'
        nav(at, 'documents')
        before = uploads()
        at.button(key='upload_button_' + kb).click().run()
        doc = at.session_state['thread'].documents[0]
        control(ready_id=doc['id'])
        at.button(key='refresh_documents_' + kb).click().run()
        clean(at)
        self.assertEqual(at.session_state['thread'].documents[0]['status'], 'ready')
        self.assertIn('服务端已确认文档就绪', text(at))
        self.assertTrue(at.button(key='upload_button_' + kb).disabled)
        self.assertEqual(uploads() - before, 1)

    def test_22_kb_search_and_empty_search_result(self):
        at = nav(start(), 'knowledge')
        at.text_input(key='m2_kb_search').set_value('Protected workspace').run()
        clean(at)
        self.assertTrue(any(b.key == 'm2_view_docs_' + PROTECTED for b in at.button))
        self.assertFalse(any(b.key == 'm2_view_docs_' + KB for b in at.button))
        at.text_input(key='m2_kb_search').set_value('no-such-kb-' + uuid4().hex).run()
        self.assertIn('No matching knowledge bases', text(at))


class SafeHelpers(unittest.TestCase):
    def test_23_missing_resource_id_never_claims_create_or_binding_success(self):
        response = requests.Response()
        response.status_code = 200
        response._content = b'{}'
        with patch('streamlit.session_state', {'user': SimpleNamespace(access_token='mock')}), patch('requests.post', return_value=response), patch('requests.patch', return_value=response):
            self.assertEqual(api_utils.create_knowledge_base('Example'), {})
            self.assertFalse(api_utils.create_knowledge_base('Example', detailed=True)['ok'])
            self.assertEqual(api_utils.set_thread_knowledge_base('thread', KB), {})
            self.assertFalse(api_utils.set_thread_knowledge_base('thread', KB, detailed=True)['ok'])

    def test_17_delete_default_is_boolean_false_for_all_http_failures(self):
        for status in (401, 403, 404, 500):
            with self.subTest(status=status):
                response = requests.Response()
                response.status_code = status
                response._content = b'{"detail":"sk-mock-secret"}'
                with patch('streamlit.session_state', {'user': SimpleNamespace(access_token='mock')}), patch('requests.delete', return_value=response):
                    self.assertIs(api_utils.delete_document(DOC), False)
                    self.assertIs(api_utils.delete_knowledge_base(KB), False)
                    self.assertNotIn('sk-mock-secret', str(api_utils.delete_document(DOC, detailed=True)))

    def test_18_timeout_network_errors_have_no_secret_payload_or_retry(self):
        for exception in (requests.ReadTimeout('sk-mock-secret'), requests.ConnectionError('postgresql://mock:secret@host')):
            with self.subTest(exception=type(exception).__name__):
                with patch('streamlit.session_state', {'user': SimpleNamespace(access_token='mock')}), patch('requests.get', side_effect=exception) as get:
                    result = api_utils.get_knowledge_bases(detailed=True)
                    self.assertFalse(result['ok'])
                    self.assertIn(result['error_type'], ('timeout', 'network'))
                    self.assertNotIn('sk-mock-secret', str(result))
                    self.assertNotIn('postgresql://', str(result))
                    get.assert_called_once()


if __name__ == '__main__':
    run = unittest.main(verbosity=2, exit=False).result
    Path('/m2-artifacts/resource_tests.json').write_text(json.dumps({'tests': run.testsRun, 'passed': run.wasSuccessful(),
        'failed_cases': [t.id() for t, _ in run.failures + run.errors]}, indent=2))
    raise SystemExit(0 if run.wasSuccessful() else 1)
