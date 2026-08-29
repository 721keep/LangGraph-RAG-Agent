import asyncio
from statistics import mean
from typing import Any

from app.db.pgvector_utils import vector_store
from app.rag.reranker import rerank_documents

from evaluation.load_dataset import load_dataset
from evaluation.prepare_corpus import EVAL_THREAD_ID


VECTOR_TOP_K = 10
VECTOR_THRESHOLD = 0.50
RERANK_TOP_N = 5

EVIDENCE_THRESHOLDS = (
    0.20,
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
    vector_score: float,
) -> dict[str, Any]:
    """Convert a retrieved document to evaluation format."""

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
        "vector_score": float(vector_score),
    }


async def evaluate_case(
    case: dict[str, Any],
) -> dict[str, Any]:
    """
    Run the evaluation query through:

    Vector Top-K
        -> vector threshold
        -> reranker
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

    thresholded_results = [
        (document, float(score))
        for document, score in vector_results
        if float(score) >= VECTOR_THRESHOLD
    ]

    # No vector candidates survived.
    if not thresholded_results:
        return {
            "id": case["id"],
            "query": query,
            "query_type": case["query_type"],
            "answerable": case["answerable"],
            "expected_sources": case["expected_sources"],
            "vector_candidate_count": len(vector_results),
            "threshold_passed_count": 0,
            "reranked_sources": [],
        }

    documents = [
        document.page_content
        for document, _ in thresholded_results
    ]

    top_n = min(
        RERANK_TOP_N,
        len(documents),
    )

    rerank_results = await rerank_documents(
        query=query,
        documents=documents,
        top_n=top_n,
    )

    reranked_sources: list[dict[str, Any]] = []

    for rerank_result in rerank_results:
        candidate_index = rerank_result["index"]

        if not (
            0 <= candidate_index < len(thresholded_results)
        ):
            raise ValueError(
                f"{case['id']}: reranker returned "
                f"invalid candidate index "
                f"{candidate_index}."
            )

        document, vector_score = thresholded_results[
            candidate_index
        ]

        source = _document_to_source(
            document=document,
            vector_score=vector_score,
        )

        source["rerank_score"] = float(
            rerank_result["relevance_score"]
        )

        reranked_sources.append(source)

    return {
        "id": case["id"],
        "query": query,
        "query_type": case["query_type"],
        "answerable": case["answerable"],
        "expected_sources": case["expected_sources"],
        "vector_candidate_count": len(vector_results),
        "threshold_passed_count": len(
            thresholded_results
        ),
        "reranked_sources": reranked_sources,
    }


def _top_rerank_score(
    result: dict[str, Any],
) -> float:
    """Return the highest reranker score."""

    sources = result["reranked_sources"]

    if not sources:
        return 0.0

    return max(
        source["rerank_score"]
        for source in sources
    )


def _best_gold_rerank_score(
    result: dict[str, Any],
) -> float | None:
    """
    Return the highest reranker score belonging
    to a gold source.
    """

    if not result["answerable"]:
        return None

    gold_keys = {
        _source_key(source)
        for source in result["expected_sources"]
    }

    gold_scores = [
        source["rerank_score"]
        for source in result["reranked_sources"]
        if _source_key(source) in gold_keys
    ]

    if not gold_scores:
        return None

    return max(gold_scores)


def _top_source_is_gold(
    result: dict[str, Any],
) -> bool:
    """Check whether reranker rank 1 is a gold source."""

    if (
        not result["answerable"]
        or not result["reranked_sources"]
    ):
        return False

    gold_keys = {
        _source_key(source)
        for source in result["expected_sources"]
    }

    top_source = result["reranked_sources"][0]

    return _source_key(top_source) in gold_keys


def _print_case_result(
    result: dict[str, Any],
) -> None:
    """Print one complete pipeline result."""

    print()
    print("=" * 80)

    print(
        f"{result['id']} | "
        f"type={result['query_type']} | "
        f"answerable={result['answerable']}"
    )

    print(
        f"Query: {result['query']}"
    )

    print(
        f"Vector candidates: "
        f"{result['vector_candidate_count']}"
    )

    print(
        f"Passed vector threshold "
        f"({VECTOR_THRESHOLD:.2f}): "
        f"{result['threshold_passed_count']}"
    )

    if result["answerable"]:
        print(
            "Gold sources: "
            + ", ".join(
                f"chunk={source['chunk_index']}"
                for source
                in result["expected_sources"]
            )
        )
    else:
        print(
            "Gold sources: none "
            "(no-evidence case)"
        )

    print("-" * 80)
    print("Reranker ranking:")

    if not result["reranked_sources"]:
        print(
            "  No candidates survived "
            "vector threshold."
        )

    else:
        gold_keys = {
            _source_key(source)
            for source in result["expected_sources"]
        }

        for rank, source in enumerate(
            result["reranked_sources"],
            start=1,
        ):
            marker = ""

            if result["answerable"]:
                if _source_key(source) in gold_keys:
                    marker = "GOLD"

            print(
                f"  Rank {rank:>2} | "
                f"chunk={source['chunk_index']:<2} | "
                f"vector="
                f"{source['vector_score']:.4f} | "
                f"rerank="
                f"{source['rerank_score']:.4f} "
                f"{marker}"
            )

    top_score = _top_rerank_score(result)

    print("-" * 80)

    if result["answerable"]:
        best_gold = _best_gold_rerank_score(
            result
        )

        best_gold_text = (
            f"{best_gold:.4f}"
            if best_gold is not None
            else "N/A"
        )

        print(
            f"Top rerank score: "
            f"{top_score:.4f}"
        )

        print(
            f"Best GOLD rerank score: "
            f"{best_gold_text}"
        )

        print(
            f"Reranker Top-1 is GOLD: "
            f"{_top_source_is_gold(result)}"
        )

    else:
        print(
            f"No-Evidence top rerank score: "
            f"{top_score:.4f}"
        )


def evaluate_evidence_threshold(
    results: list[dict[str, Any]],
    evidence_threshold: float,
) -> dict[str, float]:
    """
    Evaluate one reranker evidence-confidence threshold.

    For answerable queries:
        top rerank score >= threshold
        -> pipeline accepts evidence

    For no-evidence queries:
        top rerank score < threshold
        -> pipeline correctly rejects evidence
    """

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

    answerable_acceptance = []

    gold_support = []

    false_refusals = []

    for result in answerable_results:
        top_score = _top_rerank_score(result)

        accepted = (
            top_score >= evidence_threshold
        )

        answerable_acceptance.append(
            1.0 if accepted else 0.0
        )

        false_refusals.append(
            0.0 if accepted else 1.0
        )

        best_gold_score = (
            _best_gold_rerank_score(result)
        )

        gold_accepted = (
            best_gold_score is not None
            and best_gold_score
            >= evidence_threshold
        )

        gold_support.append(
            1.0 if gold_accepted else 0.0
        )

    no_evidence_accuracy_values = []

    false_evidence_values = []

    for result in no_evidence_results:
        top_score = _top_rerank_score(result)

        rejected = (
            top_score < evidence_threshold
        )

        no_evidence_accuracy_values.append(
            1.0 if rejected else 0.0
        )

        false_evidence_values.append(
            0.0 if rejected else 1.0
        )

    answerable_accept_rate = mean(
        answerable_acceptance
    )

    gold_support_rate = mean(
        gold_support
    )

    false_refusal_rate = mean(
        false_refusals
    )

    if no_evidence_results:
        no_evidence_accuracy = mean(
            no_evidence_accuracy_values
        )

        false_evidence_rate = mean(
            false_evidence_values
        )
    else:
        no_evidence_accuracy = 0.0
        false_evidence_rate = 0.0

    balanced_accuracy = (
        answerable_accept_rate
        + no_evidence_accuracy
    ) / 2.0

    return {
        "threshold": evidence_threshold,
        "answerable_accept_rate":
            answerable_accept_rate,
        "gold_support_rate":
            gold_support_rate,
        "false_refusal_rate":
            false_refusal_rate,
        "no_evidence_accuracy":
            no_evidence_accuracy,
        "false_evidence_rate":
            false_evidence_rate,
        "balanced_accuracy":
            balanced_accuracy,
    }


def _print_score_overview(
    results: list[dict[str, Any]],
) -> None:
    """Print answerable and no-evidence rerank score distributions."""

    print()
    print("=" * 80)
    print("Reranker Evidence Score Overview")
    print("=" * 80)

    for result in results:
        top_score = _top_rerank_score(result)

        if result["answerable"]:
            best_gold = _best_gold_rerank_score(
                result
            )

            best_gold_text = (
                f"{best_gold:.4f}"
                if best_gold is not None
                else "N/A"
            )

            print(
                f"{result['id']} | "
                f"answerable | "
                f"top={top_score:.4f} | "
                f"best_gold={best_gold_text}"
            )

        else:
            print(
                f"{result['id']} | "
                f"no_evidence | "
                f"top={top_score:.4f}"
            )


def _print_separation_analysis(
    results: list[dict[str, Any]],
) -> None:
    """
    Check whether reranker scores create a clean gap
    between valid gold evidence and no-evidence queries.
    """

    answerable_gold_scores = [
        score
        for result in results
        if result["answerable"]
        for score in [
            _best_gold_rerank_score(result)
        ]
        if score is not None
    ]

    no_evidence_top_scores = [
        _top_rerank_score(result)
        for result in results
        if not result["answerable"]
    ]

    if (
        not answerable_gold_scores
        or not no_evidence_top_scores
    ):
        return

    min_gold_score = min(
        answerable_gold_scores
    )

    max_no_evidence_score = max(
        no_evidence_top_scores
    )

    gap = (
        min_gold_score
        - max_no_evidence_score
    )

    print()
    print("=" * 80)
    print("Evidence Score Separation Analysis")
    print("=" * 80)

    print(
        f"Minimum answerable GOLD rerank score: "
        f"{min_gold_score:.4f}"
    )

    print(
        f"Maximum no-evidence rerank score: "
        f"{max_no_evidence_score:.4f}"
    )

    print(
        f"Separation gap: "
        f"{gap:+.4f}"
    )

    if gap > 0:
        print(
            "Result: clean score separation exists."
        )

        print(
            "A reranker evidence threshold may be "
            "able to separate answerable and "
            "no-evidence cases."
        )

    else:
        print(
            "Result: score distributions overlap."
        )

        print(
            "Reranker score alone is not sufficient "
            "for perfect no-evidence classification."
        )


async def main() -> None:
    """Run full evidence-pipeline evaluation."""

    dataset = load_dataset()

    print("=" * 80)
    print("Evidence Pipeline Evaluation")
    print("=" * 80)

    print(
        f"Total cases: {len(dataset)}"
    )

    print(
        f"Vector Top-K: {VECTOR_TOP_K}"
    )

    print(
        f"Vector Threshold: "
        f"{VECTOR_THRESHOLD:.2f}"
    )

    print(
        f"Reranker Top-N: {RERANK_TOP_N}"
    )

    results: list[dict[str, Any]] = []

    for case in dataset:
        result = await evaluate_case(case)

        results.append(result)

        _print_case_result(result)

    _print_score_overview(results)

    _print_separation_analysis(results)

    print()
    print("=" * 105)
    print("Reranker Evidence Threshold Sweep")
    print("=" * 105)

    print(
        f"{'Thr':>6}"
        f"{'AnsAccept':>12}"
        f"{'GoldSupport':>13}"
        f"{'FalseRef':>11}"
        f"{'NoEvAcc':>11}"
        f"{'FalseEv':>11}"
        f"{'BalAcc':>10}"
    )

    print("-" * 105)

    for evidence_threshold in EVIDENCE_THRESHOLDS:
        metrics = evaluate_evidence_threshold(
            results=results,
            evidence_threshold=evidence_threshold,
        )

        print(
            f"{evidence_threshold:>6.2f}"
            f"{metrics['answerable_accept_rate']:>12.3f}"
            f"{metrics['gold_support_rate']:>13.3f}"
            f"{metrics['false_refusal_rate']:>11.3f}"
            f"{metrics['no_evidence_accuracy']:>11.3f}"
            f"{metrics['false_evidence_rate']:>11.3f}"
            f"{metrics['balanced_accuracy']:>10.3f}"
        )

    print("=" * 105)


if __name__ == "__main__":
    asyncio.run(main())