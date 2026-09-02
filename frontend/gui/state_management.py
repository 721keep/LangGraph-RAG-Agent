from enum import StrEnum
from uuid import UUID

import api_utils
import streamlit as st



class Page(StrEnum):
    HOME = "home"
    LOGIN = "login"
    REGISTER = "register"


class User:
    def __init__(
        self,
        is_authenticated: bool = False,
        username: str | None = None,
        access_token: str | None = None,
        refresh_token: str | None = None,
        threads: list[dict] | None = None,
        knowledge_bases: list[dict] | None = None,
    ):
        self.is_authenticated = is_authenticated
        self.username = username
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.threads = threads or []
        self.knowledge_bases = knowledge_bases or []


class Thread:
    def __init__(
        self,
        id: UUID | None = None,
        title: str = "",
        user_id: UUID | None = None,
        knowledge_base_id: UUID | None = None,
        messages: list | None = None,
        documents: list | None = None,
    ):
        self.id = id
        self.user_id = user_id
        self.title = title
        self.knowledge_base_id = knowledge_base_id
        self.messages = messages or []
        self.documents = documents or []

def load_chat_config() -> dict:
    """Load chat model configuration from the backend."""

    chat_config = api_utils.get_chat_config()

    if not chat_config:
        return {
            "model_provider": None,
            "model_names": [],
            "default_model": None,
        }

    return chat_config

def initialize_state() -> None:
    if "page" not in st.session_state:
        st.session_state["page"] = Page.HOME

    if "user" not in st.session_state:
        st.session_state["user"] = User()

    if "thread" not in st.session_state:
        st.session_state["thread"] = Thread()

    if "chat_config" not in st.session_state:
        st.session_state["chat_config"] = load_chat_config()

    if "model_name" not in st.session_state:
        chat_config = st.session_state["chat_config"]

        model_names = chat_config.get(
            "model_names",
            [],
        )

        default_model = chat_config.get(
            "default_model"
        )

        if default_model in model_names:
            st.session_state["model_name"] = default_model

        elif model_names:
            st.session_state["model_name"] = model_names[0]

        else:
            st.session_state["model_name"] = None


def new_chat():
    st.session_state["thread"] = Thread()


def update_thread(thread_id: UUID, title: str):
    updated_thread_response = api_utils.update_thread(thread_id, title)
    user_id = updated_thread_response.get("user_id")
    st.session_state["thread"].id = thread_id
    st.session_state["thread"].title = title
    st.session_state["thread"].user_id = user_id


def change_thread(thread_id: UUID) -> None:
    get_thread_response = api_utils.get_thread(thread_id)

    title = get_thread_response.get("title")
    user_id = get_thread_response.get("user_id")
    knowledge_base_id = get_thread_response.get(
        "knowledge_base_id"
    )

    st.session_state["thread"] = Thread(
        id=thread_id,
        title=title,
        user_id=user_id,
        knowledge_base_id=(
            UUID(knowledge_base_id)
            if knowledge_base_id
            else None
        ),
    )

    if knowledge_base_id:
        update_knowledge_base_document_list(
            UUID(knowledge_base_id)
        )
    else:
        st.session_state["thread"].documents = []

    update_chat_history(thread_id)


def update_document_list(thread_id: UUID) -> None:
    documents = []
    documents_response = api_utils.list_document(thread_id)
    if documents_response is None:
        st.sidebar.error("Failed to retrieve document list. Please try again.")
    else:
        documents = documents_response

    st.session_state["thread"].documents = documents

def update_knowledge_base_document_list(
    knowledge_base_id: UUID,
) -> None:
    documents = []

    documents_response = (
        api_utils.list_knowledge_base_documents(
            knowledge_base_id
        )
    )

    if documents_response is None:
        st.sidebar.error(
            "Failed to retrieve knowledge base documents."
        )
    else:
        documents = documents_response

    st.session_state["thread"].documents = documents



def update_user_knowledge_bases() -> list[dict]:
    knowledge_bases = api_utils.get_knowledge_bases()
    st.session_state["user"].knowledge_bases = knowledge_bases
    return knowledge_bases


def update_user_threads() -> list[dict]:
    threads = api_utils.get_user_threads()
    st.session_state["user"].threads = threads
    return threads


def update_chat_history(thread_id: UUID) -> None:
    chat_messages = []
    chat_history_response = api_utils.get_chat_history(thread_id)
    if isinstance(chat_history_response, list):
        chat_messages = chat_history_response
    else:
        st.sidebar.error(chat_history_response.get("details", "Failed to retrieve chat history. Please try again."))

    st.session_state["thread"].messages = chat_messages


def authenticate_user(login_response: dict) -> None:
    st.session_state["user"] = User(
        is_authenticated=True,
        username=login_response.get("user", {}).get("username"),
        access_token=login_response.get("access_token"),
        refresh_token=login_response.get("refresh_token"),
    )
    update_user_threads()
    update_user_knowledge_bases()
    st.session_state["thread"] = Thread()


def logout_user() -> None:
    st.session_state["page"] = Page.HOME
    st.session_state["user"] = User()
    st.session_state["thread"] = Thread()
    chat_config = st.session_state.get(
        "chat_config",
        {},
    )

    model_names = chat_config.get(
        "model_names",
        [],
    )

    default_model = chat_config.get(
        "default_model"
    )

    if default_model in model_names:
        st.session_state["model_name"] = default_model

    elif model_names:
        st.session_state["model_name"] = model_names[0]

    else:
        st.session_state["model_name"] = None
