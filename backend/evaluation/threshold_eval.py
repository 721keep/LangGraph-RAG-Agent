import asyncio
from statistics import mean
from typing import Any

from app.db.pgvector_utils import vector_store

from evaluation.load_dataset import load_dataset
from evaluation.prepare_corpus import EVAL_THREAD_ID


VECTOR_TOP_K = 10

THRESHOLDS = (
    0.00,
    0.30,
    0.40,
    0.50,
    0.60,
    0.70,
    0.80,
    0.90,
)


def _source_key(
    source: dict[str, Any],
) -> tuple[str, int]:
    """Return stable source identity."""

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


def _document_to_source(
    document: Any,
    score: float,
) -> dict[str, Any]:
    """Convert one retrieved document into evaluation format."""

    metadata = document.metadata

    file_name = metadata.get("file_name")
    chunk_index = metadata.get("chunk_index")

    if not isinstance(file_name, str):
        raise ValueError(
            "Retrieved document is missing a valid file_name."
        )

    if not isinstance(chunk_index, int):
        raise ValueError(
            "Retrieved document is missing a valid chunk_index."
        )

    return {
        "file_name": file_name,
        "chunk_index": chunk_index,
        "page": metadata.get("page"),
        "score": float(score),
    }


async def retrieve_case(
    case: dict[str, Any],
) -> dict[str, Any]:
    """
    Retrieve the fixed Top-K vector candidates once.

    Thresholds are applied afterwards in memory so that all
    thresholds are evaluated against the exact same ranking.
    """

    results = (
        await vector_store.asimilarity_search_with_relevance_scores(
            query=case["query"],
            k=VECTOR_TOP_K,
            filter={
                "thread_id": str(EVAL_THREAD_ID),
            },
        )
    )

    sources = [
        _document_to_source(
            document=document,
            score=float(score),
        )
        for document, score in results
    ]

    return {
        "id": case["id"],
        "query": case["query"],
        "query_type": case["query_type"],
        "answerable": case["answerable"],
        "expected_sources": case["expected_sources"],
        "sources": sources,
    }


def evaluate_answerable_case(
    result: dict[str, Any],
    threshold: float,
) -> dict[str, float | int | bool]:
    """
    Evaluate threshold behavior for an answerable query.
    """

    passed_sources = [
        source
        for source in result["sources"]
        if source["score"] >= threshold
    ]

    gold_keys = {
        _source_key(source)
        for source in result["expected_sources"]
    }

    passed_keys = {
        _source_key(source)
        for source in passed_sources
    }

    matched_gold_count = len(
        gold_keys & passed_keys
    )

    gold_count = len(gold_keys)

    gold_recall = (
        matched_gold_count / gold_count
        if gold_count
        else 0.0
    )

    has_gold_evidence = (
        matched_gold_count > 0
    )

    # This matches the current production status behavior:
    # zero threshold-passed candidates -> no_evidence.
    false_refusal = (
        len(passed_sources) == 0
    )

    return {
        "passed_count": len(passed_sources),
        "matched_gold_count": matched_gold_count,
        "gold_recall": gold_recall,
        "has_gold_evidence": has_gold_evidence,
        "false_refusal": false_refusal,
    }


def evaluate_no_evidence_case(
    result: dict[str, Any],
    threshold: float,
) -> dict[str, int | bool]:
    """
    Evaluate threshold behavior for a no-evidence query.

    For these cases, the correct threshold-level behavior is
    that no candidate survives.
    """

    passed_sources = [
        source
        for source in result["sources"]
        if source["score"] >= threshold
    ]

    correctly_rejected = (
        len(passed_sources) == 0
    )

    false_evidence = (
        len(passed_sources) > 0
    )

    return {
        "passed_count": len(passed_sources),
        "correctly_rejected": correctly_rejected,
        "false_evidence": false_evidence,
    }


def evaluate_threshold(
    results: list[dict[str, Any]],
    threshold: float,
) -> dict[str, float]:
    """Calculate aggregate metrics for one threshold."""

    answerable_results = [
        result
        for result in results
        if result["answerable"]
    ]

    no_evidence_results = [
        result
        for result in results
        if not result["answerable"]
    ]

    answerable_metrics = [
        evaluate_answerable_case(
            result=result,
            threshold=threshold,
        )
        for result in answerable_results
    ]

    no_evidence_metrics = [
        evaluate_no_evidence_case(
            result=result,
            threshold=threshold,
        )
        for result in no_evidence_results
    ]

    answerable_gold_hit_rate = mean(
        1.0
        if metric["has_gold_evidence"]
        else 0.0
        for metric in answerable_metrics
    )

    answerable_gold_recall = mean(
        float(metric["gold_recall"])
        for metric in answerable_metrics
    )

    false_refusal_rate = mean(
        1.0
        if metric["false_refusal"]
        else 0.0
        for metric in answerable_metrics
    )

    average_answerable_passed = mean(
        int(metric["passed_count"])
        for metric in answerable_metrics
    )

    if no_evidence_metrics:
        no_evidence_accuracy = mean(
            1.0
            if metric["correctly_rejected"]
            else 0.0
            for metric in no_evidence_metrics
        )

        false_evidence_rate = mean(
            1.0
            if metric["false_evidence"]
            else 0.0
            for metric in no_evidence_metrics
        )

        average_no_evidence_passed = mean(
            int(metric["passed_count"])
            for metric in no_evidence_metrics
        )
    else:
        no_evidence_accuracy = 0.0
        false_evidence_rate = 0.0
        average_no_evidence_passed = 0.0

    return {
        "threshold": threshold,
        "answerable_gold_hit_rate": answerable_gold_hit_rate,
        "answerable_gold_recall": answerable_gold_recall,
        "false_refusal_rate": false_refusal_rate,
        "no_evidence_accuracy": no_evidence_accuracy,
        "false_evidence_rate": false_evidence_rate,
        "avg_answerable_passed": average_answerable_passed,
        "avg_no_evidence_passed": average_no_evidence_passed,
    }


def _print_case_scores(
    results: list[dict[str, Any]],
) -> None:
    """
    Print score distributions before the threshold sweep.

    This helps explain why particular thresholds succeed or fail.
    """

    print()
    print("=" * 80)
    print("Vector Score Overview")
    print("=" * 80)

    for result in results:
        scores = [
            source["score"]
            for source in result["sources"]
        ]

        top_score = (
            max(scores)
            if scores
            else 0.0
        )

        if result["answerable"]:
            gold_keys = {
                _source_key(source)
                for source in result["expected_sources"]
            }

            gold_scores = [
                source["score"]
                for source in result["sources"]
                if _source_key(source) in gold_keys
            ]

            best_gold_score = (
                max(gold_scores)
                if gold_scores
                else 0.0
            )

            print(
                f"{result['id']} | "
                f"answerable | "
                f"top_score={top_score:.4f} | "
                f"best_gold={best_gold_score:.4f}"
            )

        else:
            print(
                f"{result['id']} | "
                f"no_evidence | "
                f"top_score={top_score:.4f}"
            )


async def main() -> None:
    """Run similarity-threshold sweep."""

    dataset = load_dataset()

    print("=" * 80)
    print("Similarity Threshold Evaluation")
    print("=" * 80)

    print(
        f"Total cases: {len(dataset)}"
    )

    print(
        f"Answerable cases: "
        f"{sum(1 for case in dataset if case['answerable'])}"
    )

    print(
        f"No-evidence cases: "
        f"{sum(1 for case in dataset if not case['answerable'])}"
    )

    print(
        f"Vector Top-K: {VECTOR_TOP_K}"
    )

    print("-" * 80)
    print("Retrieving vector candidates once for each case...")

    results: list[dict[str, Any]] = []

    for case in dataset:
        result = await retrieve_case(case)

        results.append(result)

    _print_case_scores(results)

    print()
    print("=" * 110)
    print("Threshold Sweep")
    print("=" * 110)

    print(
        f"{'Thr':>6}"
        f"{'GoldHit':>11}"
        f"{'GoldRecall':>13}"
        f"{'FalseRef':>11}"
        f"{'NoEvAcc':>11}"
        f"{'FalseEv':>11}"
        f"{'AnsPass':>11}"
        f"{'NoEvPass':>11}"
    )

    print("-" * 110)

    threshold_results = []

    for threshold in THRESHOLDS:
        metrics = evaluate_threshold(
            results=results,
            threshold=threshold,
        )

        threshold_results.append(metrics)

        print(
            f"{threshold:>6.2f}"
            f"{metrics['answerable_gold_hit_rate']:>11.3f}"
            f"{metrics['answerable_gold_recall']:>13.3f}"
            f"{metrics['false_refusal_rate']:>11.3f}"
            f"{metrics['no_evidence_accuracy']:>11.3f}"
            f"{metrics['false_evidence_rate']:>11.3f}"
            f"{metrics['avg_answerable_passed']:>11.2f}"
            f"{metrics['avg_no_evidence_passed']:>11.2f}"
        )

    print("=" * 110)


if __name__ == "__main__":
    asyncio.run(main())