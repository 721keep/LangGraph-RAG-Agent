import asyncio
import io
import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
import requests
from fastapi import FastAPI, UploadFile
from openai import APIConnectionError, AuthenticationError, BadRequestError, PermissionDeniedError, RateLimitError
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session


def run(awaitable):
    return asyncio.run(awaitable)


def sdk_error(kind, status, code):
    request = httpx.Request("POST", "https://unused.invalid/embeddings")
    response = httpx.Response(status, request=request)
    return kind(
        "sensitive-provider-response sk-test-secret",
        response=response,
        body={"error": {"code": code, "type": code, "message": "sk-test-secret"}},
    )


def uploaded(name="same.pdf", content=b"mock-pdf"):
    return UploadFile(filename=name, file=io.BytesIO(content))


def setup_document(isolated, monkeypatch):
    document = SimpleNamespace(id=uuid4())
    create = AsyncMock(return_value=document)
    monkeypatch.setattr(isolated.service, "create_knowledge_base_document", create)
    update = AsyncMock(return_value=True)
    monkeypatch.setattr(isolated.service, "update_document_status", update)
    return document, create, update


@pytest.mark.parametrize("kind,status,provider_code,code,stage", [
    (BadRequestError, 400, "AllocationQuota.FreeTierOnly", "embedding_quota_exhausted", "embedding"),
    (RateLimitError, 429, "insufficient_quota", "embedding_quota_exhausted", "embedding"),
    (AuthenticationError, 401, "invalid_api_key", "embedding_auth_failed", "embedding"),
    (PermissionDeniedError, 403, "access_denied", "embedding_auth_failed", "embedding"),
    (RateLimitError, 429, "rate_limit_exceeded", "embedding_failed", "embedding"),
])
def test_embedding_error_classification(isolated, kind, status, provider_code, code, stage):
    isolated.store.aadd_documents.side_effect = sdk_error(kind, status, provider_code)
    with pytest.raises(isolated.errors.DocumentIndexingError) as caught:
        run(isolated.pg.index_document_to_pgvector(
            isolated.tmp_path / "test.pdf", uuid4(), None, uuid4(), uuid4(),
        ))
    assert (caught.value.code, caught.value.stage) == (code, stage)
    assert "sk-test-secret" not in str(caught.value)
    assert isolated.store.aadd_documents.await_count == 1  # no retry loop


def test_embedding_connection_failure_is_not_vector_failure(isolated):
    isolated.store.aadd_documents.side_effect = APIConnectionError(
        request=httpx.Request("POST", "https://unused.invalid/embeddings"),
    )
    with pytest.raises(isolated.errors.DocumentIndexingError) as caught:
        run(isolated.pg.index_document_to_pgvector(isolated.tmp_path / "test.pdf", uuid4(), None, uuid4(), uuid4()))
    assert caught.value.code == "embedding_failed"


@pytest.mark.parametrize("empty", [False, True])
def test_pdf_parse_failure_never_calls_embedding(isolated, empty):
    if empty:
        isolated.loader.return_value.aload.return_value = []
    else:
        isolated.loader.return_value.aload.side_effect = ValueError("corrupt PDF sk-test-secret")
    with pytest.raises(isolated.errors.DocumentIndexingError) as caught:
        run(isolated.pg.index_document_to_pgvector(isolated.tmp_path / "test.pdf", uuid4(), None, uuid4(), uuid4()))
    assert caught.value.code == "pdf_parse_failed"
    isolated.store.aadd_documents.assert_not_awaited()


def test_vector_database_failure_is_not_embedding_failure(isolated):
    isolated.store.aadd_documents.side_effect = RuntimeError("DB connection contains sk-test-secret")
    with pytest.raises(isolated.errors.DocumentIndexingError) as caught:
        run(isolated.pg.index_document_to_pgvector(isolated.tmp_path / "test.pdf", uuid4(), None, uuid4(), uuid4()))
    assert (caught.value.code, caught.value.stage) == ("vector_write_failed", "vector_write")


def test_chunk_metadata_keeps_filename_and_scope(isolated):
    document_id, user_id, kb_id = uuid4(), uuid4(), uuid4()
    run(isolated.pg.index_document_to_pgvector(isolated.tmp_path / "original.pdf", document_id, None, user_id, kb_id))
    args = isolated.store.aadd_documents.call_args
    metadata = args.args[0][0].metadata
    assert metadata["file_name"] == "original.pdf"
    assert metadata["document_id"] == str(document_id)
    assert metadata["user_id"] == str(user_id)
    assert metadata["knowledge_base_id"] == str(kb_id)
    assert metadata["chunk_index"] == 0
    assert args.kwargs["ids"] == [metadata["id"]]


@pytest.mark.parametrize("failure,expected_http", [
    ("quota", 503), ("auth", 502), ("parse", 422), ("vector", 500), (None, 200),
])
def test_http_upload_status_and_safe_error(isolated, monkeypatch, failure, expected_http):
    document, create, update = setup_document(isolated, monkeypatch)
    if failure == "quota":
        isolated.store.aadd_documents.side_effect = sdk_error(BadRequestError, 400, "AllocationQuota.FreeTierOnly")
    elif failure == "auth":
        isolated.store.aadd_documents.side_effect = sdk_error(AuthenticationError, 401, "invalid_api_key")
    elif failure == "parse":
        isolated.loader.return_value.aload.side_effect = ValueError("sk-test-secret")
    elif failure == "vector":
        isolated.store.aadd_documents.side_effect = RuntimeError("sk-test-secret")
    app = FastAPI()
    app.include_router(isolated.routes.document_router, prefix="/documents")

    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.post(f"/documents/knowledge-bases/{uuid4()}/upload", files={"file": ("same.pdf", b"mock", "application/pdf")})

    response = run(request())
    assert response.status_code == expected_http
    payload = response.json()["detail"] if failure else response.json()
    assert payload["document_id"] == str(document.id)
    assert payload["status"] == ("failed" if failure else "ready")
    assert update.call_args.kwargs["document_status"] == payload["status"]
    assert "sk-test-secret" not in json.dumps(payload)
    assert not list((isolated.tmp_path / "tmp").iterdir())
    if failure:
        assert payload["error_code"] in update.call_args.kwargs["error_message"]
        assert len(update.call_args.kwargs["error_message"]) <= 500


def test_processing_created_before_indexing(isolated, monkeypatch):
    calls = []
    document = SimpleNamespace(id=uuid4())

    async def insert(document_data, session):
        assert document_data.status == "processing"
        calls.append("processing committed")
        return document

    async def index(**kwargs):
        assert calls == ["processing committed"]
        calls.append("index")
        return ["own-chunk"]

    monkeypatch.setattr(isolated.service, "insert_document", insert)
    monkeypatch.setattr(isolated.routes, "index_document_to_pgvector", index)
    monkeypatch.setattr(isolated.service, "update_document_status", AsyncMock(return_value=True))
    result = run(isolated.routes.upload_knowledge_base_document(uuid4(), uploaded(), isolated.user, isolated.session))
    assert result["status"] == "ready"
    assert calls == ["processing committed", "index"]


def test_ownership_denial_does_not_create_or_index(isolated, monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(isolated.service.knowledge_base_service, "get_knowledge_base", AsyncMock(side_effect=HTTPException(404, "Not found")))
    insert = AsyncMock()
    monkeypatch.setattr(isolated.service, "insert_document", insert)
    with pytest.raises(HTTPException) as caught:
        run(isolated.routes.upload_knowledge_base_document(uuid4(), uploaded(), isolated.user, isolated.session))
    assert caught.value.status_code == 404
    insert.assert_not_awaited()
    isolated.store.aadd_documents.assert_not_awaited()
    assert not list((isolated.tmp_path / "tmp").iterdir())


def test_same_filename_concurrent_requests_keep_own_files(isolated, monkeypatch):
    setup_document(isolated, monkeypatch)
    paths = []
    contents = []

    async def scenario():
        arrived = asyncio.Event()
        first_finished = asyncio.Event()

        async def index(**kwargs):
            path = kwargs["file_path"]
            paths.append(path)
            contents.append(path.read_bytes())
            if len(paths) == 2:
                arrived.set()
            await arrived.wait()
            if path == paths[0]:
                return ["first-chunk"]
            await first_finished.wait()
            assert not paths[0].exists()
            assert path.read_bytes() == b"second"
            return ["second-chunk"]

        monkeypatch.setattr(isolated.routes, "index_document_to_pgvector", index)

        async def first():
            response = await isolated.routes.upload_knowledge_base_document(uuid4(), uploaded(content=b"first"), isolated.user, isolated.session)
            first_finished.set()
            return response

        return await asyncio.wait_for(asyncio.gather(
            first(),
            isolated.routes.upload_knowledge_base_document(uuid4(), uploaded(content=b"second"), isolated.user, isolated.session),
        ), timeout=5)

    assert all(result["status"] == "ready" for result in run(scenario()))
    assert len(set(paths)) == 2
    assert all(path.name == "same.pdf" for path in paths)
    assert contents == [b"first", b"second"]
    assert not any(path.exists() for path in paths)


@pytest.mark.parametrize("index_fails", [False, True])
def test_deleted_during_indexing_never_returns_ready_or_recreates(isolated, monkeypatch, index_fails):
    from fastapi import HTTPException

    document, create, update = setup_document(isolated, monkeypatch)
    update.return_value = False  # atomic UPDATE matched no processing row
    if index_fails:
        isolated.store.aadd_documents.side_effect = sdk_error(BadRequestError, 400, "AllocationQuota.FreeTierOnly")
    with pytest.raises(HTTPException) as caught:
        run(isolated.routes.upload_knowledge_base_document(uuid4(), uploaded(), isolated.user, isolated.session))
    assert caught.value.detail["status"] == "unknown"
    assert caught.value.status_code == (503 if index_fails else 409)
    create.assert_awaited_once()
    if index_fails:
        isolated.store.adelete.assert_not_awaited()
    else:
        isolated.store.adelete.assert_awaited_once_with(ids=["own-chunk"])


def test_status_failure_keeps_original_error_and_unknown_state(isolated, monkeypatch):
    from fastapi import HTTPException

    document, _, update = setup_document(isolated, monkeypatch)
    update.side_effect = RuntimeError("sk-test-secret")
    isolated.store.aadd_documents.side_effect = sdk_error(BadRequestError, 400, "AllocationQuota.FreeTierOnly")
    with pytest.raises(HTTPException) as caught:
        run(isolated.routes.upload_knowledge_base_document(uuid4(), uploaded(), isolated.user, isolated.session))
    assert caught.value.detail["error_code"] == "embedding_quota_exhausted"
    assert caught.value.detail["status"] == "unknown"
    assert "sk-test-secret" not in str(caught.value.detail)


def test_uncertain_ready_commit_keeps_vectors_for_reconciliation(isolated, monkeypatch):
    from fastapi import HTTPException

    _, _, update = setup_document(isolated, monkeypatch)
    # Commit may have succeeded before losing its acknowledgement. The next
    # conditional failed write sees an already-terminal row and changes nothing.
    update.side_effect = [RuntimeError("commit acknowledgement lost"), False]
    with pytest.raises(HTTPException) as caught:
        run(isolated.routes.upload_knowledge_base_document(uuid4(), uploaded(), isolated.user, isolated.session))
    assert caught.value.detail["error_code"] == "document_status_failed"
    assert caught.value.detail["status"] == "unknown"
    isolated.store.adelete.assert_not_awaited()


def test_confirmed_failed_status_cleans_only_own_completed_chunks(isolated, monkeypatch):
    from fastapi import HTTPException

    _, _, update = setup_document(isolated, monkeypatch)
    update.side_effect = [RuntimeError("ready write failed before commit"), True]
    with pytest.raises(HTTPException) as caught:
        run(isolated.routes.upload_knowledge_base_document(uuid4(), uploaded(), isolated.user, isolated.session))
    assert caught.value.detail["status"] == "failed"
    isolated.store.adelete.assert_awaited_once_with(ids=["own-chunk"])


class MemorySession:
    """Async-shaped adapter over a real, isolated SQLAlchemy SQLite session."""

    def __init__(self, session):
        self.session = session
        self.rollback = AsyncMock(side_effect=self.session.rollback)
        self.commit = AsyncMock(side_effect=self.session.commit)

    async def execute(self, statement):
        return self.session.execute(statement)


@pytest.fixture
def memory_db(isolated):
    from app.db.models import Base, Document

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    document = Document(id=uuid4(), file_name="test.pdf", knowledge_base_id=uuid4(), status="processing")
    session.add(document)
    session.commit()
    document_id = document.id
    try:
        yield SimpleNamespace(engine=engine, session=session, adapter=MemorySession(session), document_id=document_id, document=document, Document=Document)
    finally:
        session.close()
        engine.dispose()


@pytest.mark.parametrize("target", ["ready", "failed"])
def test_real_sql_status_update_survives_deleted_cached_orm_row(isolated, memory_db, target):
    db = memory_db
    # Keep the old object in the original identity map while another session
    # deletes its row, reproducing the source of the old StaleDataError.
    assert db.document.status == "processing"
    with Session(db.engine) as deleting_session:
        deleting_session.execute(delete(db.Document).where(db.Document.id == db.document_id))
        deleting_session.commit()
    assert run(isolated.service.update_document_status(db.document_id, target, db.adapter)) is False
    with Session(db.engine) as checking_session:
        assert checking_session.get(db.Document, db.document_id) is None


@pytest.mark.parametrize("target", ["ready", "failed"])
def test_real_sql_terminal_state_is_not_overwritten(isolated, memory_db, target):
    db = memory_db
    error_message = "pdf_parse_failed: safe" if target == "failed" else None
    assert run(isolated.service.update_document_status(db.document_id, target, db.adapter, error_message)) is True
    assert run(isolated.service.update_document_status(db.document_id, "failed" if target == "ready" else "ready", db.adapter)) is False
    db.session.expire_all()
    record = db.session.get(db.Document, db.document_id)
    assert (record.status, record.error_message) == (target, error_message)


def test_status_commit_failure_rolls_back_before_reuse(isolated):
    session = AsyncMock()
    session.execute.return_value = SimpleNamespace(rowcount=1)
    session.commit.side_effect = RuntimeError("commit failed")
    with pytest.raises(RuntimeError):
        run(isolated.service.update_document_status(uuid4(), "ready", session))
    assert session.rollback.await_count == 2
    session.commit.side_effect = None
    assert run(isolated.service.update_document_status(uuid4(), "failed", session)) is True


def request_file():
    return SimpleNamespace(name="same.pdf", type="application/pdf", getvalue=lambda: b"mock")


def http_response(status, body):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(body).encode()
    return response


def test_client_quota_error_with_document_id_is_failure(isolated, monkeypatch):
    post = Mock(return_value=http_response(503, {"detail": {
        "status": "failed", "document_id": str(uuid4()), "error_code": "embedding_quota_exhausted", "message": "sk-test-secret",
    }}))
    monkeypatch.setattr(requests, "post", post)
    response = isolated.api.upload_knowledge_base_document(uuid4(), request_file())
    assert response["status"] == "failed"
    assert response["document_id"]
    assert response["error_code"] == "embedding_quota_exhausted"
    assert "sk-test-secret" not in response["message"]
    assert post.call_args.kwargs["timeout"] == (10, 300)


@pytest.mark.parametrize("exception,code", [
    (requests.exceptions.ReadTimeout("sk-test-secret"), "upload_timeout"),
    (requests.exceptions.ConnectTimeout("sk-test-secret"), "upload_timeout"),
    (requests.exceptions.ConnectionError("sk-test-secret"), "upload_network_failed"),
])
def test_client_timeout_network_are_unknown_and_never_retried(isolated, monkeypatch, exception, code):
    post = Mock(side_effect=exception)
    monkeypatch.setattr(requests, "post", post)
    response = isolated.api.upload_knowledge_base_document(uuid4(), request_file())
    assert response["status"] == "unknown"
    assert response["error_code"] == code
    assert "sk-test-secret" not in str(response)
    post.assert_called_once()


def test_client_only_upload_timeout_changes(isolated, monkeypatch):
    post = Mock(return_value=http_response(200, {"document_id": str(uuid4()), "status": "ready"}))
    get = Mock(return_value=http_response(200, []))
    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(requests, "get", get)
    assert isolated.api.upload_document(uuid4(), request_file())["status"] == "ready"
    assert post.call_args.kwargs["timeout"] == (10, 300)
    isolated.api.list_knowledge_base_documents(uuid4())
    assert get.call_args.kwargs["timeout"] == 30
    isolated.api.login_user("mock@example.invalid", "mock")
    assert post.call_args.kwargs["timeout"] == 30


@pytest.mark.parametrize("status,body,expected", [
    (401, {"detail": "sk-test-secret"}, "upload_auth_failed"),
    (500, {"detail": "sk-test-secret"}, "upload_http_failed"),
    (500, {"detail": {"error_code": ["bad"]}}, "upload_http_failed"),
    (200, {"document_id": str(uuid4()), "status": "failed"}, "upload_result_unknown"),
])
def test_client_malformed_or_failed_result_never_means_ready(isolated, monkeypatch, status, body, expected):
    monkeypatch.setattr(requests, "post", Mock(return_value=http_response(status, body)))
    response = isolated.api.upload_knowledge_base_document(uuid4(), request_file())
    assert response["status"] != "ready"
    assert response["error_code"] == expected
    assert "sk-test-secret" not in str(response)


def configure_sidebar(isolated, monkeypatch, docs, upload_result=None, refresh=False):
    st = isolated.st
    st.session_state["thread"] = SimpleNamespace(knowledge_base_id=uuid4(), documents=docs)
    st.sidebar = SimpleNamespace(expander=Mock(side_effect=lambda *a, **k: nullcontext()), error=Mock())
    for name in ("caption", "success", "warning", "error", "markdown", "rerun"):
        setattr(st, name, Mock())
    st.spinner = Mock(side_effect=lambda *a, **k: nullcontext())
    st.container = Mock(side_effect=lambda *a, **k: nullcontext())
    st.popover = Mock(side_effect=lambda *a, **k: nullcontext())
    st.checkbox = Mock(return_value=False)
    st.columns = Mock(side_effect=lambda *a, **k: [nullcontext(), nullcontext()])
    st.file_uploader = Mock(return_value=request_file() if upload_result else None)
    st.button = Mock(side_effect=lambda label, **kwargs: not kwargs.get("disabled", False) and (label == "Upload" or (label == "Refresh documents" and refresh)))
    monkeypatch.setattr(isolated.api, "upload_knowledge_base_document", Mock(return_value=upload_result))
    return st


@pytest.mark.parametrize("result_status", ["ready", "failed", "unknown"])
def test_sidebar_refreshes_and_renders_new_list_for_every_upload_outcome(isolated, monkeypatch, result_status):
    result = {"status": result_status, "document_id": str(uuid4()), "message": "Safe explanation"}
    st = configure_sidebar(isolated, monkeypatch, [], result)
    row = {"id": str(uuid4()), "file_name": "same.pdf", "status": "processing" if result_status == "unknown" else result_status, "error_message": "embedding_quota_exhausted: safe"}

    def refresh(kb_id):
        st.session_state["thread"].documents = [row]

    update = Mock(side_effect=refresh)
    monkeypatch.setattr(isolated.sidebar, "update_knowledge_base_document_list", update)
    isolated.sidebar.document_list_component()
    update.assert_called_once()
    assert any(f"Status: {row['status']}" == c.args[0] for c in st.caption.call_args_list)
    assert st.success.call_count == (result_status == "ready")
    assert st.error.call_count == (result_status == "failed")
    assert st.warning.call_count == (result_status == "unknown")
    st.rerun.assert_not_called()  # notices remain visible after refreshing


def test_processing_disables_delete_and_upload_manual_refresh_can_reach_ready(isolated, monkeypatch):
    row = {"id": str(uuid4()), "file_name": "same.pdf", "status": "processing"}
    st = configure_sidebar(isolated, monkeypatch, [row], {"status": "ready"})
    isolated.sidebar.document_list_component()
    isolated.api.upload_knowledge_base_document.assert_not_called()
    calls = {call.kwargs.get("key", ""): call for call in st.button.call_args_list}
    assert calls[f"delete_document_{row['id']}"].kwargs["disabled"] is True

    configure_sidebar(isolated, monkeypatch, [row], refresh=True)
    def refresh(kb_id):
        st.session_state["thread"].documents = [{**row, "status": "ready"}]
    monkeypatch.setattr(isolated.sidebar, "update_knowledge_base_document_list", Mock(side_effect=refresh))
    isolated.sidebar.document_list_component()
    assert any(call.args == ("Status: ready",) for call in st.caption.call_args_list)


def test_refresh_connection_failure_preserves_cached_list(isolated, monkeypatch):
    docs = [{"id": str(uuid4()), "status": "processing"}]
    configure_sidebar(isolated, monkeypatch, docs)
    monkeypatch.setattr(isolated.api, "list_knowledge_base_documents", Mock(return_value=None))
    isolated.state.update_knowledge_base_document_list(uuid4())
    assert isolated.st.session_state["thread"].documents is docs


def test_legacy_failure_messages_do_not_leak_provider_payload(isolated):
    quota = isolated.api.document_failure_message({"error_message": "Free quota exhausted. sk-test-secret"})
    unknown = isolated.api.document_failure_message({"error_message": "postgresql://secret sk-test-secret"})
    assert "Embedding" in quota
    assert "sk-test-secret" not in quota + unknown
