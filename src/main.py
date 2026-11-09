"""LinguaProctor API application entrypoint.

AI-Powered Multilingual Examination & Assessment Platform for
Written Translation and Oral Interpretation.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.dependencies import shutdown_state
from src.api.routes_admin import router as admin_router
from src.api.routes_content import router as content_router
from src.api.routes_oral import router as oral_router
from src.api.routes_review import router as review_router
from src.api.routes_scoring import router as scoring_router
from src.config import get_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("linguaproctor")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan manager for resource warmup and graceful shutdown."""
    logger.info("Initializing LinguaProctor API services...")
    settings = get_settings()
    logger.info(
        "LinguaProctor initialized in %s mode (Debug: %s)",
        settings.app.environment,
        settings.app.debug,
    )
    yield
    logger.info("Shutting down LinguaProctor API services...")
    await shutdown_state()


app = FastAPI(
    title="LinguaProctor API",
    description=(
        "AI-Powered Bilingual Examination & Assessment Platform for "
        "Written Translation (English ↔ Arabic) and Oral Interpretation. "
        "Provides explainable scoring anchored in atomic Meaning Units, "
        "valid-alternative terminology recognition, speech assessment, and "
        "confidence-gated Human-in-the-Loop review."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# Azure Monitor / Application Insights: traces and metrics for every request
# (and outbound Azure SDK calls) when a connection string is provided.
if os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING"):
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    from src.infrastructure.telemetry import setup_telemetry

    setup_telemetry(service_name="linguaproctor-api")
    FastAPIInstrumentor.instrument_app(app, excluded_urls="health")

# Enable CORS for Streamlit dashboard and web clients
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API Routers
app.include_router(content_router, prefix="/api/v1")
app.include_router(scoring_router, prefix="/api/v1")
app.include_router(oral_router, prefix="/api/v1")
app.include_router(review_router, prefix="/api/v1")
app.include_router(admin_router, prefix="/api/v1")


@app.get("/health", tags=["System"])
async def health_check() -> dict[str, Any]:
    """Health check endpoint for Docker and Azure container probes.

    ``backends`` reports which AI services are configured; LLM/STT endpoints
    return 503 when their backend is missing.
    """
    settings = get_settings()
    return {
        "status": "healthy",
        "service": "LinguaProctor API",
        "version": "1.0.0",
        "supported_pairs": ["en-US <-> ar-AE", "en-US <-> ar-SA"],
        "backends": {
            "azure_openai": settings.azure_openai.is_configured,
            "azure_speech": settings.azure_speech.is_configured,
        },
        "audit_store": "cosmos" if settings.cosmos.is_configured else "in-memory",
    }


@app.get("/", tags=["System"])
async def root() -> dict[str, str]:
    """Root endpoint redirecting users to interactive OpenAPI documentation."""
    return {
        "message": "Welcome to LinguaProctor API. Visit /docs for the API specification.",
    }
