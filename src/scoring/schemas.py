"""Pydantic v2 schemas for the LinguaProctor scoring engine.

All data flowing through the scoring pipeline is represented by these models,
ensuring type safety, serialization, and validation at every stage.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class SeverityLevel(str, Enum):
    """Severity classification for translation issues."""

    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"


# ---------------------------------------------------------------------------
# Meaning-unit decomposition
# ---------------------------------------------------------------------------

class MeaningUnit(BaseModel):
    """An atomic unit of meaning extracted from the source text.

    Each unit represents a single complete idea, fact, or instruction that
    must be preserved in a faithful translation.
    """

    id: str = Field(description="Unique identifier for the meaning unit (e.g. 'mu_1')")
    unit_type: str = Field(
        description=(
            "Linguistic type of the unit, e.g. 'main_clause', 'conditional', "
            "'definition', 'enumeration', 'modifier', 'temporal', 'causal'"
        )
    )
    source_text: str = Field(description="Verbatim source-language text of this unit")
    source_span: tuple[int, int] = Field(
        description="Character offsets (start, end) in the full source text"
    )
    importance_weight: float = Field(
        ge=0.0,
        le=1.0,
        description="Relative importance of this unit for scoring (0.0 – 1.0)",
    )


# ---------------------------------------------------------------------------
# Semantic alignment
# ---------------------------------------------------------------------------

class AlignedUnit(BaseModel):
    """Maps a source meaning unit to its corresponding candidate translation span.

    When a meaning unit is omitted from the candidate, ``candidate_text`` and
    ``candidate_span`` are *None*.
    """

    meaning_unit: MeaningUnit
    candidate_text: str | None = Field(
        default=None,
        description="Candidate-language text aligned to this unit (None if omitted)",
    )
    candidate_span: tuple[int, int] | None = Field(
        default=None,
        description="Character offsets in the candidate translation (None if omitted)",
    )
    alignment_confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Model confidence in the alignment quality",
    )


# ---------------------------------------------------------------------------
# Terminology
# ---------------------------------------------------------------------------

class TerminologyVerdict(BaseModel):
    """Assessment of a single terminology translation.

    Distinguishes between *correct* translations, *valid alternative*
    translations (acceptable but different from the expected term), and
    *incorrect* translations.
    """

    source_term: str = Field(description="Original term in the source language")
    expected_translation: str = Field(description="Expected / glossary translation")
    candidate_translation: str = Field(description="Translation produced by the candidate")
    is_correct: bool = Field(description="Whether candidate matches the expected translation")
    is_valid_alternative: bool = Field(
        description="Whether candidate is an acceptable alternative even if different"
    )
    explanation: str = Field(description="Brief justification for the verdict")


# ---------------------------------------------------------------------------
# Criterion-level evidence and scoring
# ---------------------------------------------------------------------------

class CriterionEvidence(BaseModel):
    """A single piece of evidence supporting a criterion score.

    Evidence can be either *positive* (correctly translated) or *negative*
    (an issue was found).
    """

    meaning_unit_id: str = Field(description="ID of the meaning unit this evidence relates to")
    source_span: str = Field(description="Relevant source text excerpt")
    candidate_span: str | None = Field(
        default=None,
        description="Corresponding candidate text excerpt (None if omitted)",
    )
    issue: str | None = Field(
        default=None,
        description="Description of the issue found (None for positive evidence)",
    )
    severity: SeverityLevel | None = Field(
        default=None,
        description="Severity of the issue (None for positive evidence)",
    )
    is_positive: bool = Field(
        default=False,
        description="True when this evidence highlights a *correctly* translated unit",
    )
    explanation: str = Field(description="Detailed explanation of the evidence")


class CriterionScore(BaseModel):
    """Score and evidence for a single assessment criterion.

    Supported criteria: ``accuracy``, ``completeness``, ``terminology``,
    ``fluency``.
    """

    criterion: str = Field(
        description="Name of the criterion: accuracy | completeness | terminology | fluency"
    )
    score: float = Field(ge=0.0, description="Points awarded for this criterion")
    max_score: float = Field(gt=0.0, description="Maximum possible points")
    evidence: list[CriterionEvidence] = Field(
        default_factory=list,
        description="Supporting evidence items",
    )
    summary: str = Field(description="Human-readable summary of the criterion assessment")


# ---------------------------------------------------------------------------
# Overall scoring result
# ---------------------------------------------------------------------------

class ScoringResult(BaseModel):
    """Complete output of the scoring pipeline for one candidate translation."""

    id: str = Field(description="Unique identifier for this scoring result")
    exam_id: str = Field(description="Identifier of the examination")
    candidate_id: str = Field(description="Identifier of the candidate being assessed")
    overall_score: float = Field(ge=0.0, description="Weighted overall score")
    overall_max: float = Field(gt=0.0, description="Maximum possible overall score")
    percentage: float = Field(
        ge=0.0,
        le=100.0,
        description="Overall score expressed as a percentage",
    )
    criteria: list[CriterionScore] = Field(description="Per-criterion breakdown")
    meaning_units: list[MeaningUnit] = Field(description="Decomposed source meaning units")
    alignments: list[AlignedUnit] = Field(description="Alignment of units to candidate text")
    terminology_verdicts: list[TerminologyVerdict] = Field(
        default_factory=list,
        description="Terminology assessment verdicts",
    )
    valid_alternatives_detected: int = Field(
        default=0,
        description="Count of valid alternative translations recognised",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Model confidence in the overall scoring result",
    )
    flags: list[str] = Field(
        default_factory=list,
        description="Flags such as 'requires_human_review', 'low_confidence_unit_3'",
    )
    scoring_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Metadata: model version, prompt version, tokens used, latency",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp when the scoring result was created",
    )


# ---------------------------------------------------------------------------
# Scoring request
# ---------------------------------------------------------------------------

class GlossaryTerm(BaseModel):
    """A reference terminology entry with officially accepted alternatives.

    Accepts both the content-generator shape (``source_term``/``target_term``)
    and the short form (``source``/``target``).
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    source_term: str = Field(
        min_length=1, validation_alias=AliasChoices("source_term", "source")
    )
    target_term: str = Field(
        min_length=1, validation_alias=AliasChoices("target_term", "target")
    )
    alternatives: list[str] = Field(
        default_factory=list,
        description="Officially accepted alternative renderings (e.g. regional variants)",
    )


class ScoringRequest(BaseModel):
    """Input payload to initiate scoring of a candidate translation."""

    exam_id: str = Field(description="Identifier of the examination")
    candidate_id: str = Field(description="Identifier of the candidate")
    source_text: str = Field(min_length=1, description="Original source-language text")
    source_language: str = Field(
        default="en",
        description="BCP-47 language tag for the source",
    )
    target_language: str = Field(
        default="ar",
        description="BCP-47 language tag for the target",
    )
    candidate_translation: str = Field(min_length=1, description="Candidate's translation")
    reference_translation: str | None = Field(
        default=None,
        description="Optional gold-standard reference translation",
    )
    terminology_glossary: list[GlossaryTerm] = Field(
        default_factory=list,
        description=(
            "Reference terms, e.g. "
            '[{"source_term": "liability", "target_term": "مسؤولية", "alternatives": []}]'
        ),
    )
    rubric_version: str = Field(
        default="v1",
        pattern=r"^v\d+$",
        description="Version identifier for the rubric/prompt template",
    )


# ---------------------------------------------------------------------------
# Consistency / calibration report
# ---------------------------------------------------------------------------

class ConsistencyReport(BaseModel):
    """Statistical report on scoring consistency across multiple candidates.

    Used for quality assurance and calibration of the AI scoring pipeline.
    """

    exam_id: str = Field(description="Identifier of the examination")
    num_candidates: int = Field(description="Number of candidates analysed")
    score_mean: float = Field(description="Mean overall score")
    score_std: float = Field(description="Standard deviation of overall scores")
    criterion_variances: dict[str, float] = Field(
        default_factory=dict,
        description="Variance per criterion name",
    )
    outlier_candidate_ids: list[str] = Field(
        default_factory=list,
        description="Candidate IDs whose scores are >2 σ from the mean",
    )
    inter_rater_agreement: float | None = Field(
        default=None,
        description="Cohen's κ or ICC when human scores are available",
    )
    recommendations: list[str] = Field(
        default_factory=list,
        description="Actionable recommendations for recalibration",
    )
