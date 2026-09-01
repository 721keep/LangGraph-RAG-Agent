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