from pathlib import Path
from uuid import UUID, uuid4
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from langchain.embeddings import init_embeddings
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import Docx2txtLoader, PyPDFLoader, TextLoader
from langchain_community.document_loaders.base import BaseLoader
from langchain_core.documents import Document
from langchain_postgres import PGVector
from loguru import logger

from app.config import settings

embeddings = init_embeddings(
    model=settings.embeddings_model_name,
    base_url=settings.embeddings_base_url,
    provider=settings.model_provider,
    api_key=settings.dashscope_api_key,
    check_embedding_ctx_length=False,
    chunk_size=10,
)


vector_store = PGVector(
    embeddings=embeddings,  # type: ignore
    connection=settings.pgvector_connection,
    collection_name=settings.pgvector_collection_name,
    use_jsonb=True,
    async_mode=True,
)


text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=1000,
    chunk_overlap=200,
    length_function=len,
)


DOCUMENT_LOADER_MAPPING: dict[str, type[BaseLoader]] = {
    ".pdf": PyPDFLoader,
    ".docx": Docx2txtLoader,
    ".txt": TextLoader,
}

allowd_extensions = list(DOCUMENT_LOADER_MAPPING.keys())


async def _load_and_split_documents(file_path: Path) -> list[Document]:
    """
    Load and split documents based on file extension.
    Raises:
        UnsupportedFileTypeError: If the file extension is not supported.
        Exception: For other loading or splitting errors.
    """

    file_extension = file_path.suffix.lower()
    if file_extension not in DOCUMENT_LOADER_MAPPING:
        raise ValueError(f"Unsupported file type: {file_extension}, Allowed types: {', '.join(allowd_extensions)}")
    loader = DOCUMENT_LOADER_MAPPING[file_extension](file_path)  # type: ignore
    documents = await loader.aload()
    splits = text_splitter.split_documents(documents)
    logger.info(f"Successfully loaded and split {file_path} into {len(splits)} chunks.")

    return splits


async def index_document_to_pgvector(
    file_path: Path,
    document_id: UUID,
    thread_id: UUID | None,
    user_id: UUID,
    knowledge_base_id: UUID | None = None,
) -> list[str]:
    """
    Index a document into PGVector.

    During the migration period, a document must belong to exactly
    one retrieval scope:

    - legacy thread scope
    - knowledge base scope
    """

    has_thread_scope = thread_id is not None
    has_knowledge_base_scope = knowledge_base_id is not None

    if has_thread_scope == has_knowledge_base_scope:
        raise ValueError(
            "Exactly one of thread_id or knowledge_base_id "
            "must be provided when indexing a document."
        )

    logger.info(
        f"Starting indexing for document: {file_path}, "
        f"document_id: {document_id}, "
        f"thread_id: {thread_id}, "
        f"knowledge_base_id: {knowledge_base_id}, "
        f"user_id: {user_id}"
    )

    splits = await _load_and_split_documents(file_path)

    for chunk_index, split in enumerate(splits):
        split.metadata["id"] = str(uuid4())
        split.metadata["file_name"] = file_path.name
        split.metadata["document_id"] = str(document_id)
        split.metadata["user_id"] = str(user_id)
        split.metadata["chunk_index"] = chunk_index

        if thread_id is not None:
            split.metadata["thread_id"] = str(thread_id)

        if knowledge_base_id is not None:
            split.metadata["knowledge_base_id"] = str(
                knowledge_base_id
            )

    try:
        doc_ids = await vector_store.aadd_documents(
            splits,
            ids=[
                split.metadata["id"]
                for split in splits
            ],
        )

        logger.info(
            f"Successfully indexed {len(splits)} chunks "
            f"for document {file_path} "
            f"(document_id: {document_id}) to PGVector."
        )

        return doc_ids

    except Exception as e:
        logger.error(
            f"Error adding documents to PGVector: {e}"
        )
        raise


async def search_documents_in_pgvector(query: str = "", k: int = 1, filter: dict | None = None) -> list[Document]:
    """Search documents in PGVector based on query, k and filter."""

    logger.info(f"Search documents with query: {query}, k: {k}, filter: {filter} in PGVector.")
    documents = await vector_store.asimilarity_search(query, k=k, filter=filter)
    if not documents:
        logger.info(f"No documents found for query: {query} and filter: {filter} in PGVector.")
    else:
        logger.info(f"Found {len(documents)} document chunks for query: {query} and filter: {filter} in PGVector.")
    return documents


async def delete_document_from_pgvector(document_ids: list[str]) -> None:
    """Delete documents from PGVector based on Document ID"""

    logger.info(f"Attempting to delete {len(document_ids)} document chunks from PGVector.")
    await vector_store.adelete(ids=document_ids)
    logger.info(f"Successfully deleted {len(document_ids)} document chunks from PGVector.")

async def delete_knowledge_base_from_pgvector(
    knowledge_base_id: UUID,
    user_id: UUID,
    session: AsyncSession,
) -> int:
    """
    Delete all PGVector chunks that belong to one user's knowledge base.

    The caller owns the database transaction and is responsible for commit
    or rollback.
    """

    statement = text(
        """
        DELETE FROM langchain_pg_embedding
        WHERE collection_id IN (
            SELECT uuid
            FROM langchain_pg_collection
            WHERE name = :collection_name
        )
        AND cmetadata->>'user_id' = :user_id
        AND cmetadata->>'knowledge_base_id' = :knowledge_base_id
        """
    )

    result = await session.execute(
        statement,
        {
            "collection_name": settings.pgvector_collection_name,
            "user_id": str(user_id),
            "knowledge_base_id": str(knowledge_base_id),
        },
    )

    deleted_count = result.rowcount or 0

    logger.info(
        "Deleted "
        f"{deleted_count} PGVector chunk(s) for "
        f"knowledge_base_id={knowledge_base_id}, "
        f"user_id={user_id}."
    )

    return deleted_count

async def delete_document_chunks_from_pgvector(
    document_id: UUID,
    user_id: UUID,
    session: AsyncSession,
) -> int:
    """
    Delete all PGVector chunks that belong to one document owned by a user.

    The caller owns the database transaction and is responsible for commit
    or rollback.
    """

    statement = text(
        """
        DELETE FROM langchain_pg_embedding
        WHERE collection_id IN (
            SELECT uuid
            FROM langchain_pg_collection
            WHERE name = :collection_name
        )
        AND cmetadata->>'user_id' = :user_id
        AND cmetadata->>'document_id' = :document_id
        """
    )

    result = await session.execute(
        statement,
        {
            "collection_name": settings.pgvector_collection_name,
            "user_id": str(user_id),
            "document_id": str(document_id),
        },
    )

    deleted_count = result.rowcount or 0

    logger.info(
        "Deleted "
        f"{deleted_count} PGVector chunk(s) for "
        f"document_id={document_id}, "
        f"user_id={user_id}."
    )

    return deleted_count