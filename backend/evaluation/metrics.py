from typing import Any


Source = dict[str, Any]
SourceKey = tuple[str, int]


def _source_key(source: Source) -> SourceKey:
    """
    Convert a source dictionary into a stable identity.

    Evaluation matches sources by:
        (file_name, chunk_index)

    Runtime Source 1 / Source 2 labels are intentionally ignored
    because reranking may change their order.
    """

    file_name = source.get("file_name")
    chunk_index = source.get("chunk_index")

    if not isinstance(file_name, str):
        raise ValueError(
            "Source file_name must be a string."
        )

    if not isinstance(chunk_index, int):
        raise ValueError(
            "Source chunk_index must be an integer."
        )

    return file_name, chunk_index


def _validate_k(k: int) -> None:
    """Validate a retrieval cutoff."""

    if k < 1:
        raise ValueError(
            "k must be greater than or equal to 1."
        )


def _validate_expected_sources(
    expected_sources: list[Source],
) -> None:
    """
    Retrieval metrics require at least one gold source.

    No-evidence cases must be evaluated separately and should
    not be included in Hit@K / Recall@K / MRR.
    """

    if not expected_sources:
        raise ValueError(
            "Retrieval metrics require at least one "
            "expected source."
        )


def hit_at_k(
    retrieved_sources: list[Source],
    expected_sources: list[Source],
    k: int,
) -> float:
    """
    Return 1.0 if at least one gold source appears in the
    first k retrieved results, otherwise return 0.0.
    """

    _validate_k(k)
    _validate_expected_sources(expected_sources)

    gold_keys = {
        _source_key(source)
        for source in expected_sources
    }

    retrieved_keys = {
        _source_key(source)
        for source in retrieved_sources[:k]
    }

    return (
        1.0
        if gold_keys & retrieved_keys
        else 0.0
    )


def recall_at_k(
    retrieved_sources: list[Source],
    expected_sources: list[Source],
    k: int,
) -> float:
    """
    Calculate the proportion of gold sources found
    in the first k retrieved results.
    """

    _validate_k(k)
    _validate_expected_sources(expected_sources)

    gold_keys = {
        _source_key(source)
        for source in expected_sources
    }

    retrieved_keys = {
        _source_key(source)
        for source in retrieved_sources[:k]
    }

    matched_count = len(
        gold_keys & retrieved_keys
    )

    return matched_count / len(gold_keys)


def reciprocal_rank(
    retrieved_sources: list[Source],
    expected_sources: list[Source],
    k: int | None = None,
) -> float:
    """
    Calculate reciprocal rank for the first relevant result.

    Rank 1 -> 1.0
    Rank 2 -> 0.5
    Rank 3 -> 0.333...
    No relevant result -> 0.0
    """

    _validate_expected_sources(expected_sources)

    if k is not None:
        _validate_k(k)
        candidates = retrieved_sources[:k]
    else:
        candidates = retrieved_sources

    gold_keys = {
        _source_key(source)
        for source in expected_sources
    }

    for rank, source in enumerate(
        candidates,
        start=1,
    ):
        if _source_key(source) in gold_keys:
            return 1.0 / rank

    return 0.0


def main() -> None:
    """Run simple local checks for retrieval metrics."""

    expected_sources = [
        {
            "file_name": "retrieval_eval_source.txt",
            "chunk_index": 3,
            "page": None,
        },
        {
            "file_name": "retrieval_eval_source.txt",
            "chunk_index": 0,
            "page": None,
        },
    ]

    retrieved_sources = [
        {
            "file_name": "retrieval_eval_source.txt",
            "chunk_index": 8,
        },
        {
            "file_name": "retrieval_eval_source.txt",
            "chunk_index": 3,
        },
        {
            "file_name": "retrieval_eval_source.txt",
            "chunk_index": 0,
        },
    ]

    print("=" * 80)
    print("Retrieval Metrics Self-Test")
    print("=" * 80)

    print(
        f"Hit@1: "
        f"{hit_at_k(retrieved_sources, expected_sources, 1):.3f}"
    )

    print(
        f"Hit@3: "
        f"{hit_at_k(retrieved_sources, expected_sources, 3):.3f}"
    )

    print(
        f"Recall@1: "
        f"{recall_at_k(retrieved_sources, expected_sources, 1):.3f}"
    )

    print(
        f"Recall@2: "
        f"{recall_at_k(retrieved_sources, expected_sources, 2):.3f}"
    )

    print(
        f"Recall@3: "
        f"{recall_at_k(retrieved_sources, expected_sources, 3):.3f}"
    )

    print(
        f"Reciprocal Rank: "
        f"{reciprocal_rank(retrieved_sources, expected_sources):.3f}"
    )

    print("=" * 80)


if __name__ == "__main__":
    main()