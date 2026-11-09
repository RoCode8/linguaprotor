# syntax=docker/dockerfile:1
# Multi-stage image for LinguaProctor. Two runnable targets: `api` and `dashboard`
# (selected by azure.yaml / docker-compose.yml). Built remotely in Azure Container
# Registry by `azd deploy` (remoteBuild), so no local Docker is required.
# NOTE: azd's remote build ignores `target`, so both Azure images end up as the
# last stage; infra/app/*.bicep therefore set each container's command explicitly.
# Keep those commands in sync with the CMDs below.

# Pin the Debian release: the Speech SDK's native runtime libraries are
# release-specific (e.g. libasound2 is renamed in Debian 13).
ARG PYTHON_IMAGE=python:3.11-slim-bookworm

# ---- build: compile/install dependencies into an isolated virtualenv --------
FROM ${PYTHON_IMAGE} AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m venv /opt/venv && /opt/venv/bin/pip install ".[dashboard]"

# ---- runtime: minimal image, non-root user ----------------------------------
FROM ${PYTHON_IMAGE} AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app
# Runtime libraries required by the Azure Speech SDK (TLS + ALSA).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libssl3 libasound2 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
# Application files stay root-owned (read-only for the runtime user).
COPY src ./src
COPY dashboard ./dashboard
USER 10001

# ---- API --------------------------------------------------------------------
FROM runtime AS api
EXPOSE 8000
# --proxy-headers: honour X-Forwarded-* from the Container Apps ingress (Envoy).
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips=*"]

# ---- Dashboard --------------------------------------------------------------
FROM runtime AS dashboard
EXPOSE 8501
CMD ["streamlit", "run", "dashboard/app.py", "--server.port=8501", \
     "--server.address=0.0.0.0", "--server.headless=true"]
