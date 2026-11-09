"""Pytest configuration, scripted AI fakes and API fixtures.

No Azure credentials are required: the API is exercised end-to-end with the
LLM and speech backends replaced by deterministic fakes through FastAPI
dependency overrides.
"""

from __future__ import annotations

import copy
import io
import math
import random
import struct
import wave
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.config import get_settings
from src.infrastructure.azure_speech_client import TranscriptionResult as STTResult
from src.infrastructure.azure_speech_client import WordInfo

# ---------------------------------------------------------------------------
# Canonical exam fixture data (UAE legal register, EN → AR)
# ---------------------------------------------------------------------------

SOURCE_TEXT = (
    "The licensee shall notify the competent licensing authority in writing "
    "within thirty days."
)
# Uses the accepted regional variant "هيئة الترخيص" instead of "سلطة الترخيص".
CANDIDATE_TEXT = "يجب على المرخص له إخطار هيئة الترخيص المختصة كتابةً خلال ثلاثين يوماً."
GLOSSARY = [
    {
        "source_term": "licensing authority",
        "target_term": "سلطة الترخيص",
        "alternatives": ["هيئة الترخيص"],
    }
]

DECOMPOSITION = {
    "meaning_units": [
        {
            "id": "mu_1",
            "unit_type": "main_clause",
            "source_text": "The licensee shall notify the competent licensing authority",
            "importance_weight": 1.0,
        },
        {"id": "mu_2", "unit_type": "restriction", "source_text": "in writing", "importance_weight": 0.6},
        {"id": "mu_3", "unit_type": "temporal", "source_text": "within thirty days", "importance_weight": 1.0},
    ]
}

ALIGNMENT = {
    "alignments": [
        {
            "meaning_unit_id": "mu_1",
            "candidate_text": "يجب على المرخص له إخطار هيئة الترخيص المختصة",
            "alignment_confidence": 0.96,
            "is_omitted": False,
        },
        {"meaning_unit_id": "mu_2", "candidate_text": "كتابةً", "alignment_confidence": 0.97, "is_omitted": False},
        {"meaning_unit_id": "mu_3", "candidate_text": "خلال ثلاثين يوماً", "alignment_confidence": 0.98, "is_omitted": False},
    ]
}


def rubric(scores: dict[str, float] | None = None, confidence: float = 0.95) -> dict[str, Any]:
    scores = scores or {"accuracy": 5.0, "completeness": 5.0, "terminology": 5.0, "fluency": 4.5}
    return {
        "criteria": [
            {
                "criterion": name,
                "score": value,
                "summary": f"{name} assessed.",
                "evidence": [
                    {
                        "meaning_unit_id": "mu_1",
                        "source_span": "licensing authority",
                        "candidate_span": "هيئة الترخيص",
                        "issue": None,
                        "severity": None,
                        "is_positive": True,
                        "explanation": "Accepted UAE variant.",
                    }
                ],
            }
            for name, value in scores.items()
        ],
        "terminology_verdicts": [],
        "confidence": confidence,
        "flags": [],
    }


ORAL_RUBRIC = {
    "criteria": [
        {"criterion": c, "score": s, "max_score": 5.0, "summary": "ok", "evidence": ["…"]}
        for c, s in [("accuracy", 4.5), ("fluency", 4.0), ("comprehension", 4.5), ("delivery", 4.0)]
    ]
}


class FakeLLM:
    """Scripted stand-in for AzureOpenAIClient, dispatching on ``operation``."""

    chat_deployment = "fake-gpt"
    embedding_deployment = "fake-embedding"

    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self.responses: dict[str, Any] = {
            "written_scoring.decompose": DECOMPOSITION,
            "written_scoring.align": ALIGNMENT,
            "written_scoring.rubric": rubric(),
            "written_scoring.terminology": {
                "candidate_rendering": "?",
                "is_valid_alternative": False,
                "explanation": "not an accepted rendering",
            },
            "oral_scoring": ORAL_RUBRIC,
        }
        self.responses.update(responses or {})
        self.calls: list[str] = []

    async def chat_completion_json(self, messages: list[dict[str, str]], **kwargs: Any) -> Any:
        op = kwargs.get("operation", "chat")
        self.calls.append(op)
        resp = self.responses[op]
        if isinstance(resp, Exception):
            raise resp
        return copy.deepcopy(resp(messages) if callable(resp) else resp)

    async def embeddings(self, texts: list[str], deployment: str | None = None) -> list[list[float]]:
        self.calls.append("embedding")
        # Deterministic, non-degenerate vectors.
        return [[1.0, float(len(t) % 7) / 7.0, 0.5] for t in texts]


class FakeSpeech:
    def __init__(self, result: STTResult | Exception | None = None) -> None:
        self.result = result or STTResult(
            text="يجب على المرخص له إخطار الهيئة المختصة كتابة خلال ثلاثين يوما.",
            words=[WordInfo("يجب", 100, 300, 0.93), WordInfo("على", 420, 200, 0.95)],
            language="ar-AE",
            duration_ms=3000,
            confidence=0.92,
        )
        self.calls: list[tuple[int, str]] = []

    async def speech_to_text(self, audio: bytes, language: str) -> STTResult:
        self.calls.append((len(audio), language))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def make_wav(seconds: float = 3.0, rate: int = 16_000, freq: float = 220.0) -> bytes:
    """Speech-like test tone: 220 Hz with syllable-rate amplitude modulation."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        frames = bytearray()
        for i in range(int(seconds * rate)):
            t = i / rate
            envelope = 0.5 + 0.5 * math.sin(2 * math.pi * 3 * t)
            frames += struct.pack("<h", int(12_000 * envelope * math.sin(2 * math.pi * freq * t)))
        wf.writeframes(bytes(frames))
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _NeverSample(random.Random):
    def random(self) -> float:  # noqa: D401 - deterministic: never hit the audit sample
        return 0.999


_AZURE_ENV = (
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_API_KEY",
    "AZURE_SPEECH_KEY",
    "AZURE_SPEECH_ENDPOINT",
    "COSMOS_ENDPOINT",
    "COSMOS_KEY",
    "APPLICATIONINSIGHTS_CONNECTION_STRING",
)


@pytest.fixture(autouse=True)
def _isolate_from_azure(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Tests never reach real Azure, even if the developer's .env points at it.

    Environment variables take precedence over .env, so blank values win.
    """
    for var in _AZURE_ENV:
        monkeypatch.setenv(var, "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def mock_openai_client() -> MagicMock:
    client = MagicMock()
    client.chat_completion_json = AsyncMock()
    client.embeddings = AsyncMock()
    return client


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def fake_speech() -> FakeSpeech:
    return FakeSpeech()


@pytest.fixture
def api(fake_llm: FakeLLM, fake_speech: FakeSpeech) -> Iterator[TestClient]:
    """TestClient with fresh state and AI backends replaced by fakes."""
    from src.api import dependencies as deps
    from src.main import app

    state = deps.reset_state()
    state.review_engine._rng = _NeverSample()
    app.dependency_overrides[deps.get_llm_client] = lambda: fake_llm
    app.dependency_overrides[deps.get_speech_client] = lambda: fake_speech
    with TestClient(app) as client:
        client.state = state  # type: ignore[attr-defined]
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
def unconfigured() -> Iterator[None]:
    """Explicit marker: no Azure backends (already guaranteed by the autouse fixture)."""
    yield

