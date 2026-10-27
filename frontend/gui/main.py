import config  # noqa: F401
import streamlit as st
import ui
from pages import home_page, login_page, register_page, knowledge_page, documents_page
from sidebar import display_sidebar
from layout import toolbar
from state_management import Page, initialize_state

st.set_page_config(page_title="Agent Workspace", layout="wide", page_icon=ui.data_uri("brand"), initial_sidebar_state="expanded")


def main():
    initialize_state()
    ui.initialize_ui_state()
    ui.apply_theme()
    display_sidebar()
    toolbar()
    ui.show_flash()
    if st.session_state["page"] == Page.LOGIN:
        with st.container(key="auth_surface"):
            login_page()
    elif st.session_state["page"] == Page.REGISTER:
        with st.container(key="auth_surface"):
            register_page()
    elif st.session_state["user"].is_authenticated and st.session_state.get("ui_view") == "knowledge":
        knowledge_page()
    elif st.session_state["user"].is_authenticated and st.session_state.get("ui_view") == "documents":
        documents_page()
    else:
        home_page()


if __name__ == "__main__":
    main()
