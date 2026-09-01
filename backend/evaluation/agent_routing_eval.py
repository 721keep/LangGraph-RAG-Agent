import argparse
import asyncio
import json
from pathlib import Path
from typing import Any
import sys
import csv
import tempfile
from types import SimpleNamespace
PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(
        0,
        str(PROJECT_ROOT),
    )

DATASET_PATH = (
    Path(__file__).resolve().parent
    / "data"
    / "agent_routing_eval.jsonl"
)

REQUIRED_FIELDS = {
    "id",
    "category",
    "query",
    "expected_tool",
    "expected_args",
}

VALID_CATEGORIES = {
    "rag",
    "web",
    "mcp",
    "no_tool",
}

EXPECTED_TOOL_BY_CATEGORY = {
    "rag": "retrieve_user_documents",
    "web": "web_search",
    "mcp": "get_server_status",
    "no_tool": None,
}


def _validate_case(case: dict[str, Any]) -> None:
    """Validate one agent routing evaluation case."""

    missing_fields = REQUIRED_FIELDS - case.keys()

    if missing_fields:
        raise ValueError(
            "Routing evaluation case is missing fields: "
            f"{sorted(missing_fields)}"
        )

    case_id = case["id"]

    if not isinstance(case_id, str) or not case_id.strip():
        raise ValueError("id must be a non-empty string.")

    category = case["category"]

    if category not in VALID_CATEGORIES:
        raise ValueError(
            f"{case_id}: invalid category={category!r}. "
            f"Expected one of {sorted(VALID_CATEGORIES)}."
        )

    query = case["query"]

    if not isinstance(query, str) or not query.strip():
        raise ValueError(
            f"{case_id}: query must be a non-empty string."
        )

    expected_tool = case["expected_tool"]

    category_tool = EXPECTED_TOOL_BY_CATEGORY[category]

    if expected_tool != category_tool:
        raise ValueError(
            f"{case_id}: category={category!r} expects "
            f"tool={category_tool!r}, "
            f"but received {expected_tool!r}."
        )

    expected_args = case["expected_args"]

    if (
        expected_args is not None
        and not isinstance(expected_args, dict)
    ):
        raise ValueError(
            f"{case_id}: expected_args must be an object or null."
        )

    if category == "no_tool" and expected_args is not None:
        raise ValueError(
            f"{case_id}: no_tool case requires expected_args=null."
        )


def load_routing_dataset(
    dataset_path: Path = DATASET_PATH,
) -> list[dict[str, Any]]:
    """Load and validate the agent routing dataset."""

    if not dataset_path.exists():
        raise FileNotFoundError(
            f"Agent routing dataset not found: {dataset_path}"
        )

    cases: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    with dataset_path.open(
        "r",
        encoding="utf-8",
    ) as file:
        for line_number, line in enumerate(
            file,
            start=1,
        ):
            line = line.strip()

            if not line:
                continue

            try:
                case = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSON at line {line_number}: {exc}"
                ) from exc

            if not isinstance(case, dict):
                raise ValueError(
                    f"Line {line_number}: "
                    "routing case must be a JSON object."
                )

            _validate_case(case)

            case_id = case["id"]

            if case_id in seen_ids:
                raise ValueError(
                    f"Duplicate routing evaluation id: {case_id}"
                )

            seen_ids.add(case_id)
            cases.append(case)

    if not cases:
        raise ValueError(
            "Agent routing evaluation dataset is empty."
        )

    return cases

def extract_first_tool_call(
    messages: list[Any],
) -> tuple[str | None, dict[str, Any] | None]:
    """
    Extract the first tool call from agent messages.

    Returns:
        (tool_name, tool_args)

    If the agent does not call a tool:
        (None, None)
    """

    for message in messages:
        tool_calls = getattr(
            message,
            "tool_calls",
            None,
        )

        if not tool_calls:
            continue

        first_tool_call = tool_calls[0]

        if not isinstance(first_tool_call, dict):
            raise ValueError(
                "Tool call must be a dictionary."
            )

        tool_name = first_tool_call.get("name")
        tool_args = first_tool_call.get("args")

        if not isinstance(tool_name, str):
            raise ValueError(
                "Tool call is missing a valid name."
            )

        if tool_args is None:
            tool_args = {}

        if not isinstance(tool_args, dict):
            raise ValueError(
                "Tool call args must be a dictionary."
            )

        return tool_name, tool_args

    return None, None

def compare_routing_result(
    case: dict[str, Any],
    actual_tool: str | None,
    actual_args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare one expected routing decision with an actual result."""

    expected_tool = case["expected_tool"]
    expected_args = case["expected_args"]

    tool_correct = actual_tool == expected_tool

    argument_checked = expected_args is not None

    if argument_checked:
        argument_correct = (
            tool_correct
            and actual_args == expected_args
        )
    else:
        argument_correct = None

    return {
        "id": case["id"],
        "category": case["category"],
        "query": case["query"],
        "expected_tool": expected_tool,
        "actual_tool": actual_tool,
        "expected_args": expected_args,
        "actual_args": actual_args,
        "tool_correct": tool_correct,
        "argument_checked": argument_checked,
        "argument_correct": argument_correct,
    }


def calculate_routing_metrics(
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Calculate aggregate agent routing metrics."""

    if not results:
        raise ValueError(
            "Routing evaluation results must not be empty."
        )

    total_cases = len(results)

    correct_tool_count = sum(
        1
        for result in results
        if result["tool_correct"]
    )

    tool_selection_accuracy = (
        correct_tool_count / total_cases
    )

    wrong_tool_rate = (
        1.0 - tool_selection_accuracy
    )

    no_tool_results = [
        result
        for result in results
        if result["category"] == "no_tool"
    ]

    if no_tool_results:
        no_tool_accuracy = (
            sum(
                1
                for result in no_tool_results
                if result["tool_correct"]
            )
            / len(no_tool_results)
        )
    else:
        no_tool_accuracy = None

    argument_results = [
        result
        for result in results
        if result["argument_checked"]
    ]

    if argument_results:
        argument_accuracy = (
            sum(
                1
                for result in argument_results
                if result["argument_correct"]
            )
            / len(argument_results)
        )
    else:
        argument_accuracy = None

    per_tool: dict[str, dict[str, Any]] = {}

    for result in results:
        expected_tool = result["expected_tool"]

        tool_label = (
            expected_tool
            if expected_tool is not None
            else "no_tool"
        )

        if tool_label not in per_tool:
            per_tool[tool_label] = {
                "total": 0,
                "correct": 0,
            }

        per_tool[tool_label]["total"] += 1

        if result["tool_correct"]:
            per_tool[tool_label]["correct"] += 1

    for tool_stats in per_tool.values():
        tool_stats["accuracy"] = (
            tool_stats["correct"]
            / tool_stats["total"]
        )

    return {
        "total_cases": total_cases,
        "tool_selection_accuracy":
            tool_selection_accuracy,
        "wrong_tool_rate": wrong_tool_rate,
        "no_tool_accuracy": no_tool_accuracy,
        "argument_accuracy": argument_accuracy,
        "per_tool_accuracy": per_tool,
    }

def write_routing_report(
    results: list[dict[str, Any]],
    metrics: dict[str, Any],
    output_dir: Path,
    report_name: str,
) -> tuple[Path, Path]:
    """Write routing evaluation results to JSON and CSV."""

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    json_path = (
        output_dir
        / f"{report_name}.json"
    )

    csv_path = (
        output_dir
        / f"{report_name}.csv"
    )

    json_payload = {
        "metrics": metrics,
        "results": results,
    }

    json_path.write_text(
        json.dumps(
            json_payload,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    csv_fields = [
        "id",
        "category",
        "query",
        "expected_tool",
        "actual_tool",
        "expected_args",
        "actual_args",
        "tool_correct",
        "argument_checked",
        "argument_correct",
    ]

    with csv_path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=csv_fields,
        )

        writer.writeheader()

        for result in results:
            row = {
                field: result.get(field)
                for field in csv_fields
            }

            row["expected_args"] = json.dumps(
                result.get("expected_args"),
                ensure_ascii=False,
            )

            row["actual_args"] = json.dumps(
                result.get("actual_args"),
                ensure_ascii=False,
            )

            writer.writerow(row)

    return json_path, csv_path

async def evaluate_real_routing_case(
    case: dict[str, Any],
    model_name: str,
) -> dict[str, Any]:
    """
    Evaluate one routing case with the real LangGraph agent.

    The graph stops before the tools node so routing can be
    evaluated without executing RAG, web search, or MCP tools.
    """

    # Lazy imports keep deterministic evaluation runnable
    # without the backend runtime dependencies installed locally.
    from langchain_core.messages import HumanMessage

    from app.chat.langgraph_agent import (
        build_retrival_graph,
    )

    graph = await build_retrival_graph(
        checkpointer=None,
        model_name=model_name,
        interrupt_before=["tools"],
    )

    result = await graph.ainvoke(
        {
            "messages": [
                HumanMessage(
                    content=case["query"]
                )
            ],
        }
    )

    messages = result.get("messages", [])

    actual_tool, actual_args = (
        extract_first_tool_call(messages)
    )

    evaluation_result = compare_routing_result(
        case=case,
        actual_tool=actual_tool,
        actual_args=actual_args,
    )

    evaluation_result["query"] = case["query"]
    evaluation_result["actual_args"] = actual_args

    return evaluation_result

def _parse_args() -> argparse.Namespace:
    """Parse agent routing evaluation CLI arguments."""

    parser = argparse.ArgumentParser(
        description="Run Agent tool-routing evaluation."
    )
    
    parser.add_argument(
        "--real",
        action="store_true",
        help=(
            "Run real LLM routing evaluation. "
            "This may incur API costs."
        ),
    )

    parser.add_argument(
        "--real-all",
        action="store_true",
        help=(
            "Run the full real LLM routing dataset "
            "and write JSON/CSV reports. "
            "This may incur API costs."
        ),
    )

    parser.add_argument(
        "--case",
        dest="case_id",
        default=None,
        help=(
            "Evaluate one routing case, for example "
            "route_mcp_001."
        ),
    )

    parser.add_argument(
        "--model",
        default="deepseek-v4-flash",
        help="Model used for real routing evaluation.",
    )

    return parser.parse_args()

async def evaluate_real_routing_dataset(
    cases: list[dict[str, Any]],
    model_name: str,
) -> list[dict[str, Any]]:
    """Evaluate all routing cases with the real agent."""

    results: list[dict[str, Any]] = []

    for index, case in enumerate(
        cases,
        start=1,
    ):
        print(
            f"[{index}/{len(cases)}] "
            f"Evaluating {case['id']}..."
        )

        result = await evaluate_real_routing_case(
            case=case,
            model_name=model_name,
        )

        results.append(result)

    return results
def run_real_all_evaluation(
    model_name: str,
) -> None:
    """Run the full real routing dataset and write reports."""

    cases = load_routing_dataset()

    results = asyncio.run(
        evaluate_real_routing_dataset(
            cases=cases,
            model_name=model_name,
        )
    )

    metrics = calculate_routing_metrics(
        results
    )

    reports_dir = (
        Path(__file__).resolve().parent
        / "reports"
    )

    safe_model_name = (
        model_name
        .replace("/", "_")
        .replace("\\", "_")
    )

    report_name = (
        f"agent_routing_{safe_model_name}"
    )

    json_report, csv_report = (
        write_routing_report(
            results=results,
            metrics=metrics,
            output_dir=reports_dir,
            report_name=report_name,
        )
    )

    print()
    print("=" * 80)
    print("Real Agent Routing Evaluation Summary")
    print("=" * 80)

    print(
        f"Total Cases: "
        f"{metrics['total_cases']}"
    )

    print(
        "Tool Selection Accuracy: "
        f"{metrics['tool_selection_accuracy']:.3f}"
    )

    print(
        "Wrong Tool Rate: "
        f"{metrics['wrong_tool_rate']:.3f}"
    )

    no_tool_accuracy = metrics[
        "no_tool_accuracy"
    ]

    if no_tool_accuracy is not None:
        print(
            "No-Tool Accuracy: "
            f"{no_tool_accuracy:.3f}"
        )

    argument_accuracy = metrics[
        "argument_accuracy"
    ]

    if argument_accuracy is not None:
        print(
            "Argument Accuracy: "
            f"{argument_accuracy:.3f}"
        )

    print("Per-tool Accuracy:")

    for tool_name, stats in (
        metrics["per_tool_accuracy"].items()
    ):
        print(
            f"  {tool_name}: "
            f"{stats['correct']}/{stats['total']} "
            f"({stats['accuracy']:.3f})"
        )

    print("-" * 80)

    print(
        f"JSON Report: {json_report}"
    )

    print(
        f"CSV Report: {csv_report}"
    )

    print("=" * 80)

def run_real_evaluation(
    case_id: str,
    model_name: str,
) -> None:
    """Run one explicitly requested real LLM routing case."""

    cases = load_routing_dataset()

    case = next(
        (
            item
            for item in cases
            if item["id"] == case_id
        ),
        None,
    )

    if case is None:
        raise ValueError(
            f"Unknown routing evaluation case: {case_id}"
        )

    result = asyncio.run(
        evaluate_real_routing_case(
            case=case,
            model_name=model_name,
        )
    )

    print("=" * 80)
    print("Real Agent Routing Evaluation")
    print("=" * 80)

    print(f"Case: {result['id']}")
    print(f"Category: {result['category']}")
    print(f"Query: {result['query']}")

    print(
        f"Expected Tool: "
        f"{result['expected_tool']}"
    )

    print(
        f"Actual Tool: "
        f"{result['actual_tool']}"
    )

    print(
        f"Tool Correct: "
        f"{result['tool_correct']}"
    )

    print(
        f"Actual Args: "
        f"{result['actual_args']}"
    )

    if result["argument_checked"]:
        print(
            f"Argument Correct: "
            f"{result['argument_correct']}"
        )

    print("=" * 80)

def main() -> None:
    """Run deterministic or real agent routing evaluation."""

    args = _parse_args()

    if args.real and args.real_all:
        raise ValueError(
            "--real and --real-all "
            "cannot be used together."
        )

    if args.real_all:
        run_real_all_evaluation(
            model_name=args.model,
        )
        return

    if args.real:
        if not args.case_id:
            raise ValueError(
                "--real requires --case to avoid "
                "accidentally running the full dataset."
            )

        run_real_evaluation(
            case_id=args.case_id,
            model_name=args.model,
        )
        return

    # --------------------------------------------------
    # Deterministic evaluator starts here
    # --------------------------------------------------

    cases = load_routing_dataset()

    cases_by_id = {
        case["id"]: case
        for case in cases
    }

    # Use stable case IDs instead of dataset positions.
    # This keeps the deterministic evaluator stable even
    # when new routing cases are added to the dataset.
    rag_case = cases_by_id["route_rag_001"]
    web_case = cases_by_id["route_web_001"]
    mcp_case = cases_by_id["route_mcp_001"]
    no_tool_case = cases_by_id["route_no_tool_001"]

    # --------------------------------------------------
    # Basic evaluator self-checks
    # --------------------------------------------------

    correct_tool = compare_routing_result(
        case=rag_case,
        actual_tool="retrieve_user_documents",
    )

    wrong_tool = compare_routing_result(
        case=rag_case,
        actual_tool="web_search",
    )

    no_tool = compare_routing_result(
        case=no_tool_case,
        actual_tool=None,
    )

    correct_args = compare_routing_result(
        case=mcp_case,
        actual_tool="get_server_status",
        actual_args={
            "component": "backend",
        },
    )

    wrong_args = compare_routing_result(
        case=mcp_case,
        actual_tool="get_server_status",
        actual_args={
            "component": "frontend",
        },
    )

    # --------------------------------------------------
    # Synthetic routing results used to verify metrics.
    #
    # Intentionally:
    #   RAG     -> correct
    #   Web     -> wrong
    #   MCP     -> correct
    #   No-tool -> correct
    #
    # Expected tool-selection accuracy = 3 / 4 = 0.75
    # --------------------------------------------------

    deterministic_results = [
        compare_routing_result(
            case=rag_case,
            actual_tool="retrieve_user_documents",
        ),
        compare_routing_result(
            case=web_case,
            actual_tool="get_server_status",
        ),
        compare_routing_result(
            case=mcp_case,
            actual_tool="get_server_status",
            actual_args={
                "component": "backend",
            },
        ),
        compare_routing_result(
            case=no_tool_case,
            actual_tool=None,
        ),
    ]

    metrics = calculate_routing_metrics(
        deterministic_results
    )

    # --------------------------------------------------
    # Tool-call extraction self-check
    # --------------------------------------------------

    fake_tool_message = SimpleNamespace(
        tool_calls=[
            {
                "name": "get_server_status",
                "args": {
                    "component": "backend",
                },
            }
        ]
    )

    extracted_tool, extracted_args = (
        extract_first_tool_call(
            [fake_tool_message]
        )
    )

    no_tool_message = SimpleNamespace(
        tool_calls=[]
    )

    extracted_no_tool, extracted_no_args = (
        extract_first_tool_call(
            [no_tool_message]
        )
    )

    # --------------------------------------------------
    # Assertions
    # --------------------------------------------------

    assert correct_tool["tool_correct"] is True
    assert wrong_tool["tool_correct"] is False
    assert no_tool["tool_correct"] is True

    assert correct_args["argument_correct"] is True
    assert wrong_args["argument_correct"] is False

    assert extracted_tool == "get_server_status"
    assert extracted_args == {
        "component": "backend",
    }

    assert extracted_no_tool is None
    assert extracted_no_args is None

    # Synthetic evaluator still contains four cases.
    assert metrics["total_cases"] == 4

    assert (
        metrics["tool_selection_accuracy"]
        == 0.75
    )

    assert (
        metrics["wrong_tool_rate"]
        == 0.25
    )

    assert (
        metrics["no_tool_accuracy"]
        == 1.0
    )

    assert (
        metrics["argument_accuracy"]
        == 1.0
    )

    assert (
        metrics["per_tool_accuracy"]
        ["retrieve_user_documents"]
        ["accuracy"]
        == 1.0
    )

    assert (
        metrics["per_tool_accuracy"]
        ["web_search"]
        ["accuracy"]
        == 0.0
    )

    assert (
        metrics["per_tool_accuracy"]
        ["get_server_status"]
        ["accuracy"]
        == 1.0
    )

    assert (
        metrics["per_tool_accuracy"]
        ["no_tool"]
        ["accuracy"]
        == 1.0
    )

    # --------------------------------------------------
    # JSON / CSV report writer self-check
    # --------------------------------------------------

    with tempfile.TemporaryDirectory() as temp_dir:
        report_dir = Path(temp_dir)

        json_report, csv_report = (
            write_routing_report(
                results=deterministic_results,
                metrics=metrics,
                output_dir=report_dir,
                report_name="routing_test",
            )
        )

        assert json_report.exists()
        assert csv_report.exists()

        json_data = json.loads(
            json_report.read_text(
                encoding="utf-8",
            )
        )

        assert (
            json_data["metrics"]["total_cases"]
            == 4
        )

        assert (
            len(json_data["results"])
            == 4
        )

        with csv_report.open(
            "r",
            encoding="utf-8-sig",
            newline="",
        ) as file:
            csv_rows = list(
                csv.DictReader(file)
            )

        assert len(csv_rows) == 4

    # --------------------------------------------------
    # Terminal summary
    # --------------------------------------------------

    print("=" * 80)
    print(
        "Agent Routing Deterministic Evaluation"
    )
    print("=" * 80)

    print(
        f"Dataset cases: {len(cases)}"
    )

    print("Dataset validation: PASS")
    print("Correct tool detection: PASS")
    print("Wrong tool detection: PASS")
    print("No-tool detection: PASS")
    print("Argument match detection: PASS")
    print("Argument mismatch detection: PASS")
    print("Tool-call extraction: PASS")
    print("No-tool extraction: PASS")
    print("Routing metrics calculation: PASS")
    print("JSON report writer: PASS")
    print("CSV report writer: PASS")

    print("-" * 80)

    print(
        "Tool Selection Accuracy: "
        f"{metrics['tool_selection_accuracy']:.3f}"
    )

    print(
        "Wrong Tool Rate: "
        f"{metrics['wrong_tool_rate']:.3f}"
    )

    print(
        "No-Tool Accuracy: "
        f"{metrics['no_tool_accuracy']:.3f}"
    )

    print(
        "Argument Accuracy: "
        f"{metrics['argument_accuracy']:.3f}"
    )

    print("Per-tool Accuracy:")

    for tool_name, stats in (
        metrics["per_tool_accuracy"].items()
    ):
        print(
            f"  {tool_name}: "
            f"{stats['correct']}/{stats['total']} "
            f"({stats['accuracy']:.3f})"
        )

    print("=" * 80)

if __name__ == "__main__":
    main()