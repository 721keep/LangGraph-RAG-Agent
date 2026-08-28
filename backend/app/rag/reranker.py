from typing import TypedDict

from app.config import settings
from loguru import logger
from openai import AsyncOpenAI


RERANK_MODEL = "qwen3-rerank"

RERANK_BASE_URL = (
    "https://dashscope.aliyuncs.com/compatible-api/v1"
)


class RerankResult(TypedDict):
    """A single reranking result."""

    index: int
    relevance_score: float


rerank_client = AsyncOpenAI(
    api_key=settings.api_key.get_secret_value(),
    base_url=RERANK_BASE_URL,
    timeout=30.0,
)


async def rerank_documents(
    query: str,
    documents: list[str],
    top_n: int,
) -> list[RerankResult]:
    """
    Rerank candidate document chunks according to their
    relevance to the query.

    Args:
        query:
            User retrieval query.

        documents:
            Candidate document chunk contents.

        top_n:
            Maximum number of reranked results to return.

    Returns:
        Reranked results ordered by relevance score
        in descending order.
    """

    if not query.strip():
        raise ValueError("Rerank query must not be empty.")

    if not documents:
        return []

    if top_n < 1:
        raise ValueError("top_n must be greater than or equal to 1.")

    effective_top_n = min(top_n, len(documents))

    logger.info(
        f"Reranking {len(documents)} candidate documents "
        f"with model={RERANK_MODEL}, "
        f"top_n={effective_top_n}"
    )

    response = await rerank_client.post(
        "/reranks",
        body={
            "model": RERANK_MODEL,
            "query": query,
            "documents": documents,
            "top_n": effective_top_n,
        },
        cast_to=object,
    )

    if not isinstance(response, dict):
        raise RuntimeError(
            "Unexpected reranker response format."
        )

    raw_results = response.get("results")

    if not isinstance(raw_results, list):
        raise RuntimeError(
            "Reranker response does not contain "
            "a valid results list."
        )

    reranked_results: list[RerankResult] = []

    for item in raw_results:
        if not isinstance(item, dict):
            continue

        index = item.get("index")
        relevance_score = item.get("relevance_score")

        if index is None or relevance_score is None:
            continue

        reranked_results.append(
            {
                "index": int(index),
                "relevance_score": float(relevance_score),
            }
        )

    logger.info(
        f"Reranker returned "
        f"{len(reranked_results)} result(s)"
    )

    return reranked_results