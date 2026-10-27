"""Actual Streamlit rendering with local Mock HTTP, no real backend or model."""
import json
from pathlib import Path
import sys
import unittest
from uuid import uuid4
import xml.etree.ElementTree as ET

sys.path.insert(0, '/workspace/frontend/gui')
from streamlit.testing.v1 import AppTest
from design_tokens import THEMES
import ui

KB = '20000000-0000-0000-0000-000000000001'
KB2 = '20000000-0000-0000-0000-000000000002'
THREAD = '30000000-0000-0000-0000-000000000001'
DOC = '40000000-0000-0000-0000-000000000001'
RESULT = []


def values(at, kind):
    return '\n'.join(str(x.value) for x in getattr(at, kind))


def clean(at):
    assert not at.exception, values(at, 'exception')
    return at


def start(login=True):
    at = clean(AppTest.from_file('/m1/preview.py', default_timeout=30).run())
    if login:
        at.button(key='ui_login').click().run()
        at.text_input[0].set_value('demo@example.org')
        at.text_input[1].set_value('workspace-demo')
        at.button(key='FormSubmitter:login_form-Login').click().run()
        clean(at)
        assert at.session_state['user'].is_authenticated
    return at


def choose(at, kb):
    next(s for s in at.selectbox if str(s.key).startswith('knowledge_base_selector_')).set_value(kb).run()
    return clean(at)


class Rendering(unittest.TestCase):
    def tearDown(self):
        Path('/m1-artifacts/render_result.json').write_text(json.dumps(RESULT, ensure_ascii=False, indent=2))

    def test_01_tokens_and_local_svg_contract(self):
        self.assertEqual(set(THEMES['light']), set(THEMES['dark']))
        for name in ['brand', 'trash-2', 'book-open', 'sun', 'moon', 'workflow']:
            svg = ET.fromstring(ui.svg(name))
            self.assertEqual(svg.attrib['stroke'], 'currentColor')
            self.assertNotIn('<script', ui.svg(name))
        self.assertIn('aria-label="Switch theme"', ui.icon('sun', 'Switch theme'))
        root = ET.fromstring(ui.icon('workflow'))
        self.assertEqual(root.attrib['width'], '18')
        self.assertEqual(root.find('{http://www.w3.org/2000/svg}rect').attrib['width'], '8')
        RESULT.append({'test': 'theme token/local SVG contract', 'passed': True})

    def test_02_default_light_login_draft_and_theme(self):
        at = start(False)
        self.assertEqual(at.session_state['ui_theme'], 'light')
        at.button(key='ui_login').click().run()
        at.text_input[0].set_value('draft@example.org')
        at.text_input[1].set_value('pending-password')
        at.button(key='ui_theme_toggle').click().run()
        clean(at)
        self.assertEqual(at.session_state['ui_theme'], 'dark')
        self.assertEqual(at.text_input[0].value, 'draft@example.org')
        self.assertEqual(at.text_input[1].value, 'pending-password')
        RESULT.append({'test': 'light default/login draft/theme fragment', 'passed': True})

    def test_03_authenticated_toolbar_and_history_survive_theme(self):
        at = choose(start(), KB)
        at.button(key='select_thread_' + THREAD).click().run()
        before = list(at.session_state['thread'].messages)
        tid = at.session_state['thread'].id
        at.selectbox(key='model_selector').set_value('preview-model').run()
        at.select_slider(key='top_k').set_value(5).run()
        at.button(key='ui_theme_toggle').click().run()
        clean(at)
        self.assertEqual(at.session_state['thread'].id, tid)
        self.assertEqual(at.session_state['thread'].messages, before)
        self.assertEqual(str(at.session_state['thread'].knowledge_base_id), KB)
        self.assertEqual(at.session_state['model_name'], 'preview-model')
        self.assertEqual(at.session_state['top_k'], 5)
        RESULT.append({'test': 'toolbar/history/model/retrieval state through theme', 'passed': True})

    def test_04_kb_drafts_survive_theme_and_navigation(self):
        at = start()
        at.button(key='ui_nav_knowledge').click().run()
        at.text_input(key='new_knowledge_base_name').set_value('未完成知识库 / draft').run()
        at.text_area(key='new_knowledge_base_description').set_value('保留描述').run()
        at.button(key='ui_theme_toggle').click().run()
        self.assertEqual(at.text_input(key='new_knowledge_base_name').value, '未完成知识库 / draft')
        at.button(key='ui_nav_chat').click().run()
        at.button(key='ui_nav_knowledge').click().run()
        clean(at)
        self.assertEqual(at.text_input(key='new_knowledge_base_name').value, '未完成知识库 / draft')
        self.assertEqual(at.text_area(key='new_knowledge_base_description').value, '保留描述')
        RESULT.append({'test': 'KB drafts/theme/navigation', 'passed': True})

    def test_05_document_states_and_delete_confirmation(self):
        at = choose(start(), KB2)
        at.button(key='ui_nav_documents').click().run()
        clean(at)
        self.assertIn('Status: processing', values(at, 'caption'))
        self.assertIn('Status: failed', values(at, 'caption'))
        self.assertIn('PDF 解析失败', values(at, 'caption'))
        self.assertTrue(at.button(key='delete_document_40000000-0000-0000-0000-000000000002').disabled)
        choose(at, KB)
        self.assertTrue(at.button(key='delete_document_' + DOC).disabled)
        at.checkbox(key='confirm_delete_document_' + DOC).check().run()
        self.assertFalse(at.button(key='delete_document_' + DOC).disabled)
        at.button(key='ui_theme_toggle').click().run()
        self.assertTrue(at.checkbox(key='confirm_delete_document_' + DOC).value)
        RESULT.append({'test': 'document entries/statuses/danger confirmation/theme', 'passed': True})

    def test_06_chat_mock_stream_citation_and_new_chat(self):
        at = choose(start(), KB)
        at.chat_input[0].set_value('请总结工作区指南。').run()
        clean(at)
        messages = at.session_state['thread'].messages
        self.assertEqual([m['role'] for m in messages], ['human', 'ai'])
        self.assertIn('Mock', messages[-1]['content'])
        self.assertIn('[Source 1]', messages[-1]['content'])
        self.assertTrue(any('workspace-guide.pdf' in e.label for e in at.expander))
        self.assertNotIn('reasoning_content', values(at, 'markdown'))
        tid = str(at.session_state['thread'].id)
        at.button(key='ui_new_chat').click().run()
        self.assertIsNone(at.session_state['thread'].id)
        at.button(key='select_thread_' + tid).click().run()
        self.assertEqual(at.session_state['thread'].messages, messages)
        RESULT.append({'test': 'mock streaming/citation/thread recovery', 'passed': True})

    def test_07_kb_creation_and_confirmed_delete_keep_contract(self):
        at = start()
        at.button(key='ui_nav_knowledge').click().run()
        at.text_input(key='new_knowledge_base_name').set_value('M1 disposable KB')
        at.button(key='create_knowledge_base_button').click().run()
        clean(at)
        kb = next(k for k in at.session_state['user'].knowledge_bases if k['name'] == 'M1 disposable KB')['id']
        choose(at, kb)
        self.assertTrue(at.button(key='delete_knowledge_base_' + kb).disabled)
        at.checkbox(key='confirm_delete_kb_' + kb).check().run()
        at.button(key='delete_knowledge_base_' + kb).click().run()
        clean(at)
        self.assertNotIn(kb, [k['id'] for k in at.session_state['user'].knowledge_bases])
        self.assertIsNone(at.session_state['thread'].knowledge_base_id)
        RESULT.append({'test': 'KB create/delete UI/API contract', 'passed': True})

    def test_08_stream_error_retains_thread(self):
        at = choose(start(), KB)
        at.chat_input[0].set_value('[ERROR] UI handling').run()
        clean(at)
        self.assertIsNotNone(at.session_state['thread'].id)
        self.assertTrue(at.error)
        RESULT.append({'test': 'existing first-stream error preservation', 'passed': True})

    def test_09_registration_and_sign_out_entries(self):
        at = start(False)
        at.button(key='ui_register').click().run()
        clean(at)
        self.assertTrue(any(t.label == 'Username *' for t in at.text_input))
        at.button(key='ui_back_home').click().run()
        at = start()
        at.button(key='ui_sign_out').click().run()
        clean(at)
        self.assertFalse(at.session_state['user'].is_authenticated)
        RESULT.append({'test': 'registration/back/logout entries', 'passed': True})

    def test_10_upload_states_refresh_and_document_delete(self):
        # AppTest cannot drive a browser file chooser. Substitute only its value;
        # all buttons, session state, API helpers and HTTP responses remain real.
        script = '''
from types import SimpleNamespace
from unittest.mock import patch
import runpy
import streamlit as st
file = SimpleNamespace(name=st.session_state.get('m1_test_file', 'm1-ready.pdf'),
                       type='application/pdf', getvalue=lambda: b'%PDF-M1-Mock')
with patch('streamlit.file_uploader', return_value=file):
    runpy.run_path('/m1/preview.py')
'''
        at = clean(AppTest.from_string(script, default_timeout=30).run())
        at.button(key='ui_login').click().run()
        at.text_input[0].set_value('demo@example.org')
        at.text_input[1].set_value('workspace-demo')
        at.button(key='FormSubmitter:login_form-Login').click().run()
        at.button(key='ui_nav_knowledge').click().run()
        name = 'M1 upload-state ' + uuid4().hex[:8]
        at.text_input(key='new_knowledge_base_name').set_value(name)
        at.button(key='create_knowledge_base_button').click().run()
        kb = next(k for k in at.session_state['user'].knowledge_bases if k['name'] == name)['id']
        choose(at, kb)
        at.button(key='ui_nav_documents').click().run()
        upload = 'upload_button_' + kb
        at.button(key=upload).click().run()
        clean(at)
        self.assertIn('uploaded successfully', values(at, 'success'))
        doc = at.session_state['thread'].documents[0]
        self.assertEqual(doc['status'], 'ready')
        at.checkbox(key='confirm_delete_document_' + doc['id']).check().run()
        at.button(key='delete_document_' + doc['id']).click().run()
        clean(at)
        self.assertEqual(at.session_state['thread'].documents, [])
        self.assertIn('Document deleted successfully', values(at, 'success'))
        at.session_state['m1_test_file'] = 'm1-failed.pdf'
        at.run()
        at.button(key=upload).click().run()
        clean(at)
        self.assertTrue(at.error)
        self.assertEqual(at.session_state['thread'].documents[0]['status'], 'failed')
        at.session_state['m1_test_file'] = 'm1-processing.pdf'
        at.run()
        at.button(key=upload).click().run()
        clean(at)
        self.assertTrue(at.warning)
        before = list(at.session_state['thread'].documents)
        at.button(key='ui_theme_toggle').click().run()
        at.button(key='refresh_documents_' + kb).click().run()
        self.assertTrue(at.button(key=upload).disabled)
        self.assertEqual(at.session_state['thread'].documents, before)
        RESULT.append({'test': 'Mock upload ready/failed/timeout; confirmed delete; no duplicate pending upload', 'passed': True})


if __name__ == '__main__':
    unittest.main(verbosity=2)
