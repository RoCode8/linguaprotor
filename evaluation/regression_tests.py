"""Automated regression testing against expert ground-truth translation scores.

Ensures LLM rubric / prompt / model updates do not introduce score drift or
degrade agreement with bilingual subject-matter experts.

Exit codes: 0 = passed, 1 = failed (agreement below threshold or any item
could not be scored), 2 = skipped (Azure OpenAI not configured).  A skipped
run is never reported as passed.

Usage:  python -m evaluation.regression_tests
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

import numpy as np

from src.config import get_settings
from src.hitl.calibration import cohens_kappa, icc_2_1
from src.scoring.schemas import GlossaryTerm, ScoringRequest
from src.scoring.written_scorer import WrittenTranslationScorer

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("regression_tests")

DATASET_PATH = Path(__file__).parent / "datasets" / "expert_scored_translations.json"

# Acceptance thresholds (docs/problem_statement.md, AC-7)
MAX_MAE_PCT = 12.0
MIN_PEARSON = 0.85
MIN_ICC = 0.75


def _percent_band(pct: float, bands: int = 5) -> int:
    return min(bands - 1, max(0, int(pct * bands // 100)))


async def run_regression_suite(scorer: WrittenTranslationScorer | None = None) -> dict[str, object]:
    """Score every expert-labelled sample and compare with expert totals."""
    samples = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    logger.info("Loaded %d expert-scored translation test cases.", len(samples))
    scorer = scorer or WrittenTranslationScorer()

    ai: list[float] = []
    expert: list[float] = []
    failures: list[str] = []

    for item in samples:
        glossary = [
            GlossaryTerm(source_term=src, target_term=tgt)
            for src, tgt in (item.get("expected_verdicts") or {}).items()
        ]
        req = ScoringRequest(
            exam_id="regression_suite",
            candidate_id=item["id"],
            source_text=item["source_text"],
            candidate_translation=item["candidate_translation"],
            terminology_glossary=glossary,
        )
        expert_pct = sum(item["expert_scores"].values()) / 20.0 * 100.0
        try:
            res = await scorer.score(req, domain=item.get("domain", "legal"))
        except Exception as exc:  # an unscorable item is a regression, not a pass
            logger.error("Item %s could not be scored: %s", item["id"], exc)
            failures.append(item["id"])
            continue

        ai.append(res.percentage)
        expert.append(expert_pct)
        logger.info(
            "%s [%s]: AI=%.1f%% Expert=%.1f%% (diff %+.1f) confidence=%.2f",
            item["id"], item.get("category", "-"), res.percentage, expert_pct,
            res.percentage - expert_pct, res.confidence,
        )

    report: dict[str, object] = {"n_scored": len(ai), "failed_items": failures}
    if len(ai) >= 2:
        a, e = np.array(ai), np.array(expert)
        report["mae_pct"] = round(float(np.mean(np.abs(a - e))), 2)
        report["pearson_r"] = round(float(np.corrcoef(a, e)[0, 1]), 3) if a.std() and e.std() else None
        icc = icc_2_1(np.c_[a, e])
        report["icc_2_1"] = round(icc, 3) if icc is not None else None
        kappa = cohens_kappa([_percent_band(v) for v in ai], [_percent_band(v) for v in expert], 5)
        report["cohens_kappa_bands"] = round(kappa, 3) if kappa is not None else None

    report["passed"] = bool(
        not failures
        and len(ai) >= 2
        and report["mae_pct"] <= MAX_MAE_PCT  # type: ignore[operator]
        and (report["pearson_r"] or 0) >= MIN_PEARSON  # type: ignore[operator]
        and (report["icc_2_1"] or 0) >= MIN_ICC  # type: ignore[operator]
    )
    logger.info("=== Regression summary: %s", json.dumps(report))
    return report


def main() -> int:
    if not get_settings().azure_openai.is_configured:
        logger.warning("SKIPPED: Azure OpenAI is not configured; regression suite not run.")
        return 2
    report = asyncio.run(run_regression_suite())
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
