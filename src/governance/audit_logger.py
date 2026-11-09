"""Immutable audit-trail logger for LinguaProctor.

Records all significant platform events (scoring, reviews, overrides, etc.)
into Azure Cosmos DB.  Entries are append-only — updates and deletions are
intentionally unsupported to guarantee an immutable audit trail.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class AuditEntry(BaseModel):
    """A single immutable audit-trail record."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str
    actor: str  # user ID or "system"
    resource_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    ip_address: str | None = None


# ---------------------------------------------------------------------------
# Valid event types
# ---------------------------------------------------------------------------

VALID_EVENT_TYPES = frozenset(
    {
        "scoring_completed",
        "override_applied",
        "content_approved",
        "content_rejected",
        "exam_generated",
        "review_assigned",
        "review_escalated",
        "content_generated",
        "audio_generated",
        "calibration_completed",
        "oral_scoring_completed",
        "review_queued",
        "review_approved",
    }
)


class InMemoryAuditContainer:
    """Append-only in-process stand-in for the Cosmos ``audit`` container.

    Supports exactly the subset of the ``ContainerProxy`` async API that
    :class:`AuditLogger` uses.  Used when Cosmos DB is not provisioned
    (local development, tests, demos).  Records cannot be updated or deleted.
    """

    def __init__(self) -> None:
        self._items: list[dict[str, Any]] = []

    async def create_item(self, body: dict[str, Any]) -> dict[str, Any]:
        self._items.append(dict(body))
        return body

    def query_items(
        self,
        query: str,
        parameters: list[dict[str, Any]] | None = None,
        **_: Any,
    ) -> Any:
        params = {p["name"]: p["value"] for p in parameters or []}
        field_for = {"@rid": "resource_id", "@etype": "event_type", "@actor": "actor"}

        def matches(item: dict[str, Any]) -> bool:
            for name, value in params.items():
                if name in field_for and item.get(field_for[name]) != value:
                    return False
                if name == "@start" and item["timestamp"] < value:
                    return False
                if name == "@end" and item["timestamp"] > value:
                    return False
            return True

        selected = sorted(
            (i for i in self._items if matches(i)), key=lambda i: i["timestamp"]
        )

        async def _iter() -> Any:
            for item in selected:
                yield {k: v for k, v in item.items() if k != "partitionKey"}

        return _iter()

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AuditLoggerError(Exception):
    """Base exception for audit-logger operations."""


class InvalidEventTypeError(AuditLoggerError):
    """Raised when an unrecognised event type is supplied."""


# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------


class AuditLogger:
    """Append-only audit logger backed by Azure Cosmos DB.

    Parameters
    ----------
    cosmos_client:
        An initialised Cosmos DB container client (or compatible mock)
        that exposes ``create_item``, ``read_item``, and ``query_items``
        async methods.
    """

    def __init__(self, cosmos_client: Any | None = None) -> None:
        self._container = cosmos_client if cosmos_client is not None else InMemoryAuditContainer()
        logger.info("AuditLogger initialised")

    # -- public API ----------------------------------------------------------

    async def log_event(
        self,
        event_type: str,
        actor: str,
        details: dict[str, Any],
        resource_id: str | None = None,
        ip_address: str | None = None,
    ) -> AuditEntry:
        """Create an immutable audit entry.

        Parameters
        ----------
        event_type:
            One of the recognised event types (e.g. ``"scoring_completed"``).
        actor:
            The ID of the user or ``"system"`` for automated actions.
        details:
            Free-form dictionary with event-specific metadata.
        resource_id:
            Optional identifier of the affected resource (passage, score, etc.).
        ip_address:
            Optional IP address of the caller.

        Returns
        -------
        AuditEntry
            The persisted audit record.

        Raises
        ------
        InvalidEventTypeError
            If *event_type* is not in the allow-list.
        AuditLoggerError
            On Cosmos DB persistence failures.
        """
        if event_type not in VALID_EVENT_TYPES:
            raise InvalidEventTypeError(
                f"Unknown event type '{event_type}'. "
                f"Valid types: {sorted(VALID_EVENT_TYPES)}"
            )

        entry = AuditEntry(
            event_type=event_type,
            actor=actor,
            resource_id=resource_id,
            details=details,
            ip_address=ip_address,
        )

        try:
            item_body = entry.model_dump(mode="json")
            # Cosmos DB requires a partition key — use resource_id (or "global")
            item_body["partitionKey"] = resource_id or "global"
            await self._container.create_item(body=item_body)
            logger.info(
                "Audit event logged: type=%s actor=%s resource=%s id=%s",
                event_type,
                actor,
                resource_id,
                entry.id,
            )
        except Exception as exc:
            raise AuditLoggerError(
                f"Failed to persist audit entry: {exc}"
            ) from exc

        return entry

    async def get_audit_trail(self, resource_id: str) -> list[AuditEntry]:
        """Retrieve the full audit trail for a resource.

        Parameters
        ----------
        resource_id:
            The resource identifier to look up.

        Returns
        -------
        list[AuditEntry]
            Chronologically ordered list of audit entries.
        """
        query = (
            "SELECT * FROM c WHERE c.resource_id = @rid ORDER BY c.timestamp ASC"
        )
        parameters: list[dict[str, Any]] = [
            {"name": "@rid", "value": resource_id},
        ]

        entries: list[AuditEntry] = []
        try:
            items = self._container.query_items(
                query=query,
                parameters=parameters,
                partition_key=resource_id,
            )
            async for item in items:
                entries.append(AuditEntry(**item))
        except Exception as exc:
            raise AuditLoggerError(
                f"Failed to query audit trail for resource {resource_id}: {exc}"
            ) from exc

        return entries

    async def query_events(
        self,
        event_type: str | None = None,
        actor: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[AuditEntry]:
        """Query audit events with optional filters.

        Parameters
        ----------
        event_type:
            Filter by event type.
        actor:
            Filter by actor ID.
        start_time:
            Lower bound for the event timestamp (inclusive).
        end_time:
            Upper bound for the event timestamp (inclusive).

        Returns
        -------
        list[AuditEntry]
            Matching entries ordered by timestamp.
        """
        conditions: list[str] = []
        parameters: list[dict[str, Any]] = []

        if event_type is not None:
            conditions.append("c.event_type = @etype")
            parameters.append({"name": "@etype", "value": event_type})

        if actor is not None:
            conditions.append("c.actor = @actor")
            parameters.append({"name": "@actor", "value": actor})

        if start_time is not None:
            conditions.append("c.timestamp >= @start")
            parameters.append({"name": "@start", "value": start_time.isoformat()})

        if end_time is not None:
            conditions.append("c.timestamp <= @end")
            parameters.append({"name": "@end", "value": end_time.isoformat()})

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        query = f"SELECT * FROM c WHERE {where_clause} ORDER BY c.timestamp ASC"

        entries: list[AuditEntry] = []
        try:
            items = self._container.query_items(
                query=query,
                parameters=parameters,
                enable_cross_partition_query=True,
            )
            async for item in items:
                entries.append(AuditEntry(**item))
        except Exception as exc:
            raise AuditLoggerError(
                f"Failed to query audit events: {exc}"
            ) from exc

        return entries
