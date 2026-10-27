"""Light workspace toolbar; theme changes rerun only their fragment."""
import streamlit as st
import ui
from sidebar import model_selection_component, knowledge_base_selector_component, retrieval_settings_component


@st.fragment
def theme_control():
    ui.apply_theme()
    dark = st.session_state.get('ui_theme', 'light') == 'dark'
    ui.button('Switch to light theme' if dark else 'Switch to dark theme', key='ui_theme_toggle',
              name='sun' if dark else 'moon', variant='ghost', icon_only=True,
              on_click=ui.toggle_theme, help='Switch to light theme' if dark else 'Switch to dark theme')


def toolbar():
    with st.container(key='workspace_toolbar'):
        st.caption('WORKSPACE / ' + (st.session_state['thread'].title or 'New conversation'))
        model, knowledge, controls, theme = st.columns([2.4, 3, .48, .48], gap='small')
        with model:
            model_selection_component()
        with knowledge:
            if st.session_state['user'].is_authenticated:
                knowledge_base_selector_component()
            else:
                st.selectbox('Select Knowledge Base', ['Sign in to connect knowledge'], disabled=True,
                             label_visibility='collapsed', key='ui_guest_kb')
        with controls:
            retrieval_settings_component()
        with theme:
            theme_control()
