import shutil
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, HTTPException, UploadFile, status
from loguru import logger

from app.auth.dependencies import CurrentUserDep
from app.config import BASE_DIR
from app.db.main import SessionDep
from app.db.pgvector_utils import (
    DOCUMENT_LOADER_MAPPING,
    delete_document_from_pgvector,
    index_document_to_pgvector,
    search_documents_in_pgvector,
)

from . import service as document_service
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

@document_router.post("/upload/{thread_id}", response_model=DocumentUploadResponse)
async def upload_document(thread_id: UUID, file: UploadFile, current_user: CurrentUserDep, session: SessionDep):
    user_id = current_user.id
    if file.filename is None:
        logger.error("No file uploaded.")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No file uploaded.")

    allowd_extensions = list(DOCUMENT_LOADER_MAPPING.keys())
    message = f"Unsupported file type. Allowed types: {', '.join(allowd_extensions)}"
    if Path(file.filename).suffix not in allowd_extensions:
        logger.error(message)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)

    TEMP_DIR = BASE_DIR / "tmp"
    if not TEMP_DIR.exists():
        TEMP_DIR.mkdir()
    temp_file_path = TEMP_DIR / file.filename
    document_id = None
    chunk_ids = []
    try:
        with open(temp_file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        logger.info(f"File '{file.filename}' saved temporarily to '{temp_file_path}'.")
        document_data = DocumetCreate(file_name=file.filename, thread_id=thread_id)
        new_document = await document_service.insert_document(document_data, session)
        document_id = new_document.id
        chunk_ids = await index_document_to_pgvector(temp_file_path, document_id, thread_id, user_id)
        logger.info(f"File '{file.filename}' (document_id: {document_id}) successfully indexed to PGVector.")

        return {"document_id": document_id, "message": f"File {file.filename} uploaded and indexed successfully."}
    except Exception as e:
        logger.error(f"An unexpected error occurred during upload of '{file.filename}': {e}", exc_info=True)
        if document_id is not None:
            if chunk_ids:
                try:
                    await delete_document_from_pgvector(chunk_ids)
                    logger.info(f"Attempted cleanup of PGVector for document {document_id} after unexpected error.")
                except Exception as pgvector_clean_err:
                    logger.error(
                        f"Failed to cleanup PGVector for document {document_id} during error handling: {pgvector_clean_err}"
                    )
            await document_service.delete_document(document_id, session)
            logger.info(f"Rolled back database record for document {document_id} due to unexpected error.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An unexpected error occurred while uploading '{file.filename}'.",
        )
    finally:
        if temp_file_path.exists():
            temp_file_path.unlink()
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
    """
    Upload and index a document into a knowledge base.
    """

    user_id = current_user.id

    if file.filename is None:
        logger.error("No file uploaded.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No file uploaded.",
        )

    allowed_extensions = list(
        DOCUMENT_LOADER_MAPPING.keys()
    )

    message = (
        "Unsupported file type. Allowed types: "
        f"{', '.join(allowed_extensions)}"
    )

    if Path(file.filename).suffix.lower() not in allowed_extensions:
        logger.error(message)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=message,
        )

    temp_dir = BASE_DIR / "tmp"

    if not temp_dir.exists():
        temp_dir.mkdir()

    temp_file_path = temp_dir / file.filename

    document_id = None
    chunk_ids: list[str] = []

    try:
        # 1. Save uploaded file temporarily.
        with open(temp_file_path, "wb") as buffer:
            shutil.copyfileobj(
                file.file,
                buffer,
            )

        logger.info(
            f"File '{file.filename}' saved temporarily "
            f"to '{temp_file_path}'."
        )

        # 2. Verify KB ownership and create DB record.
        # New KB documents begin in processing state.
        new_document = (
            await document_service.create_knowledge_base_document(
                file_name=file.filename,
                knowledge_base_id=knowledge_base_id,
                user_id=user_id,
                session=session,
            )
        )

        document_id = new_document.id

        # 3. Split + embedding + PGVector indexing.
        chunk_ids = await index_document_to_pgvector(
            file_path=temp_file_path,
            document_id=document_id,
            thread_id=None,
            user_id=user_id,
            knowledge_base_id=knowledge_base_id,
        )

        # 4. Mark indexing as ready.
        await document_service.update_document_status(
            document_id=document_id,
            document_status="ready",
            session=session,
        )

        logger.info(
            f"File '{file.filename}' "
            f"(document_id: {document_id}) "
            f"successfully indexed into "
            f"knowledge_base_id: {knowledge_base_id}."
        )

        return {
            "document_id": document_id,
            "message": (
                f"File {file.filename} uploaded "
                "and indexed successfully."
            ),
        }

    except HTTPException:
        raise

    except Exception as e:
        logger.exception(
            "Knowledge base document upload failed: "
            f"file={file.filename}, "
            f"knowledge_base_id={knowledge_base_id}, "
            f"error={e}"
        )

        # Remove any chunks that were successfully written
        # before the failure occurred.
        if chunk_ids:
            try:
                await delete_document_from_pgvector(
                    chunk_ids
                )
            except Exception:
                logger.exception(
                    "Failed to clean up PGVector chunks "
                    f"for document_id={document_id}."
                )

        # Keep the database record so the frontend can
        # expose the failed status to the user.
        if document_id is not None:
            try:
                await document_service.update_document_status(
                    document_id=document_id,
                    document_status="failed",
                    error_message=str(e)[:500],
                    session=session,
                )
            except Exception:
                logger.exception(
                    "Failed to update document status "
                    f"for document_id={document_id}."
                )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"An unexpected error occurred while "
                f"uploading '{file.filename}'."
            ),
        )

    finally:
        if temp_file_path.exists():
            temp_file_path.unlink()

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
