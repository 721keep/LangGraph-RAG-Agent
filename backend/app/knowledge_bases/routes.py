from uuid import UUID

from fastapi import APIRouter, status

from app.auth.dependencies import CurrentUserDep
from app.db.main import SessionDep

from . import service as knowledge_base_service
from .schemas import (
    KnowledgeBaseCreate,
    KnowledgeBasePublic,
    KnowledgeBaseUpdate,
)


knowledge_base_router = APIRouter()


@knowledge_base_router.post(
    "/",
    response_model=KnowledgeBasePublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_knowledge_base(
    knowledge_base_data: KnowledgeBaseCreate,
    current_user: CurrentUserDep,
    session: SessionDep,
):
    """Create a new knowledge base for the current user."""

    return await knowledge_base_service.create_knowledge_base(
        knowledge_base_data=knowledge_base_data,
        user_id=current_user.id,
        session=session,
    )


@knowledge_base_router.get(
    "/",
    response_model=list[KnowledgeBasePublic],
)
async def get_user_knowledge_bases(
    current_user: CurrentUserDep,
    session: SessionDep,
):
    """Return all knowledge bases owned by the current user."""

    return await knowledge_base_service.get_user_knowledge_bases(
        user_id=current_user.id,
        session=session,
    )


@knowledge_base_router.get(
    "/{knowledge_base_id}",
    response_model=KnowledgeBasePublic,
)
async def get_knowledge_base(
    knowledge_base_id: UUID,
    current_user: CurrentUserDep,
    session: SessionDep,
):
    """Return one knowledge base owned by the current user."""

    return await knowledge_base_service.get_knowledge_base(
        knowledge_base_id=knowledge_base_id,
        user_id=current_user.id,
        session=session,
    )


@knowledge_base_router.patch(
    "/{knowledge_base_id}",
    response_model=KnowledgeBasePublic,
)
async def update_knowledge_base(
    knowledge_base_id: UUID,
    knowledge_base_data: KnowledgeBaseUpdate,
    current_user: CurrentUserDep,
    session: SessionDep,
):
    """Update one knowledge base owned by the current user."""

    return await knowledge_base_service.update_knowledge_base(
        knowledge_base_id=knowledge_base_id,
        knowledge_base_data=knowledge_base_data,
        user_id=current_user.id,
        session=session,
    )


@knowledge_base_router.delete(
    "/{knowledge_base_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_knowledge_base(
    knowledge_base_id: UUID,
    current_user: CurrentUserDep,
    session: SessionDep,
):
    """Delete one knowledge base owned by the current user."""

    await knowledge_base_service.delete_knowledge_base(
        knowledge_base_id=knowledge_base_id,
        user_id=current_user.id,
        session=session,
    )