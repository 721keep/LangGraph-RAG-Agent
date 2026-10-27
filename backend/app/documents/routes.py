import shutil
from contextlib import contextmanager
from tempfile import TemporaryDirectory
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, UploadFile
from loguru import logger

from app.auth.dependencies import CurrentUserDep
from app.config import BASE_DIR
from app.db.main import SessionDep
from app.db.pgvector_utils import (
    DOCUMENT_LOADER_MAPPING,
    delete_document_from_pgvector,
    index_document_to_pgvector,
)

from . import service as document_service
from .errors import DocumentIndexingError
from .schemas import DocumentDeleteResponse, DocumentPublic, DocumentUploadResponse, DocumetCreate

document_router = APIRouter()


@document_router.get("/{thread_id}", response_model=list[DocumentPublic])
async def get_documents(thread_id: UUID, current_user: CurrentUserDep, session: SessionDep):
    return await document_service.get_documents(thread_id, session)

@document_router.get(
    "/knowledge-bases/{knowledge_base_id}",
    response_model=list[DocumentPublic],
)
async def get_knowledge_base_documents(
    knowledge_base_id: UUID,
    current_user: CurrentUserDep,
    session: SessionDep,
):
    """
    Return all documents belonging to a knowledge base
    owned by the current user.
    """

    return await document_service.get_knowledge_base_documents(
        knowledge_base_id=knowledge_base_id,
        user_id=current_user.id,
        session=session,
    )

@contextmanager
def temporary_upload(file: UploadFile):
    """Keep the original basename inside a directory owned by this upload only."""
    temp_dir = BASE_DIR / "tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    directory = TemporaryDirectory(prefix="upload_", dir=temp_dir)
    try:
        file_path = Path(directory.name) / Path(file.filename.replace("\\", "/")).name
        with file_path.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        yield file_path
    finally:
        try:
            directory.cleanup()
        except OSError:
            logger.warning("Could not remove an upload temporary directory.")


async def _upload_document(file, current_user, session, *, thread_id=None, knowledge_base_id=None):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file uploaded.")
    if Path(file.filename).suffix.lower() not in DOCUMENT_LOADER_MAPPING:
        raise HTTPException(status_code=400, detail="Unsupported document type.")

    user_id = current_user.id
    request_id = str(uuid4())
    document_id = None
    chunk_ids = []
    stage = "save"
    try:
        with temporary_upload(file) as temp_file_path:
            if knowledge_base_id is not None:
                new_document = await document_service.create_knowledge_base_document(
                    file_name=file.filename,
                    knowledge_base_id=knowledge_base_id,
                    user_id=user_id,
                    session=session,
                )
            else:
                new_document = await document_service.insert_document(
                    DocumetCreate(file_name=file.filename, thread_id=thread_id, status="processing"),
                    session,
                )
            document_id = new_document.id
            logger.info("Upload started: request_id={}, document_id={}", request_id, document_id)
            stage = "index"
            chunk_ids = await index_document_to_pgvector(
                file_path=temp_file_path,
                document_id=document_id,
                thread_id=thread_id,
                user_id=user_id,
                knowledge_base_id=knowledge_base_id,
            )
            stage = "status"
            changed = await document_service.update_document_status(
                document_id=document_id, document_status="ready", session=session,
            )
            if not changed:
                raise DocumentIndexingError("document_unavailable", "status", 409)
            return {
                "document_id": document_id,
                "status": "ready",
                "message": "Document uploaded and indexed successfully.",
            }
    except HTTPException:
        raise
    except Exception as error:
        failure = error if isinstance(error, DocumentIndexingError) else DocumentIndexingError(
            "document_status_failed" if stage == "status" else "document_save_failed", stage,
        )
        logger.warning(
            "Upload failed: request_id={}, document_id={}, stage={}, code={}",
            request_id, document_id, failure.stage, failure.code,
        )
        result_status = "unknown"
        if document_id is not None:
            try:
                changed = await document_service.update_document_status(
                    document_id=document_id,
                    document_status="failed",
                    error_message=f"{failure.code}: {failure.message}",
                    session=session,
                )
                if changed:
                    result_status = "failed"
            except Exception:
                logger.warning("Upload status write failed: request_id={}, document_id={}", request_id, document_id)

        # Clean only this request's known IDs after a confirmed failure/deletion.
        # If a ready commit lost its acknowledgement, deleting vectors here could
        # leave a ready record without its data. Preserve uncertain writes.
        if chunk_ids and (result_status == "failed" or failure.code == "document_unavailable"):
            try:
                await delete_document_from_pgvector(chunk_ids)
            except Exception:
                logger.warning("Upload chunk cleanup failed: request_id={}", request_id)

        raise HTTPException(
            status_code=failure.http_status,
            detail={
                "document_id": str(document_id) if document_id else None,
                "status": result_status,
                "error_code": failure.code,
                "stage": failure.stage,
                "message": failure.message,
                "request_id": request_id,
            },
        ) from None


@document_router.post("/upload/{thread_id}", response_model=DocumentUploadResponse)
async def upload_document(thread_id: UUID, file: UploadFile, current_user: CurrentUserDep, session: SessionDep):
    return await _upload_document(file, current_user, session, thread_id=thread_id)


@document_router.post(
    "/knowledge-bases/{knowledge_base_id}/upload",
    response_model=DocumentUploadResponse,
)
async def upload_knowledge_base_document(
    knowledge_base_id: UUID,
    file: UploadFile,
    current_user: CurrentUserDep,
    session: SessionDep,
):
    return await _upload_document(file, current_user, session, knowledge_base_id=knowledge_base_id)


@document_router.delete(
    "/{document_id}",
    response_model=DocumentDeleteResponse,
)
async def delete_document(
    document_id: UUID,
    current_user: CurrentUserDep,
    session: SessionDep,
):
    """
    Delete a document owned by the current user together with
    all of its PGVector chunks.
    """

    deleted_chunk_count = (
        await document_service.delete_document_for_user(
            document_id=document_id,
            user_id=current_user.id,
            session=session,
        )
    )

    return {
        "message": (
            f"Successfully deleted document {document_id} "
            f"and {deleted_chunk_count} PGVector chunk(s)."
        )
    }
