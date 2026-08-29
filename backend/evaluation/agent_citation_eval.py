import argparse
import asyncio
import json
from statistics import mean
from typing import Any

from app.chat.langgraph_agent import create_model
from app.chat.prompts import dynamic_system_prompt
from app.chat.tools import tools
from app.config import settings

from evaluation.citation_eval import (
    evaluate_citation_case,
)
from evaluation.load_dataset import load_dataset
from evaluation.prepare_corpus import (
    EVAL_THREAD_ID,
    EVAL_USER_ID,
)

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt import create_react_agent


VECTOR_TOP_K = 10
SIMILARITY_THRESHOLD = 0.50
RERANK_TOP_N = 5
RERANK_EVIDENCE_THRESHOLD = 0.70


def _default_model_name() -> str:
    """Return the first configured chat model."""

    if not settings.model_names:
        raise RuntimeError(
            "No model is configured in MODEL_NAMES."
        )

    return settings.model_names[0]


def _build_eval_agent(
    model_name: str,
):
    """
    Build a stateless evaluation Agent.

    It uses the same model, tools, and dynamic system prompt
    as the production Agent, but intentionally does not use
    a persistent checkpointer.

    This prevents evaluation cases from sharing chat memory.
    """

    model = create_model(
        model_name=model_name,
    )

    return create_react_agent(
        model=model,
        tools=tools,
        prompt=dynamic_system_prompt,
    )


def _message_text(
    message: AIMessage,
) -> str:
    """Convert AIMessage content into plain text."""

    content = message.content

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts: list[str] = []

        for item in content:
            if isinstance(item, str):
                parts.append(item)

            elif isinstance(item, dict):
                text = item.get("text")

                if isinstance(text, str):
                    parts.append(text)

        return "".join(parts)

    return str(content)


def _extract_final_answer(
    messages: list[Any],
) -> str:
    """
    Return the final non-tool-calling AI answer.
    """

    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue

        if message.tool_calls:
            continue

        text = _message_text(message).strip()

        if text:
            return text

    raise RuntimeError(
        "Agent did not produce a final AI answer."
    )

def _safe_extract_final_answer(
    messages: list[Any],
) -> tuple[str | None, str | None]:
    """
    Safely extract the final Agent answer.

    Returns:
        (answer, error_reason)
    """

    try:
        answer = _extract_final_answer(
            messages
        )
        return answer, None

    except RuntimeError:
        return None, "missing_final_answer"

def _parse_retrieval_tool_message(
    message: ToolMessage,
) -> dict[str, Any] | None:
    """
    Parse one retrieve_user_documents ToolMessage.
    """

    if message.name != "retrieve_user_documents":
        return None

    content = message.content

    if not isinstance(content, str):
        return None

    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return None

    if not isinstance(payload, dict):
        return None

    if "status" not in payload:
        return None

    if "sources" not in payload:
        return None

    return payload


def _collect_retrieval_attempts(
    messages: list[Any],
) -> list[dict[str, Any]]:
    """Collect all document retrieval attempts."""

    attempts: list[dict[str, Any]] = []

    for message in messages:
        if not isinstance(message, ToolMessage):
            continue

        payload = _parse_retrieval_tool_message(
            message
        )

        if payload is not None:
            attempts.append(payload)

    return attempts


def _select_retrieval_result(
    attempts: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """
    Select the retrieval result used for citation evaluation.

    Prefer the latest successful retrieval because Source IDs
    are local to each retrieval attempt.
    """

    successful_attempts = [
        attempt
        for attempt in attempts
        if attempt.get("status") == "ok"
    ]

    if successful_attempts:
        return successful_attempts[-1]

    if attempts:
        return attempts[-1]

    return None


def _selected_sources(
    retrieval_result: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """
    Return sources only from a successful retrieval.
    """

    if retrieval_result is None:
        return []

    if retrieval_result.get("status") != "ok":
        return []

    sources = retrieval_result.get(
        "sources",
        [],
    )

    if not isinstance(sources, list):
        return []

    return [
        source
        for source in sources
        if isinstance(source, dict)
    ]


async def evaluate_case(
    agent: Any,
    case: dict[str, Any],
) -> dict[str, Any]:
    """
    Run one Gold case through the real RAG Agent.
    """

    user_prompt = (
        "请根据我上传的文档回答下面的问题：\n"
        f"{case['query']}"
    )

    config = RunnableConfig(
        configurable={
            "thread_id": str(EVAL_THREAD_ID),
            "user_id": str(EVAL_USER_ID),
            "top_k": VECTOR_TOP_K,
            "similarity_threshold":
                SIMILARITY_THRESHOLD,
            "rerank_top_n": RERANK_TOP_N,
            "rerank_evidence_threshold":
                RERANK_EVIDENCE_THRESHOLD,
        }
    )

    result = await agent.ainvoke(
        {
            "messages": [
                HumanMessage(
                    content=user_prompt
                )
            ],
            "retry_count": 0,
        },
        config=config,
    )

    messages = result.get(
        "messages",
        [],
    )

    if not isinstance(messages, list):
        raise RuntimeError(
            f"{case['id']}: Agent result "
            "does not contain a messages list."
        )

    final_answer, agent_error = (
        _safe_extract_final_answer(
            messages
        )
    )

    retrieval_attempts = (
        _collect_retrieval_attempts(
            messages
        )
    )

    selected_retrieval = (
        _select_retrieval_result(
            retrieval_attempts
        )
    )

    sources = _selected_sources(
        selected_retrieval
    )

    retrieval_success = (
        selected_retrieval is not None
        and selected_retrieval.get(
            "status"
        ) == "ok"
    )

    agent_completed = (
        final_answer is not None
    )

    if not agent_completed:
        return {
            "id": case["id"],
            "query": case["query"],
            "query_type": case["query_type"],
            "answerable": case["answerable"],
            "expected_sources":
                case["expected_sources"],

            "final_answer": None,

            "retrieval_attempts":
                retrieval_attempts,

            "selected_retrieval":
                selected_retrieval,

            "sources": sources,

            "document_tool_used":
                bool(retrieval_attempts),

            "retrieval_success":
                retrieval_success,

            "agent_completed": False,
            "agent_error": agent_error,

            "citation_metrics": None,
        }

    citation_metrics = (
        evaluate_citation_case(
            answer=final_answer,
            sources=sources,
            expected_sources=case[
                "expected_sources"
            ],
            answerable=case["answerable"],
        )
    )

    return {
        "id": case["id"],
        "query": case["query"],
        "query_type": case["query_type"],
        "answerable": case["answerable"],
        "expected_sources":
            case["expected_sources"],

        "final_answer": final_answer,

        "retrieval_attempts":
            retrieval_attempts,

        "selected_retrieval":
            selected_retrieval,

        "sources": sources,

        "document_tool_used":
            bool(retrieval_attempts),

        "retrieval_success":
            retrieval_success,

        "agent_completed": True,
        "agent_error": None,

        "citation_metrics":
            citation_metrics,
    }


def _print_retrieval_attempts(
    attempts: list[dict[str, Any]],
) -> None:
    """Print retrieval status for each attempt."""

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
    """Print runtime Source ID to Gold chunk mapping."""

    if not sources:
        print("Selected sources: NONE")
        return

    print("Selected sources:")

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
    """Print one complete Agent citation result."""

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

    _print_retrieval_attempts(
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

    metrics = result[
        "citation_metrics"
    ]

    print("-" * 90)
    print(
        "Citation IDs: "
        f"{metrics['citation_ids']}"
    )

    print(
        "Valid Citation IDs: "
        f"{metrics['valid_citation_ids']}"
    )

    print(
        "Unsupported Citation IDs: "
        f"{metrics['unsupported_citation_ids']}"
    )

    print(
        "Citation Presence: "
        f"{metrics['citation_presence']:.3f}"
    )

    print(
        "Citation Validity: "
        f"{metrics['citation_validity_rate']:.3f}"
    )

    print(
        "Gold Citation Hit: "
        f"{metrics['gold_citation_hit']:.3f}"
    )

    print(
        "Unsupported Citation Rate: "
        f"{metrics['unsupported_citation_rate']:.3f}"
    )

    no_evidence_accuracy = metrics[
        "no_evidence_citation_accuracy"
    ]

    if no_evidence_accuracy is not None:
        print(
            "No-Evidence Citation Accuracy: "
            f"{no_evidence_accuracy:.3f}"
        )


def _aggregate_results(
    results: list[dict[str, Any]],
) -> None:
    """Print aggregate Agent citation metrics."""

    if not results:
        raise ValueError(
            "No evaluation results available."
        )

    answerable_results = [
        result
        for result in results
        if (
            result["answerable"]
            and result["agent_completed"]
        )
    ]

    no_evidence_results = [
        result
        for result in results
        if (
            not result["answerable"]
            and result["agent_completed"]
        )
    ]

    print()
    print("=" * 90)
    print("Aggregate Agent Citation Metrics")
    print("=" * 90)

    agent_completion_rate = mean(
        1.0
        if result["agent_completed"]
        else 0.0
        for result in results
    )

    print(
        f"Agent Completion Rate: "
        f"{agent_completion_rate:.3f}"
    )

    tool_usage_rate = mean(
        1.0
        if result["document_tool_used"]
        else 0.0
        for result in results
    )

    print(
        f"Document Tool Usage Rate: "
        f"{tool_usage_rate:.3f}"
    )

    if answerable_results:
        retrieval_success_rate = mean(
            1.0
            if result["retrieval_success"]
            else 0.0
            for result in answerable_results
        )

        citation_presence_rate = mean(
            result["citation_metrics"][
                "citation_presence"
            ]
            for result in answerable_results
        )

        citation_validity_rate = mean(
            result["citation_metrics"][
                "citation_validity_rate"
            ]
            for result in answerable_results
        )

        gold_citation_hit_rate = mean(
            result["citation_metrics"][
                "gold_citation_hit"
            ]
            for result in answerable_results
        )

        unsupported_citation_rate = mean(
            result["citation_metrics"][
                "unsupported_citation_rate"
            ]
            for result in answerable_results
        )

        print(
            f"Answerable Retrieval Success: "
            f"{retrieval_success_rate:.3f}"
        )

        print(
            f"Citation Presence Rate: "
            f"{citation_presence_rate:.3f}"
        )

        print(
            f"Citation Validity Rate: "
            f"{citation_validity_rate:.3f}"
        )

        print(
            f"Gold Citation Hit Rate: "
            f"{gold_citation_hit_rate:.3f}"
        )

        print(
            f"Unsupported Citation Rate: "
            f"{unsupported_citation_rate:.3f}"
        )

    if no_evidence_results:
        no_evidence_citation_accuracy = mean(
            float(
                result["citation_metrics"][
                    "no_evidence_citation_accuracy"
                ]
            )
            for result in no_evidence_results
        )

        print(
            "No-Evidence Citation Accuracy: "
            f"{no_evidence_citation_accuracy:.3f}"
        )

    print("=" * 90)


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


def _parse_args() -> argparse.Namespace:
    """Parse evaluation command-line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Run end-to-end Agent citation "
            "evaluation."
        )
    )

    parser.add_argument(
        "--case",
        nargs="+",
        dest="case_ids",
        help=(
            "Optional case IDs to evaluate, "
            "for example: "
            "--case ret_007 ret_010"
        ),
    )

    parser.add_argument(
        "--model",
        default=_default_model_name(),
        help=(
            "Chat model used by the Agent. "
            "Defaults to the first configured model."
        ),
    )

    return parser.parse_args()


async def main() -> None:
    """Run real Agent citation evaluation."""

    args = _parse_args()

    dataset = load_dataset()

    cases = _select_cases(
        dataset=dataset,
        requested_ids=args.case_ids,
    )

    print("=" * 90)
    print("End-to-End Agent Citation Evaluation")
    print("=" * 90)

    print(
        f"Model: {args.model}"
    )

    print(
        f"Cases: {len(cases)}"
    )

    print(
        f"Vector Top-K: "
        f"{VECTOR_TOP_K}"
    )

    print(
        f"Similarity Threshold: "
        f"{SIMILARITY_THRESHOLD:.2f}"
    )

    print(
        f"Rerank Top-N: "
        f"{RERANK_TOP_N}"
    )

    print(
        f"Evidence Threshold: "
        f"{RERANK_EVIDENCE_THRESHOLD:.2f}"
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
        result = await evaluate_case(
            agent=agent,
            case=case,
        )

        results.append(result)

        _print_case_result(
            result
        )

    _aggregate_results(
        results
    )


if __name__ == "__main__":
    asyncio.run(main())