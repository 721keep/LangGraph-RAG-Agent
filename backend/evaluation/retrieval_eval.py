import asyncio
from statistics import mean
from typing import Any

from app.db.pgvector_utils import vector_store
from evaluation.load_dataset import load_dataset
from evaluation.metrics import (
    hit_at_k,
    recall_at_k,
    reciprocal_rank,
)
from evaluation.prepare_corpus import EVAL_THREAD_ID


TOP_K = 10
K_VALUES = (1, 3, 5, 10)


def _document_to_source(
    document: Any,
) -> dict[str, Any]:
    """Convert a retrieved LangChain Document to evaluation source format."""

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
    }


def _source_key(
    source: dict[str, Any],
) -> tuple[str, int]:
    """Return stable source identity for display comparison."""

    return (
        source["file_name"],
        source["chunk_index"],
    )


async def evaluate_case(
    case: dict[str, Any],
) -> dict[str, Any]:
    """
    Run vector-only retrieval for one answerable evaluation case.
    """

    query = case["query"]

    results = (
        await vector_store.asimilarity_search_with_relevance_scores(
            query=query,
            k=TOP_K,
            filter={
                "thread_id": str(EVAL_THREAD_ID),
            },
        )
    )

    retrieved_sources = [
        _document_to_source(document)
        for document, _ in results
    ]

    scores = [
        float(score)
        for _, score in results
    ]

    expected_sources = case["expected_sources"]

    metrics = {}

    for k in K_VALUES:
        metrics[f"hit@{k}"] = hit_at_k(
            retrieved_sources=retrieved_sources,
            expected_sources=expected_sources,
            k=k,
        )

        metrics[f"recall@{k}"] = recall_at_k(
            retrieved_sources=retrieved_sources,
            expected_sources=expected_sources,
            k=k,
        )

    metrics["rr@10"] = reciprocal_rank(
        retrieved_sources=retrieved_sources,
        expected_sources=expected_sources,
        k=TOP_K,
    )

    return {
        "id": case["id"],
        "query": query,
        "query_type": case["query_type"],
        "expected_sources": expected_sources,
        "retrieved_sources": retrieved_sources,
        "scores": scores,
        "metrics": metrics,
    }


def _print_case_result(
    result: dict[str, Any],
) -> None:
    """Print detailed retrieval ranking for one evaluation case."""

    print()
    print("=" * 80)

    print(
        f"{result['id']} | "
        f"type={result['query_type']}"
    )

    print(
        f"Query: {result['query']}"
    )

    gold_keys = {
        _source_key(source)
        for source in result["expected_sources"]
    }

    print(
        "Gold sources: "
        + ", ".join(
            f"chunk={source['chunk_index']}"
            for source in result["expected_sources"]
        )
    )

    print("-" * 80)
    print("Vector ranking:")

    for rank, (
        source,
        score,
    ) in enumerate(
        zip(
            result["retrieved_sources"],
            result["scores"],
        ),
        start=1,
    ):
        source_key = _source_key(source)

        marker = (
            "GOLD"
            if source_key in gold_keys
            else ""
        )

        print(
            f"  Rank {rank:>2} | "
            f"chunk={source['chunk_index']:<2} | "
            f"score={score:.4f} "
            f"{marker}"
        )

    print("-" * 80)

    metrics = result["metrics"]

    print(
        "Metrics: "
        f"Hit@1={metrics['hit@1']:.3f} | "
        f"Hit@3={metrics['hit@3']:.3f} | "
        f"Hit@5={metrics['hit@5']:.3f} | "
        f"Hit@10={metrics['hit@10']:.3f}"
    )

    print(
        "         "
        f"Recall@1={metrics['recall@1']:.3f} | "
        f"Recall@3={metrics['recall@3']:.3f} | "
        f"Recall@5={metrics['recall@5']:.3f} | "
        f"Recall@10={metrics['recall@10']:.3f}"
    )

    print(
        f"         RR@10={metrics['rr@10']:.3f}"
    )


async def main() -> None:
    """
    Run vector-only retrieval evaluation.

    No-evidence cases are intentionally excluded from this stage.
    """

    dataset = load_dataset()

    answerable_cases = [
        case
        for case in dataset
        if case["answerable"]
    ]

    skipped_cases = (
        len(dataset)
        - len(answerable_cases)
    )

    print("=" * 80)
    print("Vector Retrieval Baseline Evaluation")
    print("=" * 80)

    print(
        f"Total dataset cases: {len(dataset)}"
    )

    print(
        f"Answerable cases: {len(answerable_cases)}"
    )

    print(
        f"No-evidence cases skipped: {skipped_cases}"
    )

    print(
        f"Vector Top-K: {TOP_K}"
    )

    results = []

    for case in answerable_cases:
        result = await evaluate_case(case)

        results.append(result)

        _print_case_result(result)

    print()
    print("=" * 80)
    print("Aggregate Vector Retrieval Metrics")
    print("=" * 80)

    for k in K_VALUES:
        average_hit = mean(
            result["metrics"][f"hit@{k}"]
            for result in results
        )

        average_recall = mean(
            result["metrics"][f"recall@{k}"]
            for result in results
        )

        print(
            f"Hit@{k:<2}: {average_hit:.3f} | "
            f"Recall@{k:<2}: {average_recall:.3f}"
        )

    mrr_at_10 = mean(
        result["metrics"]["rr@10"]
        for result in results
    )

    print(
        f"MRR@10: {mrr_at_10:.3f}"
    )

    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())