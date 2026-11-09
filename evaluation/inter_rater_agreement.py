"""Inter-rater agreement on percentage scores (Cohen's kappa & ICC(2,1)).

Thin wrapper over the validated implementations in
:mod:`src.hitl.calibration` so that the platform and offline evaluation
report identical statistics.

Usage:  python -m evaluation.inter_rater_agreement
"""

from __future__ import annotations

import numpy as np

from src.hitl.calibration import cohens_kappa, icc_2_1


def compute_cohens_kappa(rater1: list[float], rater2: list[float], num_bins: int = 5) -> float:
    """Cohen's kappa after binning 0–100 percentage scores into ``num_bins`` equal bands."""
    if len(rater1) != len(rater2) or len(rater1) < 2:
        raise ValueError("Rater score lists must have equal length >= 2.")

    def band(v: float) -> int:
        return min(num_bins - 1, max(0, int(v * num_bins // 100)))

    kappa = cohens_kappa([band(v) for v in rater1], [band(v) for v in rater2], num_bins)
    assert kappa is not None
    return kappa


def compute_icc(rater1: list[float], rater2: list[float]) -> float:
    """ICC(2,1) — two-way random effects, absolute agreement, single rater."""
    if len(rater1) != len(rater2) or len(rater1) < 2:
        raise ValueError("Rater score lists must have equal length >= 2.")
    icc = icc_2_1(np.c_[rater1, rater2])
    return float("nan") if icc is None else icc


if __name__ == "__main__":
    ai_scores = [95.0, 82.0, 60.0, 94.0, 35.0]
    expert_scores = [96.0, 80.0, 63.0, 95.0, 38.0]
    print(f"Cohen's Kappa (5 bands): {compute_cohens_kappa(ai_scores, expert_scores):.3f}")
    print(f"ICC(2,1):                {compute_icc(ai_scores, expert_scores):.3f}")
