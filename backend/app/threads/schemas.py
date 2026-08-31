from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ThreadUpdate(BaseModel):
    title: str = Field(
        min_length=1,
        max_length=100,
    )


class ThreadKnowledgeBaseUpdate(BaseModel):
    knowledge_base_id: UUID | None = None


class ThreadPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    user_id: UUID
    created_at: datetime
    knowledge_base_id: UUID | None