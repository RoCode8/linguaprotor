"""Written translation scoring API endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.dependencies import AppState, get_consistency_checker, get_scorer, get_state
from src.infrastructure.azure_openai_client import LLMResponseError
from src.scoring.consistency_checker import ConsistencyChecker
from src.scoring.rubric_evaluator import CRITERION_MAX
from src.scoring.schemas import ConsistencyReport, ScoringRequest, ScoringResult
from src.scoring.written_scorer import ScoringError, WrittenTranslationScorer

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/scoring", tags=["Scoring Engine"])


@router.post(
    "/written",
    response_model=ScoringResult,
    status_code=status.HTTP_200_OK,
    summary="Evaluate written translation with evidence and meaning unit alignment",
)
async def score_written_translation(
    request: ScoringRequest,
    scorer: WrittenTranslationScorer = Depends(get_scorer),
    state: AppState = Depends(get_state),
) -> ScoringResult:
    """Execute four-pillar rubric evaluation and enqueue the result for HITL triage."""
    try:
        result = await scorer.score(request)
    except (ScoringError, LLMResponseError) as e:
        logger.error("Scoring failed: %s", e)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e
    except Exception as e:
        logger.exception("Scoring pipeline error")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Scoring pipeline failed: {e}",
        ) from e

    state.written_results[result.id] = result
    await state.audit_logger.log_event(
        event_type="scoring_completed",
        actor="system",
        resource_id=result.id,
        details={
            "exam_id": result.exam_id,
            "candidate_id": result.candidate_id,
            "overall_score": result.overall_score,
            "overall_max": result.overall_max,
            "confidence": result.confidence,
            "flags": result.flags,
            "rubric_version": request.rubric_version,
        },
    )
    await state.review_engine.create_review_item(
        assessment_id=result.id,
        assessment_type="written",
        candidate_id=result.candidate_id,
        exam_id=result.exam_id,
        ai_score=result.overall_score,
        ai_confidence=result.confidence,
        flags=result.flags,
        ai_criterion_scores={c.criterion: c.score for c in result.criteria},
        criterion_max=CRITERION_MAX,
    )
    return result


@router.get(
    "/{result_id}",
    response_model=ScoringResult,
    summary="Retrieve an explainable assessment result by ID",
)
async def get_scoring_result(
    result_id: str, state: AppState = Depends(get_state)
) -> ScoringResult:
    """Fetch structured scoring packet."""
    if result_id not in state.written_results:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Result {result_id} not found.",
        )
    return state.written_results[result_id]


@router.post(
    "/cohort/consistency",
    response_model=ConsistencyReport,
    summary="Calculate variance and detect outlier scores for an examination cohort",
)
async def check_cohort_consistency(
    exam_id: str,
    checker: ConsistencyChecker = Depends(get_consistency_checker),
    state: AppState = Depends(get_state),
) -> ConsistencyReport:
    """Analyze score distributions and check for grading drift."""
    cohort = [r for r in state.written_results.values() if r.exam_id == exam_id]
    return checker.check_cohort_consistency(cohort, exam_id=exam_id)
