import re
from typing import Any


Source = dict[str, Any]
SourceKey = tuple[str, int]


CITATION_PATTERN = re.compile(
    r"\[Source\s+(\d+)\]",
    flags=re.IGNORECASE,
)


def extract_citation_ids(
    answer: str,
) -> list[int]:
    """
    Extract unique citation IDs from an LLM answer.

    Example:
        "[Source 1] ... [Source 2] ... [Source 1]"

    Returns:
        [1, 2]

    Duplicate citations are removed while preserving
    their first appearance order.
    """

    if not isinstance(answer, str):
        raise ValueError(
            "answer must be a string."
        )

    citation_ids: list[int] = []
    seen_ids: set[int] = set()

    for match in CITATION_PATTERN.finditer(answer):
        citation_id = int(match.group(1))

        if citation_id not in seen_ids:
            seen_ids.add(citation_id)
            citation_ids.append(citation_id)

    return citation_ids


def _source_key(
    source: Source,
) -> SourceKey:
    """
    Build the stable source identity used by evaluation.

    Runtime Source IDs may change after reranking, so
    Gold matching still uses:

        (file_name, chunk_index)
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


def _build_source_id_map(
    sources: list[Source],
) -> dict[int, Source]:
    """
    Map runtime citation IDs to returned Tool sources.

    Example:
        1 -> chunk 6
        2 -> chunk 5
    """

    source_map: dict[int, Source] = {}

    for source in sources:
        source_id = source.get("source_id")

        if not isinstance(source_id, int):
            raise ValueError(
                "Tool source_id must be an integer."
            )

        if source_id in source_map:
            raise ValueError(
                f"Duplicate source_id: {source_id}"
            )

        source_map[source_id] = source

    return source_map


def evaluate_citation_case(
    answer: str,
    sources: list[Source],
    expected_sources: list[Source],
    answerable: bool,
) -> dict[str, Any]:
    """
    Evaluate citations for one final LLM answer.

    Metrics:

    Citation Presence:
        Does the answer contain at least one citation?

    Citation Validity Rate:
        What proportion of cited Source IDs actually exist
        in the Tool result?

    Gold Citation Hit:
        Does at least one valid citation point to a Gold chunk?

    Unsupported Citation Rate:
        What proportion of cited Source IDs do not exist
        in the Tool result?

    No-Evidence Citation Accuracy:
        For no-evidence cases, did the answer correctly
        avoid all citations?
    """

    citation_ids = extract_citation_ids(
        answer
    )

    source_id_map = _build_source_id_map(
        sources
    )

    citation_presence = (
        1.0
        if citation_ids
        else 0.0
    )

    valid_citation_ids = [
        citation_id
        for citation_id in citation_ids
        if citation_id in source_id_map
    ]

    unsupported_citation_ids = [
        citation_id
        for citation_id in citation_ids
        if citation_id not in source_id_map
    ]

    if citation_ids:
        citation_validity_rate = (
            len(valid_citation_ids)
            / len(citation_ids)
        )

        unsupported_citation_rate = (
            len(unsupported_citation_ids)
            / len(citation_ids)
        )
    else:
        citation_validity_rate = 0.0
        unsupported_citation_rate = 0.0

    gold_citation_hit = 0.0

    if answerable:
        if not expected_sources:
            raise ValueError(
                "Answerable citation evaluation "
                "requires expected_sources."
            )

        gold_keys = {
            _source_key(source)
            for source in expected_sources
        }

        for citation_id in valid_citation_ids:
            cited_source = source_id_map[
                citation_id
            ]

            if _source_key(
                cited_source
            ) in gold_keys:
                gold_citation_hit = 1.0
                break

        no_evidence_citation_accuracy = None

    else:
        if expected_sources:
            raise ValueError(
                "No-evidence citation evaluation "
                "requires expected_sources to be empty."
            )

        no_evidence_citation_accuracy = (
            1.0
            if not citation_ids
            else 0.0
        )

    return {
        "citation_ids": citation_ids,
        "valid_citation_ids":
            valid_citation_ids,
        "unsupported_citation_ids":
            unsupported_citation_ids,

        "citation_presence":
            citation_presence,

        "citation_validity_rate":
            citation_validity_rate,

        "gold_citation_hit":
            gold_citation_hit,

        "unsupported_citation_rate":
            unsupported_citation_rate,

        "no_evidence_citation_accuracy":
            no_evidence_citation_accuracy,
    }


def _print_result(
    name: str,
    metrics: dict[str, Any],
) -> None:
    """Print one citation evaluation result."""

    print("-" * 80)
    print(name)

    print(
        f"  citation_ids="
        f"{metrics['citation_ids']}"
    )

    print(
        f"  valid_citation_ids="
        f"{metrics['valid_citation_ids']}"
    )

    print(
        f"  unsupported_citation_ids="
        f"{metrics['unsupported_citation_ids']}"
    )

    print(
        f"  Citation Presence="
        f"{metrics['citation_presence']:.3f}"
    )

    print(
        f"  Citation Validity="
        f"{metrics['citation_validity_rate']:.3f}"
    )

    print(
        f"  Gold Citation Hit="
        f"{metrics['gold_citation_hit']:.3f}"
    )

    print(
        f"  Unsupported Citation Rate="
        f"{metrics['unsupported_citation_rate']:.3f}"
    )

    no_evidence_accuracy = metrics[
        "no_evidence_citation_accuracy"
    ]

    if no_evidence_accuracy is not None:
        print(
            "  No-Evidence Citation Accuracy="
            f"{no_evidence_accuracy:.3f}"
        )


def main() -> None:
    """Run deterministic Citation Metrics self-tests."""

    sources = [
        {
            "source_id": 1,
            "file_name":
                "retrieval_eval_source.txt",
            "chunk_index": 6,
        },
        {
            "source_id": 2,
            "file_name":
                "retrieval_eval_source.txt",
            "chunk_index": 5,
        },
    ]

    expected_sources = [
        {
            "file_name":
                "retrieval_eval_source.txt",
            "chunk_index": 6,
            "page": None,
        }
    ]

    print("=" * 80)
    print("Citation Evaluation Self-Test")
    print("=" * 80)

    # Case 1:
    # Valid citation pointing to Gold evidence.
    correct = evaluate_citation_case(
        answer=(
            "Administrator 可以创建和删除"
            "系统用户。[Source 1]"
        ),
        sources=sources,
        expected_sources=expected_sources,
        answerable=True,
    )

    _print_result(
        "Case 1 - Correct Gold Citation",
        correct,
    )

    assert (
        correct["citation_ids"] == [1]
    )

    assert (
        correct["citation_presence"] == 1.0
    )

    assert (
        correct["citation_validity_rate"] == 1.0
    )

    assert (
        correct["gold_citation_hit"] == 1.0
    )

    assert (
        correct["unsupported_citation_rate"] == 0.0
    )

    # Case 2:
    # Citation hallucination: Source 3 was never returned.
    unsupported = evaluate_citation_case(
        answer=(
            "Administrator 拥有账户管理权限。"
            "[Source 3]"
        ),
        sources=sources,
        expected_sources=expected_sources,
        answerable=True,
    )

    _print_result(
        "Case 2 - Unsupported Citation",
        unsupported,
    )

    assert (
        unsupported["citation_validity_rate"]
        == 0.0
    )

    assert (
        unsupported["gold_citation_hit"]
        == 0.0
    )

    assert (
        unsupported[
            "unsupported_citation_rate"
        ]
        == 1.0
    )

    # Case 3:
    # One valid Gold citation and one hallucinated citation.
    mixed = evaluate_citation_case(
        answer=(
            "Administrator 可以管理用户。"
            "[Source 1] "
            "其他细节见 [Source 3]。"
        ),
        sources=sources,
        expected_sources=expected_sources,
        answerable=True,
    )

    _print_result(
        "Case 3 - Mixed Citations",
        mixed,
    )

    assert (
        mixed["citation_ids"]
        == [1, 3]
    )

    assert (
        mixed["citation_validity_rate"]
        == 0.5
    )

    assert (
        mixed["gold_citation_hit"]
        == 1.0
    )

    assert (
        mixed["unsupported_citation_rate"]
        == 0.5
    )

    # Case 4:
    # Correct no-evidence answer with no citation.
    no_evidence_correct = (
        evaluate_citation_case(
            answer=(
                "当前文档中没有足够证据"
                "回答该问题。"
            ),
            sources=[],
            expected_sources=[],
            answerable=False,
        )
    )

    _print_result(
        "Case 4 - Correct No-Evidence",
        no_evidence_correct,
    )

    assert (
        no_evidence_correct[
            "citation_presence"
        ]
        == 0.0
    )

    assert (
        no_evidence_correct[
            "no_evidence_citation_accuracy"
        ]
        == 1.0
    )

    # Case 5:
    # No-evidence answer incorrectly invents a citation.
    no_evidence_bad = (
        evaluate_citation_case(
            answer=(
                "设备默认是黑色。[Source 1]"
            ),
            sources=[],
            expected_sources=[],
            answerable=False,
        )
    )

    _print_result(
        "Case 5 - Invalid No-Evidence Citation",
        no_evidence_bad,
    )

    assert (
        no_evidence_bad[
            "no_evidence_citation_accuracy"
        ]
        == 0.0
    )

    assert (
        no_evidence_bad[
            "unsupported_citation_rate"
        ]
        == 1.0
    )

    print("=" * 80)
    print(
        "All citation metric self-tests passed."
    )
    print("=" * 80)


if __name__ == "__main__":
    main()