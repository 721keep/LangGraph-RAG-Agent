import asyncio
import json

import api_utils
import streamlit as st
import ui
from loguru import logger
from state_management import (
    update_thread,
    update_user_threads,
)


def authenticated_user_chat_interface_component():
    from conversation_controller import chat_interface
    chat_interface(authenticated=True)


def _render_web_search_result(content: str) -> bool:
    """Render web search results in a user-friendly form."""

    try:
        search_data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return False

    if isinstance(search_data, dict):
        results = search_data.get(
            "results",
            [],
        )
    elif isinstance(search_data, list):
        results = search_data
    else:
        return False

    if not results:
        ui.icon_label("Web search complete", "globe")
        st.caption(
            "No relevant public web sources were returned."
        )
        return True

    ui.icon_label("Web search complete", "globe")

    st.caption(
        f"{len(results)} public web source"
        f"{'s' if len(results) != 1 else ''} retrieved."
    )

    for index, result in enumerate(
        results,
        start=1,
    ):
        if not isinstance(result, dict):
            continue

        title = result.get(
            "title",
            f"Web source {index}",
        )

        url = result.get("url")
        snippet = result.get(
            "content",
            "",
        )

        score = result.get("score")

        with st.expander(
            f"{index}. {title}",
            expanded=False,
        ):
            if url:
                st.markdown(
                    f"[Open source]({url})"
                )

            if snippet:
                st.write(snippet)

            if score is not None:
                try:
                    st.caption(
                        "Relevance score: "
                        f"{float(score):.3f}"
                    )
                except (TypeError, ValueError):
                    pass

    return True
                
def _render_mcp_tool_result(event: dict) -> bool:
    """Render MCP tool execution in a user-friendly form."""

    if event.get("tool_source") != "mcp":
        return False

    tool_name = event.get(
        "name",
        "unknown",
    )

    server_name = event.get(
        "server_name",
        "unknown",
    )

    status = event.get("status")
    success = event.get("success")
    latency_ms = event.get("latency_ms")
    error_reason = event.get("error_reason")

    try:
        tool_data = json.loads(
            event.get("content", "{}")
        )
    except (json.JSONDecodeError, TypeError):
        tool_data = {}

    # ---------------------------------------------------------
    # User-facing execution status
    # ---------------------------------------------------------
    if success is True:
        st.markdown(
            f"**MCP tool completed:** `{tool_name}`"
        )

        st.caption(
            "External tool execution completed successfully."
        )

    elif success is False:
        error_messages = {
            "tool_execution_error": (
                "The external tool encountered an error "
                "while processing the request."
            ),
            "invalid_arguments": (
                "The tool could not run because the provided "
                "arguments were invalid."
            ),
            "tool_timeout": (
                "The external tool took too long to respond."
            ),
            "server_unavailable": (
                "The MCP service is currently unavailable."
            ),
        }

        user_message = error_messages.get(
            error_reason,
            "The external tool could not complete the request.",
        )

        st.markdown(
            f"**MCP tool unavailable:** `{tool_name}`"
        )

        st.error(user_message)

        if error_reason in {
            "tool_timeout",
            "server_unavailable",
        }:
            st.caption(
                "Please try again shortly."
            )
        else:
            st.caption(
                "The Agent could not complete this tool action."
            )

    else:
        st.markdown(
            f"**MCP tool finished:** `{tool_name}`"
        )

    # ---------------------------------------------------------
    # Technical observability
    # ---------------------------------------------------------
    with st.expander(
        "MCP execution details",
        expanded=False,
    ):
        detail_parts = [
            "Source: `MCP`",
            f"Server: `{server_name}`",
            f"Tool: `{tool_name}`",
        ]

        if status is not None:
            detail_parts.append(
                f"Status: `{status}`"
            )

        if latency_ms is not None:
            try:
                detail_parts.append(
                    "Latency: "
                    f"`{float(latency_ms):.2f} ms`"
                )
            except (TypeError, ValueError):
                pass

        st.caption(
            " · ".join(detail_parts)
        )

        if error_reason:
            st.markdown(
                "**Technical error**"
            )

            st.caption(
                f"Reason: `{error_reason}`"
            )

    # ---------------------------------------------------------
    # Tool result
    # ---------------------------------------------------------
    result = tool_data.get("result")

    if result is not None:
        with st.expander(
            "MCP result",
            expanded=False,
        ):
            if isinstance(
                result,
                (dict, list),
            ):
                st.json(result)

            else:
                st.code(
                    str(result)
                )

    return True

def _render_retrieval_result(content: str) -> bool:
    """Render structured RAG retrieval results as observable source cards."""

    try:
        retrieval_data = json.loads(content)

    except (json.JSONDecodeError, TypeError):
        return False

    if not isinstance(retrieval_data, dict) or "sources" not in retrieval_data:
        return False
    status = retrieval_data.get("status", "ok")
    reason = retrieval_data.get("reason")
    sources = retrieval_data.get("sources", [])
    query = retrieval_data.get("query")
    top_k = retrieval_data.get("top_k")

    # Similarity Threshold observability
    similarity_threshold = retrieval_data.get("similarity_threshold")

    retrieved_count = retrieval_data.get(
        "retrieved_count",
        len(sources),
    )

    # Backward-compatible fallbacks for old ToolMessages
    candidate_count = retrieval_data.get(
        "candidate_count",
        retrieved_count,
    )

    threshold_passed_count = retrieval_data.get(
    "threshold_passed_count",
    retrieved_count,
    )

    threshold_filtered_count = retrieval_data.get(
        "threshold_filtered_count",
        max(candidate_count - threshold_passed_count, 0),
    )

    rerank_top_n = retrieval_data.get("rerank_top_n")
    reranked_count = retrieval_data.get(
        "reranked_count",
        retrieved_count,
    )
    rerank_evidence_threshold = retrieval_data.get(
    "rerank_evidence_threshold"
    )

    top_rerank_score = retrieval_data.get(
        "top_rerank_score"
    )

    evidence_gate_passed = retrieval_data.get(
        "evidence_gate_passed"
    )

    # ---------------------------------------------------------
    # User-facing retrieval status
    # ---------------------------------------------------------
    if status == "ok":
        ui.icon_label("Knowledge base search complete", "book-open")

        st.caption(
            f"{retrieved_count} evidence chunk"
            f"{'s' if retrieved_count != 1 else ''} selected."
        )

    elif status == "no_evidence":
        ui.icon_label("Knowledge base search complete", "book-open")

        st.caption(
            "No reliable evidence was selected."
        )

    else:
        ui.icon_label("Knowledge base search failed", "circle-alert")

    # ---------------------------------------------------------
    # Technical retrieval observability
    # ---------------------------------------------------------
    summary_parts = []

    if query:
        summary_parts.append(
            f"Query: `{query}`"
        )

    if top_k is not None:
        summary_parts.append(
            f"Top-K: `{top_k}`"
        )

    if similarity_threshold is not None:
        summary_parts.append(
            "Threshold: "
            f"`{float(similarity_threshold):.2f}`"
        )

    if rerank_evidence_threshold is not None:
        summary_parts.append(
            "Evidence Threshold: "
            f"`{float(rerank_evidence_threshold):.2f}`"
        )

    stats_parts = [
        f"Vector Candidates: `{candidate_count}`",
        f"Threshold Passed: `{threshold_passed_count}`",
        f"Threshold Filtered: `{threshold_filtered_count}`",
    ]

    if rerank_top_n is not None:
        stats_parts.append(
            f"Rerank Top-N: `{rerank_top_n}`"
        )

    stats_parts.append(
        f"Reranked: `{reranked_count}`"
    )

    if top_rerank_score is not None:
        stats_parts.append(
            "Top Rerank Score: "
            f"`{float(top_rerank_score):.3f}`"
        )

    if evidence_gate_passed is True:
        stats_parts.append(
            "Evidence Gate: `PASS`"
        )

    elif evidence_gate_passed is False:
        stats_parts.append(
            "Evidence Gate: `REJECT`"
        )

    stats_parts.append(
        f"Final Sources: `{retrieved_count}`"
    )

    with st.expander(
        "Retrieval details",
        expanded=False,
    ):
        if summary_parts:
            st.caption(
                " · ".join(summary_parts)
            )

        st.caption(
            " · ".join(stats_parts)
        )

    if status == "error":
        error_messages = {
            "vector_search_failed": (
                "Knowledge base search is temporarily unavailable."
            ),
            "reranker_failed": (
                "The retrieved evidence could not be ranked reliably."
            ),
        }

        user_message = error_messages.get(
            reason,
            "Knowledge base retrieval encountered a technical problem.",
        )

        st.error(user_message)

        st.caption(
            "Please try again. If the problem persists, "
            "check the retrieval service configuration."
        )

        with st.expander(
            "Technical details",
            expanded=False,
        ):
            st.caption(
                f"Reason: `{reason or 'unknown'}`"
            )

        return True

    if status == "no_evidence":
        if reason == "knowledge_base_not_selected":
            st.info(
                "No knowledge base is connected to this conversation."
            )

            st.caption(
                "Select a knowledge base from the toolbar "
                "if you want the Agent to answer from private documents."
            )

        elif reason == "no_candidates":
            st.info(
                "No matching content was found in the selected knowledge base."
            )

            st.caption(
                "Try rephrasing the question or check whether "
                "the relevant document has been uploaded."
            )

        elif reason == "below_similarity_threshold":
            st.info(
                "Some document matches were found, "
                "but none were relevant enough to use safely."
            )

            st.caption(
                "Try a more specific question or adjust "
                "the retrieval threshold if appropriate."
            )

        elif reason == "below_rerank_evidence_threshold":
            st.info(
                "Potential evidence was found, "
                "but its confidence was too low to support an answer."
            )

            if (
                top_rerank_score is not None
                and rerank_evidence_threshold is not None
            ):
                with st.expander(
                    "Evidence confidence details",
                    expanded=False,
                ):
                    st.caption(
                        "Top rerank score: "
                        f"`{float(top_rerank_score):.3f}`"
                    )

                    st.caption(
                        "Required evidence score: "
                        f"`{float(rerank_evidence_threshold):.3f}`"
                    )

        else:
            st.info(
                "No sufficiently reliable evidence was found "
                "in the selected knowledge base."
            )

        return True

    if not sources:
        st.warning(
            "Retrieval returned status=ok but no "
            "document sources were available."
        )
        return True

    for source in sources:
        source_id = source.get("source_id", "?")
        source_label = source.get(
            "source_label",
            f"[Source {source_id}]",
        )

        file_name = source.get("file_name", "Unknown file")
        page = source.get("page")
        chunk_index = source.get("chunk_index")
        relevance_score = source.get("relevance_score")
        retrieved_content = source.get("content", "")
        rerank_score = source.get("rerank_score")

        try:
            score_value = float(relevance_score)
            score_text = f"{score_value:.3f}"
        except (TypeError, ValueError):
            score_value = None
            score_text = "N/A"

        try:
            rerank_score_value = float(rerank_score)
            rerank_score_text = f"{rerank_score_value:.3f}"
        except (TypeError, ValueError):
            rerank_score_value = None
            rerank_score_text = "N/A"

        if rerank_score_value is not None:
            expander_title = (
                f"{source_label} · {file_name} · "
                f"Vector {score_text} · "
                f"Rerank {rerank_score_text}"
            )
        else:
            expander_title = (
                f"{source_label} · {file_name} · "
                f"Relevance {score_text}"
            )

        with st.expander(expander_title, expanded=False):
            metadata_parts = []

            if page is not None:
                metadata_parts.append(f"Page: `{page}`")

            if chunk_index is not None:
                metadata_parts.append(
                    f"Chunk: `{chunk_index}`"
                )

            metadata_parts.append(
                f"Rank: `{source_id}`"
            )

            st.markdown(" · ".join(metadata_parts))

            if score_value is not None:
                st.caption(
                    f"Vector relevance score: {score_value:.4f}"
                )
                normalized_score = max(
                    0.0,
                    min(1.0, score_value),
                )
                st.progress(normalized_score)

            if rerank_score_value is not None:
                st.caption(
                    f"Rerank score: {rerank_score_value:.4f}"
                )
            st.markdown("**Retrieved content**")
            st.code(retrieved_content)

    return True

def unauthenticated_user_chat_interface_component():
    from conversation_controller import chat_interface
    chat_interface(authenticated=False)
