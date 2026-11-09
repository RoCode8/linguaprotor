"""Pydantic v2 schemas for the content generation module.

Defines domain enumerations, terminology entries, model answers,
generated passages, generation requests, review feedback, and
validation result models used throughout the content pipeline.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ExamDifficulty(str, Enum):
    """CEFR-aligned difficulty tiers for translation examination passages."""

    B2 = "B2"
    C1 = "C1"
    C2 = "C2"


class ExamDomain(str, Enum):
    """Subject-matter domains for examination passages."""

    LEGAL = "legal"
    MEDICAL = "medical"
    TECHNICAL = "technical"
    GENERAL = "general"
    FINANCIAL = "financial"


# ---------------------------------------------------------------------------
# Terminology
# ---------------------------------------------------------------------------


class TerminologyEntry(BaseModel):
    """A single bilingual terminology mapping with optional alternatives.

    Attributes:
        source_term: Term in the source language.
        target_term: Canonical translation in the target language.
        domain: Subject-matter domain this term belongs to.
        alternatives: Acceptable alternative translations.
        notes: Free-text notes for reviewers / translators.
    """

    source_term: str
    target_term: str
    domain: ExamDomain
    alternatives: list[str] = Field(default_factory=list)
    notes: str | None = None


# ---------------------------------------------------------------------------
# Model answer & generated passage
# ---------------------------------------------------------------------------


class ModelAnswer(BaseModel):
    """A reference ("gold-standard") translation together with explanatory metadata.

    Attributes:
        translation: Full target-language translation of the source passage.
        key_terminology: Domain terms that must appear in a competent translation.
        translation_notes: Guidance on stylistic / register choices.
        difficulty_justification: Why this passage matches its target CEFR level.
    """

    translation: str
    key_terminology: list[TerminologyEntry] = Field(default_factory=list)
    translation_notes: str = ""
    difficulty_justification: str = ""


class GeneratedPassage(BaseModel):
    """A fully generated examination passage ready for expert review.

    Attributes:
        id: Unique identifier (UUID4 string).
        source_language: BCP-47 code of the source language (e.g. ``"en"``).
        target_language: BCP-47 code of the target language (e.g. ``"ar"``).
        source_text: The passage text in the source language.
        model_answer: Reference translation and terminology.
        domain: Subject-matter domain.
        difficulty: Target CEFR difficulty tier.
        cefr_level: Human-readable CEFR label (e.g. ``"C1"``).
        word_count: Number of words in ``source_text``.
        terminology_glossary: Glossary of domain terms relevant to this passage.
        metadata: Arbitrary key-value metadata (prompt version, model, etc.).
        version: Schema version for forward-compatibility.
        created_at: UTC timestamp of generation.
        review_status: Current position in the human-review workflow.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_language: str = "en"
    target_language: str = "ar"
    source_text: str
    model_answer: ModelAnswer
    domain: ExamDomain
    difficulty: ExamDifficulty
    cefr_level: str
    word_count: int = 0
    terminology_glossary: list[TerminologyEntry] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    version: int = 1
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    review_status: str = "pending_expert_review"


class ContentGenerationRequest(BaseModel):
    """Parameters for requesting new examination passages.

    Attributes:
        source_language: BCP-47 source language code.
        target_language: BCP-47 target language code.
        domain: Subject-matter domain.
        difficulty: Target CEFR difficulty tier.
        reference_texts: Optional exemplar passages to guide style/content.
        num_passages: Number of passages to generate in one batch.
        include_model_answer: Whether to include a gold-standard translation.
        specific_terminology: Domain terms that *must* appear in the passage.
    """

    source_language: str = "en"
    target_language: str = "ar"
    domain: ExamDomain
    difficulty: ExamDifficulty
    reference_texts: list[str] = Field(default_factory=list)
    num_passages: int = Field(default=1, ge=1, le=10)
    include_model_answer: bool = True
    specific_terminology: list[str] = Field(default_factory=list)


class ContentReviewFeedback(BaseModel):
    """Expert reviewer feedback on a generated passage.

    Attributes:
        passage_id: UUID of the passage under review.
        reviewer_id: Identifier of the human reviewer.
        decision: Review outcome — ``"approved"``, ``"rejected"``, or
            ``"revision_requested"``.
        comments: Free-text reviewer commentary.
        suggested_edits: Mapping of original text spans to suggested replacements.
        reviewed_at: UTC timestamp of the review.
    """

    passage_id: str
    reviewer_id: str
    decision: Literal["approved", "rejected", "revision_requested"]
    comments: str
    suggested_edits: dict[str, str] = Field(default_factory=dict)
    reviewed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ---------------------------------------------------------------------------
# Validation results
# ---------------------------------------------------------------------------


class ValidationResult(BaseModel):
    """Result of a single validation check."""

    is_valid: bool = Field(description="Whether the validation check passed")
    score: float = Field(ge=0.0, le=1.0, description="Validation score from 0.0 to 1.0")
    issues: list[str] = Field(default_factory=list, description="List of issues found")
    details: dict[str, Any] = Field(
        default_factory=dict, description="Additional details from the validation"
    )


class DuplicationResult(BaseModel):
    """Result of duplication checking against existing passages."""

    is_duplicate: bool = Field(description="Whether the passage is a duplicate")
    max_similarity: float = Field(
        ge=0.0, le=1.0, description="Maximum similarity score found"
    )
    similar_passage_ids: list[str] = Field(
        default_factory=list, description="IDs of similar passages found"
    )


class ComprehensiveValidationResult(BaseModel):
    """Combined validation results for a passage."""

    difficulty: ValidationResult
    terminology: ValidationResult
    duplication: DuplicationResult
    overall_valid: bool = Field(description="Whether the passage passed all validations")
