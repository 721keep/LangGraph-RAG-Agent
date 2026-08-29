import asyncio
from pathlib import Path

from app.db.pgvector_utils import _load_and_split_documents


FIXTURE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "retrieval_eval_source.txt"
)


async def main() -> None:
    """Inspect how the evaluation fixture is split into chunks."""

    if not FIXTURE_PATH.exists():
        raise FileNotFoundError(
            f"Evaluation fixture not found: {FIXTURE_PATH}"
        )

    print(f"Loading fixture: {FIXTURE_PATH}")
    print("=" * 80)

    chunks = await _load_and_split_documents(FIXTURE_PATH)

    print(f"Total chunks: {len(chunks)}")
    print("=" * 80)

    for chunk_index, chunk in enumerate(chunks):
        print()
        print("=" * 80)
        print(f"Chunk Index: {chunk_index}")
        print(f"Character Count: {len(chunk.page_content)}")
        print(f"Metadata: {chunk.metadata}")
        print("-" * 80)
        print(chunk.page_content)
        print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())