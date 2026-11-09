"""The offline evaluation harness must never report a pass it did not earn."""

from __future__ import annotations

from evaluation.regression_tests import run_regression_suite
from src.scoring.written_scorer import WrittenTranslationScorer
from tests.conftest import FakeLLM


async def test_regression_suite_fails_when_items_cannot_be_scored() -> None:
    broken = FakeLLM({"written_scoring.decompose": RuntimeError("endpoint down")})
    report = await run_regression_suite(WrittenTranslationScorer(broken))  # type: ignore[arg-type]
    assert report["n_scored"] == 0
    assert len(report["failed_items"]) == 5  # type: ignore[arg-type]
    assert report["passed"] is False


async def test_regression_suite_fails_on_poor_agreement() -> None:
    # A scorer that gives every script the same mark cannot track the experts.
    report = await run_regression_suite(WrittenTranslationScorer(FakeLLM()))  # type: ignore[arg-type]
    assert report["n_scored"] == 5 and not report["failed_items"]
    assert report["passed"] is False
