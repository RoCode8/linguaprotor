"""Human score override handler.

Processes assessor score modifications with mandatory justification
and immutable audit logging.

The *original* scores are always taken from the stored AI assessment, never
from the client, so the audit trail and calibration statistics cannot be
skewed by a mis-reported baseline.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from src.hitl.review_engine import ReviewEngine
from src.hitl.schemas import CalibrationDatapoint, ReviewStatus, ScoreOverride

logger = logging.getLogger(__name__)

MIN_JUSTIFICATION_CHARS = 10


def agreement_level(ai: dict[str, float], human: dict[str, float]) -> str:
    """Categorise AI/human agreement by the largest per-criterion difference."""
    diffs = [abs(ai[k] - human[k]) for k in human if k in ai]
    worst = max(diffs, default=0.0)
    if worst == 0:
        return "strong_agree"
    if worst <= 0.5:
        return "agree"
    if worst <= 1.5:
        return "disagree"
    return "strong_disagree"


class OverrideHandler:
    """Manages assessor score overrides and justification tracking."""

    def __init__(self, review_engine: ReviewEngine) -> None:
        self.review_engine = review_engine
        self.audit_logger = review_engine.audit_logger
        self.calibration = review_engine.calibration
        self._overrides: list[ScoreOverride] = []

    async def apply_override(
        self,
        review_item_id: str,
        reviewer_id: str,
        overridden_scores: dict[str, float],
        justification: str,
        override_type: str = "criterion_adjustment",
    ) -> ScoreOverride:
        """Apply an assessor override to an open review item.

        Raises:
            KeyError: unknown review item.
            ReviewWorkflowError: item already closed.
            ValueError: missing justification, unknown criterion, or out-of-range score.
        """
        item = self.review_engine.get_item(review_item_id)
        self.review_engine._require_open(item)

        if not justification or len(justification.strip()) < MIN_JUSTIFICATION_CHARS:
            raise ValueError(
                f"A detailed justification (min {MIN_JUSTIFICATION_CHARS} characters) "
                "is required for any score override."
            )
        if not overridden_scores:
            raise ValueError("At least one criterion score must be overridden.")

        original = dict(item.ai_criterion_scores)
        unknown = sorted(set(overridden_scores) - set(original))
        if unknown:
            raise ValueError(
                f"Unknown criteria {unknown}; valid criteria: {sorted(original)}"
            )
        for crit, value in overridden_scores.items():
            if not 0.0 <= value <= item.criterion_max:
                raise ValueError(
                    f"Score for '{crit}' must be within [0, {item.criterion_max:g}], got {value}."
                )

        final_scores = {**original, **overridden_scores}
        override = ScoreOverride(
            review_item_id=review_item_id,
            reviewer_id=reviewer_id,
            original_scores=original,
            overridden_scores=final_scores,
            justification=justification.strip(),
            override_type=override_type,
            created_at=datetime.now(UTC),
        )
        self._overrides.append(override)

        item.status = ReviewStatus.OVERRIDDEN
        item.reviewed_at = override.created_at
        item.assigned_reviewer = reviewer_id
        item.final_score = round(sum(final_scores.values()), 2)

        await self.audit_logger.log_event(
            event_type="override_applied",
            actor=reviewer_id,
            resource_id=item.assessment_id,
            details={
                "review_item_id": item.id,
                "original": original,
                "overridden": final_scores,
                "ai_total": item.ai_score,
                "final_total": item.final_score,
                "justification": override.justification,
            },
        )
        await self.calibration.add_calibration_datapoint(
            CalibrationDatapoint(
                exam_id=item.exam_id,
                candidate_id=item.candidate_id,
                ai_scores=original,
                human_scores=final_scores,
                agreement_level=agreement_level(original, final_scores),
                notes=override.justification,
                is_override=True,
            )
        )
        return override

    async def get_overrides(self, reviewer_id: str | None = None) -> list[ScoreOverride]:
        """Fetch historical overrides."""
        if reviewer_id:
            return [o for o in self._overrides if o.reviewer_id == reviewer_id]
        return list(self._overrides)
