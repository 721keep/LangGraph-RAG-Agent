import asyncio
import json
from typing import Any

from app.config import settings
from langchain.chat_models import init_chat_model
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
)
from pydantic import BaseModel, ConfigDict, Field


class AnswerJudgeDecision(BaseModel):
    """
    Structured semantic judgment returned by the Judge model.
    """

    model_config = ConfigDict(
        extra="forbid",
    )

    fact_matches: list[bool]

    correctness_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
    )

    groundedness_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
    )

    refusal_correct: bool | None = None


JUDGE_SYSTEM_PROMPT = """
You are an evaluation judge for a Retrieval-Augmented Generation
(RAG) system.

Your task is to evaluate the FINAL ANSWER strictly using the
evaluation inputs provided to you.

Do not use outside knowledge.

You must evaluate three different concepts separately.

1. Required Fact Coverage

For every item in REQUIRED_FACTS, determine whether the FINAL ANSWER
explicitly communicates that fact or a semantically equivalent fact.

Return one boolean for every required fact, preserving the exact
original order.

Do not require exact wording.

A fact should be true only when the answer clearly communicates its
meaning.

2. Correctness

Evaluate whether the FINAL ANSWER correctly answers the QUESTION when
compared with the REFERENCE_ANSWER.

Score between 0.0 and 1.0.

Use these anchors:

1.00 = fully correct
0.75 = mostly correct with a minor omission or imprecision
0.50 = partially correct
0.25 = mostly incorrect
0.00 = incorrect or contradicts the reference answer

Do not penalize harmless differences in wording.

3. Groundedness

Evaluate whether factual claims in the FINAL ANSWER are supported by
the RETRIEVED_EVIDENCE.

Score between 0.0 and 1.0.

Use these anchors:

1.00 = all meaningful factual claims are supported
0.75 = almost all claims are supported
0.50 = some claims are unsupported
0.25 = most claims are unsupported
0.00 = the answer is unsupported or contradicts the evidence

The REFERENCE_ANSWER may be used for correctness evaluation, but it
must NOT be treated as retrieved evidence when judging groundedness.

For NO-EVIDENCE cases:

- fact_matches must be []
- correctness_score must be null
- groundedness_score must be null
- refusal_correct must be true only when the answer appropriately
  refuses to provide the requested factual answer because reliable
  document evidence is unavailable.
- refusal_correct must be false if the answer invents or guesses the
  requested fact.

For ANSWERABLE cases:

- refusal_correct must be null
- correctness_score must be a number
- groundedness_score must be a number
- fact_matches must contain exactly one boolean for every required fact

Ignore citation formatting when judging answer quality. Citation
validity is evaluated by another component.

Return ONLY one JSON object.

Do not include Markdown.
Do not include explanations before or after the JSON.

Required format:

{
  "fact_matches": [true, false],
  "correctness_score": 0.75,
  "groundedness_score": 1.0,
  "refusal_correct": null
}
"""


def _default_judge_model() -> str:
    """Return the first configured chat model."""

    if not settings.model_names:
        raise RuntimeError(
            "No model is configured in MODEL_NAMES."
        )

    return settings.model_names[0]


def _create_judge_model(
    model_name: str,
):
    """
    Create a deterministic Judge model.

    temperature=0 improves evaluation reproducibility.
    """

    return init_chat_model(
        model=model_name,
        model_provider=settings.model_provider,
        api_key=settings.chat_api_key,
        base_url=settings.model_base_url or None,
        temperature=0,
    )


def _message_text(
    message: AIMessage,
) -> str:
    """Convert Judge AIMessage content to text."""

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


def _extract_json_object(
    text: str,
) -> dict[str, Any]:
    """
    Extract one JSON object from Judge output.

    This tolerates accidental Markdown fences or surrounding text
    while still requiring a valid JSON object.
    """

    cleaned = text.strip()

    if cleaned.startswith("```"):
        lines = cleaned.splitlines()

        if lines:
            lines = lines[1:]

        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]

        cleaned = "\n".join(lines).strip()

    start = cleaned.find("{")
    end = cleaned.rfind("}")

    if start == -1 or end == -1 or end < start:
        raise ValueError(
            "Judge response does not contain "
            "a JSON object."
        )

    json_text = cleaned[
        start : end + 1
    ]

    try:
        payload = json.loads(
            json_text
        )
    except json.JSONDecodeError as exc:
        raise ValueError(
            "Judge returned invalid JSON."
        ) from exc

    if not isinstance(payload, dict):
        raise ValueError(
            "Judge result must be a JSON object."
        )

    return payload


def _format_evidence(
    sources: list[dict[str, Any]],
) -> str:
    """Format retrieved Tool sources for Judge evaluation."""

    if not sources:
        return "(no retrieved document evidence)"

    parts: list[str] = []

    for source in sources:
        source_id = source.get(
            "source_id",
            "?",
        )

        file_name = source.get(
            "file_name",
            "unknown",
        )

        chunk_index = source.get(
            "chunk_index",
        )

        content = source.get(
            "content",
            "",
        )

        parts.append(
            (
                f"[Source {source_id}]\n"
                f"file={file_name}\n"
                f"chunk={chunk_index}\n"
                f"content:\n{content}"
            )
        )

    return "\n\n".join(parts)


def _validate_decision(
    decision: AnswerJudgeDecision,
    *,
    answerable: bool,
    required_facts: list[str],
) -> None:
    """
    Validate semantic consistency of Judge output.
    """

    if answerable:
        if (
            len(decision.fact_matches)
            != len(required_facts)
        ):
            raise ValueError(
                "Judge fact_matches length "
                "does not match required_facts."
            )

        if decision.correctness_score is None:
            raise ValueError(
                "Answerable Judge result requires "
                "correctness_score."
            )

        if decision.groundedness_score is None:
            raise ValueError(
                "Answerable Judge result requires "
                "groundedness_score."
            )

        if decision.refusal_correct is not None:
            raise ValueError(
                "Answerable Judge result requires "
                "refusal_correct=null."
            )

        return

    if decision.fact_matches:
        raise ValueError(
            "No-evidence Judge result requires "
            "fact_matches=[]."
        )

    if decision.correctness_score is not None:
        raise ValueError(
            "No-evidence Judge result requires "
            "correctness_score=null."
        )

    if decision.groundedness_score is not None:
        raise ValueError(
            "No-evidence Judge result requires "
            "groundedness_score=null."
        )

    if decision.refusal_correct is None:
        raise ValueError(
            "No-evidence Judge result requires "
            "refusal_correct."
        )


def _build_user_prompt(
    *,
    question: str,
    final_answer: str,
    answerable: bool,
    reference_answer: str | None,
    required_facts: list[str],
    sources: list[dict[str, Any]],
) -> str:
    """Build one Judge evaluation prompt."""

    evidence = _format_evidence(
        sources
    )

    return f"""
QUESTION:
{question}

ANSWERABLE:
{str(answerable).lower()}

REFERENCE_ANSWER:
{reference_answer if reference_answer is not None else "null"}

REQUIRED_FACTS:
{json.dumps(required_facts, ensure_ascii=False)}

RETRIEVED_EVIDENCE:
{evidence}

FINAL_ANSWER:
{final_answer}

Evaluate the FINAL_ANSWER according to the system instructions.
""".strip()


async def judge_answer_case(
    *,
    question: str,
    final_answer: str,
    answerable: bool,
    reference_answer: str | None,
    required_facts: list[str],
    sources: list[dict[str, Any]],
    model_name: str | None = None,
) -> dict[str, Any]:
    """
    Run semantic Answer Quality evaluation with the Judge model.
    """

    judge_model_name = (
        model_name
        or _default_judge_model()
    )

    model = _create_judge_model(
        model_name=judge_model_name,
    )

    user_prompt = _build_user_prompt(
        question=question,
        final_answer=final_answer,
        answerable=answerable,
        reference_answer=reference_answer,
        required_facts=required_facts,
        sources=sources,
    )

    response = await model.ainvoke(
        [
            SystemMessage(
                content=JUDGE_SYSTEM_PROMPT
            ),
            HumanMessage(
                content=user_prompt
            ),
        ]
    )

    if not isinstance(
        response,
        AIMessage,
    ):
        raise RuntimeError(
            "Judge did not return "
            "an AIMessage."
        )

    raw_text = _message_text(
        response
    )

    payload = _extract_json_object(
        raw_text
    )

    decision = (
        AnswerJudgeDecision.model_validate(
            payload
        )
    )

    _validate_decision(
        decision,
        answerable=answerable,
        required_facts=required_facts,
    )

    return {
        "model_name":
            judge_model_name,

        "fact_matches":
            decision.fact_matches,

        "correctness_score":
            decision.correctness_score,

        "groundedness_score":
            decision.groundedness_score,

        "refusal_correct":
            decision.refusal_correct,

        "raw_response":
            raw_text,
    }


def _print_judgment(
    name: str,
    result: dict[str, Any],
) -> None:
    """Print one Judge smoke-test result."""

    print("-" * 80)
    print(name)

    print(
        "  fact_matches="
        f"{result['fact_matches']}"
    )

    print(
        "  correctness_score="
        f"{result['correctness_score']}"
    )

    print(
        "  groundedness_score="
        f"{result['groundedness_score']}"
    )

    print(
        "  refusal_correct="
        f"{result['refusal_correct']}"
    )


async def main() -> None:
    """
    Run two real Judge smoke tests.

    This validates:
    - model invocation
    - strict JSON parsing
    - schema validation
    - answerable judgment
    - no-evidence judgment
    """

    print("=" * 80)
    print("Answer Judge Smoke Test")
    print("=" * 80)

    answerable_result = (
        await judge_answer_case(
            question=(
                "谁有权限创建和删除系统用户？"
            ),
            final_answer=(
                "只有 Administrator 角色具有"
                "创建和删除系统用户的权限。"
                "[Source 1]"
            ),
            answerable=True,
            reference_answer=(
                "只有 Administrator 角色具有"
                "账户管理权限，可以创建和删除"
                "系统用户。"
            ),
            required_facts=[
                (
                    "Administrator 有权限"
                    "创建和删除系统用户"
                )
            ],
            sources=[
                {
                    "source_id": 1,
                    "file_name":
                        "retrieval_eval_source.txt",
                    "chunk_index": 6,
                    "content": (
                        "Administrator 拥有账户管理、"
                        "角色分配和系统级配置权限。"
                        "Engineer 不能创建或删除"
                        "系统用户。"
                    ),
                }
            ],
        )
    )

    _print_judgment(
        "Case 1 - Answerable",
        answerable_result,
    )

    assert (
        len(
            answerable_result[
                "fact_matches"
            ]
        )
        == 1
    )

    no_evidence_result = (
        await judge_answer_case(
            question=(
                "Aster-X1 出厂时机身"
                "默认是什么颜色？"
            ),
            final_answer=(
                "当前检索结果没有提供足够"
                "可靠的文档证据来回答"
                "Aster-X1 的默认机身颜色。"
            ),
            answerable=False,
            reference_answer=None,
            required_facts=[],
            sources=[],
        )
    )

    _print_judgment(
        "Case 2 - No Evidence",
        no_evidence_result,
    )

    assert (
        no_evidence_result[
            "fact_matches"
        ]
        == []
    )

    assert (
        no_evidence_result[
            "refusal_correct"
        ]
        is not None
    )

    print("=" * 80)
    print(
        "Answer Judge smoke tests passed."
    )
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())