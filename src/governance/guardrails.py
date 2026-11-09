"""Scoring guardrails for LinguaProctor.

Validates AI-generated scoring outputs to ensure they are internally
consistent, within valid ranges, and adequately evidenced.  Determines
when mandatory human review is required.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class GuardrailResult(BaseModel):
    """Result of running guardrail checks on a scoring output."""

    is_valid: bool
    requires_human_review: bool
    violations: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Valid score range for each criterion (inclusive)
MIN_SCORE = 0.0
MAX_SCORE = 10.0

# Minimum confidence for an AI scoring result to be accepted
MIN_CONFIDENCE_THRESHOLD = 0.60

# Below this confidence, human review is mandatory
MANDATORY_REVIEW_CONFIDENCE = 0.75

# Minimum number of evidence items expected per criterion
MIN_EVIDENCE_ITEMS = 1

# Extreme score threshold — scores at the absolute min/max need strong evidence
EXTREME_MIN_EVIDENCE = 3

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class GuardrailsError(Exception):
    """Raised on guardrail processing failures."""


# ---------------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------------


class ScoringGuardrails:
    """Validates AI scoring outputs against quality and safety rules.

    The guardrails enforce:

    1. All numeric scores are within ``[0, 10]``.
    2. Overall confidence is above a minimum threshold.
    3. Each scored criterion has supporting evidence.
    4. Extreme scores (0 or 10) require strong evidence.
    5. Mandatory human review flags are set when warranted.
    """

    def validate_scoring_output(self, result: dict[str, Any]) -> GuardrailResult:
        """Run all guardrail checks on a scoring result.

        Parameters
        ----------
        result:
            A dictionary representation of a scoring result.  Expected keys:

            - ``"overall_score"`` (float): 0–10 aggregate score.
            - ``"confidence"`` (float): 0–1 model confidence.
            - ``"criteria"`` (list[dict]): per-criterion scores, each with
              ``"name"``, ``"score"`` (float), ``"evidence"`` (list[str]).

        Returns
        -------
        GuardrailResult
        """
        violations: list[str] = []
        warnings: list[str] = []
        requires_review = False

        # --- overall score range ---
        overall_score = result.get("overall_score")
        if overall_score is not None and not (MIN_SCORE <= overall_score <= MAX_SCORE):
            violations.append(
                f"Overall score {overall_score} outside valid range "
                f"[{MIN_SCORE}, {MAX_SCORE}]"
            )

        # --- confidence threshold ---
        confidence = result.get("confidence", 0.0)
        if confidence < MIN_CONFIDENCE_THRESHOLD:
            violations.append(
                f"Confidence {confidence:.2f} below minimum threshold "
                f"{MIN_CONFIDENCE_THRESHOLD}"
            )
        if confidence < MANDATORY_REVIEW_CONFIDENCE:
            requires_review = True
            warnings.append(
                f"Confidence {confidence:.2f} below mandatory-review threshold "
                f"{MANDATORY_REVIEW_CONFIDENCE} — human review required"
            )

        # --- per-criterion checks ---
        criteria: list[dict[str, Any]] = result.get("criteria", [])
        for criterion in criteria:
            name = criterion.get("name", "unknown")
            score = criterion.get("score")
            evidence: list[str] = criterion.get("evidence", [])

            # Score range
            if score is not None and not (MIN_SCORE <= score <= MAX_SCORE):
                violations.append(
                    f"Criterion '{name}' score {score} outside valid range "
                    f"[{MIN_SCORE}, {MAX_SCORE}]"
                )

            # Evidence completeness
            if len(evidence) < MIN_EVIDENCE_ITEMS:
                warnings.append(
                    f"Criterion '{name}' has no supporting evidence"
                )
                requires_review = True

            # Extreme scores need strong evidence
            if (
                score is not None
                and (score <= MIN_SCORE or score >= MAX_SCORE)
                and len(evidence) < EXTREME_MIN_EVIDENCE
            ):
                warnings.append(
                    f"Criterion '{name}' has extreme score ({score}) "
                    f"with only {len(evidence)} evidence items "
                    f"(minimum {EXTREME_MIN_EVIDENCE} expected)"
                )
                requires_review = True

        is_valid = len(violations) == 0

        logger.info(
            "Guardrail check: valid=%s review=%s violations=%d warnings=%d",
            is_valid,
            requires_review,
            len(violations),
            len(warnings),
        )

        return GuardrailResult(
            is_valid=is_valid,
            requires_human_review=requires_review,
            violations=violations,
            warnings=warnings,
        )

    def check_consistency(
        self,
        current: dict[str, Any],
        historical_mean: float,
        historical_std: float,
        z_threshold: float = 2.5,
    ) -> bool:
        """Check whether a scoring result is consistent with historical norms.

        Uses a Z-score test: if the current overall score deviates by more
        than *z_threshold* standard deviations from the historical mean,
        the result is flagged as inconsistent.

        Parameters
        ----------
        current:
            The scoring result dictionary.  Must contain ``"overall_score"``.
        historical_mean:
            The historical average score for similar translations.
        historical_std:
            The historical standard deviation.
        z_threshold:
            Number of standard deviations allowed.

        Returns
        -------
        bool
            ``True`` if the score is within acceptable bounds; ``False``
            if it is an outlier.
        """
        overall_score = current.get("overall_score")
        if overall_score is None:
            logger.warning("No overall_score in result; cannot check consistency")
            return True

        if historical_std <= 0:
            logger.warning("Historical std is zero; skipping consistency check")
            return True

        z_score = abs(overall_score - historical_mean) / historical_std
        is_consistent = z_score <= z_threshold

        if not is_consistent:
            logger.warning(
                "Score %.2f is an outlier (z=%.2f, mean=%.2f, std=%.2f)",
                overall_score,
                z_score,
                historical_mean,
                historical_std,
            )

        return is_consistent
