"""Application configuration for LinguaProctor.

Uses pydantic-settings to load configuration from environment variables
and .env files. All Azure service credentials and application settings
are organized into logical groups.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class AzureOpenAISettings(BaseSettings):
    """Azure OpenAI service credentials and configuration."""

    model_config = SettingsConfigDict(env_prefix="AZURE_OPENAI_", env_file=".env", extra="ignore")

    endpoint: str | None = Field(
        default=None,
        description="Azure OpenAI endpoint URL (e.g. https://<resource>.openai.azure.com/)",
    )
    api_key: SecretStr | None = Field(
        default=None,
        description=(
            "Optional API key. When unset, Microsoft Entra ID is used "
            "(managed identity in Azure, developer credentials locally)."
        ),
    )
    api_version: str = Field(
        default="2025-04-01-preview",
        description="Azure OpenAI data-plane API version",
    )
    chat_deployment: str = Field(
        default="chat",
        description="Deployment name for the chat completion model",
    )
    embedding_deployment: str = Field(
        default="embedding",
        description="Deployment name for the embedding model",
    )
    chat_reasoning_model: bool = Field(
        default=True,
        description=(
            "True for reasoning models (GPT-5.x, o-series): sends max_completion_tokens and "
            "omits temperature, which those models reject."
        ),
    )
    reasoning_effort: str | None = Field(
        default=None,
        description="Optional reasoning_effort for reasoning models (e.g. none, low, medium).",
    )
    max_retries: int = Field(default=3, description="Maximum retry attempts for API calls")
    timeout_seconds: int = Field(default=60, description="Request timeout in seconds")

    @property
    def is_configured(self) -> bool:
        """An endpoint is sufficient: authentication falls back to Microsoft Entra ID."""
        return bool(self.endpoint)

    @property
    def uses_key_auth(self) -> bool:
        return bool(self.api_key and self.api_key.get_secret_value())


class AzureSpeechSettings(BaseSettings):
    """Azure AI Speech service credentials and configuration."""

    model_config = SettingsConfigDict(env_prefix="AZURE_SPEECH_", env_file=".env", extra="ignore")

    key: SecretStr | None = Field(
        default=None,
        description="Optional subscription key. When unset, Entra ID is used with `endpoint`.",
    )
    endpoint: str | None = Field(
        default=None,
        description="Custom-domain endpoint (https://<name>.cognitiveservices.azure.com/), "
        "required for Microsoft Entra ID authentication.",
    )
    region: str = Field(default="eastus", description="Azure Speech service region")
    default_voice_en: str = Field(
        default="en-US-JennyNeural",
        description="Default English TTS voice",
    )
    default_voice_ar: str = Field(
        default="ar-SA-HamedNeural",
        description="Default Arabic TTS voice",
    )

    @property
    def is_configured(self) -> bool:
        return bool(self.endpoint) or self.uses_key_auth

    @property
    def uses_key_auth(self) -> bool:
        return bool(self.key and self.key.get_secret_value())


class CosmosSettings(BaseSettings):
    """Azure Cosmos DB credentials and configuration."""

    model_config = SettingsConfigDict(env_prefix="COSMOS_", env_file=".env", extra="ignore")

    endpoint: str | None = Field(
        default=None,
        description="Azure Cosmos DB endpoint URL",
    )
    key: SecretStr | None = Field(
        default=None,
        description="Optional account key. When unset, Microsoft Entra ID (data-plane RBAC) is used.",
    )
    database_name: str = Field(
        default="linguaproctor",
        description="Cosmos DB database name",
    )
    audit_container: str = Field(
        default="audit",
        description="Container holding the append-only audit trail (partition key /partitionKey)",
    )
    preferred_regions: list[str] = Field(
        default_factory=list,
        description="Preferred Cosmos DB regions for geo-redundancy",
    )

    @property
    def is_configured(self) -> bool:
        return bool(self.endpoint)


class BlobStorageSettings(BaseSettings):
    """Azure Blob Storage credentials and configuration."""

    model_config = SettingsConfigDict(env_prefix="BLOB_STORAGE_", env_file=".env", extra="ignore")

    connection_string: SecretStr | None = Field(
        default=None,
        description="Azure Blob Storage connection string",
    )
    account_name: str | None = Field(default=None, description="Azure Storage account name")
    account_key: SecretStr | None = Field(default=None, description="Azure Storage account key")
    default_container: str = Field(
        default="examinations",
        description="Default blob container for exam assets",
    )


class AppSettings(BaseSettings):
    """General application settings for LinguaProctor."""

    model_config = SettingsConfigDict(env_prefix="APP_", env_file=".env", extra="ignore")

    name: str = Field(default="LinguaProctor", description="Application name")
    version: str = Field(default="0.1.0", description="Application version")
    environment: str = Field(
        default="development",
        description="Deployment environment (development, staging, production)",
    )
    log_level: str = Field(default="INFO", description="Logging level")
    debug: bool = Field(default=False, description="Enable debug mode")
    allowed_languages: list[str] = Field(
        default_factory=lambda: ["en-US", "ar-AE", "ar-SA", "ar-EG"],
        description="Supported BCP-47 language tags",
    )
    max_exam_duration_minutes: int = Field(
        default=180,
        description="Maximum allowed examination duration in minutes",
    )
    scoring_confidence_threshold: float = Field(
        default=0.75,
        description="Threshold below which scoring requires human review",
    )
    auto_approve_threshold: float = Field(
        default=0.90,
        description="Threshold above which scoring is auto-approved",
    )
    mandatory_review_threshold: float = Field(
        default=0.60,
        description="Threshold below which review is mandatory",
    )
    max_concurrent_scoring: int = Field(
        default=5,
        description="Maximum concurrent scoring operations",
    )
    audit_sample_rate: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        description="Fraction of auto-approvable assessments randomly held for human audit",
    )


class Settings(BaseSettings):
    """Root settings aggregator for the LinguaProctor platform.

    Composes all sub-setting groups and loads values from a ``.env``
    file at the project root.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    azure_openai: AzureOpenAISettings = Field(default_factory=AzureOpenAISettings)
    azure_speech: AzureSpeechSettings = Field(default_factory=AzureSpeechSettings)
    cosmos: CosmosSettings = Field(default_factory=CosmosSettings)
    blob_storage: BlobStorageSettings = Field(default_factory=BlobStorageSettings)
    app: AppSettings = Field(default_factory=AppSettings)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the singleton application settings instance.

    Uses :func:`functools.lru_cache` so the ``.env`` file is read at most
    once per process lifetime.

    Returns:
        The fully-resolved :class:`Settings` object.
    """
    logger.info("Loading LinguaProctor application settings")
    return Settings()  # type: ignore[call-arg]
