"""Preview current original frontend against a local-only in-memory Mock API."""
import importlib
from pathlib import Path
import sys
from urllib.parse import urlsplit

sys.path.insert(0, '/workspace/frontend/gui')
assert not Path('/workspace/.env').exists()
import requests
import httpx

if not getattr(requests.Session.send, '_m3_guard', False):
    original = requests.Session.send
    def local_send(self, request, **kwargs):
        assert urlsplit(request.url).hostname == 'm3-api', 'M3 preview forbids all external services'
        return original(self, request, **kwargs)
    local_send._m3_guard = True
    requests.Session.send = local_send
if not getattr(httpx.AsyncHTTPTransport.handle_async_request, '_m3_guard', False):
    original_async = httpx.AsyncHTTPTransport.handle_async_request
    async def local_async(self, request):
        assert request.url.host == 'm3-api', 'M3 preview forbids all external services'
        return await original_async(self, request)
    local_async._m3_guard = True
    httpx.AsyncHTTPTransport.handle_async_request = local_async
if 'main' in sys.modules:
    main = importlib.reload(sys.modules['main'])
else:
    import main
import streamlit as st
with st.container(key='preview_notice'):
    st.caption('M3 conversation preview · Local Mock API · No database or model requests')
main.main()
