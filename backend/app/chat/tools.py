import json

from app.config import settings
from app.db.pgvector_utils import vector_store
from app.rag.reranker import rerank_documents

from langchain_community.tools.tavily_search import TavilySearchResults
from langchain_core.documents import Document
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from loguru import logger

DEFAULT_RERANK_EVIDENCE_THRESHOLD = 0.70
tavily = TavilySearchResults(
    name="web_search",
    description=(
        "Search the public web for current and external information. "
        "Use this tool when the user's question requires up-to-date information. "
        "For time-sensitive queries such as latest, recent, today, current, "
        "this year, 最新, 最近, 今天, 当前, or 今年, "
        "construct the search query according to the current date and year "
        "provided in the system prompt. "
        "Never guess or reuse an outdated year from model knowledge."
    ),
    tavily_api_key=settings.tavily_api_key,
    max_results=3,
    include_answer=False,
    include_raw_content=False,
    include_images=False,
)


def _build_retrieved_source(
    doc: Document,
    source_index: int,
    relevance_score: float,
    rerank_score: float | None = None,
) -> dict:
    """Build a structured retrieval source for citation and observability."""

    metadata = doc.metadata

    file_name = metadata.get("file_name", "Unknown file")
    chunk_index = metadata.get("chunk_index")

    page_label = metadata.get("page_label")
    page = metadata.get("page")

    display_page = None

    if page_label is not None:
        display_page = page_label
    elif isinstance(page, int):
        display_page = page + 1

    return {
        "source_id": source_index,
        "source_label": f"[Source {source_index}]",
        "file_name": file_name,
        "page": display_page,
        "chunk_index": chunk_index,
        "relevance_score": round(float(relevance_score), 4),
        "rerank_score": (
            round(float(rerank_score), 4)
            if rerank_score is not None
            else None
        ),
        "content": doc.page_content,
    }


@tool
async def retrieve_user_documents(query: str, config: RunnableConfig) -> str:
    """
    Use this tool to answer questions about the user's uploaded documents.
    It automatically retrieves relevant document chunks for the current thread
    and returns structured content, citation metadata, and relevance scores.
    """

    user_id = config["configurable"].get("user_id")  # type: ignore
    thread_id = config["configurable"].get("thread_id")  # type: ignore
    top_k = config["configurable"].get("top_k", 3)  # type: ignore
    similarity_threshold = config["configurable"].get(
        "similarity_threshold", 0.50
    )
    rerank_top_n = config["configurable"].get(
        "rerank_top_n",
        3,
    )
    rerank_evidence_threshold = config["configurable"].get(
    "rerank_evidence_threshold",
    DEFAULT_RERANK_EVIDENCE_THRESHOLD,
    )
    logger.info(
        f"Retrieving documents for user_id: {user_id}, "
        f"thread_id: {thread_id}, top_k: {top_k}, "
        f"similarity_threshold: {similarity_threshold}, "
        f"rerank_top_n: {rerank_top_n}, "
        f"rerank_evidence_threshold: "
        f"{rerank_evidence_threshold}"
    )

    try:
        results = await vector_store.asimilarity_search_with_relevance_scores(
            query=query,
            k=top_k,
            filter={"thread_id": thread_id},
        )
    except Exception:
        logger.exception(
            "Vector retrieval failed for "
            f"thread_id={thread_id}, query={query!r}"
        )

        retrieval_result = {
            "status": "error",
            "reason": "vector_search_failed",
            "query": query,
            "top_k": top_k,
            "similarity_threshold": similarity_threshold,
            "rerank_top_n": rerank_top_n,
            "rerank_evidence_threshold": (
                rerank_evidence_threshold
            ),
            "top_rerank_score": None,
            "evidence_gate_passed": None,
            "candidate_count": 0,
            "threshold_passed_count": 0,
            "threshold_filtered_count": 0,
            "reranked_count": 0,
            "retrieved_count": 0,
            "filtered_count": 0,
            "sources": [],
        }

        return json.dumps(
            retrieval_result,
            ensure_ascii=False,
        )
    candidate_count = len(results)

    filtered_results = [
        (doc, score)
        for doc, score in results
        if score >= similarity_threshold
    ]

    threshold_passed_count = len(filtered_results)
    threshold_filtered_count = (
        candidate_count - threshold_passed_count
    )

    status = "ok"
    reason = None

    if candidate_count == 0:
        status = "no_evidence"
        reason = "no_candidates"

    elif threshold_passed_count == 0:
        status = "no_evidence"
        reason = "below_similarity_threshold"
    if filtered_results:
        try:
            rerank_results = await rerank_documents(
                query=query,
                documents=[
                    doc.page_content
                    for doc, _ in filtered_results
                ],
                top_n=rerank_top_n,
            )
        except Exception:
            logger.exception(
                "Reranker failed for "
                f"thread_id={thread_id}, query={query!r}"
            )

            retrieval_result = {
                "status": "error",
                "reason": "reranker_failed",
                "rerank_evidence_threshold": (
                    rerank_evidence_threshold
                ),
                "top_rerank_score": None,
                "evidence_gate_passed": None,
                "query": query,
                "top_k": top_k,
                "similarity_threshold": similarity_threshold,
                "rerank_top_n": rerank_top_n,
                "candidate_count": candidate_count,
                "threshold_passed_count": threshold_passed_count,
                "threshold_filtered_count": threshold_filtered_count,
                "reranked_count": 0,
                "retrieved_count": 0,
                "filtered_count": candidate_count,
                "sources": [],
            }

            return json.dumps(
                retrieval_result,
                ensure_ascii=False,
            )
    else:
        rerank_results = []

    reranked_results = []

    for rerank_result in rerank_results:
        candidate_index = rerank_result["index"]
        rerank_score = rerank_result["relevance_score"]

        if not (
            0 <= candidate_index < len(filtered_results)
        ):
            logger.warning(
                f"Skipping invalid rerank index: "
                f"{candidate_index}"
            )
            continue

        doc, vector_score = filtered_results[
            candidate_index
        ]

        reranked_results.append(
            (
                doc,
                vector_score,
                rerank_score,
            )
        )


    # Calculate evidence confidence only after
    # all reranker results have been processed.
    top_rerank_score = (
        max(
            float(rerank_score)
            for _, _, rerank_score
            in reranked_results
        )
        if reranked_results
        else None
    )

    evidence_gate_passed = None


    # Apply the reranker evidence gate only when
    # vector retrieval itself has valid candidates.
    if status == "ok":
        if top_rerank_score is None:
            status = "error"
            reason = "reranker_failed"

            logger.error(
                "Reranker returned no usable results for "
                f"thread_id={thread_id}, "
                f"query={query!r}"
            )

        elif (
            top_rerank_score
            < rerank_evidence_threshold
        ):
            status = "no_evidence"
            reason = (
                "below_rerank_evidence_threshold"
            )

            evidence_gate_passed = False

            logger.info(
                "Reranker evidence gate rejected "
                f"query={query!r}, "
                f"top_rerank_score="
                f"{top_rerank_score:.4f}, "
                f"threshold="
                f"{rerank_evidence_threshold:.4f}"
            )

        else:
            evidence_gate_passed = True


    reranked_count = len(reranked_results)


    # Sources are exposed to the LLM only when
    # the evidence gate passes.
    if status == "ok":
        sources = [
            _build_retrieved_source(
                doc=doc,
                source_index=source_index,
                relevance_score=vector_score,
                rerank_score=rerank_score,
            )
            for source_index, (
                doc,
                vector_score,
                rerank_score,
            ) in enumerate(
                reranked_results,
                start=1,
            )
        ]
    else:
        sources = []


    retrieved_count = len(sources)

    filtered_count = candidate_count - retrieved_count

    retrieval_result = {
        "status": status,
        "reason": reason,

        "query": query,
        "top_k": top_k,
        "similarity_threshold": similarity_threshold,

        "candidate_count": candidate_count,
        "threshold_passed_count": threshold_passed_count,
        "threshold_filtered_count": threshold_filtered_count,

        "rerank_top_n": rerank_top_n,
        "rerank_evidence_threshold": (
            rerank_evidence_threshold
        ),
        "top_rerank_score": (
            round(float(top_rerank_score), 4)
            if top_rerank_score is not None
            else None
        ),
        "evidence_gate_passed": (
            evidence_gate_passed
        ),
        "reranked_count": reranked_count,

        "retrieved_count": retrieved_count,
        "filtered_count": filtered_count,

        "sources": sources,
    }

    serialized_result = json.dumps(
        retrieval_result,
        ensure_ascii=False,
    )

    logger.info(
        f"Retrieval result: "
        f"status={status}, "
        f"reason={reason}, "
        f"candidate_count={candidate_count}, "
        f"threshold_passed_count="
        f"{threshold_passed_count}, "
        f"reranked_count={reranked_count}, "
        f"top_rerank_score="
        f"{top_rerank_score}, "
        f"evidence_gate_passed="
        f"{evidence_gate_passed}, "
        f"retrieved_count={retrieved_count}"
    )

    return serialized_result


tools = [retrieve_user_documents, tavily]