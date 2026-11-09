"""Oral interpretation scoring engine.

Evaluates spoken interpretation across accuracy, fluency, delivery,
and comprehension using speech transcripts and alignment metadata.
"""

from __future__ import annotations

import logging

from src.infrastructure.azure_openai_client import AzureOpenAIClient
from src.oral.schemas import (
    InterpretationMode,
    OralCriterionScore,
    TranscriptAlignment,
    TranscriptionResult,
)

logger = logging.getLogger(__name__)

ORAL_CRITERIA: tuple[str, ...] = ("accuracy", "fluency", "comprehension", "delivery")
ORAL_CRITERION_MAX = 5.0


class OralScorer:
    """Evaluates oral translation candidate transcripts against source."""

    def __init__(self, openai_client: AzureOpenAIClient | None = None) -> None:
        self.openai_client = openai_client or AzureOpenAIClient()

    async def score(
        self,
        source_transcript: TranscriptionResult,
        candidate_transcript: TranscriptionResult,
        alignments: list[TranscriptAlignment],
        mode: InterpretationMode,
        source_language: str = "en",
        target_language: str = "ar",
    ) -> tuple[list[OralCriterionScore], list[str]]:
        """Score oral performance on accuracy, fluency, comprehension, and delivery.

        Returns the four criteria (always present, clamped to range) and any
        flags raised while repairing the model output.
        """
        alignment_summary = "\n".join(
            [
                f"- Source: \"{a.source_segment}\" -> Candidate: \"{a.candidate_segment}\" (Score: {a.alignment_score:.2f})"
                for a in alignments
            ]
        )

        prompt = (
            f"You are an expert oral interpretation examination assessor for {source_language} ↔ {target_language}.\n"
            f"Mode: {mode.value.upper()}\n\n"
            f"Source Transcript:\n\"\"\"{source_transcript.text}\"\"\"\n\n"
            f"Candidate Interpretation Transcript:\n\"\"\"{candidate_transcript.text}\"\"\"\n\n"
            f"Alignments & Segment Scores:\n{alignment_summary}\n\n"
            "Score the interpretation across 4 criteria (each out of 5.0):\n"
            "1. accuracy (semantic content conveyed accurately without critical distortions)\n"
            "2. fluency (speech flow, natural sentence completions, hesitation markers)\n"
            "3. comprehension (demonstrated grasp of nuanced idioms and specialized vocabulary)\n"
            "4. delivery (pace, professional demeanor, register consistency)\n\n"
            "Respond in JSON format with key 'criteria', a list of objects with:\n"
            "- criterion (str)\n"
            "- score (float 0.0-5.0)\n"
            "- max_score (5.0)\n"
            "- summary (str)\n"
            "- evidence (list of str citations from transcript)"
        )

        response = await self.openai_client.chat_completion_json(
            messages=[
                {
                    "role": "system",
                    "content": "You are a senior oral interpretation examiner.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            operation="oral_scoring",
        )

        raw = response.get("criteria", []) if isinstance(response, dict) else []
        flags: list[str] = []
        by_name: dict[str, OralCriterionScore] = {}
        for c in raw:
            if not isinstance(c, dict):
                continue
            name = str(c.get("criterion") or "").strip().lower()
            if name not in ORAL_CRITERIA or name in by_name:
                flags.append(f"unexpected_criterion_ignored:{name}")
                continue
            try:
                value = float(c.get("score", 0.0))
            except (TypeError, ValueError):
                value = 0.0
            clamped = min(max(value, 0.0), ORAL_CRITERION_MAX)
            if clamped != value:
                flags.append(f"score_clamped:{name}")
            evidence = c.get("evidence", [])
            by_name[name] = OralCriterionScore(
                criterion=name,
                score=clamped,
                max_score=ORAL_CRITERION_MAX,
                summary=str(c.get("summary") or ""),
                evidence=[str(e) for e in evidence] if isinstance(evidence, list) else [],
            )

        for name in ORAL_CRITERIA:
            if name not in by_name:
                flags.append(f"criterion_missing:{name}")
                by_name[name] = OralCriterionScore(
                    criterion=name,
                    score=0.0,
                    max_score=ORAL_CRITERION_MAX,
                    summary="Not assessed by the model — requires human scoring.",
                )

        return [by_name[n] for n in ORAL_CRITERIA], flags
