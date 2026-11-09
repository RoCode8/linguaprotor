"""Shared application state and FastAPI dependency providers.

Every router resolves services through these providers so that the scoring
routes, the HITL queue, the audit trail and the admin endpoints all operate
on the *same* instances.  Tests swap implementations with
``app.dependency_overrides``.

Persistence: the audit trail is written to Azure Cosmos DB when
``COSMOS_ENDPOINT`` is set (Microsoft Entra ID auth unless a key is given);
otherwise an in-process append-only store is used.  Results, the review queue
and calibration data are in-process, so the API runs as a single replica.

Azure SDK clients are process-wide singletons: one HTTP connection pool and
one credential (with its token cache) per service, closed on shutdown.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from fastapi import Depends, HTTPException, status

from src.config import get_settings
from src.content_generation.generator import ContentGenerator
from src.content_generation.schemas import GeneratedPassage
from src.content_generation.validator import ContentValidator
from src.governance.audit_logger import AuditLogger
from src.governance.cost_tracker import CostTracker
from src.hitl.calibration import CalibrationService
from src.hitl.override_handler import OverrideHandler
from src.hitl.review_engine import ReviewEngine
from src.infrastructure.azure_openai_client import AzureOpenAIClient
from src.infrastructure.azure_speech_client import AzureSpeechClient
from src.oral.interpretation_pipeline import InterpretationPipeline
from src.oral.schemas import OralAssessmentResult
from src.scoring.consistency_checker import ConsistencyChecker
from src.scoring.schemas import ScoringResult
from src.scoring.written_scorer import WrittenTranslationScorer

logger = logging.getLogger(__name__)


def _build_audit_logger(closers: list[Any]) -> AuditLogger:
    """Cosmos-backed audit logger when configured, in-memory otherwise."""
    cosmos = get_settings().cosmos
    if not cosmos.is_configured:
        return AuditLogger()

    from azure.cosmos.aio import CosmosClient

    credential: Any
    if cosmos.key is not None and cosmos.key.get_secret_value():
        credential = cosmos.key.get_secret_value()
    else:
        from azure.identity.aio import DefaultAzureCredential

        credential = DefaultAzureCredential()
        closers.append(credential)
    client = CosmosClient(cosmos.endpoint, credential=credential)
    closers.append(client)
    container = client.get_database_client(cosmos.database_name).get_container_client(
        cosmos.audit_container
    )
    logger.info(
        "Audit trail persisted to Cosmos DB %s/%s", cosmos.database_name, cosmos.audit_container
    )
    return AuditLogger(container)


@dataclass
class AppState:
    """Process-wide services and stores."""

    cost_tracker: CostTracker = field(default_factory=CostTracker)
    calibration: CalibrationService = field(default_factory=CalibrationService)
    written_results: dict[str, ScoringResult] = field(default_factory=dict)
    oral_results: dict[str, OralAssessmentResult] = field(default_factory=dict)
    passages: dict[str, GeneratedPassage] = field(default_factory=dict)
    audit_logger: AuditLogger = field(init=False)
    review_engine: ReviewEngine = field(init=False)
    override_handler: OverrideHandler = field(init=False)
    _llm: AzureOpenAIClient | None = field(default=None, init=False, repr=False)
    _speech: AzureSpeechClient | None = field(default=None, init=False, repr=False)
    _closers: list[Any] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        self.audit_logger = _build_audit_logger(self._closers)
        self.review_engine = ReviewEngine(
            audit_logger=self.audit_logger, calibration=self.calibration
        )
        self.override_handler = OverrideHandler(self.review_engine)

    @property
    def llm(self) -> AzureOpenAIClient:
        if self._llm is None:
            self._llm = AzureOpenAIClient(cost_tracker=self.cost_tracker)
            self._closers.append(self._llm)
        return self._llm

    @property
    def speech(self) -> AzureSpeechClient:
        if self._speech is None:
            self._speech = AzureSpeechClient()
        return self._speech

    async def aclose(self) -> None:
        """Release network clients and credentials (called on shutdown)."""
        for closer in reversed(self._closers):
            try:
                await closer.close()
            except Exception:  # pragma: no cover - best effort on shutdown
                logger.warning("Failed to close %r", closer, exc_info=True)
        self._closers.clear()


_state: AppState | None = None


def get_state() -> AppState:
    global _state
    if _state is None:
        _state = AppState()
    return _state


def reset_state() -> AppState:
    """Discard all in-process state (used by tests)."""
    global _state
    _state = AppState()
    return _state


async def shutdown_state() -> None:
    """Close the current state's clients (FastAPI lifespan shutdown hook)."""
    if _state is not None:
        await _state.aclose()


def get_llm_client(state: AppState = Depends(get_state)) -> AzureOpenAIClient:
    if not get_settings().azure_openai.is_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Azure OpenAI is not configured; set AZURE_OPENAI_ENDPOINT.",
        )
    return state.llm


def get_speech_client(state: AppState = Depends(get_state)) -> AzureSpeechClient:
    if not get_settings().azure_speech.is_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Azure Speech is not configured; set AZURE_SPEECH_ENDPOINT "
                "(or AZURE_SPEECH_KEY and AZURE_SPEECH_REGION)."
            ),
        )
    return state.speech


def get_scorer(llm: AzureOpenAIClient = Depends(get_llm_client)) -> WrittenTranslationScorer:
    return WrittenTranslationScorer(llm)


def get_pipeline(
    llm: AzureOpenAIClient = Depends(get_llm_client),
    speech: AzureSpeechClient = Depends(get_speech_client),
) -> InterpretationPipeline:
    return InterpretationPipeline(speech_client=speech, openai_client=llm)


def get_generator(llm: AzureOpenAIClient = Depends(get_llm_client)) -> ContentGenerator:
    return ContentGenerator(llm)


def get_validator(llm: AzureOpenAIClient = Depends(get_llm_client)) -> ContentValidator:
    return ContentValidator(llm)


def get_consistency_checker() -> ConsistencyChecker:
    return ConsistencyChecker()
