import json

from app.config import settings
from app.db.pgvector_utils import vector_store
from langchain_community.tools.tavily_search import TavilySearchResults
from langchain_core.documents import Document
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from loguru import logger


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

    logger.info(
        f"Retrieving documents for user_id: {user_id} and thread_id: {thread_id}"
    )

    top_k = 3

    results = await vector_store.asimilarity_search_with_relevance_scores(
        query=query,
        k=top_k,
        filter={"thread_id": thread_id},
    )

    if not results:
        return "No relevant documents"

    sources = [
        _build_retrieved_source(
            doc=doc,
            source_index=source_index,
            relevance_score=relevance_score,
        )
        for source_index, (doc, relevance_score) in enumerate(results, start=1)
    ]

    retrieval_result = {
        "query": query,
        "top_k": top_k,
        "retrieved_count": len(sources),
        "sources": sources,
    }

    return json.dumps(
        retrieval_result,
        ensure_ascii=False,
    )


tools = [retrieve_user_documents, tavily]