FROM python:3.12.11-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/backend:/release PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY backend/requirements.txt /build/requirements.txt
COPY deployment/release/backend.constraints.txt /build/constraints.txt
RUN python -m pip install --no-cache-dir --retries 0 -c /build/constraints.txt -r /build/requirements.txt \
    && python -m pip check \
    && useradd --create-home --uid 10001 workspace \
    && mkdir -p /app/logs /app/tmp \
    && chown -R 10001:10001 /app/logs /app/tmp
COPY backend/app/ /app/backend/app/
COPY deployment/release/r2-requirements.txt /build/r2-requirements.txt
RUN python -m pip install --no-cache-dir --retries 0 -c /build/constraints.txt -r /build/r2-requirements.txt \
    && python -m pip check
COPY deployment/release/runtime/ /release/
USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["python", "-B", "/release/bootstrap.py"]

FROM runtime AS checks
USER root
RUN python -m pip install --no-cache-dir --retries 0 -c /build/constraints.txt pytest==8.3.4
USER 10001:10001
ENTRYPOINT ["python", "-B", "-m", "pytest"]
