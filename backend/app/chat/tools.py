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


def _format_retrieved_document(doc: Document, source_index: int) -> str:
    """Format a retrieved document chunk with citation metadata."""

    metadata = doc.metadata

    file_name = metadata.get("file_name", "Unknown file")
    chunk_index = metadata.get("chunk_index")
    page_label = metadata.get("page_label")
    page = metadata.get("page")

    source_lines = [
        f"[Source {source_index}]",
        f"file_name: {file_name}",
    ]

    if page_label is not None:
        source_lines.append(f"page: {page_label}")
    elif isinstance(page, int):
        source_lines.append(f"page: {page + 1}")

    if chunk_index is not None:
        source_lines.append(f"chunk_index: {chunk_index}")

    source_lines.append("content:")
    source_lines.append(doc.page_content)

    return "\n".join(source_lines)


@tool
async def retrieve_user_documents(query: str, config: RunnableConfig) -> str:
    """
    Use this tool to answer questions about the user's uploaded documents.
    It automatically retrieves relevant document chunks for the current thread
    and returns both their content and citation metadata.
    """
    user_id = config["configurable"].get("user_id")  # type: ignore
    thread_id = config["configurable"].get("thread_id")  # type: ignore

    logger.info(
        f"Retrieving documents for user_id: {user_id} and thread_id: {thread_id}"
    )

    retriever = vector_store.as_retriever(
        search_kwargs={
            "k": 3,
            "filter": {"thread_id": thread_id},
        }
    )

    result_docs = await retriever.ainvoke(query)

    if not result_docs:
        return "No relevant documents"

    formatted_documents = [
        _format_retrieved_document(doc, source_index)
        for source_index, doc in enumerate(result_docs, start=1)
    ]

    return "\n\n".join(formatted_documents)


tools = [retrieve_user_documents, tavily]