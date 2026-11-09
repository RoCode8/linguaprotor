"""Psychometric statistics validated against published / hand-computed values."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np
import pytest

from src.config import AzureOpenAISettings
from src.governance.cost_tracker import CostTracker
from src.hitl.calibration import cohens_kappa, icc_2_1
from src.infrastructure.azure_openai_client import (
    AzureOpenAIClient,
    LLMNotConfiguredError,
    LLMResponseError,
)

# Shrout & Fleiss (1979), Table 2: 6 targets × 4 judges.
SHROUT_FLEISS = [[9, 2, 5, 8], [6, 1, 3, 2], [8, 4, 6, 8], [7, 1, 2, 6], [10, 5, 6, 9], [6, 2, 4, 7]]


def test_icc_2_1_matches_shrout_fleiss_published_value() -> None:
    assert icc_2_1(SHROUT_FLEISS) == pytest.approx(0.29, abs=0.005)


def test_icc_2_1_penalises_systematic_bias() -> None:
    human = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 2.5, 3.5])
    assert icc_2_1(np.c_[human, human]) == pytest.approx(1.0)
    # +1 offset: MSR = 3.5, MSC = 3.5, MSE = 0 → 3.5 / (3.5 + 2·3.5/7) = 7/9.
    # A consistency ICC(3,1) would still be 1.0; absolute agreement is penalised.
    assert icc_2_1(np.c_[human, human + 1.0]) == pytest.approx(7 / 9)


def test_cohens_kappa_hand_computed() -> None:
    # 2x2 table [[20, 5], [10, 15]] → po = 0.70, pe = 0.50, κ = 0.40
    a = [0] * 25 + [1] * 25
    b = [0] * 20 + [1] * 5 + [0] * 10 + [1] * 15
    assert cohens_kappa(a, b, 2) == pytest.approx(0.40)
    assert cohens_kappa([1, 2, 3], [1, 2, 3], 5) == pytest.approx(1.0)


def _client(tracker: CostTracker | None = None) -> AzureOpenAIClient:
    return AzureOpenAIClient(
        settings=AzureOpenAISettings(endpoint="https://x.openai.azure.com/", api_key="k",
                                     chat_deployment="gpt-4o"),
        cost_tracker=tracker,
    )


def _sdk_response(content: str) -> SimpleNamespace:
    return SimpleNamespace(
        id="req-1",
        model="gpt-4o",
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=500, total_tokens=1500),
    )


async def test_every_llm_call_is_cost_tracked() -> None:
    tracker = CostTracker()
    client = _client(tracker)
    sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=AsyncMock(return_value=_sdk_response('{"ok": true}')))))
    client._client = sdk  # type: ignore[assignment]

    assert await client.chat_completion_json([], operation="written_scoring.rubric") == {"ok": True}
    summary = tracker.get_usage_summary()
    assert summary.total_input_tokens == 1000 and summary.total_output_tokens == 500
    assert summary.estimated_cost_usd == pytest.approx(0.0125)


async def test_invalid_json_raises_typed_error() -> None:
    client = _client()
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(  # type: ignore[assignment]
        create=AsyncMock(return_value=_sdk_response("not json")))))
    with pytest.raises(LLMResponseError):
        await client.chat_completion_json([])


async def test_unconfigured_client_raises() -> None:
    client = AzureOpenAIClient(settings=AzureOpenAISettings(endpoint=None, api_key=None))
    with pytest.raises(LLMNotConfiguredError):
        await client.chat_completion_json([])
