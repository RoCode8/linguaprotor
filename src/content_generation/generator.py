"""Passage and exam content generation service.

Orchestrates Azure OpenAI to generate authentic, domain-specific,
CEFR-calibrated translation passages along with model translations,
glossaries, and difficulty justifications.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from jinja2 import BaseLoader, Environment

from src.content_generation.schemas import (
    ContentGenerationRequest,
    ExamDomain,
    GeneratedPassage,
    ModelAnswer,
    TerminologyEntry,
)
from src.infrastructure.azure_openai_client import AzureOpenAIClient

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"

# A passage response carries the source text, a full model translation, two
# glossaries and notes. Measured on gpt-5.1 it runs ~3,000-4,100+ completion
# tokens (Arabic and C2 legal/financial texts are the largest), so the default
# 4,096 budget truncated the JSON. For reasoning models this budget also covers
# reasoning tokens.
_PASSAGE_MAX_TOKENS = 16_000
_MODEL_ANSWER_MAX_TOKENS = 8_000
# Generation streams ~90 tokens/s; a large passage can take well over the
# client's default 60 s timeout.
_GENERATION_TIMEOUT_SECONDS = 180.0


class ContentGenerator:
    """Generates multilingual translation exam passages using Azure OpenAI."""

    def __init__(self, openai_client: AzureOpenAIClient | None = None) -> None:
        self.openai_client = openai_client or AzureOpenAIClient()
        self._jinja_env = Environment(loader=BaseLoader(), autoescape=False)

    def _load_prompt_template(self, filename: str) -> dict[str, str]:
        path = _PROMPTS_DIR / filename
        if not path.exists():
            raise FileNotFoundError(f"Prompt template not found at {path}")
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)

    def _render(self, template_str: str, **kwargs: Any) -> str:
        template = self._jinja_env.from_string(template_str)
        return template.render(**kwargs)

    async def generate_passages(
        self, request: ContentGenerationRequest
    ) -> list[GeneratedPassage]:
        """Generate one or more passages matching the request criteria."""
        template_data = self._load_prompt_template("v1_passage_gen.yaml")

        passages: list[GeneratedPassage] = []
        for _ in range(request.num_passages):
            passage = await self._generate_single_passage(request, template_data)
            passages.append(passage)

        return passages

    async def _generate_single_passage(
        self, request: ContentGenerationRequest, template_data: dict[str, str]
    ) -> GeneratedPassage:
        variables = {
            "source_language": request.source_language,
            "target_language": request.target_language,
            "domain": request.domain.value,
            "difficulty": request.difficulty.value,
            "cefr_level": request.difficulty.value,
            "reference_texts": request.reference_texts,
            "specific_terminology": request.specific_terminology,
            "include_model_answer": request.include_model_answer,
        }

        system_msg = self._render(template_data["system"], **variables)
        user_msg = self._render(template_data["user"], **variables)

        # Call OpenAI with JSON format enforcement
        response = await self.openai_client.chat_completion_json(
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.3,
            max_tokens=_PASSAGE_MAX_TOKENS,
            operation="content_generation.passage",
            timeout=_GENERATION_TIMEOUT_SECONDS,
        )

        # Parse terms
        terminology_list = []
        for t in response.get("terminology_glossary", []):
            terminology_list.append(
                TerminologyEntry(
                    source_term=t.get("source_term", ""),
                    target_term=t.get("target_term", ""),
                    domain=request.domain,
                    alternatives=t.get("alternatives", []),
                    notes=t.get("notes"),
                )
            )

        model_answer_raw = response.get("model_answer", {})
        model_answer_terms = [
            TerminologyEntry(
                source_term=t.get("source_term", ""),
                target_term=t.get("target_term", ""),
                domain=request.domain,
                alternatives=t.get("alternatives", []),
                notes=t.get("notes"),
            )
            for t in model_answer_raw.get("key_terminology", [])
        ]

        model_answer = ModelAnswer(
            translation=model_answer_raw.get("translation", ""),
            key_terminology=model_answer_terms,
            translation_notes=model_answer_raw.get("translation_notes", ""),
            difficulty_justification=model_answer_raw.get(
                "difficulty_justification", ""
            ),
        )

        source_text = response.get("source_text", "").strip()
        word_count = len(source_text.split())

        return GeneratedPassage(
            id=str(uuid.uuid4()),
            source_language=request.source_language,
            target_language=request.target_language,
            source_text=source_text,
            model_answer=model_answer,
            domain=request.domain,
            difficulty=request.difficulty,
            cefr_level=request.difficulty.value,
            word_count=word_count,
            terminology_glossary=terminology_list,
            metadata={
                "requested_difficulty": request.difficulty.value,
                "domain": request.domain.value,
                "has_reference_texts": len(request.reference_texts) > 0,
            },
            version=1,
            created_at=datetime.now(UTC),
            review_status="pending_expert_review",
        )

    async def generate_model_answer(
        self,
        passage: str,
        source_lang: str,
        target_lang: str,
        domain: ExamDomain,
    ) -> ModelAnswer:
        """Generate an expert-grade model answer with annotations for an existing passage."""
        prompt = (
            f"Provide an expert-level model translation of the following {source_lang} passage "
            f"into {target_lang} for the {domain.value} domain. Identify key terminology and translation notes.\n\n"
            f"Passage:\n{passage}\n\n"
            "Respond in JSON format with keys: translation, key_terminology (list of objects with source_term, "
            "target_term, alternatives, notes), translation_notes, difficulty_justification."
        )

        response = await self.openai_client.chat_completion_json(
            messages=[
                {
                    "role": "system",
                    "content": "You are a master bilingual translator and assessment expert.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=_MODEL_ANSWER_MAX_TOKENS,
            operation="content_generation.model_answer",
            timeout=_GENERATION_TIMEOUT_SECONDS,
        )

        key_terms = [
            TerminologyEntry(
                source_term=t.get("source_term", ""),
                target_term=t.get("target_term", ""),
                domain=domain,
                alternatives=t.get("alternatives", []),
                notes=t.get("notes"),
            )
            for t in response.get("key_terminology", [])
        ]

        return ModelAnswer(
            translation=response.get("translation", ""),
            key_terminology=key_terms,
            translation_notes=response.get("translation_notes", ""),
            difficulty_justification=response.get("difficulty_justification", ""),
        )
