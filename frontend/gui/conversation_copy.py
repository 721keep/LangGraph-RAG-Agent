"""Local clipboard control. Text is encoded as data, never interpolated as HTML/JS."""
import base64
import json

from design_tokens import tokens
import ui


def copy_answer(text, theme):
    import streamlit.components.v1 as components
    encoded = base64.b64encode(text.encode()).decode()
    colors = tokens(theme)
    initial = {k: colors[k] for k in ('text', 'surface', 'border', 'accent', 'accent-soft')}
    document = '''<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline';">
<style>body{margin:0;background:transparent;font:12px system-ui,sans-serif;color:var(--text)}
button{display:flex;align-items:center;gap:7px;padding:6px 10px;background:var(--surface);color:var(--text);border:1px solid var(--border);border-radius:8px;cursor:pointer;font:inherit}
button:hover{color:var(--accent);background:var(--accent-soft)}button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
svg{width:14px;height:14px;stroke:currentColor}textarea{box-sizing:border-box;width:100%;height:48px;color:var(--text);background:var(--surface);border:1px solid var(--border)}</style></head>
<body><button id="copy" title="Copy answer / 复制回答" aria-label="Copy answer / 复制回答">__ICON__<span id="label">Copy answer / 复制</span></button>
<script>
const text=new TextDecoder().decode(Uint8Array.from(atob(__TEXT__),c=>c.charCodeAt(0)));
const initial=__COLORS__;for(const [k,v] of Object.entries(initial))document.documentElement.style.setProperty('--'+k,v);
let scheduled=false;function theme(){scheduled=false;try{const s=parent.getComputedStyle(parent.document.documentElement);for(const k of Object.keys(initial)){const v=s.getPropertyValue('--color-'+k).trim();if(v)document.documentElement.style.setProperty('--'+k,v);}}catch{}}
theme();try{new MutationObserver(()=>{if(!scheduled){scheduled=true;requestAnimationFrame(theme)}}).observe(parent.document.body,{childList:true,subtree:true});}catch{}
document.getElementById('copy').onclick=async()=>{let ok=false;try{await navigator.clipboard.writeText(text);ok=true;}catch{const t=document.createElement('textarea');t.value=text;document.body.append(t);t.select();try{ok=document.execCommand('copy')}catch{}t.remove();}
document.getElementById('label').textContent=ok?'Copied / 已复制':'复制未授权，请使用下方文本';if(!ok){let t=document.getElementById('fallback');if(!t){t=document.createElement('textarea');t.id='fallback';t.readOnly=true;t.setAttribute('aria-label','Answer text for manual copy');document.body.append(t)}t.value=text;t.focus();t.select();}};
</script></body></html>'''
    document = document.replace('__ICON__', ui.icon('file-text', size=14)).replace('__TEXT__', json.dumps(encoded)).replace('__COLORS__', json.dumps(initial))
    components.html(document, height=40, scrolling=True)
