import api_utils
import streamlit as st
from config import settings
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
    st.sidebar.title("🔗Langgraph RAG Agent")

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
    st.sidebar.subheader("🔒 Authentication", divider="rainbow")
    col1, col2 = st.sidebar.columns(2)
    if col1.button("Login"):
        st.session_state["page"] = Page.LOGIN
    if col2.button("Register"):
        st.session_state["page"] = Page.REGISTER
    st.sidebar.markdown("**Please login or register for full acces.**")


def greeting_component():
    st.sidebar.subheader(f"👋 Welcome {st.session_state['user'].username}", divider="rainbow")
    if st.sidebar.button("New Chat"):
        new_chat()


def model_selection_component():
    st.sidebar.subheader("🤖 Select Model", divider="rainbow")
    st.session_state["model_name"] = st.sidebar.selectbox(
        "Select Model",
        options=settings.model_names,
        key="model",
        label_visibility="collapsed",
    )

def retrieval_settings_component():
    st.sidebar.subheader("🔎 Retrieval Settings", divider="rainbow")

    st.sidebar.select_slider(
        "Top-K",
        options=[1, 3, 5, 8, 10],
        value=3,
        key="top_k",
        help="Maximum number of document chunks retrieved for each RAG query.",
    )

    st.sidebar.slider(
        "Similarity Threshold",
        min_value=0.0,
        max_value=1.0,
        value=0.50,
        step=0.05,
        key="similarity_threshold",
        help="Minimum relevance score required for a retrieved chunk to be used.",
    )

    st.sidebar.select_slider(
        "Rerank Top-N",
        options=[1, 3, 5],
        value=3,
        key="rerank_top_n",
        help="Maximum number of reranked chunks passed to the LLM.",
    )

def knowledge_base_component():
    st.sidebar.subheader(
        "📚 Knowledge Base",
        divider="rainbow",
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
    st.sidebar.subheader("🗨️ Chat History", divider="rainbow")
    threads = st.session_state["user"].threads

    if threads:
        for thread in threads:
            col1, col2, col3 = st.sidebar.columns([0.15, 0.70, 0.15])
            with col1:
                if st.button("✅", key=f"select_{thread['id']}"):
                    change_thread(thread["id"])
            with col2:
                st.markdown(f"{thread['title']}")
            with col3:
                if st.button("❌", key=f"delete_{thread['id']}"):
                    with st.spinner(""):
                        delete_response = api_utils.delete_thread(thread["id"])
                        if delete_response.get("status") == "ok":
                            success_message = f"Thread with ID {thread['title']} deleted successfully."
                            st.sidebar.success(success_message)
                            update_user_threads()
                            new_chat()
                            st.rerun()
                        else:
                            st.sidebar.error("Failed to delete the thread")


def document_list_component():
    st.sidebar.subheader(
        "📂 Documents",
        divider="rainbow",
    )

    thread = st.session_state["thread"]
    knowledge_base_id = thread.knowledge_base_id

    if knowledge_base_id is None:
        st.sidebar.caption(
            "Select a knowledge base to manage documents."
        )
        return

    # ---------------------------------------------------------
    # Upload Document
    # ---------------------------------------------------------
    uploaded_file = st.sidebar.file_uploader(
        "Upload document",
        type=["pdf", "docx", "txt"],
        key=f"kb_upload_{knowledge_base_id}",
    )

    if uploaded_file is not None:
        if st.sidebar.button(
            "Upload",
            key=f"upload_button_{knowledge_base_id}",
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
                st.sidebar.success(
                    f"{uploaded_file.name} uploaded successfully."
                )

                update_knowledge_base_document_list(
                    knowledge_base_id
                )

                st.rerun()

            else:
                st.sidebar.error(
                    "Failed to upload document."
                )

    # ---------------------------------------------------------
    # Document List
    # ---------------------------------------------------------
    documents = thread.documents

    if not documents:
        st.sidebar.write(
            "No documents uploaded yet."
        )
        return

    for number, doc in enumerate(
        documents,
        start=1,
    ):
        col1, col2 = st.sidebar.columns(
            [0.85, 0.15]
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
                "❌",
                key=f"delete_document_{doc['id']}",
            ):
                with st.spinner(""):
                    delete_response = (
                        api_utils.delete_document(
                            doc["id"]
                        )
                    )

                if delete_response:
                    st.sidebar.success(
                        "Document deleted successfully."
                    )

                    update_knowledge_base_document_list(
                        knowledge_base_id
                    )

                    st.rerun()

                else:
                    st.sidebar.error(
                        "Failed to delete the document."
                    )


def logout_component():
    st.sidebar.subheader("❌ Logout", divider="rainbow")
    if st.sidebar.button("logout"):
        logout_user()
        st.rerun()
