import api_utils
import streamlit as st
from uuid import UUID

from state_management import (
    Page,
    change_thread,
    logout_user,
    new_chat,
    update_knowledge_base_document_list,
    update_user_knowledge_bases,
    update_user_threads,
)


def display_sidebar():
    st.sidebar.title("🤖 LangGraph Agent")

    if st.session_state["user"].is_authenticated:
        greeting_component()
        model_selection_component()
        retrieval_settings_component()

        knowledge_base_component()
        document_list_component()

        chat_history_component()
        logout_component()
    else:
        authentication_component()


def authentication_component():
    st.sidebar.subheader("🔒 Authentication", divider="gray")
    col1, col2 = st.sidebar.columns(2)
    if col1.button("Login"):
        st.session_state["page"] = Page.LOGIN
    if col2.button("Register"):
        st.session_state["page"] = Page.REGISTER
    st.sidebar.markdown("**Please login or register for full acces.**")


def greeting_component():
    st.sidebar.subheader(f"👋 Welcome {st.session_state['user'].username}", divider="gray")
    if st.sidebar.button("New Chat"):
        new_chat()


def model_selection_component():
    st.sidebar.subheader(
        "🤖 Model",
        divider="gray",
    )

    chat_config = st.session_state.get(
        "chat_config",
        {},
    )

    model_names = chat_config.get(
        "model_names",
        [],
    )

    model_provider = chat_config.get(
        "model_provider",
    )

    default_model = chat_config.get(
        "default_model",
    )

    if not model_names:
        st.session_state["model_name"] = None

        st.sidebar.error(
            "Unable to load available models."
        )
        return

    current_model = st.session_state.get(
        "model_name",
    )

    if current_model not in model_names:
        current_model = (
            default_model
            if default_model in model_names
            else model_names[0]
        )

        st.session_state["model_name"] = current_model

    selected_index = model_names.index(
        current_model
    )

    selected_model = st.sidebar.selectbox(
        "Chat Model",
        options=model_names,
        index=selected_index,
        key="model_selector",
        label_visibility="collapsed",
    )

    st.session_state["model_name"] = selected_model

    if model_provider:
        st.sidebar.caption(
            f"Provider: {model_provider}"
        )

def retrieval_settings_component():
    top_k = st.session_state.get(
        "top_k",
        3,
    )

    similarity_threshold = st.session_state.get(
        "similarity_threshold",
        0.50,
    )

    rerank_top_n = st.session_state.get(
        "rerank_top_n",
        3,
    )

    st.sidebar.caption(
        "⚙️ "
        f"Top-K {top_k} · "
        f"Threshold {similarity_threshold:.2f} · "
        f"Rerank {rerank_top_n}"
    )

    with st.sidebar.expander(
        "🔎 Retrieval Settings",
        expanded=False,
    ):
        st.select_slider(
            "Top-K",
            options=[1, 3, 5, 8, 10],
            value=top_k,
            key="top_k",
            help=(
                "Maximum number of document chunks "
                "retrieved for each RAG query."
            ),
        )

        st.slider(
            "Similarity Threshold",
            min_value=0.0,
            max_value=1.0,
            value=similarity_threshold,
            step=0.05,
            key="similarity_threshold",
            help=(
                "Minimum relevance score required "
                "for a retrieved chunk to be used."
            ),
        )

        st.select_slider(
            "Rerank Top-N",
            options=[1, 3, 5],
            value=rerank_top_n,
            key="rerank_top_n",
            help=(
                "Maximum number of reranked chunks "
                "passed to the LLM."
            ),
        )

def knowledge_base_component():
    st.sidebar.subheader(
        "📚 Knowledge Base",
        divider="gray",
    )

    knowledge_bases = (
        st.session_state["user"].knowledge_bases
    )

    thread = st.session_state["thread"]

    # ---------------------------------------------------------
    # Create Knowledge Base
    # ---------------------------------------------------------
    with st.sidebar.expander("➕ Create Knowledge Base"):
        kb_name = st.text_input(
            "Name",
            key="new_knowledge_base_name",
            placeholder="e.g. Robot Manual",
        )

        kb_description = st.text_area(
            "Description",
            key="new_knowledge_base_description",
            placeholder="Optional description",
        )

        if st.button(
            "Create",
            key="create_knowledge_base_button",
        ):
            if not kb_name.strip():
                st.error(
                    "Knowledge base name cannot be empty."
                )
            else:
                create_response = (
                    api_utils.create_knowledge_base(
                        name=kb_name.strip(),
                        description=(
                            kb_description.strip()
                            or None
                        ),
                    )
                )

                if create_response.get("id"):
                    st.success(
                        "Knowledge base created successfully."
                    )

                    update_user_knowledge_bases()
                    st.rerun()

                else:
                    st.error(
                        "Failed to create knowledge base."
                    )

    # ---------------------------------------------------------
    # Knowledge Base selector
    # ---------------------------------------------------------
    kb_map = {
        str(kb["id"]): kb["name"]
        for kb in knowledge_bases
    }

    options = [None] + list(kb_map.keys())

    current_kb_id = (
        str(thread.knowledge_base_id)
        if thread.knowledge_base_id
        else None
    )

    # If the current KB has been deleted or is unavailable,
    # fall back to "No Knowledge Base".
    if current_kb_id not in options:
        current_kb_id = None
        thread.knowledge_base_id = None

    selected_index = options.index(current_kb_id)

    selector_key = (
        f"knowledge_base_selector_"
        f"{thread.id or 'new'}"
    )

    selected_kb_id = st.sidebar.selectbox(
        "Select Knowledge Base",
        options=options,
        index=selected_index,
        format_func=lambda kb_id: (
            "No Knowledge Base"
            if kb_id is None
            else kb_map.get(
                kb_id,
                "Unknown Knowledge Base",
            )
        ),
        key=selector_key,
    )

    # ---------------------------------------------------------
    # Bind / switch / unbind
    # ---------------------------------------------------------
    if selected_kb_id != current_kb_id:
        selected_uuid = (
            UUID(selected_kb_id)
            if selected_kb_id
            else None
        )

        # Existing thread:
        # persist the KB binding in backend.
        if thread.id is not None:
            bind_response = (
                api_utils.set_thread_knowledge_base(
                    thread_id=thread.id,
                    knowledge_base_id=selected_uuid,
                )
            )

            if not bind_response.get("id"):
                st.sidebar.error(
                    "Failed to update knowledge base."
                )
                return

        # New chat:
        # keep the selection locally until the thread
        # is created by the first message.
        thread.knowledge_base_id = selected_uuid

        if selected_uuid is not None:
            update_knowledge_base_document_list(
                selected_uuid
            )
        else:
            thread.documents = []

        st.rerun()

    # ---------------------------------------------------------
    # Selected KB information
    # ---------------------------------------------------------
    if selected_kb_id:
        selected_kb = next(
            (
                kb
                for kb in knowledge_bases
                if str(kb["id"]) == selected_kb_id
            ),
            None,
        )

        if selected_kb:
            description = selected_kb.get(
                "description"
            )

            if description:
                st.sidebar.caption(description)

        # -----------------------------------------------------
        # Delete KB
        # -----------------------------------------------------
        with st.sidebar.expander(
            "⚙️ Manage Knowledge Base"
        ):
            confirmed = st.checkbox(
                "Confirm knowledge base deletion",
                key=(
                    "confirm_delete_kb_"
                    f"{selected_kb_id}"
                ),
            )

            if st.button(
                "🗑️ Delete Knowledge Base",
                key=(
                    "delete_knowledge_base_"
                    f"{selected_kb_id}"
                ),
                disabled=not confirmed,
            ):
                delete_success = (
                    api_utils.delete_knowledge_base(
                        UUID(selected_kb_id)
                    )
                )

                if delete_success:
                    thread.knowledge_base_id = None
                    thread.documents = []

                    update_user_knowledge_bases()

                    st.session_state[
                        selector_key
                    ] = None

                    st.sidebar.success(
                        "Knowledge base deleted."
                    )

                    st.rerun()

                else:
                    st.sidebar.error(
                        "Failed to delete knowledge base."
                    )


def chat_history_component():
    st.sidebar.subheader(
        "💬 Conversations",
        divider="gray",
    )

    threads = st.session_state["user"].threads
    current_thread_id = st.session_state["thread"].id

    if not threads:
        st.sidebar.caption(
            "No conversations yet."
        )
        return

    # ---------------------------------------------------------
    # Conversation selector
    # ---------------------------------------------------------
    for thread in threads:
        thread_id = thread["id"]
        title = thread["title"] or "Untitled conversation"

        is_current = (
            current_thread_id is not None
            and str(current_thread_id) == str(thread_id)
        )

        button_label = (
            f"▸ {title}"
            if is_current
            else title
        )

        if st.sidebar.button(
            button_label,
            key=f"select_thread_{thread_id}",
            use_container_width=True,
            type="secondary",
        ):
            if not is_current:
                change_thread(thread_id)
                st.rerun()

    # ---------------------------------------------------------
    # Conversation management
    # ---------------------------------------------------------
    with st.sidebar.expander(
        "⚙️ Manage conversations",
        expanded=False,
    ):
        st.caption(
            "Delete conversations you no longer need."
        )

        for thread in threads:
            thread_id = thread["id"]
            title = thread["title"] or "Untitled conversation"

            col1, col2 = st.columns(
                [0.80, 0.20]
            )

            with col1:
                st.caption(title)

            with col2:
                if st.button(
                    "🗑️",
                    key=f"delete_thread_{thread_id}",
                    help="Delete conversation",
                ):
                    with st.spinner(""):
                        delete_response = (
                            api_utils.delete_thread(
                                thread_id
                            )
                        )

                    if (
                        delete_response.get("status")
                        == "ok"
                    ):
                        update_user_threads()

                        if (
                            current_thread_id is not None
                            and str(current_thread_id)
                            == str(thread_id)
                        ):
                            new_chat()

                        st.rerun()

                    else:
                        st.error(
                            "Failed to delete conversation."
                        )


def document_list_component():
    thread = st.session_state["thread"]
    knowledge_base_id = thread.knowledge_base_id

    if knowledge_base_id is None:
        st.sidebar.caption(
            "📂 Documents · Select a knowledge base first"
        )
        return

    documents = thread.documents

    document_count = len(documents)

    with st.sidebar.expander(
        f"📂 Documents · {document_count}",
        expanded=False,
    ):
        # -----------------------------------------------------
        # Upload Document
        # -----------------------------------------------------
        uploaded_file = st.file_uploader(
            "Upload document",
            type=["pdf", "docx", "txt"],
            key=f"kb_upload_{knowledge_base_id}",
        )

        if uploaded_file is not None:
            if st.button(
                "Upload",
                key=f"upload_button_{knowledge_base_id}",
                use_container_width=True,
            ):
                with st.spinner(
                    "Indexing document..."
                ):
                    upload_response = (
                        api_utils.upload_knowledge_base_document(
                            knowledge_base_id=knowledge_base_id,
                            file=uploaded_file,
                        )
                    )

                document_id = (
                    upload_response.get("document_id")
                    if upload_response
                    else None
                )

                if document_id:
                    st.success(
                        f"{uploaded_file.name} uploaded successfully."
                    )

                    update_knowledge_base_document_list(
                        knowledge_base_id
                    )

                    st.rerun()

                else:
                    st.error(
                        "Failed to upload document."
                    )

        # -----------------------------------------------------
        # Document List
        # -----------------------------------------------------
        if not documents:
            st.caption(
                "No documents uploaded yet."
            )
            return

        st.caption(
            f"{document_count} document"
            f"{'s' if document_count != 1 else ''} available"
        )

        for number, doc in enumerate(
            documents,
            start=1,
        ):
            col1, col2 = st.columns(
                [0.82, 0.18]
            )

            with col1:
                status = doc.get(
                    "status",
                    "ready",
                )

                st.markdown(
                    f"{number}. {doc['file_name']}"
                )

                if status != "ready":
                    st.caption(
                        f"Status: {status}"
                    )

            with col2:
                if st.button(
                    "🗑️",
                    key=f"delete_document_{doc['id']}",
                    help="Delete document",
                ):
                    with st.spinner(""):
                        delete_response = (
                            api_utils.delete_document(
                                doc["id"]
                            )
                        )

                    if delete_response:
                        st.success(
                            "Document deleted successfully."
                        )

                        update_knowledge_base_document_list(
                            knowledge_base_id
                        )

                        st.rerun()

                    else:
                        st.error(
                            "Failed to delete the document."
                        )


def logout_component():
    st.sidebar.subheader(
        "Account",
        divider="gray",
    )

    if st.sidebar.button(
        "↪ Sign out",
        use_container_width=True,
    ):
        logout_user()
        st.rerun()
