"""Pydantic v2 schemas for the LinguaProctor human-in-the-loop (HITL) module.

Defines data models for:
- Review queue items and lifecycle statuses
- Score overrides with full audit trail
- Calibration datapoints for AI-human agreement analysis
- Calibration reports with statistical agreement metrics
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class ReviewStatus(str, Enum):
    """Lifecycle status of a review queue item.

    Attributes:
        PENDING: Awaiting reviewer assignment.
        IN_REVIEW: Currently being reviewed by an assigned reviewer.
        APPROVED: AI score confirmed or auto-approved.
        OVERRIDDEN: Reviewer overrode the AI score.
        ESCALATED: Flagged for senior review or additional attention.
    """

    PENDING = "pending"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    OVERRIDDEN = "overridden"
    ESCALATED = "escalated"


class ReviewPriority(str, Enum):
    """Priority level for a review item, driving queue ordering.

    Attributes:
        LOW: High-confidence assessments; may be auto-approved.
        MEDIUM: Flagged assessments requiring routine review.
        HIGH: Low-confidence assessments requiring careful review.
        CRITICAL: Below mandatory threshold; must be reviewed before release.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ReviewItem(BaseModel):
    """An item in the human review queue.

    Represents a single AI assessment (written or oral) that requires
    or may benefit from human review.
    """

    id: str = Field(..., description="Unique identifier for this review item.")
    assessment_id: str = Field(
        ...,
        description="ID of the scored assessment (written or oral result).",
    )
    assessment_type: str = Field(
        ...,
        description="Type of assessment: 'written' or 'oral'.",
    )
    candidate_id: str = Field(..., description="ID of the assessed candidate.")
    exam_id: str = Field(..., description="ID of the parent examination.")
    ai_score: float = Field(
        ...,
        ge=0.0,
        description="The AI-generated overall score.",
    )
    ai_confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="AI system's confidence in the score (0.0–1.0).",
    )
    priority: ReviewPriority = Field(
        ...,
        description="Review priority, auto-determined from confidence and flags.",
    )
    status: ReviewStatus = Field(
        default=ReviewStatus.PENDING,
        description="Current lifecycle status of this review item.",
    )
    assigned_reviewer: str | None = Field(
        default=None,
        description="ID of the reviewer assigned to this item.",
    )
    flags: list[str] = Field(
        default_factory=list,
        description="Flags from AI assessment (e.g. 'low_confidence', 'possible_plagiarism').",
    )
    created_at: datetime = Field(
        ...,
        description="Timestamp when this review item was created.",
    )
    reviewed_at: datetime | None = Field(
        default=None,
        description="Timestamp when the review was completed.",
    )
    ai_criterion_scores: dict[str, float] = Field(
        default_factory=dict,
        description="Authoritative AI per-criterion scores (source of truth for overrides).",
    )
    criterion_max: float = Field(
        default=5.0,
        gt=0.0,
        description="Maximum points per criterion.",
    )
    required_approvals: int = Field(
        default=1,
        ge=1,
        description="Number of distinct assessors that must sign off (2 = dual-assessor panel).",
    )
    approvals: list[str] = Field(
        default_factory=list,
        description="Reviewer IDs that have approved this item.",
    )
    final_score: float | None = Field(
        default=None,
        description="Released score once the item is approved or overridden.",
    )

    @property
    def is_closed(self) -> bool:
        return self.status in (ReviewStatus.APPROVED, ReviewStatus.OVERRIDDEN)


class ScoreOverride(BaseModel):
    """Record of a human reviewer overriding AI-generated scores.

    Maintains full audit trail of what changed and why.
    """

    review_item_id: str = Field(
        ...,
        description="ID of the review item that was overridden.",
    )
    reviewer_id: str = Field(
        ...,
        description="ID of the reviewer who applied the override.",
    )
    original_scores: dict[str, float] = Field(
        ...,
        description="Original AI-generated scores by criterion.",
    )
    overridden_scores: dict[str, float] = Field(
        ...,
        description="New scores set by the reviewer, by criterion.",
    )
    justification: str = Field(
        ...,
        min_length=10,
        description="Reviewer's justification for the override (minimum 10 characters).",
    )
    override_type: str = Field(
        ...,
        description=(
            "Type of override: 'criterion_adjustment' (partial), "
            "'full_override' (all criteria), or 'score_confirmation'."
        ),
    )
    created_at: datetime = Field(
        ...,
        description="Timestamp when this override was applied.",
    )


class CalibrationDatapoint(BaseModel):
    """A single datapoint for AI-vs-human score calibration analysis.

    Records both AI and human scores for the same assessment to
    enable agreement analysis over time.
    """

    exam_id: str = Field(..., description="ID of the examination.")
    candidate_id: str = Field(..., description="ID of the candidate.")
    ai_scores: dict[str, float] = Field(
        ...,
        description="AI-generated scores per criterion.",
    )
    human_scores: dict[str, float] = Field(
        ...,
        description="Human reviewer scores per criterion.",
    )
    agreement_level: str = Field(
        ...,
        description=(
            "Categorical agreement level: 'strong_agree', 'agree', "
            "'disagree', or 'strong_disagree'."
        ),
    )
    notes: str | None = Field(
        default=None,
        description="Optional reviewer notes about discrepancies.",
    )
    is_override: bool = Field(
        default=False,
        description="True when the human changed the AI scores; False for confirmations.",
    )


class CalibrationReport(BaseModel):
    """Aggregated calibration report over a time period.

    Provides statistical metrics on AI-human agreement and
    actionable recommendations for system improvement.
    """

    report_id: str = Field(..., description="Unique identifier for this report.")
    period_start: datetime = Field(
        ...,
        description="Start of the reporting period (inclusive).",
    )
    period_end: datetime = Field(
        ...,
        description="End of the reporting period (inclusive).",
    )
    total_assessments: int = Field(
        ...,
        ge=0,
        description="Total number of assessments in the period.",
    )
    human_reviewed: int = Field(
        ...,
        ge=0,
        description="Number of assessments that received human review.",
    )
    overrides: int = Field(
        ...,
        ge=0,
        description="Number of assessments where scores were overridden.",
    )
    override_rate: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Fraction of reviewed assessments that were overridden.",
    )
    mean_ai_human_difference: dict[str, float] = Field(
        default_factory=dict,
        description="Mean (AI − human) score difference per criterion.",
    )
    cohens_kappa: float | None = Field(
        default=None,
        description="Cohen's kappa inter-rater agreement coefficient.",
    )
    icc: float | None = Field(
        default=None,
        description="Intraclass correlation coefficient (ICC) for score reliability.",
    )
    recommendations: list[str] = Field(
        default_factory=list,
        description="Actionable recommendations based on calibration analysis.",
    )
    created_at: datetime = Field(
        ...,
        description="Timestamp when this report was generated.",
    )
