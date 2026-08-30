from typing import Any


def calculate_fact_coverage(
    required_facts: list[str],
    fact_matches: list[bool],
) -> float:
    """
    Calculate required-fact coverage.

    Example:
        required_facts = ["fact A", "fact B"]
        fact_matches = [True, False]

        coverage = 0.5
    """

    if len(required_facts) != len(fact_matches):
        raise ValueError(
            "required_facts and fact_matches "
            "must have the same length."
        )

    if not required_facts:
        raise ValueError(
            "Fact coverage requires at least "
            "one required fact."
        )

    if not all(
        isinstance(match, bool)
        for match in fact_matches
    ):
        raise ValueError(
            "Every fact match must be a boolean."
        )

    matched_count = sum(
        1
        for match in fact_matches
        if match
    )

    return (
        matched_count
        / len(required_facts)
    )


def evaluate_answer_case(
    answerable: bool,
    required_facts: list[str],
    fact_matches: list[bool] | None,
    correctness_score: float | None,
    groundedness_score: float | None,
    refusal_correct: bool | None = None,
) -> dict[str, Any]:
    """
    Convert semantic Judge decisions into deterministic
    Answer Evaluation metrics.

    Answerable cases:
        - Fact Coverage
        - Correctness
        - Groundedness

    No-evidence cases:
        - Refusal Accuracy
    """

    if answerable:
        if not required_facts:
            raise ValueError(
                "Answerable cases require "
                "required_facts."
            )

        if fact_matches is None:
            raise ValueError(
                "Answerable cases require "
                "fact_matches."
            )

        if correctness_score is None:
            raise ValueError(
                "Answerable cases require "
                "correctness_score."
            )

        if groundedness_score is None:
            raise ValueError(
                "Answerable cases require "
                "groundedness_score."
            )

        if not (
            0.0
            <= correctness_score
            <= 1.0
        ):
            raise ValueError(
                "correctness_score must be "
                "between 0 and 1."
            )

        if not (
            0.0
            <= groundedness_score
            <= 1.0
        ):
            raise ValueError(
                "groundedness_score must be "
                "between 0 and 1."
            )

        fact_coverage = (
            calculate_fact_coverage(
                required_facts=
                    required_facts,
                fact_matches=
                    fact_matches,
            )
        )

        return {
            "answerable": True,

            "required_fact_count":
                len(required_facts),

            "matched_fact_count":
                sum(fact_matches),

            "fact_coverage":
                fact_coverage,

            "correctness_score":
                correctness_score,

            "groundedness_score":
                groundedness_score,

            "refusal_accuracy":
                None,
        }

    if required_facts:
        raise ValueError(
            "No-evidence cases must not "
            "contain required_facts."
        )

    if fact_matches not in (
        None,
        [],
    ):
        raise ValueError(
            "No-evidence cases must not "
            "contain fact matches."
        )

    if refusal_correct is None:
        raise ValueError(
            "No-evidence cases require "
            "refusal_correct."
        )

    return {
        "answerable": False,

        "required_fact_count": 0,
        "matched_fact_count": 0,
        "fact_coverage": None,

        "correctness_score": None,
        "groundedness_score": None,

        "refusal_accuracy":
            1.0
            if refusal_correct
            else 0.0,
    }


def _print_result(
    name: str,
    result: dict[str, Any],
) -> None:
    """Print one deterministic answer evaluation."""

    print("-" * 80)
    print(name)

    if result["answerable"]:
        print(
            "  Required Facts="
            f"{result['required_fact_count']}"
        )

        print(
            "  Matched Facts="
            f"{result['matched_fact_count']}"
        )

        print(
            "  Fact Coverage="
            f"{result['fact_coverage']:.3f}"
        )

        print(
            "  Correctness="
            f"{result['correctness_score']:.3f}"
        )

        print(
            "  Groundedness="
            f"{result['groundedness_score']:.3f}"
        )

    else:
        print(
            "  Refusal Accuracy="
            f"{result['refusal_accuracy']:.3f}"
        )


def main() -> None:
    """
    Run deterministic Answer Evaluation self-tests.

    The semantic judgments in these tests are manually
    supplied. A later LLM Judge module will generate them
    automatically.
    """

    print("=" * 80)
    print("Answer Evaluation Self-Test")
    print("=" * 80)

    # Case 1:
    # Complete and fully grounded answer.
    complete = evaluate_answer_case(
        answerable=True,
        required_facts=[
            "maintenance is required after 240 hours",
            "maintenance is required after 30 days",
        ],
        fact_matches=[
            True,
            True,
        ],
        correctness_score=1.0,
        groundedness_score=1.0,
    )

    _print_result(
        "Case 1 - Complete Answer",
        complete,
    )

    assert (
        complete["fact_coverage"]
        == 1.0
    )

    assert (
        complete["matched_fact_count"]
        == 2
    )

    # Case 2:
    # Correct but incomplete answer.
    incomplete = evaluate_answer_case(
        answerable=True,
        required_facts=[
            "logs archive at 02:30",
            "sync starts at 03:00",
        ],
        fact_matches=[
            True,
            False,
        ],
        correctness_score=0.75,
        groundedness_score=1.0,
    )

    _print_result(
        "Case 2 - Incomplete Answer",
        incomplete,
    )

    assert (
        incomplete["fact_coverage"]
        == 0.5
    )

    # Case 3:
    # Answer contains correct facts but also unsupported claims.
    ungrounded = evaluate_answer_case(
        answerable=True,
        required_facts=[
            "Administrator manages users",
        ],
        fact_matches=[
            True,
        ],
        correctness_score=0.8,
        groundedness_score=0.5,
    )

    _print_result(
        "Case 3 - Partially Ungrounded Answer",
        ungrounded,
    )

    assert (
        ungrounded["fact_coverage"]
        == 1.0
    )

    assert (
        ungrounded["groundedness_score"]
        == 0.5
    )

    # Case 4:
    # Correct no-evidence refusal.
    refusal = evaluate_answer_case(
        answerable=False,
        required_facts=[],
        fact_matches=None,
        correctness_score=None,
        groundedness_score=None,
        refusal_correct=True,
    )

    _print_result(
        "Case 4 - Correct Refusal",
        refusal,
    )

    assert (
        refusal["refusal_accuracy"]
        == 1.0
    )

    # Case 5:
    # No-evidence question, but the model hallucinated an answer.
    hallucinated = evaluate_answer_case(
        answerable=False,
        required_facts=[],
        fact_matches=None,
        correctness_score=None,
        groundedness_score=None,
        refusal_correct=False,
    )

    _print_result(
        "Case 5 - Incorrect Refusal",
        hallucinated,
    )

    assert (
        hallucinated["refusal_accuracy"]
        == 0.0
    )

    print("=" * 80)
    print(
        "All answer evaluation "
        "self-tests passed."
    )
    print("=" * 80)


if __name__ == "__main__":
    main()