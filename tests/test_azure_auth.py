"""Keyless (Microsoft Entra ID) authentication and reasoning-model request shape."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import AzureOpenAISettings, get_settings
from src.infrastructure import azure_openai_client as oai_mod
from src.infrastructure import azure_speech_client as speech_mod
from src.infrastructure.azure_openai_client import AzureOpenAIClient


def _response() -> SimpleNamespace:
    return SimpleNamespace(
        id="r", model="m",
        choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )


async def _sent_params(settings: AzureOpenAISettings) -> dict[str, Any]:
    client = AzureOpenAIClient(settings=settings)
    create = AsyncMock(return_value=_response())
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))  # type: ignore[assignment]
    await client.chat_completion_json([], temperature=0.0, max_tokens=123)
    return create.call_args.kwargs


async def test_reasoning_models_get_max_completion_tokens_and_no_temperature() -> None:
    sent = await _sent_params(AzureOpenAISettings(endpoint="https://x", reasoning_effort="low"))
    assert sent["max_completion_tokens"] == 123 and sent["reasoning_effort"] == "low"
    assert "temperature" not in sent and "max_tokens" not in sent


async def test_classic_models_keep_temperature_and_max_tokens() -> None:
    sent = await _sent_params(AzureOpenAISettings(endpoint="https://x", chat_reasoning_model=False))
    assert sent["temperature"] == 0.0 and sent["max_tokens"] == 123
    assert "max_completion_tokens" not in sent and "reasoning_effort" not in sent


def test_openai_uses_entra_token_provider_when_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(oai_mod, "AsyncAzureOpenAI", lambda **kw: captured.update(kw) or MagicMock())
    AzureOpenAIClient(settings=AzureOpenAISettings(endpoint="https://x.openai.azure.com/"))._get_client()
    assert "api_key" not in captured
    assert callable(captured["azure_ad_token_provider"])


def test_openai_uses_key_when_provided(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(oai_mod, "AsyncAzureOpenAI", lambda **kw: captured.update(kw) or MagicMock())
    AzureOpenAIClient(settings=AzureOpenAISettings(endpoint="https://x", api_key="k"))._get_client()
    assert captured["api_key"] == "k" and "azure_ad_token_provider" not in captured


def test_speech_uses_entra_with_custom_domain_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_SPEECH_ENDPOINT", "https://spch-x.cognitiveservices.azure.com/")
    get_settings.cache_clear()
    fake = MagicMock()
    monkeypatch.setattr(speech_mod.speechsdk, "SpeechConfig", fake)
    speech_mod.AzureSpeechClient()._get_speech_config()
    kwargs = fake.call_args.kwargs
    assert kwargs["endpoint"] == "https://spch-x.cognitiveservices.azure.com/"
    assert kwargs["token_credential"] is not None and "subscription" not in kwargs


async def test_audit_trail_uses_cosmos_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    from azure.cosmos.aio import ContainerProxy

    from src.api.dependencies import AppState
    from src.governance.audit_logger import InMemoryAuditContainer

    assert isinstance(AppState().audit_logger._container, InMemoryAuditContainer)

    monkeypatch.setenv("COSMOS_ENDPOINT", "https://cosmos-x.documents.azure.com:443/")
    get_settings.cache_clear()
    state = AppState()
    assert isinstance(state.audit_logger._container, ContainerProxy)
    assert state.audit_logger._container.id == "audit"
    await state.aclose()  # closes the Cosmos client and the Entra credential
    assert state._closers == []


async def test_truncated_json_output_reports_token_limit_not_parse_error() -> None:
    client = AzureOpenAIClient(settings=AzureOpenAISettings(endpoint="https://x"))
    resp = _response()
    resp.choices[0].finish_reason = "length"
    resp.choices[0].message.content = '{"source_text": "cut of'
    create = AsyncMock(return_value=resp)
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))  # type: ignore[assignment]
    with pytest.raises(oai_mod.LLMResponseError, match="truncated at the 50-token limit"):
        await client.chat_completion_json([], max_tokens=50, operation="op")
