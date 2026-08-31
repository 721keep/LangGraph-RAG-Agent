from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


DocumentStatus = Literal[
    "processing",
    "ready",
    "failed",
]


class DocumentCreate(BaseModel):
    file_name: str = Field(
        min_length=1,
        max_length=255,
    )

    # Legacy scope kept temporarily during migration.
    thread_id: UUID | None = None

    # New knowledge-base scope.
    knowledge_base_id: UUID | None = None

    status: DocumentStatus = "ready"

    error_message: str | None = Field(
        default=None,
        max_length=500,
    )

    @model_validator(mode="after")
    def validate_document_scope(self):
        """
        During migration, a document must belong to exactly one scope:
        either a legacy thread or a knowledge base.
        """

        has_thread = self.thread_id is not None
        has_knowledge_base = self.knowledge_base_id is not None

        if has_thread == has_knowledge_base:
            raise ValueError(
                "Exactly one of thread_id or knowledge_base_id "
                "must be provided."
            )

        return self


# Temporary backward-compatible alias.
# Existing document routes/service still import the old misspelled name.
DocumetCreate = DocumentCreate


class DocumentPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    file_name: str
    uploaded_at: datetime

    thread_id: UUID | None
    knowledge_base_id: UUID | None

    status: DocumentStatus
    error_message: str | None


class DocumentUploadResponse(BaseModel):
    document_id: UUID
    message: str


class DocumentDeleteResponse(BaseModel):
    message: str