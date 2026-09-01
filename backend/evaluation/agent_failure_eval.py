import subprocess
import sys
from pathlib import Path
from typing import Any
import csv
import json

BACKEND_ROOT = Path(__file__).resolve().parents[1]
MCP_TEST_DIR = BACKEND_ROOT / "app" / "mcp"

FAILURE_CASES = [
    {
        "name": "tool_execution_error",
        "script": "failure_smoke_test.py",
    },
    {
        "name": "invalid_arguments",
        "script": "invalid_arguments_smoke_test.py",
    },
    {
        "name": "tool_timeout",
        "script": "timeout_smoke_test.py",
    },
    {
        "name": "server_unavailable",
        "script": "server_unavailable_smoke_test.py",
    },
]


def run_failure_case(
    case: dict[str, str],
) -> dict[str, Any]:
    """Run one existing MCP failure regression test."""

    script_path = (
        MCP_TEST_DIR
        / case["script"]
    )

    if not script_path.exists():
        return {
            "name": case["name"],
            "script": case["script"],
            "passed": False,
            "return_code": None,
            "error": "test_script_not_found",
        }

    module_name = (
        f"app.mcp.{Path(case['script']).stem}"
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            module_name,
        ],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    return {
        "name": case["name"],
        "script": case["script"],
        "passed": completed.returncode == 0,
        "return_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def calculate_failure_metrics(
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Calculate reliability metrics for failure handling."""

    if not results:
        raise ValueError(
            "Failure evaluation results must not be empty."
        )

    total_cases = len(results)

    passed_cases = sum(
        1
        for result in results
        if result["passed"]
    )

    return {
        "total_failure_cases": total_cases,
        "passed_failure_cases": passed_cases,
        "failed_failure_cases": (
            total_cases - passed_cases
        ),
        "failure_handling_pass_rate": (
            passed_cases / total_cases
        ),
    }

def write_failure_report(
    results: list[dict[str, Any]],
    metrics: dict[str, Any],
    output_dir: Path,
) -> tuple[Path, Path]:
    """Write failure reliability results to JSON and CSV."""

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    json_path = (
        output_dir
        / "agent_failure_reliability.json"
    )

    csv_path = (
        output_dir
        / "agent_failure_reliability.csv"
    )

    report_results = [
        {
            "name": result["name"],
            "script": result["script"],
            "passed": result["passed"],
            "return_code": result["return_code"],
            "error": result.get("error"),
        }
        for result in results
    ]

    payload = {
        "metrics": metrics,
        "results": report_results,
    }

    json_path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    csv_fields = [
        "name",
        "script",
        "passed",
        "return_code",
        "error",
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

        for result in report_results:
            writer.writerow(result)

    return json_path, csv_path


def main() -> None:
    """Run MCP failure reliability evaluation."""

    results = [
        run_failure_case(case)
        for case in FAILURE_CASES
    ]

    metrics = calculate_failure_metrics(
        results
    )
    reports_dir = (
        Path(__file__).resolve().parent
        / "reports"
    )

    json_report, csv_report = (
        write_failure_report(
            results=results,
            metrics=metrics,
            output_dir=reports_dir,
        )
    )
    print("=" * 80)
    print("Agent Failure Reliability Evaluation")
    print("=" * 80)

    for result in results:
        status = (
            "PASS"
            if result["passed"]
            else "FAIL"
        )

        print(
            f"{result['name']}: {status}"
        )

        if not result["passed"]:
            print(
                f"  script={result['script']}"
            )
            print(
                f"  return_code="
                f"{result['return_code']}"
            )

            stderr = result.get(
                "stderr",
                "",
            ).strip()

            if stderr:
                print(
                    f"  stderr={stderr}"
                )

    print("-" * 80)

    print(
        "Total Failure Cases: "
        f"{metrics['total_failure_cases']}"
    )

    print(
        "Passed Failure Cases: "
        f"{metrics['passed_failure_cases']}"
    )

    print(
        "Failed Failure Cases: "
        f"{metrics['failed_failure_cases']}"
    )

    print(
        "Failure Handling Pass Rate: "
        f"{metrics['failure_handling_pass_rate']:.3f}"
    )
    print("-" * 80)

    print(
        f"JSON Report: {json_report}"
    )

    print(
        f"CSV Report: {csv_report}"
    )
    print("=" * 80)

    if metrics["failed_failure_cases"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()