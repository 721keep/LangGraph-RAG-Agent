import asyncio
import json

import api_utils
import streamlit as st
from loguru import logger
from state_management import (
    new_chat,
    update_thread,
    update_user_threads,
)


def authenticated_user_chat_interface_component():
    is_first_message = False
    for message in st.session_state["thread"].messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    if prompt := st.chat_input(
            "Ask a question...",
            key="prompt",
        ):
        if st.session_state["thread"].id is None:
            is_first_message = True

            # Preserve the KB selected before the Thread exists.
            selected_knowledge_base_id = (
                st.session_state["thread"].knowledge_base_id
            )

            create_response = api_utils.create_new_thread()
            thread_id = create_response.get("id")

            if thread_id is None:
                raise ValueError(
                    "Failed to create a new thread."
                )

            st.session_state["thread"].id = thread_id

            # If a KB was selected for the new chat,
            # persist the binding immediately after Thread creation.
            if selected_knowledge_base_id is not None:
                bind_response = (
                    api_utils.set_thread_knowledge_base(
                        thread_id=thread_id,
                        knowledge_base_id=selected_knowledge_base_id,
                    )
                )

                if not bind_response.get("id"):
                    # Avoid leaving a half-created Thread behind.
                    api_utils.delete_thread(thread_id)

                    st.session_state["thread"].id = None

                    raise ValueError(
                        "Failed to bind the selected "
                        "knowledge base to the new thread."
                    )

        text = prompt
        if text:
            st.session_state["thread"].messages.append({"role": "human", "content": text})

        if is_first_message:
            update_thread(st.session_state["thread"].id, f"{text[:30]}")
            update_user_threads()

        with st.chat_message("human"):
            st.markdown(text)

        with st.chat_message("ai"):
            steps_container = st.container()
            answer_placeholder = st.empty()
            full_response = ""

            async def fetch_stream():
                nonlocal full_response
                try:
                    chat_data = {
                        "prompt": text,
                        "model_name": st.session_state["model_name"],
                        "top_k": st.session_state.get("top_k", 3),
                        "similarity_threshold": st.session_state.get(
                            "similarity_threshold",
                            0.50,
                        ),
                        "rerank_top_n": st.session_state.get(
                            "rerank_top_n",
                            3,
                        ),
                    }
                    async for line in api_utils.chat_stream(chat_data, st.session_state["thread"].id):
                        try:
                            event: dict = json.loads(line)
                            event_type = event.get("type")

                            if event_type == "tool_call":
                                with steps_container:
                                    st.markdown(
                                        f"**Tool Call:** Running `{event['name']}` with arguments: `{event['args']}`"
                                    )

                            elif event_type == "tool_result":
                                with steps_container:
                                    if event["name"] == "retrieve_user_documents":
                                        rendered = _render_retrieval_result(
                                            event["content"]
                                        )

                                        if not rendered:
                                            with st.expander(
                                                "**Tool Result:** `retrieve_user_documents`",
                                                expanded=False,
                                            ):
                                                st.code(
                                                    event["content"],
                                                    language="json",
                                                )

                                    else:
                                        with st.expander(
                                            f"**Tool Result:** `{event['name']}`",
                                            expanded=False,
                                        ):
                                            st.code(
                                                event["content"],
                                                language="json",
                                            )

                            elif event_type == "llm_chunk":
                                full_response += event.get("content", "")
                                answer_placeholder.markdown(full_response + "▌")

                            else:
                                logger.warning(f"Unknown event type: {event_type}")
                                st.warning(event)

                        except (json.JSONDecodeError, KeyError) as e:
                            logger.warning(f"Could not parse stream event: {line} - Error: {e}")

                    answer_placeholder.markdown(full_response)
                    if full_response:
                        st.session_state["thread"].messages.append({"role": "ai", "content": full_response})

                except Exception as e:
                    st.error("An error occurred while processing your request.")
                    logger.error(f"Error in fetch_stream: {e}")
                    if is_first_message:
                        api_utils.delete_thread(st.session_state["thread"].id)
                        logger.info(f"Thread {st.session_state['thread'].id} deleted")
                        new_chat()

            with st.spinner("Generating response..."):
                asyncio.run(fetch_stream())

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

    st.markdown(
        f"**🔎 Retrieved Sources:** {retrieved_count} chunk(s)"
    )

    summary_parts = []

    if query:
        summary_parts.append(f"Query: `{query}`")

    if top_k is not None:
        summary_parts.append(f"Top-K: `{top_k}`")

    if similarity_threshold is not None:
        summary_parts.append(
            f"Threshold: `{float(similarity_threshold):.2f}`"
        )
    if rerank_evidence_threshold is not None:
        summary_parts.append(
            "Evidence Threshold: "
            f"`{float(rerank_evidence_threshold):.2f}`"
        )
    if summary_parts:
        st.caption(" · ".join(summary_parts))

    # Retrieval filtering statistics
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

    st.caption(" · ".join(stats_parts))

    if status == "error":
        if reason == "vector_search_failed":
            st.error(
                "Document retrieval failed during vector search."
            )
        elif reason == "reranker_failed":
            st.error(
                "Document retrieval failed during reranking."
            )
        else:
            st.error(
                "Document retrieval encountered a technical error."
            )

        return True

    if status == "no_evidence":
        if reason == "knowledge_base_not_selected":
            st.info(
                "No knowledge base is selected "
                "for this conversation."
            )

        elif reason == "no_candidates":
            st.info(
                "No candidate document chunks were retrieved."
            )

        elif reason == "below_similarity_threshold":
            st.info(
                "No retrieved chunks passed the "
                "similarity threshold."
            )

        elif reason == "below_rerank_evidence_threshold":
            if (
                top_rerank_score is not None
                and rerank_evidence_threshold is not None
            ):
                st.info(
                    "Candidate chunks passed vector filtering, "
                    "but the reranker evidence confidence was "
                    "too low. "
                    f"Top rerank score: "
                    f"{float(top_rerank_score):.3f}; "
                    f"required: "
                    f"{float(rerank_evidence_threshold):.3f}."
                )
            else:
                st.info(
                    "Candidate chunks were rejected by "
                    "the reranker evidence gate."
                )

        else:
            st.info(
                "No sufficiently reliable document "
                "evidence was found."
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
                metadata_parts.append(f"📄 Page: `{page}`")

            if chunk_index is not None:
                metadata_parts.append(
                    f"🧩 Chunk: `{chunk_index}`"
                )

            metadata_parts.append(
                f"🏷️ Rank: `{source_id}`"
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
    for message in st.session_state["thread"].messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    if prompt := st.chat_input("Ask a question...", key="prompt"):
        st.session_state["thread"].messages.append({"role": "human", "content": prompt})

        with st.chat_message("human"):
            st.markdown(prompt)

        with st.chat_message("ai"):
            placeholder = st.empty()
            full_response = ""

            async def fetch_stream():
                nonlocal full_response
                try:
                    chat_data = {"prompt": prompt, "model_name": st.session_state["model_name"]}
                    async for line in api_utils.simple_chat_stream(chat_data):
                        try:
                            chunk = json.loads(line).get("content")
                            full_response += chunk
                            placeholder.markdown(full_response + "▌")
                        except (json.JSONDecodeError, KeyError) as e:
                            logger.warning(f"Could not parse stream event: {line} - Error: {e}")

                    placeholder.markdown(full_response)
                    st.session_state["thread"].messages.append({"role": "ai", "content": full_response})
                except Exception:
                    st.error("An error occurred while processing your request. Please try again.")

            with st.spinner("Generating response..."):
                asyncio.run(fetch_stream())
