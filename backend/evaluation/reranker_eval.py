import asyncio
from statistics import mean
from typing import Any
import argparse
from app.db.pgvector_utils import vector_store
from app.rag.reranker import rerank_documents

from evaluation.load_dataset import load_dataset
from evaluation.metrics import (
    hit_at_k,
    recall_at_k,
    reciprocal_rank,
)
from evaluation.prepare_corpus import EVAL_THREAD_ID


VECTOR_TOP_K = 10
RERANK_TOP_N = 5
K_VALUES = (1, 3, 5)



def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments for reranker evaluation."""

    parser = argparse.ArgumentParser(
        description="Run reranker evaluation."
    )

    parser.add_argument(
        "--case",
        dest="case_ids",
        nargs="+",
        default=None,
        help=(
            "Evaluate only the specified case IDs, "
            "for example: --case ret_011 ret_012"
        ),
    )

    return parser.parse_args()

def _document_to_source(
    document: Any,
) -> dict[str, Any]:
    """Convert a retrieved document into evaluation source format."""

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
    """Return stable source identity."""

    return (
        source["file_name"],
        source["chunk_index"],
    )


def _calculate_metrics(
    retrieved_sources: list[dict[str, Any]],
    expected_sources: list[dict[str, Any]],
) -> dict[str, float]:
    """Calculate retrieval metrics for one ranking."""

    metrics: dict[str, float] = {}

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

    metrics["rr@5"] = reciprocal_rank(
        retrieved_sources=retrieved_sources,
        expected_sources=expected_sources,
        k=RERANK_TOP_N,
    )

    return metrics


async def evaluate_case(
    case: dict[str, Any],
) -> dict[str, Any]:
    """
    Compare vector ranking with reranked ranking
    for one answerable evaluation case.
    """

    query = case["query"]

    vector_results = (
        await vector_store.asimilarity_search_with_relevance_scores(
            query=query,
            k=VECTOR_TOP_K,
            filter={
                "thread_id": str(EVAL_THREAD_ID),
            },
        )
    )

    if not vector_results:
        raise RuntimeError(
            f"{case['id']}: vector retrieval returned no candidates."
        )

    vector_sources = [
        _document_to_source(document)
        for document, _ in vector_results
    ]

    vector_scores = [
        float(score)
        for _, score in vector_results
    ]

    documents = [
        document.page_content
        for document, _ in vector_results
    ]

    rerank_results = await rerank_documents(
        query=query,
        documents=documents,
        top_n=RERANK_TOP_N,
    )

    reranked_sources: list[dict[str, Any]] = []
    rerank_scores: list[float] = []
    reranked_vector_scores: list[float] = []

    for rerank_result in rerank_results:
        candidate_index = rerank_result["index"]
        rerank_score = rerank_result["relevance_score"]

        if not (
            0 <= candidate_index < len(vector_results)
        ):
            raise ValueError(
                f"{case['id']}: reranker returned invalid "
                f"candidate index {candidate_index}."
            )

        document, vector_score = vector_results[
            candidate_index
        ]

        reranked_sources.append(
            _document_to_source(document)
        )

        rerank_scores.append(
            float(rerank_score)
        )

        reranked_vector_scores.append(
            float(vector_score)
        )

    if not reranked_sources:
        raise RuntimeError(
            f"{case['id']}: reranker returned no results."
        )

    expected_sources = case["expected_sources"]

    # Fair comparison:
    # Vector and Reranker are both evaluated with cutoff <= 5.
    vector_metrics = _calculate_metrics(
        retrieved_sources=vector_sources,
        expected_sources=expected_sources,
    )

    rerank_metrics = _calculate_metrics(
        retrieved_sources=reranked_sources,
        expected_sources=expected_sources,
    )

    return {
        "id": case["id"],
        "query": query,
        "query_type": case["query_type"],
        "expected_sources": expected_sources,
        "vector_sources": vector_sources,
        "vector_scores": vector_scores,
        "reranked_sources": reranked_sources,
        "rerank_scores": rerank_scores,
        "reranked_vector_scores": reranked_vector_scores,
        "vector_metrics": vector_metrics,
        "rerank_metrics": rerank_metrics,
    }


def _print_case_result(
    result: dict[str, Any],
) -> None:
    """Print vector and reranker rankings for one case."""

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
    print("Vector Top-5:")

    for rank, (
        source,
        score,
    ) in enumerate(
        zip(
            result["vector_sources"][:RERANK_TOP_N],
            result["vector_scores"][:RERANK_TOP_N],
        ),
        start=1,
    ):
        marker = (
            "GOLD"
            if _source_key(source) in gold_keys
            else ""
        )

        print(
            f"  Rank {rank:>2} | "
            f"chunk={source['chunk_index']:<2} | "
            f"vector={score:.4f} "
            f"{marker}"
        )

    print("-" * 80)
    print("Reranker Top-5:")

    for rank, (
        source,
        vector_score,
        rerank_score,
    ) in enumerate(
        zip(
            result["reranked_sources"],
            result["reranked_vector_scores"],
            result["rerank_scores"],
        ),
        start=1,
    ):
        marker = (
            "GOLD"
            if _source_key(source) in gold_keys
            else ""
        )

        print(
            f"  Rank {rank:>2} | "
            f"chunk={source['chunk_index']:<2} | "
            f"vector={vector_score:.4f} | "
            f"rerank={rerank_score:.4f} "
            f"{marker}"
        )

    vector_metrics = result["vector_metrics"]
    rerank_metrics = result["rerank_metrics"]

    print("-" * 80)

    print(
        "Vector : "
        f"Hit@1={vector_metrics['hit@1']:.3f} | "
        f"Hit@3={vector_metrics['hit@3']:.3f} | "
        f"Hit@5={vector_metrics['hit@5']:.3f} | "
        f"RR@5={vector_metrics['rr@5']:.3f}"
    )

    print(
        "Rerank : "
        f"Hit@1={rerank_metrics['hit@1']:.3f} | "
        f"Hit@3={rerank_metrics['hit@3']:.3f} | "
        f"Hit@5={rerank_metrics['hit@5']:.3f} | "
        f"RR@5={rerank_metrics['rr@5']:.3f}"
    )


def _average_metric(
    results: list[dict[str, Any]],
    result_key: str,
    metric_key: str,
) -> float:
    """Average one metric across evaluation cases."""

    return mean(
        result[result_key][metric_key]
        for result in results
    )


async def main() -> None:
    """Run vector vs reranker evaluation."""
    
    args = _parse_args()
    dataset = load_dataset()
    if args.case_ids:
        available_case_ids = {
            case["id"]
            for case in dataset
        }

        unknown_case_ids = [
            case_id
            for case_id in args.case_ids
            if case_id not in available_case_ids
        ]

        if unknown_case_ids:
            raise ValueError(
                "Unknown evaluation case ID(s): "
                + ", ".join(unknown_case_ids)
            )

        requested_case_ids = set(args.case_ids)

        dataset = [
            case
            for case in dataset
            if case["id"] in requested_case_ids
        ]

    answerable_cases = [
        case
        for case in dataset
        if case["answerable"]
    ]

    print("=" * 80)
    print("Vector vs Reranker Evaluation")
    print("=" * 80)

    print(
        f"Answerable cases: {len(answerable_cases)}"
    )

    print(
        f"Vector candidate Top-K: {VECTOR_TOP_K}"
    )

    print(
        f"Reranker Top-N: {RERANK_TOP_N}"
    )

    results = []

    for case in answerable_cases:
        result = await evaluate_case(case)

        results.append(result)

        _print_case_result(result)

    print()
    print("=" * 80)
    print("Aggregate Vector vs Reranker Metrics")
    print("=" * 80)

    print(
        f"{'Metric':<12}"
        f"{'Vector':>12}"
        f"{'Reranker':>12}"
        f"{'Delta':>12}"
    )

    print("-" * 48)

    metric_names = [
        "hit@1",
        "hit@3",
        "hit@5",
        "recall@1",
        "recall@3",
        "recall@5",
        "rr@5",
    ]

    for metric_name in metric_names:
        vector_value = _average_metric(
            results=results,
            result_key="vector_metrics",
            metric_key=metric_name,
        )

        rerank_value = _average_metric(
            results=results,
            result_key="rerank_metrics",
            metric_key=metric_name,
        )

        delta = rerank_value - vector_value

        print(
            f"{metric_name:<12}"
            f"{vector_value:>12.3f}"
            f"{rerank_value:>12.3f}"
            f"{delta:>+12.3f}"
        )

    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())