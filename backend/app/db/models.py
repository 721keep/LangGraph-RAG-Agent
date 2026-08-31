from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, String, func
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(AsyncAttrs, DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    username: Mapped[str] = mapped_column(String(16), unique=True)
    email: Mapped[str] = mapped_column(String(40), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    first_name: Mapped[str] = mapped_column(String(25), nullable=True)
    last_name: Mapped[str] = mapped_column(String(25), nullable=True)
    is_verified: Mapped[bool] = mapped_column(default=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self):
        return f"<User {self.username}>"


class Thread(Base):
    __tablename__ = "threads"

    id: Mapped[UUID] = mapped_column(
        primary_key=True,
        default=uuid4,
    )

    title: Mapped[str] = mapped_column(
        String(100),
        default="New Chat",
    )

    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now()
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )

    knowledge_base_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "knowledge_bases.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    def __repr__(self):
        return f"<Thread {self.title}>"


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)

    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now()
    )

    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self):
        return f"<KnowledgeBase {self.name}>"


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[UUID] = mapped_column(
        primary_key=True,
        default=uuid4,
    )

    file_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    uploaded_at: Mapped[datetime] = mapped_column(
        server_default=func.now(),
    )

    # Legacy relation kept temporarily during the migration
    # from thread-scoped documents to knowledge-base-scoped documents.
    thread_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("threads.id", ondelete="CASCADE"),
        nullable=True,
    )

    knowledge_base_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        default="ready",
        server_default="ready",
        nullable=False,
    )

    error_message: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    def __repr__(self):
        return f"<Document {self.file_name}>"