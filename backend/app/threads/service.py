from collections.abc import Sequence
from uuid import UUID

from fastapi import HTTPException, status
from langgraph.checkpoint.base import BaseCheckpointSaver
from loguru import logger
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Thread
from app.knowledge_bases import service as knowledge_base_service

from .schemas import ThreadKnowledgeBaseUpdate, ThreadUpdate


async def create_new_thread(user_id: UUID, session: AsyncSession) -> Thread:
    new_thread = Thread()
    new_thread.user_id = user_id
    session.add(new_thread)
    await session.commit()
    return new_thread


async def get_user_threads(user_id: UUID, session: AsyncSession) -> Sequence[Thread]:
    statement = select(Thread).where(Thread.user_id == user_id).order_by(desc(Thread.created_at))
    result = await session.execute(statement)
    return result.scalars().all()


async def get_thread(thread_id: UUID, user_id: UUID, session: AsyncSession) -> Thread:
    db_thread = await session.get(Thread, thread_id)
    if db_thread is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread with ID {thread_id} not found.",
        )
    if db_thread.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to get this thread.",
        )
    return db_thread


async def update_thread(thread_data: ThreadUpdate, thread_id: UUID, user_id: UUID, session: AsyncSession) -> Thread:
    db_thread = await session.get(Thread, thread_id)
    if db_thread is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread with ID {thread_id} not found.",
        )
    if db_thread.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to update this thread.",
        )
    db_thread.title = thread_data.title
    await session.commit()
    await session.refresh(db_thread)
    return db_thread

async def set_thread_knowledge_base(
    thread_id: UUID,
    knowledge_base_data: ThreadKnowledgeBaseUpdate,
    user_id: UUID,
    session: AsyncSession,
) -> Thread:
    """
    Bind, switch, or unbind a knowledge base for a thread.

    Both the thread and the target knowledge base must belong
    to the current user.
    """

    # Verify that the thread belongs to the current user.
    db_thread = await get_thread(
        thread_id=thread_id,
        user_id=user_id,
        session=session,
    )

    knowledge_base_id = knowledge_base_data.knowledge_base_id

    # When binding or switching to a knowledge base,
    # verify that the knowledge base also belongs to the user.
    if knowledge_base_id is not None:
        await knowledge_base_service.get_knowledge_base(
            knowledge_base_id=knowledge_base_id,
            user_id=user_id,
            session=session,
        )

    # None means unbind the current knowledge base.
    db_thread.knowledge_base_id = knowledge_base_id

    await session.commit()
    await session.refresh(db_thread)

    return db_thread

async def delete_thread(
    thread_id: UUID,
    user_id: UUID,
    session: AsyncSession,
    checkpointer: BaseCheckpointSaver,
) -> None:
    db_thread = await session.get(Thread, thread_id)

    if db_thread is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread with ID {thread_id} not found.",
        )

    if db_thread.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to delete this thread.",
        )

    await session.delete(db_thread)
    await session.commit()

    try:
        logger.info(
            f"Attempting to delete thread {thread_id} "
            "from checkpointer (chat messages)"
        )

        await checkpointer.adelete_thread(
            thread_id=str(thread_id)
        )

        logger.info(
            f"Successfully deleted thread {thread_id} "
            "from checkpointer (chat messages)"
        )

    except Exception as e:
        logger.error(
            f"Failed to delete thread {thread_id} "
            f"from checkpointer (chat messages): {str(e)}"
        )
