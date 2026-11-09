"""Content generation endpoints for examinations."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.dependencies import AppState, get_generator, get_state, get_validator
from src.content_generation.generator import ContentGenerator
from src.content_generation.schemas import (
    ComprehensiveValidationResult,
    ContentGenerationRequest,
    ContentReviewFeedback,
    GeneratedPassage,
)
from src.content_generation.validator import ContentValidator

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/content", tags=["Content Generation"])



@router.post(
    "/generate",
    response_model=list[GeneratedPassage],
    status_code=status.HTTP_201_CREATED,
    summary="Generate examination passages with model answers and glossaries",
)
async def generate_passages(
    request: ContentGenerationRequest,
    generator: ContentGenerator = Depends(get_generator),
    state: AppState = Depends(get_state),
) -> list[GeneratedPassage]:
    """Generate domain-specific, CEFR-calibrated translation passages."""
    try:
        passages = await generator.generate_passages(request)
        for p in passages:
            state.passages[p.id] = p
            await state.audit_logger.log_event(
                event_type="content_generated",
                actor="system",
                resource_id=p.id,
                details={"domain": p.domain.value, "difficulty": p.difficulty.value},
            )
        return passages
    except Exception as e:
        logger.error("Passage generation failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Content generation failed: {str(e)}",
        ) from e


@router.get(
    "/{passage_id}",
    response_model=GeneratedPassage,
    summary="Retrieve a generated examination passage",
)
async def get_passage(passage_id: str, state: AppState = Depends(get_state)) -> GeneratedPassage:
    """Fetch passage by unique identifier."""
    if passage_id not in state.passages:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Passage {passage_id} not found.",
        )
    return state.passages[passage_id]


@router.post(
    "/{passage_id}/validate",
    response_model=ComprehensiveValidationResult,
    summary="Validate difficulty, terminology, and duplication for a passage",
)
async def validate_passage(
    passage_id: str,
    validator: ContentValidator = Depends(get_validator),
    state: AppState = Depends(get_state),
) -> ComprehensiveValidationResult:
    """Run automated quality validation checks on a generated passage."""
    if passage_id not in state.passages:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Passage {passage_id} not found.",
        )
    passage = state.passages[passage_id]
    return await validator.validate_all(passage)


@router.post(
    "/{passage_id}/review",
    response_model=GeneratedPassage,
    summary="Submit expert proofreading decision (approve/reject/revise)",
)
async def review_passage(
    passage_id: str,
    feedback: ContentReviewFeedback,
    state: AppState = Depends(get_state),
) -> GeneratedPassage:
    """Record expert reviewer decision on examination content."""
    if passage_id not in state.passages:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Passage {passage_id} not found.",
        )
    passage = state.passages[passage_id]
    passage.review_status = feedback.decision
    if feedback.decision in ("approved", "rejected"):
        await state.audit_logger.log_event(
            event_type=f"content_{feedback.decision}",
            actor=feedback.reviewer_id,
            resource_id=passage_id,
            details={"comments": feedback.comments},
        )
    if feedback.suggested_edits.get("source_text"):
        passage.source_text = feedback.suggested_edits["source_text"]
        passage.version += 1
    return passage


@router.get(
    "/queue/pending",
    response_model=list[GeneratedPassage],
    summary="List passages awaiting expert proofreading",
)
async def list_pending_passages(state: AppState = Depends(get_state)) -> list[GeneratedPassage]:
    """Retrieve all passages awaiting human expert proofreading."""
    return [
        p for p in state.passages.values() if p.review_status == "pending_expert_review"
    ]
