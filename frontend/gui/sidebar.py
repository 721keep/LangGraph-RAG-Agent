import api_utils
import streamlit as st
import ui
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
    with st.sidebar:
        ui.brand()
        ui.button("New Chat", key="ui_new_chat", name="plus", variant="primary",
                  use_container_width=True, on_click=_new_chat)
        st.caption("WORKSPACE")
        authenticated = st.session_state["user"].is_authenticated
        for view, label, name in [("chat", "Conversations", "message-square"),
                                  ("knowledge", "Knowledge bases", "book-open"),
                                  ("documents", "Documents", "file-text")]:
            ui.button(label, key="ui_nav_" + view, name=name,
                      variant="active" if st.session_state.get("ui_view") == view else "ghost",
                      disabled=not authenticated and view != "chat", use_container_width=True,
                      on_click=ui.set_view, args=(view,),
                      help=label if authenticated or view == "chat" else "Sign in to manage your knowledge")
        st.divider()
        if authenticated:
            chat_history_component()
            st.divider()
            greeting_component()
            logout_component()
        else:
            authentication_component()


def _new_chat():
    new_chat()
    st.session_state.pop("knowledge_base_selector_new", None)
    ui.set_view("chat")


def authentication_component():
    st.caption("Make room for your knowledge.")
    col1, col2 = st.columns(2)
    with col1:
        if ui.button("Login", key="ui_login", name="log-in", use_container_width=True):
            st.session_state["page"] = Page.LOGIN
    with col2:
        if ui.button("Register", key="ui_register", name="user-round", use_container_width=True):
            st.session_state["page"] = Page.REGISTER


def greeting_component():
    ui.icon_label(st.session_state["user"].username or "Your account", "user-round")
    st.caption("Personal workspace")


def model_selection_component():
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

        st.error(
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

    selected_model = st.selectbox(
        "Chat Model",
        options=model_names,
        index=selected_index,
        key="model_selector",
        label_visibility="collapsed",
    )

    st.session_state["model_name"] = selected_model



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

    with ui.popover("Retrieval settings", key="toolbar_retrieval", name="sliders-horizontal", icon_only=True):
        ui.icon_label("Retrieval", "sliders-horizontal")
        st.caption("Agent tools: RAG / Web Search / MCP. Availability follows backend configuration.")
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


def knowledge_base_selector_component():
    knowledge_bases = st.session_state["user"].knowledge_bases
    thread = st.session_state["thread"]
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

    if st.session_state.pop("ui_reset_kb", False) or st.session_state.get(selector_key) not in options:
        st.session_state.pop(selector_key, None)
    selected_kb_id = st.selectbox(
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
        label_visibility="collapsed",
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
                    detailed=True,
                )
            )

            if not bind_response["ok"]:
                st.error(bind_response["message"])
                st.session_state["ui_reset_kb"] = True
                return

        # New chat:
        # keep the selection locally until the thread
        # is created by the first message.
        thread.knowledge_base_id = selected_uuid
        thread.documents = []

        if selected_uuid is not None:
            update_knowledge_base_document_list(
                selected_uuid
            )
        else:
            thread.documents = []

        st.rerun()



def knowledge_base_component():
    knowledge_bases = (
        st.session_state["user"].knowledge_bases
    )

    thread = st.session_state["thread"]

    # ---------------------------------------------------------
    # Create Knowledge Base
    # ---------------------------------------------------------
    ui.restore_draft("new_knowledge_base_name")
    ui.restore_draft("new_knowledge_base_description")
    with st.expander("Create Knowledge Base", expanded=not knowledge_bases):
        kb_name = st.text_input(
            "Name",
            key="new_knowledge_base_name",
            on_change=ui.remember_draft, args=("new_knowledge_base_name",),
            placeholder="e.g. Robot Manual",
        )

        kb_description = st.text_area(
            "Description",
            key="new_knowledge_base_description",
            on_change=ui.remember_draft, args=("new_knowledge_base_description",),
            placeholder="Optional description",
        )

        if ui.button(
            "Create", name="plus", variant="primary",
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
                    ui.flash(
                        "Knowledge base created successfully."
                    )

                    update_user_knowledge_bases()
                    st.rerun()

                else:
                    st.error(
                        "Failed to create knowledge base."
                    )

    selected_kb_id = str(thread.knowledge_base_id) if thread.knowledge_base_id else None
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
                st.caption(description)

        # -----------------------------------------------------
        # Delete KB
        # -----------------------------------------------------
        with st.expander(
            "Manage Knowledge Base"
        ):
            confirmed = st.checkbox(
                "Confirm knowledge base deletion",
                key=(
                    "confirm_delete_kb_"
                    f"{selected_kb_id}"
                ),
            )

            ui.danger_note("Deleting this knowledge base permanently removes its documents and indexed chunks. This cannot be undone.")
            if ui.button(
                "Delete Knowledge Base", name="trash-2", variant="danger",
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

                    st.session_state["ui_reset_kb"] = True

                    ui.flash(
                        "Knowledge base deleted."
                    )

                    st.rerun()

                else:
                    st.error(
                        "Failed to delete knowledge base."
                    )


def chat_history_component():
    st.caption("RECENT CONVERSATIONS")
    ui.restore_draft("ui_history_search")
    query = st.text_input("Search conversations", placeholder="Search conversations…",
                          key="ui_history_search", label_visibility="collapsed",
                          on_change=ui.remember_draft, args=("ui_history_search",))
    threads = st.session_state["user"].threads
    current = st.session_state["thread"].id
    shown = [t for t in threads if query.casefold() in (t["title"] or "Untitled conversation").casefold()]
    if not shown:
        st.caption("No matching conversations." if query else "Your conversations will appear here.")
    for thread in shown:
        tid = thread["id"]
        title = thread["title"] or "Untitled conversation"
        selected = current is not None and str(current) == str(tid)
        item, actions = st.columns([.84, .16], gap="small")
        with item:
            if ui.button(title, key=f"select_thread_{tid}", name="message-square",
                         variant="active" if selected else "ghost", use_container_width=True):
                if not selected:
                    change_thread(tid)
                ui.set_view("chat")
                st.rerun()
        with actions:
            with ui.popover("Conversation actions: " + title, key=f"thread_actions_{tid}", icon_only=True):
                st.caption(title)
                ui.danger_note("Delete this conversation and its saved history? This cannot be undone.")
                confirm = st.checkbox("Confirm conversation deletion", key=f"confirm_delete_thread_{tid}")
                if ui.button("Delete conversation", key=f"delete_thread_{tid}", name="trash-2",
                             variant="danger", disabled=not confirm):
                    with st.spinner("Deleting conversation…"):
                        response = api_utils.delete_thread(tid)
                    if response.get("status") == "ok":
                        update_user_threads()
                        if selected:
                            _new_chat()
                        ui.flash("Conversation deleted.")
                        st.rerun()
                    else:
                        st.error("Failed to delete conversation.")


def document_list_component():
    thread = st.session_state["thread"]
    knowledge_base_id = thread.knowledge_base_id

    if knowledge_base_id is None:
        st.caption(
            "Documents · Select a knowledge base first"
        )
        return

    documents = thread.documents

    document_count = len(documents)

    with st.container(key="document_manager"):
        # -----------------------------------------------------
        # Upload Document
        # -----------------------------------------------------
        uploaded_file = st.file_uploader(
            "Upload document",
            type=["pdf", "docx", "txt"],
            key=f"kb_upload_{knowledge_base_id}",
        )

        if uploaded_file is not None:
            if ui.button(
                "Upload", name="upload", variant="primary",
                key=f"upload_button_{knowledge_base_id}",
                use_container_width=True,
                disabled=any(doc.get("status") == "processing" for doc in documents),
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

                if upload_response.get("status") == "ready":
                    st.success(f"{uploaded_file.name} uploaded successfully.")
                elif upload_response.get("status") == "unknown":
                    st.warning(upload_response["message"])
                else:
                    st.error(upload_response["message"])

                # The backend commits processing before indexing. Refresh on all
                # outcomes, including timeouts and errors carrying document_id.
                update_knowledge_base_document_list(knowledge_base_id)
                documents = thread.documents
                document_count = len(documents)

        if ui.button("Refresh documents", name="refresh-cw", key=f"refresh_documents_{knowledge_base_id}"):
            update_knowledge_base_document_list(knowledge_base_id)
            documents = thread.documents
            document_count = len(documents)

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

                ui.icon_label(doc["file_name"], "file-text")

                st.caption(f"Status: {status}")
                if status == "processing":
                    st.caption("文档仍在处理中，请稍后刷新列表。")
                elif status == "failed":
                    st.caption(api_utils.document_failure_message(doc))

            with col2:
                with ui.popover("Document actions: " + doc["file_name"], key=f"doc_actions_{doc['id']}", icon_only=True):
                    ui.danger_note("Delete this document and its indexed chunks? This cannot be undone.")
                    confirmed = st.checkbox("Confirm document deletion", key=f"confirm_delete_document_{doc['id']}", disabled=status == "processing")
                    if ui.button(
                        "Delete document", name="trash-2", variant="danger",
                        key=f"delete_document_{doc['id']}",
                        help="Processing documents cannot be deleted here." if status == "processing" else "Delete document",
                        disabled=status == "processing" or not confirmed,
                    ):
                        with st.spinner(""):
                            delete_response = (
                                api_utils.delete_document(
                                    doc["id"]
                                )
                            )

                        if delete_response:
                            ui.flash(
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
    if ui.button("Sign out", key="ui_sign_out", name="log-out", variant="ghost", use_container_width=True):
        logout_user()
        ui.set_view("chat")
        st.rerun()
