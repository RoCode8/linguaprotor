"""Human-in-the-loop review engine.

Manages the queue of assessments requiring expert human oversight,
routing borderline and low-confidence scores with priority triage.

Triage policy (docs/scoring_methodology.md §3):

=====================  ===========================================
AI confidence          Action
=====================  ===========================================
``>= auto_approve``    Auto-approved, unless flagged or randomly
                       sampled (``audit_sample_rate``) for audit
``>= confidence``      Standard review (LOW, or MEDIUM if flagged)
``>= mandatory``       Priority review (HIGH)
``<  mandatory``       Dual-assessor panel (CRITICAL, 2 approvals)
=====================  ===========================================
"""

from __future__ import annotations

import logging
import random
import uuid
from datetime import UTC, datetime

from src.config import AppSettings, get_settings
from src.governance.audit_logger import AuditLogger
from src.hitl.calibration import CalibrationService
from src.hitl.schemas import CalibrationDatapoint, ReviewItem, ReviewPriority, ReviewStatus

logger = logging.getLogger(__name__)

_PRIORITY_ORDER = {
    ReviewPriority.CRITICAL: 0,
    ReviewPriority.HIGH: 1,
    ReviewPriority.MEDIUM: 2,
    ReviewPriority.LOW: 3,
}


class ReviewWorkflowError(ValueError):
    """Raised for an invalid review state transition (maps to HTTP 409)."""


class ReviewEngine:
    """Manages the lifecycle and routing of assessments requiring human review."""

    def __init__(
        self,
        settings: AppSettings | None = None,
        audit_logger: AuditLogger | None = None,
        calibration: CalibrationService | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.settings = settings or get_settings().app
        self.audit_logger = audit_logger or AuditLogger()
        self.calibration = calibration or CalibrationService()
        self._rng = rng or random.Random()
        self._items: dict[str, ReviewItem] = {}

    # -- triage ---------------------------------------------------------------

    def _determine_priority(
        self, confidence: float, flags: list[str]
    ) -> tuple[ReviewPriority, ReviewStatus, int, bool]:
        """Return (priority, status, required_approvals, sampled_for_audit)."""
        s = self.settings
        if confidence < s.mandatory_review_threshold:
            return ReviewPriority.CRITICAL, ReviewStatus.PENDING, 2, False
        if confidence < s.scoring_confidence_threshold:
            return ReviewPriority.HIGH, ReviewStatus.PENDING, 1, False
        if flags:
            return ReviewPriority.MEDIUM, ReviewStatus.PENDING, 1, False
        if confidence >= s.auto_approve_threshold:
            if self._rng.random() < s.audit_sample_rate:
                return ReviewPriority.LOW, ReviewStatus.PENDING, 1, True
            return ReviewPriority.LOW, ReviewStatus.APPROVED, 1, False
        return ReviewPriority.LOW, ReviewStatus.PENDING, 1, False

    async def create_review_item(
        self,
        assessment_id: str,
        assessment_type: str,
        candidate_id: str,
        exam_id: str,
        ai_score: float,
        ai_confidence: float,
        flags: list[str],
        ai_criterion_scores: dict[str, float] | None = None,
        criterion_max: float = 5.0,
    ) -> ReviewItem:
        """Triage a freshly scored assessment into the review queue."""
        priority, status, required, sampled = self._determine_priority(ai_confidence, flags)
        now = datetime.now(UTC)
        item_flags = list(flags) + (["random_audit_sample"] if sampled else [])

        item = ReviewItem(
            id=str(uuid.uuid4()),
            assessment_id=assessment_id,
            assessment_type=assessment_type,
            candidate_id=candidate_id,
            exam_id=exam_id,
            ai_score=ai_score,
            ai_confidence=ai_confidence,
            priority=priority,
            status=status,
            flags=item_flags,
            created_at=now,
            reviewed_at=now if status == ReviewStatus.APPROVED else None,
            ai_criterion_scores=dict(ai_criterion_scores or {}),
            criterion_max=criterion_max,
            required_approvals=required,
            final_score=ai_score if status == ReviewStatus.APPROVED else None,
        )
        self._items[item.id] = item

        await self.audit_logger.log_event(
            event_type="review_queued",
            actor="system",
            resource_id=assessment_id,
            details={
                "review_item_id": item.id,
                "priority": priority.value,
                "status": status.value,
                "required_approvals": required,
                "ai_confidence": ai_confidence,
                "flags": item_flags,
            },
        )
        return item

    # -- queries ----------------------------------------------------------------

    def get_item(self, item_id: str) -> ReviewItem:
        if item_id not in self._items:
            raise KeyError(f"Review item {item_id} not found.")
        return self._items[item_id]

    def find_by_assessment(self, assessment_id: str) -> ReviewItem | None:
        return next((i for i in self._items.values() if i.assessment_id == assessment_id), None)

    async def get_review_queue(
        self,
        status: ReviewStatus | None = None,
        priority: ReviewPriority | None = None,
    ) -> list[ReviewItem]:
        """Fetch filtered review items: most urgent first, oldest first within a tier."""
        items = list(self._items.values())
        if status:
            items = [i for i in items if i.status == status]
        if priority:
            items = [i for i in items if i.priority == priority]
        return sorted(items, key=lambda x: (_PRIORITY_ORDER.get(x.priority, 99), x.created_at))

    # -- transitions ------------------------------------------------------------

    def _require_open(self, item: ReviewItem) -> None:
        if item.is_closed:
            raise ReviewWorkflowError(
                f"Review item {item.id} is already {item.status.value}; no further changes allowed."
            )

    async def assign_reviewer(self, item_id: str, reviewer_id: str) -> ReviewItem:
        """Assign a human assessor to a review item."""
        item = self.get_item(item_id)
        self._require_open(item)
        item.assigned_reviewer = reviewer_id
        item.status = ReviewStatus.IN_REVIEW
        await self.audit_logger.log_event(
            event_type="review_assigned",
            actor=reviewer_id,
            resource_id=item.assessment_id,
            details={"review_item_id": item.id},
        )
        return item

    async def approve(self, item_id: str, reviewer_id: str) -> ReviewItem:
        """Record an assessor's confirmation of the AI score.

        Items requiring dual review stay ``in_review`` until a *second,
        distinct* assessor approves.
        """
        item = self.get_item(item_id)
        self._require_open(item)
        if reviewer_id in item.approvals:
            raise ReviewWorkflowError(
                f"Reviewer {reviewer_id} has already approved item {item.id}; "
                "a different assessor must provide the second approval."
            )
        item.approvals.append(reviewer_id)
        item.assigned_reviewer = reviewer_id

        released = len(item.approvals) >= item.required_approvals
        if released:
            item.status = ReviewStatus.APPROVED
            item.reviewed_at = datetime.now(UTC)
            item.final_score = item.ai_score
            if item.ai_criterion_scores:
                await self.calibration.add_calibration_datapoint(
                    CalibrationDatapoint(
                        exam_id=item.exam_id,
                        candidate_id=item.candidate_id,
                        ai_scores=item.ai_criterion_scores,
                        human_scores=dict(item.ai_criterion_scores),
                        agreement_level="strong_agree",
                        is_override=False,
                    )
                )
        else:
            item.status = ReviewStatus.IN_REVIEW

        await self.audit_logger.log_event(
            event_type="review_approved",
            actor=reviewer_id,
            resource_id=item.assessment_id,
            details={
                "review_item_id": item.id,
                "approvals": list(item.approvals),
                "required_approvals": item.required_approvals,
                "released": released,
            },
        )
        return item

    async def escalate(self, item_id: str, actor: str, reason: str) -> ReviewItem:
        """Escalate an item to the senior panel (requires dual sign-off afterwards)."""
        item = self.get_item(item_id)
        self._require_open(item)
        item.status = ReviewStatus.ESCALATED
        item.priority = ReviewPriority.CRITICAL
        item.required_approvals = max(item.required_approvals, 2)
        item.flags.append(f"escalated: {reason}")
        await self.audit_logger.log_event(
            event_type="review_escalated",
            actor=actor,
            resource_id=item.assessment_id,
            details={"review_item_id": item.id, "reason": reason},
        )
        return item
