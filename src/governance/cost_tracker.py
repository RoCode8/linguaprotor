"""Azure OpenAI cost tracking and budget monitoring for LinguaProctor.

Records per-request token usage, computes estimated costs based on model
pricing tables, and provides budget-alert functionality.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class UsageRecord(BaseModel):
    """A single token-usage record."""

    model: str
    input_tokens: int
    output_tokens: int
    operation: str
    request_id: str
    estimated_cost_usd: float
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class UsageSummary(BaseModel):
    """Aggregated usage statistics for a time period."""

    period: str
    total_requests: int
    total_input_tokens: int
    total_output_tokens: int
    estimated_cost_usd: float
    by_model: dict[str, dict[str, Any]]
    by_operation: dict[str, dict[str, Any]]


class BudgetStatus(BaseModel):
    """Current budget utilisation and projection."""

    monthly_budget_usd: float
    spent_usd: float
    utilization_pct: float
    alert: bool
    projected_monthly_usd: float


# ---------------------------------------------------------------------------
# Pricing table (per 1 000 tokens, approximate Azure OpenAI pricing)
# ---------------------------------------------------------------------------

# Prices are indicative and should be overridden by configuration in
# production deployments.
# Keys are model-name prefixes: the API reports versioned names such as
# "gpt-5.1-2025-11-13"; the longest matching prefix wins.
_DEFAULT_PRICING: dict[str, dict[str, float]] = {
    "gpt-5.1": {"input": 0.00125, "output": 0.01},
    "gpt-4o": {"input": 0.005, "output": 0.015},
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "gpt-4": {"input": 0.03, "output": 0.06},
    "gpt-35-turbo": {"input": 0.0005, "output": 0.0015},
    "text-embedding-3-small": {"input": 0.00002, "output": 0.0},
    "text-embedding-3-large": {"input": 0.00013, "output": 0.0},
}

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class CostTrackerError(Exception):
    """Base exception for cost-tracker operations."""


# ---------------------------------------------------------------------------
# Tracker
# ---------------------------------------------------------------------------


class CostTracker:
    """In-process cost tracker for Azure OpenAI usage.

    Stores usage records in memory.  In production, swap the backing store
    for Cosmos DB or Azure Monitor custom metrics.

    Parameters
    ----------
    monthly_budget_usd:
        Monthly spend cap (USD).  Used by :meth:`check_budget`.
    pricing:
        Per-model pricing overrides.  Falls back to built-in defaults.
    """

    def __init__(
        self,
        monthly_budget_usd: float = 500.0,
        pricing: dict[str, dict[str, float]] | None = None,
    ) -> None:
        self._monthly_budget = monthly_budget_usd
        self._pricing = pricing or _DEFAULT_PRICING
        self._records: list[UsageRecord] = []
        logger.info(
            "CostTracker initialised, monthly_budget=%.2f USD",
            self._monthly_budget,
        )

    # -- internal helpers ----------------------------------------------------

    def _estimate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """Estimate the cost of a request in USD."""
        name = model.lower()
        matches = [key for key in self._pricing if name.startswith(key)]
        if not matches:
            logger.warning("No pricing data for model '%s'; cost set to 0.0", model)
            return 0.0
        pricing = self._pricing[max(matches, key=len)]

        input_cost = (input_tokens / 1_000) * pricing.get("input", 0.0)
        output_cost = (output_tokens / 1_000) * pricing.get("output", 0.0)
        return round(input_cost + output_cost, 8)

    # -- public API ----------------------------------------------------------

    def record_usage(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        operation: str,
        request_id: str,
    ) -> None:
        """Record a single API request's token usage.

        Parameters
        ----------
        model:
            The model deployment name (e.g. ``"gpt-4o"``).
        input_tokens:
            Number of prompt (input) tokens consumed.
        output_tokens:
            Number of completion (output) tokens consumed.
        operation:
            Logical operation label (e.g. ``"scoring"``).
        request_id:
            Unique request identifier for traceability.
        """
        cost = self._estimate_cost(model, input_tokens, output_tokens)
        record = UsageRecord(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            operation=operation,
            request_id=request_id,
            estimated_cost_usd=cost,
        )
        self._records.append(record)
        logger.debug(
            "Usage recorded: model=%s op=%s tokens=%d/%d cost=$%.6f",
            model,
            operation,
            input_tokens,
            output_tokens,
            cost,
        )

    def get_usage_summary(self, period: str = "daily") -> UsageSummary:
        """Compute an aggregated usage summary.

        Parameters
        ----------
        period:
            Aggregation period label.  Currently used as metadata; the
            summary always covers all stored records.  In production,
            records should be pre-filtered by the caller.

        Returns
        -------
        UsageSummary
        """
        now = datetime.now(UTC)

        # Filter records by period
        if period == "daily":
            filtered = [
                r for r in self._records
                if r.timestamp.date() == now.date()
            ]
        elif period == "monthly":
            filtered = [
                r for r in self._records
                if r.timestamp.year == now.year and r.timestamp.month == now.month
            ]
        else:
            filtered = list(self._records)

        total_input = sum(r.input_tokens for r in filtered)
        total_output = sum(r.output_tokens for r in filtered)
        total_cost = sum(r.estimated_cost_usd for r in filtered)

        by_model: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"requests": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
        )
        by_operation: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"requests": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
        )

        for r in filtered:
            by_model[r.model]["requests"] += 1
            by_model[r.model]["input_tokens"] += r.input_tokens
            by_model[r.model]["output_tokens"] += r.output_tokens
            by_model[r.model]["cost_usd"] += r.estimated_cost_usd

            by_operation[r.operation]["requests"] += 1
            by_operation[r.operation]["input_tokens"] += r.input_tokens
            by_operation[r.operation]["output_tokens"] += r.output_tokens
            by_operation[r.operation]["cost_usd"] += r.estimated_cost_usd

        return UsageSummary(
            period=period,
            total_requests=len(filtered),
            total_input_tokens=total_input,
            total_output_tokens=total_output,
            estimated_cost_usd=round(total_cost, 6),
            by_model=dict(by_model),
            by_operation=dict(by_operation),
        )

    def check_budget(self, alert_threshold: float = 0.8) -> BudgetStatus:
        """Check current budget utilisation and project monthly spend.

        Parameters
        ----------
        alert_threshold:
            Fraction of the monthly budget at which an alert is raised.
            Defaults to ``0.8`` (80 %).

        Returns
        -------
        BudgetStatus
        """
        now = datetime.now(UTC)
        month_records = [
            r for r in self._records
            if r.timestamp.year == now.year and r.timestamp.month == now.month
        ]
        spent = sum(r.estimated_cost_usd for r in month_records)

        # Project monthly total based on days elapsed
        day_of_month = now.day
        days_in_month = 30  # approximation
        projected = spent / day_of_month * days_in_month if day_of_month > 0 else 0.0

        utilization = (spent / self._monthly_budget) if self._monthly_budget > 0 else 0.0
        alert = utilization >= alert_threshold

        if alert:
            logger.warning(
                "Budget alert! Spent $%.4f of $%.2f (%.1f%%)",
                spent,
                self._monthly_budget,
                utilization * 100,
            )

        return BudgetStatus(
            monthly_budget_usd=self._monthly_budget,
            spent_usd=round(spent, 6),
            utilization_pct=round(utilization * 100, 2),
            alert=alert,
            projected_monthly_usd=round(projected, 4),
        )
