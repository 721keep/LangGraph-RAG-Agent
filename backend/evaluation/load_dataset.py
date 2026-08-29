import json
from pathlib import Path
from typing import Any


DATASET_PATH = (
    Path(__file__).resolve().parent
    / "data"
    / "retrieval_eval.jsonl"
)

REQUIRED_FIELDS = {
    "id",
    "query",
    "answerable",
    "query_type",
    "expected_sources",
}

VALID_QUERY_TYPES = {
    "direct",
    "paraphrase",
    "semantic",
    "no_evidence",
}


def _validate_source(
    source: dict[str, Any],
    case_id: str,
) -> None:
    """Validate one expected source entry."""

    required_source_fields = {
        "file_name",
        "chunk_index",
        "page",
    }

    missing_fields = required_source_fields - source.keys()

    if missing_fields:
        raise ValueError(
            f"{case_id}: expected source is missing fields: "
            f"{sorted(missing_fields)}"
        )

    if not isinstance(source["file_name"], str):
        raise ValueError(
            f"{case_id}: file_name must be a string."
        )

    if not isinstance(source["chunk_index"], int):
        raise ValueError(
            f"{case_id}: chunk_index must be an integer."
        )

    if (
        source["page"] is not None
        and not isinstance(source["page"], int)
    ):
        raise ValueError(
            f"{case_id}: page must be an integer or null."
        )


def _validate_case(case: dict[str, Any]) -> None:
    """Validate one retrieval evaluation case."""

    missing_fields = REQUIRED_FIELDS - case.keys()

    if missing_fields:
        raise ValueError(
            f"Evaluation case is missing fields: "
            f"{sorted(missing_fields)}"
        )

    case_id = case["id"]

    if not isinstance(case_id, str):
        raise ValueError("id must be a string.")

    if not isinstance(case["query"], str):
        raise ValueError(
            f"{case_id}: query must be a string."
        )

    if not isinstance(case["answerable"], bool):
        raise ValueError(
            f"{case_id}: answerable must be a boolean."
        )

    query_type = case["query_type"]

    if query_type not in VALID_QUERY_TYPES:
        raise ValueError(
            f"{case_id}: invalid query_type={query_type!r}. "
            f"Expected one of {sorted(VALID_QUERY_TYPES)}."
        )

    expected_sources = case["expected_sources"]

    if not isinstance(expected_sources, list):
        raise ValueError(
            f"{case_id}: expected_sources must be a list."
        )

    if case["answerable"] and not expected_sources:
        raise ValueError(
            f"{case_id}: answerable=true requires "
            "at least one expected source."
        )

    if not case["answerable"] and expected_sources:
        raise ValueError(
            f"{case_id}: answerable=false requires "
            "expected_sources to be empty."
        )

    if (
        query_type == "no_evidence"
        and case["answerable"]
    ):
        raise ValueError(
            f"{case_id}: no_evidence case must have "
            "answerable=false."
        )

    for source in expected_sources:
        if not isinstance(source, dict):
            raise ValueError(
                f"{case_id}: every expected source "
                "must be an object."
            )

        _validate_source(
            source=source,
            case_id=case_id,
        )


def load_dataset(
    dataset_path: Path = DATASET_PATH,
) -> list[dict[str, Any]]:
    """Load and validate retrieval evaluation dataset."""

    if not dataset_path.exists():
        raise FileNotFoundError(
            f"Evaluation dataset not found: "
            f"{dataset_path}"
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
                    f"Invalid JSON at line "
                    f"{line_number}: {exc}"
                ) from exc

            if not isinstance(case, dict):
                raise ValueError(
                    f"Line {line_number}: "
                    "evaluation case must be a JSON object."
                )

            _validate_case(case)

            case_id = case["id"]

            if case_id in seen_ids:
                raise ValueError(
                    f"Duplicate evaluation id: "
                    f"{case_id}"
                )

            seen_ids.add(case_id)
            cases.append(case)

    if not cases:
        raise ValueError(
            "Evaluation dataset is empty."
        )

    return cases


def main() -> None:
    """Load dataset and print a short validation summary."""

    cases = load_dataset()

    print("=" * 80)
    print(
        f"Dataset loaded successfully: "
        f"{DATASET_PATH}"
    )
    print(
        f"Total evaluation cases: "
        f"{len(cases)}"
    )
    print("=" * 80)

    for case in cases:
        print(
            f"{case['id']} | "
            f"type={case['query_type']} | "
            f"answerable={case['answerable']} | "
            f"gold_sources="
            f"{len(case['expected_sources'])}"
        )

        print(
            f"  Query: {case['query']}"
        )

    print("=" * 80)


if __name__ == "__main__":
    main()