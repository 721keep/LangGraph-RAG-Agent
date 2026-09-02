import api_utils
import streamlit as st
from chat_components import authenticated_user_chat_interface_component, unauthenticated_user_chat_interface_component
from state_management import Page, authenticate_user

def agent_status_component():
    """Display the current Agent configuration."""

    model_name = st.session_state.get(
        "model_name",
        "Unavailable",
    )

    chat_config = st.session_state.get(
        "chat_config",
        {},
    )

    model_provider = chat_config.get(
        "model_provider",
        "Unknown",
    )

    thread = st.session_state["thread"]
    knowledge_bases = (
        st.session_state["user"].knowledge_bases
    )

    knowledge_base_name = "No Knowledge Base"

    if thread.knowledge_base_id is not None:
        selected_kb = next(
            (
                kb
                for kb in knowledge_bases
                if str(kb["id"])
                == str(thread.knowledge_base_id)
            ),
            None,
        )

        if selected_kb:
            knowledge_base_name = selected_kb["name"]

    top_k = st.session_state.get("top_k", 3)
    similarity_threshold = st.session_state.get(
        "similarity_threshold",
        0.50,
    )
    rerank_top_n = st.session_state.get(
        "rerank_top_n",
        3,
    )

    with st.container(border=True):
        col1, col2 = st.columns(2)

        with col1:
            st.markdown("**🤖 Model**")
            st.write(model_name)
            st.caption(f"Provider: {model_provider}")

        with col2:
            st.markdown("**📚 Knowledge Base**")
            st.write(knowledge_base_name)

        st.markdown("**🔎 Retrieval**")
        st.caption(
            f"Top-K {top_k} · "
            f"Threshold {similarity_threshold:.2f} · "
            f"Rerank {rerank_top_n}"
        )

        st.markdown("**🧰 Agent Tools**")
        st.caption("RAG · Web Search · MCP")

def home_page():
    if not st.session_state["user"].is_authenticated:
        st.title("🤖 LangGraph Agent")
        st.markdown(
            """
            Welcome to LangGraph Agent, an Agentic RAG assistant with private knowledge, web search, tool use, and conversational memory.

            ### ✨ Key Features for Logged-in Users:
            - Ask questions and receive smart, context-aware answers.
            - Upload your own documents for tailored assistance.
            - Use tavily search for general knowledge questions.
            - Enjoy **chat memory** to remember and recall previous conversations.
            """
        )
        st.subheader(
            "💬 Try the assistant",
            divider="gray",
        )
        unauthenticated_user_chat_interface_component()
    else:
        st.title("🤖 LangGraph Agent")

        st.caption(
            "Agentic RAG assistant with private knowledge, "
            "web search, and MCP tools."
        )

        agent_status_component()

        st.subheader(
            "💬 Ask your agent",
            divider="gray",
        )

        authenticated_user_chat_interface_component()


def login_page():
    st.subheader("🔐 Login", divider="gray")
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
    st.subheader("✍ Register", divider="gray")
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
    if st.button("⬅️ Back to Home", type="tertiary"):
        st.session_state["page"] = Page.HOME
        st.rerun()
