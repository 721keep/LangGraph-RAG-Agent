from urllib.parse import urlsplit

from app.config import settings
from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import create_react_agent

from app.tools.registry import get_agent_tools
from .prompts import dynamic_system_prompt


def create_model(model_name: str, streaming: bool = False) -> BaseChatModel:
    """Create a retrieval chain based on the provided model name."""

    endpoint = urlsplit(settings.model_base_url or "")
    if (
        settings.model_provider == "openai"
        and endpoint.scheme == "https"
        and endpoint.hostname == "api.deepseek.com"
        and endpoint.port in (None, 443)
        and not endpoint.username and not endpoint.password
        and not endpoint.query and not endpoint.fragment
        and endpoint.path.rstrip("/") in ("", "/v1", "/beta")
    ):
        from .deepseek import DeepSeekChatOpenAI

        return DeepSeekChatOpenAI(
            model=model_name,
            api_key=settings.chat_api_key,
            base_url=settings.model_base_url,
            streaming=streaming,
        )

    model = init_chat_model(
        model=model_name,
        model_provider=settings.model_provider,
        api_key=settings.chat_api_key,
        base_url=settings.model_base_url or None,
        streaming=streaming,
    )

    return model


async def build_retrival_graph(
    checkpointer: BaseCheckpointSaver | None,
    model_name: str,
    interrupt_before: list[str] | None = None,
) -> CompiledStateGraph:

    model = create_model(model_name=model_name)

    tools = await get_agent_tools()
    agent = create_react_agent(
        model=model,
        tools=tools,
        prompt=dynamic_system_prompt,
        checkpointer=checkpointer,
        interrupt_before=interrupt_before,
    )

    return agent