"""Focused real deletion integration: only new resources in a dedicated database."""
import asyncio
import os
from uuid import uuid4

import pytest
if os.environ.get('DELETE_ACCEPTANCE_MODE') != 'isolated':
    pytest.skip('Run only in the new dedicated delete-test containers', allow_module_level=True)
from harness import Harness, REPORT, ERRORS, async_session, text, save


@pytest.fixture(scope='module')
def setup():
    with asyncio.Runner() as runner:
        harness = Harness()
        runner.run(harness.init())
        yield runner, harness
        runner.run(harness.close())


def record(case, **fields):
    REPORT['cases'].append({'case': case, 'passed': True, **fields})
    save()


def test_kb_delete_204_cascades_docs_vectors_and_preserves_other_kbs(setup):
    r, h = setup
    async def run():
        d = await h.seed('kb-success')
        before = await h.rows(d)
        response = await h.delete('kb', d['kb'][0])
        assert response.status_code == 204 and response.content == b''
        after = await h.rows(d)
        assert not await h.exists('kb', d['kb'][0])
        assert not await h.exists('doc', d['doc'][0]) and not await h.exists('doc', d['doc'][1])
        for doc in d['doc'][:2]:
            assert all(i not in after['vectors'] for i in d['chunks'][doc])
        for kb in d['kb'][1:]:
            assert before['knowledge_bases'][str(kb)] == after['knowledge_bases'][str(kb)]
        for doc in d['doc'][2:]:
            assert before['documents'][str(doc)] == after['documents'][str(doc)]
            assert all(before['vectors'][i] == after['vectors'][i] for i in d['chunks'][doc])
        async with async_session() as s:
            assert (await s.execute(text('SELECT knowledge_base_id FROM threads WHERE id=:id'), {'id': d['thread'][0]})).scalar_one() is None
        assert before['threads'][str(d['thread'][1])] == after['threads'][str(d['thread'][1])]
        listing = await h.client.get('/api/v1/knowledge-bases/', headers=h.headers[0])
        assert str(d['kb'][0]) not in {k['id'] for k in listing.json()}
        record('kb owner delete', http=204, deleted_documents=2, deleted_chunks=4,
               other_users_and_kbs_unchanged=True, bound_thread_preserved_and_unbound=True)
    r.run(run())


@pytest.mark.parametrize('actor,missing,expected', [(1, False, 403), (0, True, 404), (None, False, 401)])
def test_kb_permissions_missing_and_unauthenticated(setup, actor, missing, expected):
    r, h = setup
    async def run():
        d = await h.seed('kb-negative-' + str(expected))
        before = await h.rows(d)
        ident = uuid4() if missing else d['kb'][0]
        h.allowed['kb'].add(ident)
        response = await h.delete('kb', ident, actor=actor, missing=missing)
        assert response.status_code == expected
        assert await h.rows(d) == before
        record('kb rejection', http=expected, dataset_unchanged=True)
    r.run(run())


def test_kb_real_database_error_rolls_back_vectors_and_business_rows(setup):
    r, h = setup
    async def run():
        d = await h.seed('kb-rollback')
        await h.fault('kb', d['kb'][0])
        before = await h.rows(d)
        start = len(ERRORS)
        response = await h.delete('kb', d['kb'][0])
        assert response.status_code == 500
        assert len(ERRORS) == start + 1 and 'delete_acceptance_injected_after_vector_delete' in ERRORS[-1]['message']
        assert await h.rows(d) == before
        listing = await h.client.get('/api/v1/documents/knowledge-bases/' + str(d['kb'][0]), headers=h.headers[0])
        assert listing.status_code == 200 and len(listing.json()) == 2
        record('kb rollback', http=500, injected_database_error=True, all_rows_and_vectors_restored=True)
    r.run(run())


def test_ready_pdf_delete_preserves_sibling_and_repeated_delete_is_404(setup):
    r, h = setup
    async def run():
        d = await h.seed('pdf-success')
        before = await h.rows(d)
        response = await h.delete('doc', d['doc'][0])
        assert response.status_code == 200 and '2 PGVector chunk(s)' in response.json()['message']
        after = await h.rows(d)
        assert not await h.exists('doc', d['doc'][0])
        assert all(i not in after['vectors'] for i in d['chunks'][d['doc'][0]])
        for doc in d['doc'][1:]:
            assert before['documents'][str(doc)] == after['documents'][str(doc)]
            assert all(before['vectors'][i] == after['vectors'][i] for i in d['chunks'][doc])
        assert before['knowledge_bases'] == after['knowledge_bases']
        listing = await h.client.get('/api/v1/documents/knowledge-bases/' + str(d['kb'][0]), headers=h.headers[0])
        assert listing.status_code == 200 and {x['id'] for x in listing.json()} == {str(d['doc'][1])}
        again = await h.delete('doc', d['doc'][0], missing=True)
        assert again.status_code == 404 and await h.rows(d) == after
        record('ready PDF delete', http=200, deleted_chunks=2, sibling_preserved=True,
               list_matches_database=True, repeated_http=404, stale_data_error=False)
    r.run(run())


@pytest.mark.parametrize('actor,missing,expected', [(1, False, 403), (0, True, 404), (None, False, 401)])
def test_pdf_permissions_missing_and_unauthenticated(setup, actor, missing, expected):
    r, h = setup
    async def run():
        d = await h.seed('pdf-negative-' + str(expected))
        before = await h.rows(d)
        ident = uuid4() if missing else d['doc'][0]
        h.allowed['doc'].add(ident)
        response = await h.delete('doc', ident, actor=actor, missing=missing)
        assert response.status_code == expected
        assert await h.rows(d) == before
        record('PDF rejection', http=expected, dataset_unchanged=True)
    r.run(run())


def test_pdf_real_database_error_rolls_back_vector_and_document_deletion(setup):
    r, h = setup
    async def run():
        d = await h.seed('pdf-rollback')
        await h.fault('doc', d['doc'][0])
        before = await h.rows(d)
        start = len(ERRORS)
        response = await h.delete('doc', d['doc'][0])
        assert response.status_code == 500
        assert len(ERRORS) == start + 1 and 'delete_acceptance_injected_after_vector_delete' in ERRORS[-1]['message']
        assert await h.rows(d) == before
        listing = await h.client.get('/api/v1/documents/knowledge-bases/' + str(d['kb'][0]), headers=h.headers[0])
        assert len(listing.json()) == 2 and all(x['status'] == 'ready' for x in listing.json())
        record('PDF rollback', http=500, injected_database_error=True, all_rows_and_vectors_restored=True)
    r.run(run())


def test_delete_two_pdfs_sequentially_without_stale_orm_state(setup):
    r, h = setup
    async def run():
        d = await h.seed('pdf-sequential')
        start = len(ERRORS)
        for doc in d['doc'][:2]:
            assert (await h.delete('doc', doc)).status_code == 200
        response = await h.client.get('/api/v1/documents/knowledge-bases/' + str(d['kb'][0]), headers=h.headers[0])
        assert response.status_code == 200 and response.json() == []
        assert await h.exists('kb', d['kb'][0])
        after = await h.rows(d)
        assert all(i not in after['vectors'] for doc in d['doc'][:2] for i in d['chunks'][doc])
        assert len(ERRORS) == start
        record('sequential PDF deletes', http=[200, 200], empty_document_list=True, kb_preserved=True, stale_data_error=False)
    r.run(run())
