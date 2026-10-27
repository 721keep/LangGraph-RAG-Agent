"""Workspace presentation helpers. No API calls or business state replacement."""
import base64
from contextlib import contextmanager
from functools import lru_cache
import html
from pathlib import Path
import re

from design_tokens import ICON_SIZE, ICON_STROKE, tokens, variables

ASSETS = Path(__file__).resolve().parent / 'assets'


@lru_cache(maxsize=64)
def svg(name):
    if not re.fullmatch(r'[a-z0-9-]+', name):
        raise ValueError('Invalid local icon name')
    path = ASSETS / ('brand/mark.svg' if name == 'brand' else f'lucide/{name}.svg')
    value = path.read_text(encoding='utf-8')
    return value.replace('stroke-width="2"', f'stroke-width="{ICON_STROKE}"')


def data_uri(name):
    return 'data:image/svg+xml;base64,' + base64.b64encode(svg(name).encode()).decode()


def icon(name, label=None, size=ICON_SIZE):
    value = svg(name)
    attrs = f'width="{size}" height="{size}" focusable="false" '
    attrs += f'role="img" aria-label="{html.escape(label, quote=True)}"' if label else 'aria-hidden="true"'
    # Resize only the SVG viewport; child rectangles need their own geometry.
    value = re.sub(r'<svg\b[^>]*>', lambda m: re.sub(r'\s(?:width|height)="[^"]*"', '', m.group()), value, count=1)
    value = re.sub(r'\s*\n\s*', ' ', value.strip())
    return re.sub(r'<svg\b', '<svg class="ws-icon" ' + attrs, value, count=1)


def initialize_ui_state():
    import streamlit as st
    for key, value in [('ui_theme', 'light'), ('ui_view', 'chat'), ('ui_drafts', {})]:
        if key not in st.session_state:
            st.session_state[key] = value


def toggle_theme():
    import streamlit as st
    st.session_state['ui_theme'] = 'dark' if st.session_state.get('ui_theme', 'light') == 'light' else 'light'


def set_view(view):
    import streamlit as st
    from state_management import Page
    st.session_state['ui_view'] = view
    st.session_state['page'] = Page.HOME


def restore_draft(key):
    import streamlit as st
    draft = st.session_state.setdefault('ui_drafts', {})
    if key not in st.session_state and key in draft:
        st.session_state[key] = draft[key]


def remember_draft(key):
    import streamlit as st
    st.session_state.setdefault('ui_drafts', {})[key] = st.session_state.get(key, '')


def flash(message):
    import streamlit as st
    st.session_state['ui_flash'] = message


def show_flash():
    import streamlit as st
    if message := st.session_state.pop('ui_flash', None):
        st.success(message)


def icon_label(label, name='workflow', *, muted=False):
    import streamlit as st
    cls = 'ws-icon-label ws-muted' if muted else 'ws-icon-label'
    st.markdown(f'<div class="{cls}">{icon(name)}<span>{html.escape(str(label))}</span></div>', unsafe_allow_html=True)


def danger_note(label):
    import streamlit as st
    st.markdown(f'<div class="ws-danger-note" role="note">{icon("circle-alert")}<span>{html.escape(label)}</span></div>', unsafe_allow_html=True)


def brand():
    import streamlit as st
    st.markdown(f'<div class="ws-brand"><span class="ws-brand-mark">{icon("brand", size=32)}</span>'
                '<span><strong>Agent Workspace</strong><small>KNOWLEDGE IN FLOW</small></span></div>', unsafe_allow_html=True)


def section_header(title, description='', eyebrow='WORKSPACE'):
    import streamlit as st
    st.markdown(f'<div class="ws-section-heading"><span class="ws-eyebrow">{html.escape(eyebrow)}</span>'
                f'<h1>{html.escape(title)}</h1><p>{html.escape(description)}</p></div>', unsafe_allow_html=True)


def empty_chat():
    import streamlit as st
    st.markdown(f'<section class="ws-welcome"><div class="ws-welcome-mark">{icon("brand", size=42)}</div>'
                '<span class="ws-eyebrow">A LITTLE CONTEXT. A CLEARER ANSWER.</span>'
                '<h1>Good questions.<br/>Connected answers.</h1>'
                '<p>Bring your knowledge, explore an idea, or find a new perspective.<br/>'
                '<span lang="zh">连接知识，理清思路，让每个答案都有据可循。</span></p>'
                '<div class="ws-capabilities">'
                f'<span>{icon("book-open")}Grounded in your knowledge</span>'
                f'<span>{icon("workflow")}Tools, when you need them</span>'
                f'<span>{icon("shield-check")}Sources you can trace</span>'
                '</div></section>', unsafe_allow_html=True)


def _action_style(key, name, variant, icon_only):
    import streamlit as st
    safe = re.sub(r'[^a-zA-Z0-9_-]', '_', key)
    # Keyed component rules outrank shared button defaults after a theme fragment.
    scope = f'div.st-key-{safe}'
    rules = f'''{scope} button::before {{content:"";flex:none;width:var(--icon-size);height:var(--icon-size);
      background:currentColor;mask:url("{data_uri(name)}") center/contain no-repeat;
      -webkit-mask:url("{data_uri(name)}") center/contain no-repeat;}}
      {scope} button {{display:flex;gap:9px;align-items:center;justify-content:{'center' if icon_only else 'flex-start'};}}
    '''
    if icon_only:
        rules += f'{scope} button {{width:36px;min-width:36px;padding:8px;}}{scope} button p {{font-size:0;line-height:0;}}'
        rules += f'{scope} button svg,{scope} button [data-testid="stIconMaterial"]{{display:none;}}'
    if variant == 'danger':
        rules += f'{scope} button {{color:var(--color-danger);border-color:var(--color-danger);background:var(--color-danger-soft);}}'
    elif variant == 'active':
        rules += f'{scope} button {{color:var(--color-accent);background:var(--color-accent-soft);border-color:transparent;}}'
    elif variant == 'ghost':
        rules += f'{scope} button {{background:transparent;border-color:transparent;}}'
    st.markdown('<style>' + rules + '</style>', unsafe_allow_html=True)


def button(label, *, key, name='plus', variant='secondary', icon_only=False, help=None, **kwargs):
    import streamlit as st
    # The actual text label remains in the accessibility tree even for icon-only buttons.
    wrapper = 'action_' + re.sub(r'[^a-zA-Z0-9_-]', '_', key)
    _action_style(wrapper, name, variant, icon_only)
    with st.container(key=wrapper):
        return st.button(label, key=key, help=help or label,
                         type='primary' if variant == 'primary' else 'secondary', **kwargs)


@contextmanager
def popover(label, *, key, name='ellipsis', icon_only=False, disabled=False, help=None):
    import streamlit as st
    wrapper = 'action_' + re.sub(r'[^a-zA-Z0-9_-]', '_', key)
    _action_style(wrapper, name, 'ghost', icon_only)
    with st.container(key=wrapper):
        with st.popover(label, help=help or label, disabled=disabled, use_container_width=not icon_only):
            yield


@contextmanager
def chat_message(role, key):
    import streamlit as st
    normalized = 'ai' if role in ['ai', 'assistant'] else 'human'
    with st.container(key=f'msg_{normalized}_{key}'):
        with st.chat_message(role, avatar=data_uri('brand' if normalized == 'ai' else 'user-round')):
            yield


def apply_theme():
    import streamlit as st
    theme = st.session_state.get('ui_theme', 'light')
    styles = (ASSETS / 'workspace.css').read_text(encoding='utf-8')
    native_icons = {
        '[data-testid="stChatInputSubmitButton"]': 'arrow-up',
        '[data-testid="stFileUploaderDropzone"] [data-testid="stIconMaterial"]': 'upload',
        '[data-testid="stExpanderToggleIcon"]': 'chevron-down',
    }
    extra = ''
    for selector, name in native_icons.items():
        if name == 'arrow-up':
            extra += f'{selector} svg,{selector} span{{visibility:hidden}}{selector}::before{{content:"";position:absolute;width:18px;height:18px;background:currentColor;mask:url("{data_uri(name)}") center/contain no-repeat;}}'
        else:
            extra += f'{selector}{{font-size:0;background:currentColor;mask:url("{data_uri(name)}") center/contain no-repeat;width:18px;height:18px;}}'
    for role, name in [('ai', 'brand'), ('human', 'user-round')]:
        prefix = f'[class*="st-key-msg_{role}_"] [data-testid="stChatMessage"]'
        extra += f'{prefix}>img{{display:none;}}{prefix}::before{{content:"";flex:none;width:30px;height:30px;background:var(--color-accent);mask:url("{data_uri(name)}") center/24px no-repeat;}}'
    st.markdown(f'<style>:root,[data-testid="stApp"]{{{variables(theme)}}}{styles}{extra}</style>', unsafe_allow_html=True)
