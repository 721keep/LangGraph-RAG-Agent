import api_utils
import streamlit as st
import ui
from chat_components import authenticated_user_chat_interface_component, unauthenticated_user_chat_interface_component
from state_management import Page, authenticate_user


def agent_status_component():
    """Compatibility entry point; configuration now lives in the compact toolbar."""
    st.caption("RAG · Web Search · MCP")


def home_page():
    thread = st.session_state["thread"]
    from conversation_session import pending
    if not thread.messages and not st.session_state.get("prompt") and not pending(thread):
        ui.empty_chat()
    else:
        ui.section_header(thread.title or "Conversation", "A focused place to think with your knowledge.", "CONVERSATION")
    if st.session_state["user"].is_authenticated:
        authenticated_user_chat_interface_component()
    else:
        unauthenticated_user_chat_interface_component()


def knowledge_page():
    from resource_components import knowledge_base_component
    ui.section_header("Knowledge bases", "Give your conversations a trusted source of context.")
    knowledge_base_component()


def documents_page():
    from resource_components import document_list_component
    ui.section_header("Documents", "Add sources, check indexing progress, and keep your knowledge up to date.")
    document_list_component()


def login_page():
    st.subheader("Login", divider="gray")
    with st.form("login_form"):
        email = st.text_input("Email *")
        password = st.text_input("Password *", type="password")
        submitted = st.form_submit_button("Login")

    back_to_home_component()

    if submitted:
        with st.spinner("Logging in..."):
            login_response = api_utils.login_user(email, password)
            if message := login_response.get("message"):
                st.success(message)
                st.session_state["page"] = Page.HOME
                authenticate_user(login_response)
                st.rerun()
            else:
                st.error(login_response.get("detail", "Registration failed. Please try again."))


def register_page():
    st.subheader("Create your account", divider="gray")
    with st.form("register_form"):
        col1, col2 = st.columns(2)
        email = col1.text_input("Email *")
        username = col1.text_input("Username *", max_chars=16)
        password = col1.text_input("Password *", type="password", max_chars=32)
        first_name = col2.text_input("First Name", max_chars=50)
        last_name = col2.text_input("Last Name", max_chars=50)

        submitted = st.form_submit_button("Register")

    back_to_home_component()

    if submitted:
        register_data = {
            "email": email,
            "username": username,
            "password": password,
            "first_name": first_name,
            "last_name": last_name,
        }
        with st.spinner("Registering..."):
            register_response = api_utils.register_user(register_data)
            if message := register_response.get("message"):
                st.success(message)
                st.session_state["page"] = Page.LOGIN
                st.rerun()
            else:
                st.error(register_response.get("detail", "Registration failed. Please try again."))


def back_to_home_component():
    if ui.button("Back to workspace", key="ui_back_home", name="arrow-left", variant="ghost"):
        st.session_state["page"] = Page.HOME
        st.rerun()
