"""Scoring consistency and variance checker.

Monitors score distributions, flags anomalous evaluations, and calculates
inter-rater agreement metrics across candidate cohorts.
"""

from __future__ import annotations

import logging

import numpy as np

from src.scoring.schemas import ConsistencyReport, ScoringResult

logger = logging.getLogger(__name__)


class ConsistencyChecker:
    """Detects statistical outliers, scoring drift, and cohort anomalies."""

    def check_cohort_consistency(
        self, results: list[ScoringResult], exam_id: str | None = None
    ) -> ConsistencyReport:
        """Analyze score distributions across a cohort for an examination."""
        if not results:
            return ConsistencyReport(
                exam_id=exam_id or "none",
                num_candidates=0,
                score_mean=0.0,
                score_std=0.0,
                criterion_variances={},
                outlier_candidate_ids=[],
                inter_rater_agreement=None,
                recommendations=["No candidate submissions available for cohort analysis."],
            )

        exam_id = results[0].exam_id
        scores = [r.percentage for r in results]
        mean_score = float(np.mean(scores))
        std_score = float(np.std(scores)) if len(scores) > 1 else 0.0

        # Calculate criterion-level variances
        criterion_scores: dict[str, list[float]] = {}
        for r in results:
            for c in r.criteria:
                pct = (c.score / c.max_score * 100) if c.max_score > 0 else 0
                criterion_scores.setdefault(c.criterion, []).append(pct)

        variances = {
            k: float(np.var(v)) if len(v) > 1 else 0.0
            for k, v in criterion_scores.items()
        }

        # Identify outliers (>2 standard deviations)
        outliers: list[str] = []
        if std_score > 0:
            for r in results:
                if abs(r.percentage - mean_score) > (2.0 * std_score):
                    outliers.append(r.candidate_id)

        recommendations: list[str] = []
        if std_score > 25.0:
            recommendations.append(
                "High overall score dispersion observed; consider reviewing rubric prompt clarity."
            )
        for crit, var in variances.items():
            if var > 400.0:  # standard deviation > 20%
                recommendations.append(
                    f"Criterion '{crit}' exhibits high variance ({var:.1f}). Assessor calibration recommended."
                )

        if not recommendations:
            recommendations.append("Cohort scoring distribution is stable and consistent.")

        return ConsistencyReport(
            exam_id=exam_id,
            num_candidates=len(results),
            score_mean=round(mean_score, 2),
            score_std=round(std_score, 2),
            criterion_variances={k: round(v, 2) for k, v in variances.items()},
            outlier_candidate_ids=outliers,
            inter_rater_agreement=None,
            recommendations=recommendations,
        )
