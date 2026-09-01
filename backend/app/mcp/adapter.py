import asyncio
import json
import time
from typing import Any

import httpx2
from langchain_core.tools import StructuredTool
from mcp import Client


def _contains_exception(
    exc: BaseException,
    exception_types: tuple[type[BaseException], ...],
) -> bool:
    """Check whether an exception group contains a target exception."""

    if isinstance(exc, exception_types):
        return True

    if isinstance(exc, BaseExceptionGroup):
        return any(
            _contains_exception(
                nested_exc,
                exception_types,
            )
            for nested_exc in exc.exceptions
        )

    return False


def create_langchain_tool_from_mcp(
    mcp_server: Any,
    mcp_tool: Any,
    server_name: str,
    timeout_seconds: float = 10.0,
) -> StructuredTool:
    """Convert one MCP tool into a LangChain StructuredTool."""

    async def call_mcp_tool(**kwargs: Any) -> str:
        started_at = time.perf_counter()

        async def invoke_mcp_tool():
            async with Client(mcp_server) as client:
                return await client.call_tool(
                    mcp_tool.name,
                    kwargs,
                )

        try:
            result = await asyncio.wait_for(
                invoke_mcp_tool(),
                timeout=timeout_seconds,
            )

        # Adapter-level timeout
        except TimeoutError:
            latency_ms = round(
                (time.perf_counter() - started_at) * 1000,
                2,
            )

            return json.dumps(
                {
                    "status": "error",
                    "success": False,
                    "tool_source": "mcp",
                    "server_name": server_name,
                    "tool_name": mcp_tool.name,
                    "latency_ms": latency_ms,
                    "error_reason": "tool_timeout",
                    "result": None,
                    "content": [
                        (
                            "MCP tool execution exceeded "
                            f"the {timeout_seconds:.2f}s timeout."
                        )
                    ],
                },
                ensure_ascii=False,
            )

        # Direct HTTP connection failure
        except (
            httpx2.ConnectError,
            httpx2.ConnectTimeout,
        ):
            latency_ms = round(
                (time.perf_counter() - started_at) * 1000,
                2,
            )

            return json.dumps(
                {
                    "status": "error",
                    "success": False,
                    "tool_source": "mcp",
                    "server_name": server_name,
                    "tool_name": mcp_tool.name,
                    "latency_ms": latency_ms,
                    "error_reason": "server_unavailable",
                    "result": None,
                    "content": [
                        (
                            "The MCP server is currently "
                            "unavailable."
                        )
                    ],
                },
                ensure_ascii=False,
            )

        # MCP Streamable HTTP uses AnyIO TaskGroup,
        # so connection failures may be wrapped
        # inside an ExceptionGroup.
        except ExceptionGroup as exc:
            if _contains_exception(
                exc,
                (
                    httpx2.ConnectError,
                    httpx2.ConnectTimeout,
                ),
            ):
                latency_ms = round(
                    (time.perf_counter() - started_at) * 1000,
                    2,
                )

                return json.dumps(
                    {
                        "status": "error",
                        "success": False,
                        "tool_source": "mcp",
                        "server_name": server_name,
                        "tool_name": mcp_tool.name,
                        "latency_ms": latency_ms,
                        "error_reason": "server_unavailable",
                        "result": None,
                        "content": [
                            (
                                "The MCP server is currently "
                                "unavailable."
                            )
                        ],
                    },
                    ensure_ascii=False,
                )

            # Unknown ExceptionGroup should not be
            # incorrectly classified as server_unavailable.
            raise

        latency_ms = round(
            (time.perf_counter() - started_at) * 1000,
            2,
        )

        # MCP returned a normal CallToolResult,
        # but the tool execution itself failed.
        if result.is_error:
            error_content = [
                getattr(item, "text", str(item))
                for item in result.content
            ]

            error_text = "\n".join(
                str(item)
                for item in error_content
            ).lower()

            schema_title = str(
                mcp_tool.input_schema.get(
                    "title",
                    "",
                )
            ).lower()

            error_reason = "tool_execution_error"

            # Distinguish invalid arguments from
            # ordinary tool execution errors.
            if (
                schema_title
                and f"validation error for {schema_title}"
                in error_text
            ):
                error_reason = "invalid_arguments"

            return json.dumps(
                {
                    "status": "error",
                    "success": False,
                    "tool_source": "mcp",
                    "server_name": server_name,
                    "tool_name": mcp_tool.name,
                    "latency_ms": latency_ms,
                    "error_reason": error_reason,
                    "result": None,
                    "content": error_content,
                },
                ensure_ascii=False,
            )

        # Successful MCP result
        if result.structured_content is not None:
            tool_result: Any = (
                result.structured_content
            )
        else:
            tool_result = {
                "content": [
                    getattr(
                        item,
                        "text",
                        str(item),
                    )
                    for item in result.content
                ]
            }

        return json.dumps(
            {
                "status": "ok",
                "success": True,
                "tool_source": "mcp",
                "server_name": server_name,
                "tool_name": mcp_tool.name,
                "latency_ms": latency_ms,
                "error_reason": None,
                "result": tool_result,
            },
            ensure_ascii=False,
        )

    return StructuredTool(
        name=mcp_tool.name,
        description=(
            mcp_tool.description
            or f"MCP tool from {server_name}"
        ),
        args_schema=mcp_tool.input_schema,
        coroutine=call_mcp_tool,
        metadata={
            "tool_source": "mcp",
            "server_name": server_name,
        },
    )