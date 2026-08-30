import argparse
import asyncio
from statistics import mean
from typing import Any

from app.config import settings

from evaluation.agent_citation_eval import (
    _build_eval_agent,
    evaluate_case as run_agent_case,
)
from evaluation.answer_eval import (
    evaluate_answer_case,
)
from evaluation.answer_judge import (
    judge_answer_case,
)
from evaluation.load_dataset import (
    load_dataset,
)


def _default_model_name() -> str:
    """Return the first configured chat model."""

    if not settings.model_names:
        raise RuntimeError(
            "No model is configured in MODEL_NAMES."
        )

    return settings.model_names[0]


def _select_cases(
    dataset: list[dict[str, Any]],
    requested_ids: list[str] | None,
) -> list[dict[str, Any]]:
    """Select requested evaluation cases."""

    if not requested_ids:
        return dataset

    available_ids = {
        case["id"]
        for case in dataset
    }

    unknown_ids = (
        set(requested_ids)
        - available_ids
    )

    if unknown_ids:
        raise ValueError(
            "Unknown evaluation case IDs: "
            f"{sorted(unknown_ids)}"
        )

    requested_set = set(
        requested_ids
    )

    return [
        case
        for case in dataset
        if case["id"] in requested_set
    ]


async def evaluate_answer_quality_case(
    *,
    agent: Any,
    case: dict[str, Any],
    judge_model_name: str,
) -> dict[str, Any]:
    """
    Run one complete end-to-end Answer Quality case.

    Pipeline:
        Real Agent
        -> final answer
        -> selected retrieval evidence
        -> LLM Judge
        -> deterministic Answer metrics
    """

    agent_result = await run_agent_case(
        agent=agent,
        case=case,
    )

    if not agent_result["agent_completed"]:
        return {
            "id": case["id"],
            "query": case["query"],
            "query_type": case["query_type"],
            "answerable": case["answerable"],

            "agent_completed": False,
            "agent_error":
                agent_result["agent_error"],

            "judge_completed": False,
            "judge_error":
                "agent_not_completed",

            "final_answer": None,
            "sources":
                agent_result["sources"],

            "retrieval_attempts":
                agent_result[
                    "retrieval_attempts"
                ],

            "judge_result": None,
            "answer_metrics": None,
        }

    final_answer = agent_result[
        "final_answer"
    ]

    sources = agent_result[
        "sources"
    ]

    try:
        judge_result = (
            await judge_answer_case(
                question=case["query"],
                final_answer=final_answer,
                answerable=
                    case["answerable"],
                reference_answer=
                    case["reference_answer"],
                required_facts=
                    case["required_facts"],
                sources=sources,
                model_name=
                    judge_model_name,
            )
        )

    except Exception as exc:
        return {
            "id": case["id"],
            "query": case["query"],
            "query_type": case["query_type"],
            "answerable": case["answerable"],

            "agent_completed": True,
            "agent_error": None,

            "judge_completed": False,
            "judge_error":
                f"{type(exc).__name__}: {exc}",

            "final_answer":
                final_answer,

            "sources": sources,

            "retrieval_attempts":
                agent_result[
                    "retrieval_attempts"
                ],

            "judge_result": None,
            "answer_metrics": None,
        }

    answer_metrics = (
        evaluate_answer_case(
            answerable=
                case["answerable"],

            required_facts=
                case["required_facts"],

            fact_matches=
                judge_result[
                    "fact_matches"
                ],

            correctness_score=
                judge_result[
                    "correctness_score"
                ],

            groundedness_score=
                judge_result[
                    "groundedness_score"
                ],

            refusal_correct=
                judge_result[
                    "refusal_correct"
                ],
        )
    )

    return {
        "id": case["id"],
        "query": case["query"],
        "query_type": case["query_type"],
        "answerable": case["answerable"],

        "agent_completed": True,
        "agent_error": None,

        "judge_completed": True,
        "judge_error": None,

        "final_answer":
            final_answer,

        "sources": sources,

        "retrieval_attempts":
            agent_result[
                "retrieval_attempts"
            ],

        "judge_result":
            judge_result,

        "answer_metrics":
            answer_metrics,
    }


def _print_retrieval_summary(
    attempts: list[dict[str, Any]],
) -> None:
    """Print compact retrieval attempt information."""

    if not attempts:
        print(
            "Retrieval attempts: NONE"
        )
        return

    print(
        f"Retrieval attempts: "
        f"{len(attempts)}"
    )

    for index, attempt in enumerate(
        attempts,
        start=1,
    ):
        print(
            f"  Attempt {index}: "
            f"status="
            f"{attempt.get('status')} | "
            f"reason="
            f"{attempt.get('reason')} | "
            f"top_rerank_score="
            f"{attempt.get('top_rerank_score')} | "
            f"sources="
            f"{len(attempt.get('sources', []))}"
        )


def _print_sources(
    sources: list[dict[str, Any]],
) -> None:
    """Print selected evidence sources."""

    if not sources:
        print(
            "Selected evidence: NONE"
        )
        return

    print("Selected evidence:")

    for source in sources:
        print(
            f"  [Source "
            f"{source.get('source_id')}] "
            f"→ "
            f"{source.get('file_name')} "
            f"chunk="
            f"{source.get('chunk_index')} "
            f"rerank="
            f"{source.get('rerank_score')}"
        )


def _print_case_result(
    result: dict[str, Any],
) -> None:
    """Print one Answer Quality evaluation case."""

    print()
    print("=" * 90)

    print(
        f"{result['id']} | "
        f"type={result['query_type']} | "
        f"answerable="
        f"{result['answerable']}"
    )

    print(
        f"Query: {result['query']}"
    )

    print("-" * 90)

    _print_retrieval_summary(
        result["retrieval_attempts"]
    )

    _print_sources(
        result["sources"]
    )

    if not result["agent_completed"]:
        print("-" * 90)

        print(
            "Agent Completion: FAILED"
        )

        print(
            "Agent Error: "
            f"{result['agent_error']}"
        )

        return

    print("-" * 90)
    print("Final Answer:")

    print(
        result["final_answer"]
    )

    if not result["judge_completed"]:
        print("-" * 90)

        print(
            "Judge Completion: FAILED"
        )

        print(
            "Judge Error: "
            f"{result['judge_error']}"
        )

        return

    judge_result = result[
        "judge_result"
    ]

    metrics = result[
        "answer_metrics"
    ]

    print("-" * 90)
    print("Judge Decision:")

    print(
        "  fact_matches="
        f"{judge_result['fact_matches']}"
    )

    print(
        "  correctness_score="
        f"{judge_result['correctness_score']}"
    )

    print(
        "  groundedness_score="
        f"{judge_result['groundedness_score']}"
    )

    print(
        "  refusal_correct="
        f"{judge_result['refusal_correct']}"
    )

    print("-" * 90)
    print("Answer Metrics:")

    if result["answerable"]:
        print(
            "  Required Facts="
            f"{metrics['required_fact_count']}"
        )

        print(
            "  Matched Facts="
            f"{metrics['matched_fact_count']}"
        )

        print(
            "  Fact Coverage="
            f"{metrics['fact_coverage']:.3f}"
        )

        print(
            "  Correctness="
            f"{metrics['correctness_score']:.3f}"
        )

        print(
            "  Groundedness="
            f"{metrics['groundedness_score']:.3f}"
        )

    else:
        print(
            "  Refusal Accuracy="
            f"{metrics['refusal_accuracy']:.3f}"
        )


def _aggregate_results(
    results: list[dict[str, Any]],
) -> None:
    """Print aggregate Answer Quality metrics."""

    if not results:
        raise ValueError(
            "No evaluation results available."
        )

    print()
    print("=" * 90)
    print(
        "Aggregate Agent Answer Quality Metrics"
    )
    print("=" * 90)

    agent_completion_rate = mean(
        1.0
        if result["agent_completed"]
        else 0.0
        for result in results
    )

    judge_completion_rate = mean(
        1.0
        if result["judge_completed"]
        else 0.0
        for result in results
    )

    print(
        "Agent Completion Rate: "
        f"{agent_completion_rate:.3f}"
    )

    print(
        "Judge Completion Rate: "
        f"{judge_completion_rate:.3f}"
    )

    evaluated_results = [
        result
        for result in results
        if (
            result["agent_completed"]
            and result["judge_completed"]
        )
    ]

    evaluation_rate = (
        len(evaluated_results)
        / len(results)
    )

    print(
        "Successfully Evaluated Rate: "
        f"{evaluation_rate:.3f}"
    )

    answerable_results = [
        result
        for result in evaluated_results
        if result["answerable"]
    ]

    no_evidence_results = [
        result
        for result in evaluated_results
        if not result["answerable"]
    ]

    if answerable_results:
        fact_coverage = mean(
            result["answer_metrics"][
                "fact_coverage"
            ]
            for result in answerable_results
        )

        correctness = mean(
            result["answer_metrics"][
                "correctness_score"
            ]
            for result in answerable_results
        )

        groundedness = mean(
            result["answer_metrics"][
                "groundedness_score"
            ]
            for result in answerable_results
        )

        print(
            "Mean Fact Coverage: "
            f"{fact_coverage:.3f}"
        )

        print(
            "Mean Answer Correctness: "
            f"{correctness:.3f}"
        )

        print(
            "Mean Groundedness: "
            f"{groundedness:.3f}"
        )

    if no_evidence_results:
        refusal_accuracy = mean(
            result["answer_metrics"][
                "refusal_accuracy"
            ]
            for result in no_evidence_results
        )

        print(
            "No-Evidence Refusal Accuracy: "
            f"{refusal_accuracy:.3f}"
        )

    failed_agent_cases = [
        result["id"]
        for result in results
        if not result["agent_completed"]
    ]

    failed_judge_cases = [
        result["id"]
        for result in results
        if (
            result["agent_completed"]
            and not result["judge_completed"]
        )
    ]

    if failed_agent_cases:
        print(
            "Failed Agent Cases: "
            f"{failed_agent_cases}"
        )

    if failed_judge_cases:
        print(
            "Failed Judge Cases: "
            f"{failed_judge_cases}"
        )

    print("=" * 90)


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Run end-to-end RAG Agent "
            "Answer Quality evaluation."
        )
    )

    parser.add_argument(
        "--case",
        nargs="+",
        dest="case_ids",
        help=(
            "Optional case IDs, for example: "
            "--case ret_007 ret_010"
        ),
    )

    parser.add_argument(
        "--model",
        default=_default_model_name(),
        help=(
            "Model used by the RAG Agent."
        ),
    )

    parser.add_argument(
        "--judge-model",
        default=_default_model_name(),
        help=(
            "Model used as the Answer Judge."
        ),
    )

    return parser.parse_args()


async def main() -> None:
    """Run end-to-end Answer Quality evaluation."""

    args = _parse_args()

    dataset = load_dataset()

    cases = _select_cases(
        dataset=dataset,
        requested_ids=args.case_ids,
    )

    print("=" * 90)

    print(
        "End-to-End Agent Answer "
        "Quality Evaluation"
    )

    print("=" * 90)

    print(
        f"Agent Model: "
        f"{args.model}"
    )

    print(
        f"Judge Model: "
        f"{args.judge_model}"
    )

    print(
        f"Cases: "
        f"{len(cases)}"
    )

    print(
        "Conversation memory: disabled "
        "(stateless evaluation)"
    )

    agent = _build_eval_agent(
        model_name=args.model,
    )

    results: list[
        dict[str, Any]
    ] = []

    for case in cases:
        result = (
            await evaluate_answer_quality_case(
                agent=agent,
                case=case,
                judge_model_name=
                    args.judge_model,
            )
        )

        results.append(
            result
        )

        _print_case_result(
            result
        )

    _aggregate_results(
        results
    )


if __name__ == "__main__":
    asyncio.run(main())