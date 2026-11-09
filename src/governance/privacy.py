"""PII detection and redaction for LinguaProctor.

Uses Microsoft Presidio to scan text for Personally Identifiable Information
(PII) and redact it before storage or LLM processing.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class PIIEntity(BaseModel):
    """A detected PII entity in text."""

    entity_type: str  # PERSON, EMAIL, PHONE, etc.
    start: int
    end: int
    score: float


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PrivacyGuardError(Exception):
    """Raised on privacy-guard initialisation or processing failures."""


# ---------------------------------------------------------------------------
# Guard
# ---------------------------------------------------------------------------


class PrivacyGuard:
    """PII detector and redactor backed by Presidio.

    The Presidio analyzer and anonymizer are loaded lazily on first use
    so that the import overhead is deferred.
    """

    def __init__(self) -> None:
        self._analyzer: Any | None = None
        self._anonymizer: Any | None = None
        logger.info("PrivacyGuard created (Presidio will be loaded lazily)")

    # -- lazy initialisation -------------------------------------------------

    def _ensure_loaded(self) -> None:
        """Lazily import and instantiate Presidio components."""
        if self._analyzer is not None:
            return

        try:
            from presidio_analyzer import AnalyzerEngine
            from presidio_anonymizer import AnonymizerEngine

            self._analyzer = AnalyzerEngine()
            self._anonymizer = AnonymizerEngine()
            logger.info("Presidio engines loaded successfully")
        except ImportError as exc:
            raise PrivacyGuardError(
                "Presidio libraries are not installed.  "
                "Install with: pip install presidio-analyzer presidio-anonymizer"
            ) from exc
        except Exception as exc:
            raise PrivacyGuardError(
                f"Failed to initialise Presidio engines: {exc}"
            ) from exc

    # -- public API ----------------------------------------------------------

    def detect_pii(self, text: str, language: str = "en") -> list[PIIEntity]:
        """Scan text for PII entities.

        Parameters
        ----------
        text:
            The input text to scan.
        language:
            BCP-47 language code (Presidio currently supports ``"en"``
            best).  For Arabic text, detection accuracy may be lower.

        Returns
        -------
        list[PIIEntity]
            Detected entities sorted by start position.
        """
        self._ensure_loaded()

        results = self._analyzer.analyze(  # type: ignore[union-attr]
            text=text,
            language=language,
            entities=None,  # detect all entity types
        )

        entities = [
            PIIEntity(
                entity_type=r.entity_type,
                start=r.start,
                end=r.end,
                score=round(r.score, 4),
            )
            for r in results
        ]

        entities.sort(key=lambda e: e.start)
        logger.debug("Detected %d PII entities in text of length %d", len(entities), len(text))
        return entities

    def redact(self, text: str, language: str = "en") -> str:
        """Redact PII from text, replacing each entity with ``<REDACTED:TYPE>``.

        Parameters
        ----------
        text:
            The input text.
        language:
            BCP-47 language code.

        Returns
        -------
        str
            Text with PII replaced by redaction tokens.
        """
        entities = self.detect_pii(text, language=language)
        if not entities:
            return text

        # Process entities in reverse order to preserve character positions
        result = text
        for entity in reversed(entities):
            replacement = f"<REDACTED:{entity.entity_type}>"
            result = result[:entity.start] + replacement + result[entity.end:]

        logger.info("Redacted %d PII entities from text", len(entities))
        return result

    def is_safe_to_store(self, text: str) -> bool:
        """Check whether text is free of high-confidence PII.

        Returns ``True`` when no PII is detected above a confidence
        threshold of 0.7.

        Parameters
        ----------
        text:
            The text to evaluate.

        Returns
        -------
        bool
            ``True`` if no significant PII was found.
        """
        entities = self.detect_pii(text)
        high_confidence = [e for e in entities if e.score >= 0.7]

        if high_confidence:
            logger.warning(
                "Text contains %d high-confidence PII entities — not safe to store",
                len(high_confidence),
            )
            return False

        return True
