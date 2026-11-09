"""Calibration service for human-assessor and AI score alignment.

Computes statistical agreement metrics (Cohen's Kappa, ICC, MAE)
and identifies systematic scoring divergence across assessment criteria.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

import numpy as np

from src.hitl.schemas import CalibrationDatapoint, CalibrationReport

logger = logging.getLogger(__name__)


def cohens_kappa(
    rater_a: list[int], rater_b: list[int], num_categories: int
) -> float | None:
    """Unweighted Cohen's kappa for two raters over integer categories ``0..k-1``."""
    n = len(rater_a)
    if n < 2 or n != len(rater_b):
        return None
    matrix = np.zeros((num_categories, num_categories))
    for a, b in zip(rater_a, rater_b, strict=True):
        matrix[a, b] += 1
    po = np.trace(matrix) / n
    pe = float(np.sum(matrix.sum(axis=1) * matrix.sum(axis=0))) / (n * n)
    if pe == 1.0:
        return 1.0
    return float((po - pe) / (1.0 - pe))


def icc_2_1(ratings: np.ndarray | list[list[float]]) -> float | None:
    """ICC(2,1): two-way random effects, absolute agreement, single rater.

    ``ratings`` has shape ``(n_targets, k_raters)``.  Shrout & Fleiss (1979):

        ICC(2,1) = (MSR - MSE) / (MSR + (k-1)·MSE + k·(MSC - MSE)/n)

    Unlike ICC(1,1), a systematic offset between raters (e.g. the AI scoring
    every script 1 point higher) *lowers* the coefficient.
    """
    data = np.asarray(ratings, dtype=np.float64)
    if data.ndim != 2 or data.shape[0] < 2 or data.shape[1] < 2:
        return None
    n, k = data.shape
    grand = data.mean()
    ss_rows = k * np.sum((data.mean(axis=1) - grand) ** 2)
    ss_cols = n * np.sum((data.mean(axis=0) - grand) ** 2)
    ss_total = np.sum((data - grand) ** 2)
    ss_err = ss_total - ss_rows - ss_cols

    msr = ss_rows / (n - 1)
    msc = ss_cols / (k - 1)
    mse = ss_err / ((n - 1) * (k - 1))
    denom = msr + (k - 1) * mse + k * (msc - mse) / n
    if denom == 0:
        return 1.0 if ss_total == 0 else None
    return float(np.clip((msr - mse) / denom, -1.0, 1.0))


class CalibrationService:
    """Calculates inter-rater reliability between AI and human assessors."""

    def __init__(self) -> None:
        self._datapoints: list[CalibrationDatapoint] = []

    async def add_calibration_datapoint(
        self, datapoint: CalibrationDatapoint
    ) -> None:
        """Register an AI vs Human comparative scoring result."""
        self._datapoints.append(datapoint)

    async def generate_calibration_report(
        self,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
    ) -> CalibrationReport:
        """Generate comprehensive statistical calibration report."""
        now = datetime.now(UTC)
        start = period_start or datetime(2025, 1, 1, tzinfo=UTC)
        end = period_end or now

        total = len(self._datapoints)
        if total == 0:
            return CalibrationReport(
                report_id=str(uuid.uuid4()),
                period_start=start,
                period_end=end,
                total_assessments=0,
                human_reviewed=0,
                overrides=0,
                override_rate=0.0,
                mean_ai_human_difference={},
                cohens_kappa=None,
                icc=None,
                recommendations=["No calibration datapoints collected yet."],
                created_at=now,
            )

        # Compute criterion-level differences
        differences: dict[str, list[float]] = {}
        all_ai: list[float] = []
        all_human: list[float] = []

        for dp in self._datapoints:
            for crit, ai_val in dp.ai_scores.items():
                if crit in dp.human_scores:
                    hum_val = dp.human_scores[crit]
                    diff = ai_val - hum_val
                    differences.setdefault(crit, []).append(diff)
                    all_ai.append(ai_val)
                    all_human.append(hum_val)

        mean_diffs = {
            k: float(np.mean(v)) for k, v in differences.items()
        }

        # Calculate Cohen's Kappa (discretized into 5 bands)
        kappa = self._compute_cohens_kappa(all_ai, all_human)

        # Calculate ICC (two-way random effects, absolute agreement)
        icc_val = self._compute_icc(all_ai, all_human)

        # Generate recommendations based on bias
        recommendations = self._identify_recommendations(mean_diffs, kappa)

        overrides = sum(1 for dp in self._datapoints if dp.is_override)
        return CalibrationReport(
            report_id=str(uuid.uuid4()),
            period_start=start,
            period_end=end,
            total_assessments=total,
            human_reviewed=total,
            overrides=overrides,
            override_rate=round(overrides / total, 3),
            mean_ai_human_difference={k: round(v, 2) for k, v in mean_diffs.items()},
            cohens_kappa=round(kappa, 3) if kappa is not None else None,
            icc=round(icc_val, 3) if icc_val is not None else None,
            recommendations=recommendations,
            created_at=now,
        )

    @staticmethod
    def _compute_cohens_kappa(
        ai: list[float], human: list[float], num_bins: int = 5
    ) -> float | None:
        """Kappa over 5 one-point bands of the 0–5 criterion scale (5.0 → top band)."""
        to_band = lambda v: min(num_bins - 1, max(0, int(v)))  # noqa: E731
        return cohens_kappa([to_band(v) for v in ai], [to_band(v) for v in human], num_bins)

    @staticmethod
    def _compute_icc(ai: list[float], human: list[float]) -> float | None:
        if len(ai) < 3:
            return None
        return icc_2_1(np.array([ai, human]).T)

    def _identify_recommendations(
        self, mean_diffs: dict[str, float], kappa: float | None
    ) -> list[str]:
        recs: list[str] = []
        if kappa is not None:
            if kappa < 0.60:
                recs.append("Overall inter-rater agreement is moderate/low (κ < 0.60). Calibration workshop needed.")
            elif kappa >= 0.80:
                recs.append("Strong inter-rater agreement achieved (κ >= 0.80). AI rubric is well-aligned with experts.")

        for crit, diff in mean_diffs.items():
            if diff > 0.4:
                recs.append(f"AI grades criterion '{crit}' systematically higher than human assessors (+{diff:.2f}).")
            elif diff < -0.4:
                recs.append(f"AI grades criterion '{crit}' systematically harsher than human assessors ({diff:.2f}).")

        if not recs:
            recs.append("AI and assessor scores are in balanced alignment across all rubric criteria.")
        return recs
