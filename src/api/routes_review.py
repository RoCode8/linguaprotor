"""Human-in-the-loop (HITL) review and calibration API endpoints."""

from __future__ import annotations

import logging
from collections.abc import Awaitable
from typing import TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from src.api.dependencies import AppState, get_state
from src.hitl.review_engine import ReviewWorkflowError
from src.hitl.schemas import (
    CalibrationReport,
    ReviewItem,
    ReviewPriority,
    ReviewStatus,
    ScoreOverride,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/review", tags=["Human-in-the-Loop & Calibration"])

T = TypeVar("T")


class OverrideRequest(BaseModel):
    reviewer_id: str = Field(min_length=1)
    overridden_scores: dict[str, float] = Field(
        description="Per-criterion human scores; omitted criteria keep the AI score."
    )
    justification: str
    override_type: str = "criterion_adjustment"


class ReviewerRequest(BaseModel):
    reviewer_id: str = Field(min_length=1)


class EscalateRequest(BaseModel):
    reviewer_id: str = Field(min_length=1)
    reason: str = Field(min_length=3)


async def _guard(item_id: str, op: Awaitable[T]) -> T:
    """Translate domain errors into HTTP status codes."""
    try:
        return await op
    except KeyError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Review item {item_id} not found.") from e
    except ReviewWorkflowError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e


@router.get(
    "/queue",
    response_model=list[ReviewItem],
    summary="Fetch review items prioritized by uncertainty and flags",
)
async def get_review_queue(
    status_filter: ReviewStatus | None = Query(None, alias="status"),
    priority: ReviewPriority | None = Query(None),
    state: AppState = Depends(get_state),
) -> list[ReviewItem]:
    """Retrieve filtered review queue sorted by urgency."""
    return await state.review_engine.get_review_queue(status=status_filter, priority=priority)


@router.get(
    "/calibration/report",
    response_model=CalibrationReport,
    summary="Compute Cohen's Kappa, ICC(2,1), and assessor agreement metrics",
)
async def get_calibration_report(state: AppState = Depends(get_state)) -> CalibrationReport:
    """Generate statistical alignment report between AI and human assessors."""
    return await state.calibration.generate_calibration_report()


@router.get(
    "/overrides",
    response_model=list[ScoreOverride],
    summary="List score overrides (optionally by reviewer)",
)
async def list_overrides(
    reviewer_id: str | None = None, state: AppState = Depends(get_state)
) -> list[ScoreOverride]:
    return await state.override_handler.get_overrides(reviewer_id)


@router.get("/{item_id}", response_model=ReviewItem, summary="Get a single review item")
async def get_review_item(item_id: str, state: AppState = Depends(get_state)) -> ReviewItem:
    try:
        return state.review_engine.get_item(item_id)
    except KeyError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Review item {item_id} not found.") from e


@router.post(
    "/{item_id}/assign",
    response_model=ReviewItem,
    summary="Assign reviewer to a pending assessment",
)
async def assign_reviewer(
    item_id: str, payload: ReviewerRequest, state: AppState = Depends(get_state)
) -> ReviewItem:
    return await _guard(item_id, state.review_engine.assign_reviewer(item_id, payload.reviewer_id))


@router.post(
    "/{item_id}/approve",
    response_model=ReviewItem,
    summary="Assessor confirms the AI score (dual-review items need two distinct assessors)",
)
async def approve_item(
    item_id: str, payload: ReviewerRequest, state: AppState = Depends(get_state)
) -> ReviewItem:
    return await _guard(item_id, state.review_engine.approve(item_id, payload.reviewer_id))


@router.post(
    "/{item_id}/escalate",
    response_model=ReviewItem,
    summary="Escalate to the senior panel (requires dual sign-off)",
)
async def escalate_item(
    item_id: str, payload: EscalateRequest, state: AppState = Depends(get_state)
) -> ReviewItem:
    return await _guard(
        item_id, state.review_engine.escalate(item_id, payload.reviewer_id, payload.reason)
    )


@router.post(
    "/{item_id}/override",
    response_model=ScoreOverride,
    summary="Apply human score override with mandatory justification",
)
async def override_score(
    item_id: str, payload: OverrideRequest, state: AppState = Depends(get_state)
) -> ScoreOverride:
    return await _guard(
        item_id,
        state.override_handler.apply_override(
            review_item_id=item_id,
            reviewer_id=payload.reviewer_id,
            overridden_scores=payload.overridden_scores,
            justification=payload.justification,
            override_type=payload.override_type,
        ),
    )
