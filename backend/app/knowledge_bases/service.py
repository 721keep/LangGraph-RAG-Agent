from collections.abc import Sequence
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import KnowledgeBase

from .schemas import KnowledgeBaseCreate, KnowledgeBaseUpdate
from loguru import logger

from app.db.pgvector_utils import (
    delete_knowledge_base_from_pgvector,
)

async def create_knowledge_base(
    knowledge_base_data: KnowledgeBaseCreate,
    user_id: UUID,
    session: AsyncSession,
) -> KnowledgeBase:
    """Create a new knowledge base for the current user."""

    knowledge_base = KnowledgeBase(
        **knowledge_base_data.model_dump(),
        user_id=user_id,
    )

    session.add(knowledge_base)
    await session.commit()
    await session.refresh(knowledge_base)

    return knowledge_base


async def get_user_knowledge_bases(
    user_id: UUID,
    session: AsyncSession,
) -> Sequence[KnowledgeBase]:
    """Return all knowledge bases owned by the current user."""

    statement = (
        select(KnowledgeBase)
        .where(KnowledgeBase.user_id == user_id)
        .order_by(desc(KnowledgeBase.created_at))
    )

    result = await session.execute(statement)

    return result.scalars().all()


async def get_knowledge_base(
    knowledge_base_id: UUID,
    user_id: UUID,
    session: AsyncSession,
) -> KnowledgeBase:
    """Return one knowledge base after verifying ownership."""

    knowledge_base = await session.get(
        KnowledgeBase,
        knowledge_base_id,
    )

    if knowledge_base is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Knowledge base with ID "
                f"{knowledge_base_id} not found."
            ),
        )

    if knowledge_base.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "You do not have permission to access "
                "this knowledge base."
            ),
        )

    return knowledge_base


async def update_knowledge_base(
    knowledge_base_id: UUID,
    knowledge_base_data: KnowledgeBaseUpdate,
    user_id: UUID,
    session: AsyncSession,
) -> KnowledgeBase:
    """Update a knowledge base owned by the current user."""

    knowledge_base = await get_knowledge_base(
        knowledge_base_id=knowledge_base_id,
        user_id=user_id,
        session=session,
    )

    update_data = knowledge_base_data.model_dump(
        exclude_unset=True,
    )

    for field, value in update_data.items():
        setattr(knowledge_base, field, value)

    await session.commit()
    await session.refresh(knowledge_base)

    return knowledge_base


async def delete_knowledge_base(
    knowledge_base_id: UUID,
    user_id: UUID,
    session: AsyncSession,
) -> None:
    """
    Delete a knowledge base and all of its vector chunks atomically.

    Database foreign keys handle:
    - Documents: ON DELETE CASCADE
    - Threads: knowledge_base_id -> SET NULL
    """

    db_knowledge_base = await get_knowledge_base(
        knowledge_base_id=knowledge_base_id,
        user_id=user_id,
        session=session,
    )

    try:
        deleted_chunk_count = (
            await delete_knowledge_base_from_pgvector(
                knowledge_base_id=knowledge_base_id,
                user_id=user_id,
                session=session,
            )
        )

        await session.delete(db_knowledge_base)

        await session.commit()

        logger.info(
            "Deleted knowledge base "
            f"{knowledge_base_id} for user_id={user_id}, "
            f"including {deleted_chunk_count} PGVector chunk(s)."
        )

    except Exception:
        await session.rollback()

        logger.exception(
            "Failed to delete knowledge base "
            f"{knowledge_base_id} for user_id={user_id}. "
            "Transaction rolled back."
        )

        raise