"""Tests for governance, privacy, and guardrails."""

from __future__ import annotations

from src.governance.cost_tracker import CostTracker
from src.governance.privacy import PrivacyGuard


def test_cost_tracker_calculation():
    tracker = CostTracker()
    tracker.record_usage(
        model="gpt-4o",
        input_tokens=1000,
        output_tokens=500,
        operation="written_scoring",
        request_id="req-1",
    )
    summary = tracker.get_usage_summary()
    assert summary.total_input_tokens == 1000
    assert summary.total_output_tokens == 500
    assert summary.estimated_cost_usd > 0.0


def test_privacy_guard():
    guard = PrivacyGuard()
    sample = "Candidate John Doe at john.doe@example.com submitted translation."
    redacted = guard.redact(sample)
    assert "john.doe@example.com" not in redacted or "<REDACTED" in redacted


def test_cost_is_priced_by_served_model_not_deployment_name() -> None:
    tracker = CostTracker()
    # Deployment names are model-agnostic ("chat"); the API reports versioned models.
    tracker.record_usage("gpt-5.1-2025-11-13", 1_000, 1_000, "written_scoring", "r1")
    tracker.record_usage("gpt-4o-mini-2024-07-18", 1_000, 0, "x", "r2")
    records = tracker._records
    assert records[0].estimated_cost_usd == 0.00125 + 0.01
    assert records[1].estimated_cost_usd == 0.00015  # longest prefix: gpt-4o-mini, not gpt-4o
    tracker.record_usage("chat", 1_000, 1_000, "x", "r3")  # unknown → 0, logged
    assert records[2].estimated_cost_usd == 0.0
