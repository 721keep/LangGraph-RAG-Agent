import asyncio
from typing import Any

import app.chat.langgraph_agent as agent_module


async def main() -> None:
    captured: dict[str, Any] = {}

    original_create_model = agent_module.create_model
    original_create_react_agent = agent_module.create_react_agent

    def fake_create_model(
        model_name: str,
        streaming: bool = False,
    ) -> object:
        return object()

    def fake_create_react_agent(
        *,
        model: Any,
        tools: list[Any],
        prompt: Any,
        checkpointer: Any,
    ) -> str:
        captured["tools"] = tools
        return "fake-agent"

    try:
        agent_module.create_model = fake_create_model
        agent_module.create_react_agent = fake_create_react_agent

        result = await agent_module.build_retrival_graph(
            checkpointer=None,
            model_name="test-model",
        )
    finally:
        agent_module.create_model = original_create_model
        agent_module.create_react_agent = original_create_react_agent

    tools = captured["tools"]
    tool_names = [tool.name for tool in tools]

    print("Agent build result:", result)
    print("Tools passed to create_react_agent:")

    for tool in tools:
        print(
            f"- {tool.name} | "
            f"metadata={tool.metadata}"
        )

    expected_tools = {
        "retrieve_user_documents",
        "web_search",
        "get_server_status",
    }

    assert set(tool_names) == expected_tools, (
        f"Unexpected tools: {tool_names}"
    )

    print("\nAgent tool wiring test passed")


if __name__ == "__main__":
    asyncio.run(main())