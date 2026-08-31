from collections.abc import Sequence
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.db.pgvector_utils import (
    delete_document_chunks_from_pgvector,
)
from app.db.models import Document
from app.knowledge_bases import service as knowledge_base_service
from app.threads import service as thread_service
from .schemas import DocumentCreate, DocumentStatus


async def get_documents(
    thread_id: UUID,
    session: AsyncSession,
) -> Sequence[Document]:
    """
    Legacy document query.

    Keep this temporarily so the existing thread-scoped
    document flow continues to work during migration.
    """

    statement = (
        select(Document)
        .where(Document.thread_id == thread_id)
        .order_by(desc(Document.uploaded_at))
    )

    result = await session.execute(statement)

    return result.scalars().all()


async def get_knowledge_base_documents(
    knowledge_base_id: UUID,
    user_id: UUID,
    session: AsyncSession,
) -> Sequence[Document]:
    """
    Return all documents belonging to a knowledge base
    after verifying that the current user owns it.
    """

    await knowledge_base_service.get_knowledge_base(
        knowledge_base_id=knowledge_base_id,
        user_id=user_id,
        session=session,
    )

    statement = (
        select(Document)
        .where(
            Document.knowledge_base_id == knowledge_base_id
        )
        .order_by(desc(Document.uploaded_at))
    )

    result = await session.execute(statement)

    return result.scalars().all()

async def get_document_for_user(
    document_id: UUID,
    user_id: UUID,
    session: AsyncSession,
) -> Document:
    """
    Get a document and verify that it belongs to the current user.

    Knowledge-base documents inherit ownership from KnowledgeBase.
    Legacy thread-scoped documents inherit ownership from Thread.
    """

    db_document = await session.get(
        Document,
        document_id,
    )

    if db_document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document with ID {document_id} not found.",
        )

    if db_document.knowledge_base_id is not None:
        await knowledge_base_service.get_knowledge_base(
            knowledge_base_id=db_document.knowledge_base_id,
            user_id=user_id,
            session=session,
        )

    elif db_document.thread_id is not None:
        await thread_service.get_thread(
            thread_id=db_document.thread_id,
            user_id=user_id,
            session=session,
        )

    else:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Document ownership could not be verified."
            ),
        )

    return db_document

async def delete_document_for_user(
    document_id: UUID,
    user_id: UUID,
    session: AsyncSession,
) -> int:
    """
    Delete one document and all of its PGVector chunks after
    verifying that the current user owns the document.

    Returns the number of deleted PGVector chunks.
    """

    db_document = await get_document_for_user(
        document_id=document_id,
        user_id=user_id,
        session=session,
    )

    try:
        deleted_chunk_count = (
            await delete_document_chunks_from_pgvector(
                document_id=document_id,
                user_id=user_id,
                session=session,
            )
        )

        await session.delete(db_document)

        await session.commit()

        logger.info(
            "Deleted document "
            f"{document_id} for user_id={user_id}, "
            f"including {deleted_chunk_count} "
            "PGVector chunk(s)."
        )

        return deleted_chunk_count

    except Exception:
        await session.rollback()

        logger.exception(
            "Failed to delete document "
            f"{document_id} for user_id={user_id}. "
            "Transaction rolled back."
        )

        raise

async def insert_document(
    document_data: DocumentCreate,
    session: AsyncSession,
) -> Document:
    """
    Insert either a legacy thread-scoped document or a new
    knowledge-base-scoped document.
    """

    new_document = Document(
        **document_data.model_dump()
    )

    session.add(new_document)

    await session.commit()
    await session.refresh(new_document)

    return new_document


async def create_knowledge_base_document(
    file_name: str,
    knowledge_base_id: UUID,
    user_id: UUID,
    session: AsyncSession,
) -> Document:
    """
    Create a document record for a knowledge base.

    New knowledge-base uploads begin in processing state.
    """

    await knowledge_base_service.get_knowledge_base(
        knowledge_base_id=knowledge_base_id,
        user_id=user_id,
        session=session,
    )

    document_data = DocumentCreate(
        file_name=file_name,
        knowledge_base_id=knowledge_base_id,
        status="processing",
    )

    return await insert_document(
        document_data=document_data,
        session=session,
    )


async def update_document_status(
    document_id: UUID,
    document_status: DocumentStatus,
    session: AsyncSession,
    error_message: str | None = None,
) -> Document:
    """
    Update document indexing status.

    processing -> ready
    processing -> failed
    """

    document = await session.get(
        Document,
        document_id,
    )

    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document with ID {document_id} not found.",
        )

    document.status = document_status
    document.error_message = error_message

    await session.commit()
    await session.refresh(document)

    return document


async def delete_document(
    document_id: UUID,
    session: AsyncSession,
) -> None:
    """
    Delete a document database record.

    Ownership-aware deletion will be handled by the API layer
    when the knowledge-base document routes are migrated.
    """

    db_document = await session.get(
        Document,
        document_id,
    )

    if db_document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document with ID {document_id} not found.",
        )

    await session.delete(db_document)
    await session.commit()