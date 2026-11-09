"""Async wrapper around the Azure OpenAI SDK.

Provides chat-completion and embedding methods used by the scoring engine and
other modules.  The client is initialised lazily so that import-time side-effects
are avoided.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from openai import AsyncAzureOpenAI

from src.config import AzureOpenAISettings, get_settings
from src.governance.cost_tracker import CostTracker

logger = logging.getLogger(__name__)


COGNITIVE_SERVICES_SCOPE = "https://cognitiveservices.azure.com/.default"


class LLMNotConfiguredError(RuntimeError):
    """Raised when an LLM call is attempted without Azure OpenAI credentials."""


class LLMResponseError(RuntimeError):
    """Raised when the model returns output that cannot be parsed."""


class AzureOpenAIClient:
    """Thin async façade over the Azure OpenAI SDK.

    Parameters
    ----------
    settings:
        Explicit settings override.  When *None* the global ``AppSettings``
        are used.
    """

    def __init__(
        self,
        settings: AzureOpenAISettings | None = None,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        self._settings = settings or get_settings().azure_openai
        self._client: AsyncAzureOpenAI | None = None
        self._credential: Any | None = None
        self._cost_tracker = cost_tracker

    @property
    def chat_deployment(self) -> str:
        return self._settings.chat_deployment

    @property
    def embedding_deployment(self) -> str:
        return self._settings.embedding_deployment

    # -- lazy client init ----------------------------------------------------

    def _get_client(self) -> AsyncAzureOpenAI:
        if self._client is None:
            if not self._settings.is_configured:
                raise LLMNotConfiguredError(
                    "Azure OpenAI is not configured (set AZURE_OPENAI_ENDPOINT)."
                )
            assert self._settings.endpoint
            auth: dict[str, Any]
            if self._settings.uses_key_auth:
                assert self._settings.api_key
                auth = {"api_key": self._settings.api_key.get_secret_value()}
            else:
                # Keyless: managed identity in Azure (AZURE_CLIENT_ID selects the
                # user-assigned identity), developer credentials locally.
                from azure.identity.aio import DefaultAzureCredential, get_bearer_token_provider

                self._credential = DefaultAzureCredential()
                auth = {
                    "azure_ad_token_provider": get_bearer_token_provider(
                        self._credential, COGNITIVE_SERVICES_SCOPE
                    )
                }
            self._client = AsyncAzureOpenAI(
                azure_endpoint=self._settings.endpoint,
                api_version=self._settings.api_version,
                max_retries=self._settings.max_retries,
                timeout=self._settings.timeout_seconds,
                **auth,
            )
        return self._client

    # -- public API ----------------------------------------------------------

    async def chat_completion(
        self,
        messages: list[dict[str, str]],
        *,
        deployment: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        response_format: dict[str, Any] | None = None,
        operation: str = "chat",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Send a chat-completion request and return the parsed response.

        Parameters
        ----------
        messages:
            OpenAI-style message list.
        deployment:
            Model deployment name.  Defaults to the chat deployment from
            settings.
        temperature:
            Sampling temperature (classic models only; reasoning models ignore it).
        max_tokens:
            Maximum tokens in the completion (sent as ``max_completion_tokens``
            for reasoning models, where it also bounds reasoning tokens).
        response_format:
            Optional ``response_format`` parameter (e.g. ``{"type": "json_object"}``).
        **kwargs:
            Forwarded verbatim to the SDK call.

        Returns
        -------
        dict
            ``{"content": <str>, "usage": <dict>, "model": <str>}``
        """
        client = self._get_client()
        deploy = deployment or self._settings.chat_deployment

        extra: dict[str, Any] = {}
        if response_format is not None:
            extra["response_format"] = response_format
        if self._settings.chat_reasoning_model:
            # GPT-5.x / o-series reject `temperature` and `max_tokens`.
            extra["max_completion_tokens"] = max_tokens
            if self._settings.reasoning_effort:
                extra["reasoning_effort"] = self._settings.reasoning_effort
        else:
            extra["temperature"] = temperature
            extra["max_tokens"] = max_tokens

        response = await client.chat.completions.create(
            model=deploy,
            messages=messages,  # type: ignore[arg-type]
            **extra,
            **kwargs,
        )

        choice = response.choices[0]
        content = choice.message.content or ""

        usage = {}
        if response.usage is not None:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }
            if self._cost_tracker is not None:
                self._cost_tracker.record_usage(
                    # Price by the served model, not the (model-agnostic) deployment name.
                    model=response.model or deploy,
                    input_tokens=response.usage.prompt_tokens,
                    output_tokens=response.usage.completion_tokens,
                    operation=operation,
                    request_id=response.id,
                )

        return {
            "content": content,
            "usage": usage,
            "model": response.model,
            "finish_reason": getattr(choice, "finish_reason", None),
        }

    async def chat_completion_json(
        self,
        messages: list[dict[str, str]],
        *,
        deployment: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        operation: str = "chat",
        **kwargs: Any,
    ) -> Any:
        """Convenience wrapper that forces JSON output and parses it.

        Returns the parsed JSON object (``dict`` or ``list``).
        """
        result = await self.chat_completion(
            messages,
            deployment=deployment,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            operation=operation,
            **kwargs,
        )
        if result.get("finish_reason") == "length":
            # The JSON was cut off mid-stream; parsing would only report a
            # misleading "unterminated string" error.
            completion_tokens = result["usage"].get("completion_tokens", "?")
            raise LLMResponseError(
                f"Model output was truncated at the {max_tokens}-token limit "
                f"({completion_tokens} completion tokens) for operation '{operation}'; "
                "raise max_tokens for this operation."
            )
        try:
            return json.loads(result["content"])
        except json.JSONDecodeError as exc:
            raise LLMResponseError(f"Model returned invalid JSON: {exc}") from exc

    async def embeddings(
        self,
        texts: list[str],
        *,
        deployment: str | None = None,
    ) -> list[list[float]]:
        """Generate embeddings for a list of texts.

        Parameters
        ----------
        texts:
            Input strings to embed.
        deployment:
            Embedding model deployment.  Defaults to the embedding deployment
            from settings.

        Returns
        -------
        list[list[float]]
            One embedding vector per input text.
        """
        client = self._get_client()
        deploy = deployment or self._settings.embedding_deployment

        response = await client.embeddings.create(
            model=deploy,
            input=texts,
        )
        if self._cost_tracker is not None and response.usage is not None:
            self._cost_tracker.record_usage(
                model=getattr(response, "model", None) or deploy,
                input_tokens=response.usage.prompt_tokens,
                output_tokens=0,
                operation="embedding",
                request_id=f"emb-{id(response)}",
            )

        return [item.embedding for item in response.data]

    async def close(self) -> None:
        """Close the underlying HTTP transport."""
        if self._client is not None:
            await self._client.close()
            self._client = None
        if self._credential is not None:
            await self._credential.close()
            self._credential = None
