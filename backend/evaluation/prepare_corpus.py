from pathlib import Path
from uuid import UUID

from app.db.pgvector_utils import (
    delete_document_from_pgvector,
    index_document_to_pgvector,
    search_documents_in_pgvector,
)


FIXTURE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "retrieval_eval_source.txt"
)

# Fixed IDs make the evaluation corpus reproducible.
#
# These UUIDs are only used as PGVector metadata for the evaluation corpus.
# They do not represent real application users or chat threads.
EVAL_USER_ID = UUID(
    "00000000-0000-0000-0000-000000000101"
)

EVAL_THREAD_ID = UUID(
    "00000000-0000-0000-0000-000000000102"
)

EVAL_DOCUMENT_ID = UUID(
    "00000000-0000-0000-0000-000000000103"
)


async def _delete_existing_eval_chunks() -> int:
    """
    Delete previously indexed evaluation chunks.

    This makes prepare_corpus idempotent:
    running it repeatedly will not create duplicate chunks.
    """

    existing_chunks = await search_documents_in_pgvector(
        query="Aster-X1 evaluation corpus",
        k=100,
        filter={
            "document_id": str(EVAL_DOCUMENT_ID),
        },
    )

    if not existing_chunks:
        return 0

    chunk_ids = []

    for chunk in existing_chunks:
        chunk_id = chunk.metadata.get("id")

        if isinstance(chunk_id, str):
            chunk_ids.append(chunk_id)

    if not chunk_ids:
        return 0

    await delete_document_from_pgvector(chunk_ids)

    return len(chunk_ids)


async def prepare_corpus() -> list[str]:
    """
    Rebuild the dedicated retrieval evaluation corpus.
    """

    if not FIXTURE_PATH.exists():
        raise FileNotFoundError(
            f"Evaluation fixture not found: "
            f"{FIXTURE_PATH}"
        )

    print("=" * 80)
    print("Preparing Retrieval Evaluation Corpus")
    print("=" * 80)

    print(f"Fixture: {FIXTURE_PATH}")
    print(f"Evaluation thread_id: {EVAL_THREAD_ID}")
    print(f"Evaluation document_id: {EVAL_DOCUMENT_ID}")

    print("-" * 80)
    print("Checking for existing evaluation chunks...")

    deleted_count = await _delete_existing_eval_chunks()

    print(
        f"Deleted existing chunks: "
        f"{deleted_count}"
    )

    print("-" * 80)
    print("Indexing evaluation fixture...")

    chunk_ids = await index_document_to_pgvector(
        file_path=FIXTURE_PATH,
        document_id=EVAL_DOCUMENT_ID,
        thread_id=EVAL_THREAD_ID,
        user_id=EVAL_USER_ID,
    )

    print(
        f"Indexed evaluation chunks: "
        f"{len(chunk_ids)}"
    )

    print("=" * 80)
    print("Evaluation corpus prepared successfully.")
    print("=" * 80)

    return chunk_ids


async def main() -> None:
    await prepare_corpus()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())