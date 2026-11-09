"""Semantic aligner for translation evaluation.

Maps each source meaning unit to corresponding spans in the candidate
translation, estimating alignment confidence and identifying omissions.
"""

from __future__ import annotations

import logging

from src.infrastructure.azure_openai_client import AzureOpenAIClient
from src.scoring.schemas import AlignedUnit, MeaningUnit

logger = logging.getLogger(__name__)


class SemanticAligner:
    """Aligns atomic meaning units from source text to candidate translation."""

    def __init__(self, openai_client: AzureOpenAIClient | None = None) -> None:
        self.openai_client = openai_client or AzureOpenAIClient()

    async def align(
        self,
        meaning_units: list[MeaningUnit],
        candidate_text: str,
        source_language: str = "en",
        target_language: str = "ar",
    ) -> list[AlignedUnit]:
        """Align meaning units against candidate translation."""
        units_payload = [
            {"id": u.id, "text": u.source_text, "type": u.unit_type}
            for u in meaning_units
        ]

        prompt = (
            f"You are a translation alignment model. Given a list of source Meaning Units ({source_language}) "
            f"and a candidate translation ({target_language}), identify where each meaning unit is translated in the candidate text.\n\n"
            f"Candidate Translation:\n\"\"\"{candidate_text}\"\"\"\n\n"
            f"Source Meaning Units:\n{units_payload}\n\n"
            "Respond in JSON format with key 'alignments', a list of objects containing:\n"
            "- meaning_unit_id (str)\n"
            "- candidate_text (str or null if omitted)\n"
            "- alignment_confidence (float 0.0 to 1.0)\n"
            "- is_omitted (bool)"
        )

        response = await self.openai_client.chat_completion_json(
            messages=[
                {
                    "role": "system",
                    "content": "You are an expert bilingual alignment model for translation assessment.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            operation="written_scoring.align",
        )

        raw = response.get("alignments", []) if isinstance(response, dict) else []
        alignments_data = {
            item.get("meaning_unit_id"): item for item in raw if isinstance(item, dict)
        }

        aligned_units: list[AlignedUnit] = []
        for unit in meaning_units:
            match = alignments_data.get(unit.id)
            if match is None:
                # The model did not report this unit at all. This is a model
                # failure, not evidence of a candidate omission: confidence 0
                # lets the scorer flag it for human review.
                aligned_units.append(
                    AlignedUnit(meaning_unit=unit, alignment_confidence=0.0)
                )
                continue

            try:
                conf = float(match.get("alignment_confidence", 0.5))
            except (TypeError, ValueError):
                conf = 0.5
            conf = min(max(conf, 0.0), 1.0)

            cand_text = match.get("candidate_text")
            cand_text = str(cand_text).strip() if cand_text else None
            if cand_text and not match.get("is_omitted", False):
                span: tuple[int, int] | None = None
                st = candidate_text.find(cand_text)
                if st != -1:
                    span = (st, st + len(cand_text))
                aligned_units.append(
                    AlignedUnit(
                        meaning_unit=unit,
                        candidate_text=cand_text,
                        candidate_span=span,
                        alignment_confidence=conf,
                    )
                )
            else:
                # Explicit omission: confidence expresses certainty of the omission.
                aligned_units.append(
                    AlignedUnit(meaning_unit=unit, alignment_confidence=max(conf, 0.01))
                )

        return aligned_units
