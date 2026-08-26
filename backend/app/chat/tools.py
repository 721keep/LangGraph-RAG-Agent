from app.config import settings
from app.db.pgvector_utils import vector_store
from langchain_community.tools.tavily_search import TavilySearchResults
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


@tool
async def retrieve_user_documents(query: str, config: RunnableConfig) -> str:
    """
    Use this tool to answer questions about the user's uploaded documents.
    It will automatically retrieve documents relevant to the current user and thread.
    """
    user_id = config["configurable"].get("user_id")  # type: ignore
    thread_id = config["configurable"].get("thread_id")  # type: ignore
    logger.info(f"Retrieving documents for user_id: {user_id} and thread_id: {thread_id}")

    retriever = vector_store.as_retriever(search_kwargs={"k": 3, "filter": {"thread_id": thread_id}})
    result_docs = await retriever.ainvoke(query)

    if not result_docs:
        return "No relevant documents"

    return "\n\n".join([doc.page_content for doc in result_docs])


tools = [retrieve_user_documents, tavily]
