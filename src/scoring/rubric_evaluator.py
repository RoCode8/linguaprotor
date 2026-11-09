"""Rubric-based evaluation engine for written translation.

Generates criterion-level scores with supporting evidence citations
across Accuracy, Completeness, Terminology, and Fluency.

The LLM output is treated as untrusted: the evaluator enforces the rubric
contract (exactly four criteria, scores clamped to ``[0, CRITERION_MAX]``,
fixed maximum) and reports every repair it had to make as a flag so the
assessment is routed to a human.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from jinja2 import BaseLoader, Environment

from src.infrastructure.azure_openai_client import AzureOpenAIClient
from src.scoring.schemas import (
    AlignedUnit,
    CriterionEvidence,
    CriterionScore,
    SeverityLevel,
    TerminologyVerdict,
)

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"

RUBRIC_CRITERIA: tuple[str, ...] = ("accuracy", "completeness", "terminology", "fluency")
CRITERION_MAX = 5.0

# Tolerant mapping of criterion names the model may produce.
_CRITERION_ALIASES = {
    "accuracy": "accuracy",
    "completeness": "completeness",
    "terminology": "terminology",
    "fluency": "fluency",
    "language quality": "fluency",
    "language_quality": "fluency",
    "fluency & quality": "fluency",
    "language quality & fluency": "fluency",
}


@dataclass
class RubricEvaluation:
    """Normalised output of the rubric LLM call."""

    criteria: list[CriterionScore]
    terminology_verdicts: list[TerminologyVerdict]
    confidence: float
    flags: list[str] = field(default_factory=list)


def _canonical_criterion(name: Any) -> str | None:
    key = str(name or "").strip().lower()
    if key in _CRITERION_ALIASES:
        return _CRITERION_ALIASES[key]
    for alias, canonical in _CRITERION_ALIASES.items():
        if alias in key:
            return canonical
    return None


def _to_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_evidence(raw: Any) -> list[CriterionEvidence]:
    items: list[CriterionEvidence] = []
    for ev in raw if isinstance(raw, list) else []:
        if not isinstance(ev, dict):
            continue
        severity: SeverityLevel | None = None
        sev = ev.get("severity")
        if sev:
            try:
                severity = SeverityLevel(str(sev).lower())
            except ValueError:
                severity = SeverityLevel.MINOR
        items.append(
            CriterionEvidence(
                meaning_unit_id=str(ev.get("meaning_unit_id") or "mu_unknown"),
                source_span=str(ev.get("source_span") or ""),
                candidate_span=ev.get("candidate_span"),
                issue=ev.get("issue"),
                severity=severity,
                is_positive=bool(ev.get("is_positive", False)),
                explanation=str(ev.get("explanation") or ""),
            )
        )
    return items


class RubricEvaluator:
    """Evaluates candidate translations against an explainable four-pillar rubric."""

    def __init__(self, openai_client: AzureOpenAIClient | None = None) -> None:
        self.openai_client = openai_client or AzureOpenAIClient()
        self._jinja_env = Environment(loader=BaseLoader(), autoescape=False)

    def _load_prompt_template(self, filename: str) -> dict[str, str]:
        path = _PROMPTS_DIR / filename
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)

    async def evaluate(
        self,
        source_text: str,
        candidate_translation: str,
        reference_translation: str | None,
        alignments: list[AlignedUnit],
        terminology_summary: str,
        source_language: str = "en",
        target_language: str = "ar",
        rubric_version: str = "v1",
    ) -> RubricEvaluation:
        """Run the rubric prompt and return a contract-conformant evaluation."""
        template_data = self._load_prompt_template(f"{rubric_version}_scoring_rubric.yaml")

        def _candidate_label(a: AlignedUnit) -> str:
            if a.candidate_text is not None:
                return f'"{a.candidate_text}"'
            if a.alignment_confidence == 0.0:
                # The aligner never reported this unit: a model failure, not a
                # candidate omission (see written_scorer.is_unreported).
                return "[NOT ALIGNED - unknown; do not treat as omitted or deduct for it]"
            return '"[OMITTED]"'

        alignments_summary = "\n".join(
            f'- [Unit: {a.meaning_unit.id} ({a.meaning_unit.unit_type}, '
            f'weight {a.meaning_unit.importance_weight:.1f})]: "{a.meaning_unit.source_text}" -> '
            f"Candidate: {_candidate_label(a)} (Conf: {a.alignment_confidence:.2f})"
            for a in alignments
        )

        variables = {
            "source_language": source_language,
            "target_language": target_language,
            "source_text": source_text,
            "candidate_translation": candidate_translation,
            "reference_translation": reference_translation,
            "alignments_summary": alignments_summary,
            "terminology_summary": terminology_summary or "No specific glossary provided.",
        }

        system_msg = self._jinja_env.from_string(template_data["system"]).render(**variables)
        user_msg = self._jinja_env.from_string(template_data["user"]).render(**variables)

        response = await self.openai_client.chat_completion_json(
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.0,
            operation="written_scoring.rubric",
        )
        if not isinstance(response, dict):
            response = {}
        return self._normalise(response)

    @staticmethod
    def _normalise(response: dict[str, Any]) -> RubricEvaluation:
        flags: list[str] = [str(f) for f in response.get("flags", []) or [] if f]
        by_name: dict[str, CriterionScore] = {}

        for c in response.get("criteria", []) or []:
            if not isinstance(c, dict):
                continue
            name = _canonical_criterion(c.get("criterion"))
            if name is None:
                flags.append(f"unknown_criterion_ignored:{c.get('criterion')}")
                continue
            if name in by_name:
                flags.append(f"duplicate_criterion_ignored:{name}")
                continue
            raw = _to_float(c.get("score"), 0.0)
            score = min(max(raw, 0.0), CRITERION_MAX)
            if score != raw:
                flags.append(f"score_clamped:{name}")
            by_name[name] = CriterionScore(
                criterion=name,
                score=score,
                max_score=CRITERION_MAX,
                evidence=_parse_evidence(c.get("evidence")),
                summary=str(c.get("summary") or ""),
            )

        missing = [n for n in RUBRIC_CRITERIA if n not in by_name]
        for name in missing:
            flags.append(f"criterion_missing:{name}")
            by_name[name] = CriterionScore(
                criterion=name,
                score=0.0,
                max_score=CRITERION_MAX,
                evidence=[],
                summary="Not assessed by the model — requires human scoring.",
            )

        verdicts: list[TerminologyVerdict] = []
        for v in response.get("terminology_verdicts", []) or []:
            if not isinstance(v, dict) or not v.get("source_term"):
                continue
            verdicts.append(
                TerminologyVerdict(
                    source_term=str(v.get("source_term")),
                    expected_translation=str(v.get("expected_translation") or ""),
                    candidate_translation=str(v.get("candidate_translation") or ""),
                    is_correct=bool(v.get("is_correct", False)),
                    is_valid_alternative=bool(v.get("is_valid_alternative", False)),
                    explanation=str(v.get("explanation") or ""),
                )
            )

        # A model that does not report confidence gets no benefit of the doubt.
        confidence = min(max(_to_float(response.get("confidence"), 0.5), 0.0), 1.0)
        if missing:
            confidence = min(confidence, 0.5)

        return RubricEvaluation(
            criteria=[by_name[n] for n in RUBRIC_CRITERIA],
            terminology_verdicts=verdicts,
            confidence=confidence,
            flags=flags,
        )
