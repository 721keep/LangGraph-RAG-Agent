FROM python:3.12.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
WORKDIR /app
COPY frontend/requirements.txt /build/original-requirements.txt
COPY deployment/release/frontend.constraints.txt /build/constraints.txt
# The original requirements file is UTF-16. Decode only the build copy.
RUN python -c "from pathlib import Path; p=Path('/build/original-requirements.txt'); b=p.read_bytes(); Path('/build/requirements.txt').write_text(b.decode('utf-16' if b.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8'), encoding='utf-8')" \
    && python -m pip install --no-cache-dir --retries 0 -c /build/constraints.txt -r /build/requirements.txt \
    && python -m pip check \
    && useradd --create-home --uid 10001 workspace \
    && mkdir -p /app/logs /app/tmp /home/workspace/.streamlit \
    && chown -R 10001:10001 /app/logs /app/tmp /home/workspace/.streamlit
COPY frontend/gui/ /app/frontend/gui/
COPY frontend/.streamlit/ /app/.streamlit/
USER 10001:10001
EXPOSE 8501
ENTRYPOINT ["python", "-B", "-m", "streamlit", "run", "frontend/gui/main.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true", "--browser.gatherUsageStats=false"]
