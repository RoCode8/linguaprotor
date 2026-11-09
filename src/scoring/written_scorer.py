"""Main written translation scoring orchestrator.

Coordinates semantic decomposition, alignment, valid-alternative detection,
and rubric-based scoring to generate auditable assessment packets.

Scoring policy (see docs/scoring_methodology.md):

* **Completeness** is capped by the importance-weighted coverage of meaning
  units: ``cap = 5 × Σw(aligned) / Σw(all)``.  The rubric model may deduct
  further (e.g. for unwarranted additions) but can never award points for
  content the alignment shows was omitted.
* **Terminology** verdicts for glossary terms come from the tiered
  :class:`AlternativeDetector`; valid alternatives are counted from those
  verdicts rather than from the model's self-report.
* **Confidence** is the minimum of the rubric model's self-reported
  confidence and the mean alignment confidence of rendered units, further
  capped whenever the pipeline had to repair or could not trust model output.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, datetime

from src.config import get_settings
from src.infrastructure.azure_openai_client import AzureOpenAIClient
from src.scoring.alternative_detector import AlternativeDetector
from src.scoring.meaning_unit_decomposer import MeaningUnitDecomposer
from src.scoring.rubric_evaluator import CRITERION_MAX, RubricEvaluator
from src.scoring.schemas import (
    AlignedUnit,
    CriterionScore,
    ScoringRequest,
    ScoringResult,
    TerminologyVerdict,
)
from src.scoring.semantic_aligner import SemanticAligner

logger = logging.getLogger(__name__)

# Confidence ceiling applied when model output was incomplete/repaired:
# lands the item in priority (not auto-approved) review.
_REPAIRED_OUTPUT_CONFIDENCE_CAP = 0.74


class ScoringError(RuntimeError):
    """Raised when a translation cannot be scored reliably."""


def is_unreported(alignment: AlignedUnit) -> bool:
    """True when the aligner model said nothing about this unit.

    The aligner marks such units with confidence 0.0 and no candidate text
    (explicit omissions carry confidence >= 0.01). It is a model failure, not
    evidence that the candidate left the content out.
    """
    return alignment.candidate_text is None and alignment.alignment_confidence == 0.0


def weighted_coverage(alignments: list[AlignedUnit]) -> float:
    """Importance-weighted fraction of meaning units rendered by the candidate.

    Units the aligner did not report are excluded from both sides of the ratio,
    so a model failure neither lowers nor raises the candidate's coverage.
    """
    assessed = [a for a in alignments if not is_unreported(a)]
    total = sum(a.meaning_unit.importance_weight for a in assessed)
    if total <= 0:
        return 0.0
    covered = sum(
        a.meaning_unit.importance_weight for a in assessed if a.candidate_text is not None
    )
    return covered / total


def _terminology_summary(verdicts: list[TerminologyVerdict]) -> str:
    lines = []
    for v in verdicts:
        if v.is_valid_alternative:
            tag = "VALID ALTERNATIVE"
        elif v.is_correct:
            tag = "CORRECT"
        else:
            tag = "INCORRECT"
        lines.append(
            f"- {v.source_term} => expected: {v.expected_translation}; "
            f"candidate used: {v.candidate_translation or '[none]'} [{tag}] ({v.explanation})"
        )
    return "\n".join(lines)


class WrittenTranslationScorer:
    """Orchestrates end-to-end explainable translation scoring."""

    def __init__(self, openai_client: AzureOpenAIClient | None = None) -> None:
        self.openai_client = openai_client or AzureOpenAIClient()
        self.decomposer = MeaningUnitDecomposer(self.openai_client)
        self.aligner = SemanticAligner(self.openai_client)
        self.alternative_detector = AlternativeDetector(self.openai_client)
        self.evaluator = RubricEvaluator(self.openai_client)
        self.settings = get_settings().app

    async def score(self, request: ScoringRequest, domain: str = "legal") -> ScoringResult:
        """Execute the full translation scoring pipeline."""
        start_time = time.time()
        logger.info(
            "Starting scoring for candidate %s, exam %s",
            request.candidate_id,
            request.exam_id,
        )

        # 1. Decompose source text into atomic meaning units
        meaning_units = await self.decomposer.decompose(
            source_text=request.source_text,
            source_language=request.source_language,
        )
        if not meaning_units:
            raise ScoringError("Meaning-unit decomposition returned no units; cannot score.")

        # 2. Semantically align candidate text against meaning units
        alignments = await self.aligner.align(
            meaning_units=meaning_units,
            candidate_text=request.candidate_translation,
            source_language=request.source_language,
            target_language=request.target_language,
        )
        if all(is_unreported(a) for a in alignments):
            raise ScoringError("Alignment reported none of the meaning units; cannot score.")

        # 3. Tiered terminology / valid-alternative detection on the glossary
        glossary_verdicts = await self.alternative_detector.assess_glossary(
            glossary=request.terminology_glossary,
            candidate_translation=request.candidate_translation,
            alignments=alignments,
            domain=domain,
        )

        # 4. Rubric evaluation, informed by verified terminology verdicts
        evaluation = await self.evaluator.evaluate(
            source_text=request.source_text,
            candidate_translation=request.candidate_translation,
            reference_translation=request.reference_translation,
            alignments=alignments,
            terminology_summary=_terminology_summary(glossary_verdicts),
            source_language=request.source_language,
            target_language=request.target_language,
            rubric_version=request.rubric_version,
        )
        flags: list[str] = list(evaluation.flags)
        confidence = evaluation.confidence

        # 5. Completeness is capped by weighted meaning-unit coverage
        coverage = weighted_coverage(alignments)
        completeness_cap = round(CRITERION_MAX * coverage * 2) / 2  # half-point grid
        criteria: list[CriterionScore] = []
        model_completeness: float | None = None
        for c in evaluation.criteria:
            if c.criterion == "completeness":
                model_completeness = c.score
                if c.score > completeness_cap:
                    c = c.model_copy(
                        update={
                            "score": completeness_cap,
                            "summary": (
                                f"{c.summary} [Capped at {completeness_cap:g}/{CRITERION_MAX:g}: "
                                f"weighted meaning-unit coverage is {coverage:.0%}.]"
                            ).strip(),
                        }
                    )
            criteria.append(c)

        # 6. Merge terminology verdicts: glossary verdicts are authoritative
        glossary_terms = {v.source_term.lower() for v in glossary_verdicts}
        verdicts = glossary_verdicts + [
            v for v in evaluation.terminology_verdicts
            if v.source_term.lower() not in glossary_terms
        ]
        valid_alts = sum(1 for v in verdicts if v.is_valid_alternative)

        # 7. Confidence policy
        rendered = [a for a in alignments if a.candidate_text is not None]
        if rendered:
            mean_alignment_conf = sum(a.alignment_confidence for a in rendered) / len(rendered)
            confidence = min(confidence, mean_alignment_conf)

        unreported = [a.meaning_unit.id for a in alignments if is_unreported(a)]
        if unreported:
            flags.append(f"alignment_incomplete_{len(unreported)}_units")
        if unreported or any(f.startswith("score_clamped") for f in flags):
            confidence = min(confidence, _REPAIRED_OUTPUT_CONFIDENCE_CAP)

        # Only explicit omissions count against the candidate; unreported units
        # are left to the reviewer via the alignment_incomplete flag.
        omitted = [
            a.meaning_unit.id
            for a in alignments
            if a.candidate_text is None and not is_unreported(a)
        ]
        if omitted:
            flags.append(f"omissions_detected_{len(omitted)}_units")
        if confidence < self.settings.scoring_confidence_threshold:
            flags.append("requires_human_review_low_confidence")

        total_score = sum(c.score for c in criteria)
        total_max = sum(c.max_score for c in criteria)
        percentage = (total_score / total_max * 100.0) if total_max > 0 else 0.0

        return ScoringResult(
            id=str(uuid.uuid4()),
            exam_id=request.exam_id,
            candidate_id=request.candidate_id,
            overall_score=round(total_score, 2),
            overall_max=round(total_max, 2),
            percentage=round(percentage, 2),
            criteria=criteria,
            meaning_units=meaning_units,
            alignments=alignments,
            terminology_verdicts=verdicts,
            valid_alternatives_detected=valid_alts,
            confidence=round(confidence, 3),
            flags=flags,
            scoring_metadata={
                "latency_seconds": round(time.time() - start_time, 2),
                "model": getattr(self.openai_client, "chat_deployment", "unknown"),
                "rubric_version": request.rubric_version,
                "weighted_coverage": round(coverage, 4),
                "completeness_cap": completeness_cap,
                "model_completeness_score": model_completeness,
                "llm_self_reported_confidence": evaluation.confidence,
            },
            created_at=datetime.now(UTC),
        )
