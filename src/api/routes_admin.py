"""Governance, telemetry, cost, and administration endpoints."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends

from src.api.dependencies import AppState, get_state
from src.governance.audit_logger import AuditEntry
from src.governance.cost_tracker import BudgetStatus, UsageSummary

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["Governance & Administration"])


@router.get(
    "/usage",
    response_model=UsageSummary,
    summary="Get token usage, costs, and model consumption breakdown",
)
async def get_usage_summary(
    period: str = "daily", state: AppState = Depends(get_state)
) -> UsageSummary:
    """Retrieve cost and token consumption analytics."""
    return state.cost_tracker.get_usage_summary(period=period)


@router.get(
    "/budget",
    response_model=BudgetStatus,
    summary="Get monthly spending budget alert status",
)
async def get_budget_status(state: AppState = Depends(get_state)) -> BudgetStatus:
    """Check budget utilization thresholds."""
    return state.cost_tracker.check_budget()


@router.get(
    "/audit-trail/{resource_id}",
    response_model=list[AuditEntry],
    summary="Retrieve immutable audit log for an assessment or exam",
)
async def get_audit_trail(
    resource_id: str, state: AppState = Depends(get_state)
) -> list[AuditEntry]:
    """Fetch complete immutable audit history."""
    return await state.audit_logger.get_audit_trail(resource_id)


@router.get(
    "/prompts",
    summary="List active prompt templates and versions",
)
async def list_prompts() -> dict[str, Any]:
    """Retrieve all versioned prompt templates registered in the system."""
    return {
        "active_prompts": [
            {"module": "content_generation", "template": "v1_passage_gen.yaml", "status": "active"},
            {"module": "scoring", "template": "v1_meaning_units.yaml", "status": "active"},
            {"module": "scoring", "template": "v1_scoring_rubric.yaml", "status": "active"},
        ]
    }
