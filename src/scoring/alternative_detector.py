"""Alternative terminology detector.

Detects and justifies acceptable lexical variation in translations so that
candidates are not penalised for valid regional / official synonyms.

Tiered resolution for every glossary entry:

1. **Canonical match** – the expected target term occurs in the candidate
   translation (after Arabic orthographic normalisation).
2. **Authoritative lexicon** – one of the glossary's officially accepted
   ``alternatives`` occurs in the candidate.  Deterministic; no LLM call.
3. **Contextual LLM judgement** – neither form occurs, so the model is shown
   the candidate segment aligned to the term and decides whether the rendering
   used is a valid alternative or an error.
"""

from __future__ import annotations

import logging
import re

from src.infrastructure.azure_openai_client import AzureOpenAIClient
from src.scoring.schemas import AlignedUnit, GlossaryTerm, TerminologyVerdict

logger = logging.getLogger(__name__)

_ARABIC_DIACRITICS = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]")
_TATWEEL = "\u0640"


def normalise_text(text: str) -> str:
    """Normalise text for robust term matching.

    Removes Arabic diacritics and tatweel, unifies alef/yaa/taa-marbuta
    variants, lower-cases Latin script and collapses whitespace.
    """
    t = _ARABIC_DIACRITICS.sub("", text).replace(_TATWEEL, "")
    t = re.sub("[\u0622\u0623\u0625\u0671]", "\u0627", t)
    t = t.replace("\u0649", "\u064A").replace("\u0629", "\u0647")
    return re.sub(r"\s+", " ", t).strip().lower()


def _contains(haystack: str, needle: str) -> bool:
    n = normalise_text(needle)
    return bool(n) and n in normalise_text(haystack)


class AlternativeDetector:
    """Evaluates whether candidate terms are correct, valid variants, or errors."""

    def __init__(self, openai_client: AzureOpenAIClient | None = None) -> None:
        self.openai_client = openai_client or AzureOpenAIClient()

    async def assess_glossary(
        self,
        glossary: list[GlossaryTerm],
        candidate_translation: str,
        alignments: list[AlignedUnit],
        domain: str = "legal",
    ) -> list[TerminologyVerdict]:
        """Return one verdict per glossary entry using the three-tier strategy."""
        verdicts: list[TerminologyVerdict] = []
        for term in glossary:
            verdicts.append(
                await self._assess_term(term, candidate_translation, alignments, domain)
            )
        return verdicts

    async def _assess_term(
        self,
        term: GlossaryTerm,
        candidate_translation: str,
        alignments: list[AlignedUnit],
        domain: str,
    ) -> TerminologyVerdict:
        # Tier 1 – canonical glossary rendering present.
        if _contains(candidate_translation, term.target_term):
            return TerminologyVerdict(
                source_term=term.source_term,
                expected_translation=term.target_term,
                candidate_translation=term.target_term,
                is_correct=True,
                is_valid_alternative=False,
                explanation="Canonical glossary term used.",
            )

        # Tier 2 – officially accepted alternative present.
        for alt in term.alternatives:
            if _contains(candidate_translation, alt):
                return TerminologyVerdict(
                    source_term=term.source_term,
                    expected_translation=term.target_term,
                    candidate_translation=alt,
                    is_correct=True,
                    is_valid_alternative=True,
                    explanation="Matches an officially accepted alternative in the reference lexicon.",
                )

        # Locate the meaning unit that carries this term.
        unit = next(
            (a for a in alignments if _contains(a.meaning_unit.source_text, term.source_term)),
            None,
        )
        if unit is not None and unit.candidate_text is None:
            return TerminologyVerdict(
                source_term=term.source_term,
                expected_translation=term.target_term,
                candidate_translation="",
                is_correct=False,
                is_valid_alternative=False,
                explanation=f"Term not rendered: meaning unit {unit.meaning_unit.id} was omitted.",
            )

        # Tier 3 – contextual judgement on the aligned segment.
        segment = unit.candidate_text if unit and unit.candidate_text else candidate_translation
        return await self.evaluate_in_context(term, segment, domain)

    async def evaluate_in_context(
        self, term: GlossaryTerm, candidate_segment: str, domain: str = "legal"
    ) -> TerminologyVerdict:
        """Ask the model to identify and judge the candidate's rendering of *term*."""
        accepted = ", ".join(term.alternatives) or "none listed"
        prompt = (
            f"You are a terminology examiner for {domain} translation.\n\n"
            f"Source term: {term.source_term}\n"
            f"Canonical translation: {term.target_term}\n"
            f"Officially accepted alternatives: {accepted}\n"
            f'Candidate segment: "{candidate_segment}"\n\n'
            "Identify the exact words in the candidate segment that render the source term, "
            "then decide whether that rendering is (a) an exact synonym or valid official/"
            "regional alternative preserving legal effect and register, or (b) incorrect, "
            "misleading, or missing.\n\n"
            "Respond in JSON with keys: candidate_rendering (str, empty if missing), "
            "is_valid_alternative (bool), explanation (str)."
        )
        res = await self.openai_client.chat_completion_json(
            messages=[
                {"role": "system", "content": "You are a bilingual lexicographer and legal translator."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            operation="written_scoring.terminology",
        )
        if not isinstance(res, dict):
            res = {}
        valid = bool(res.get("is_valid_alternative", False))
        return TerminologyVerdict(
            source_term=term.source_term,
            expected_translation=term.target_term,
            candidate_translation=str(res.get("candidate_rendering") or ""),
            is_correct=valid,
            is_valid_alternative=valid,
            explanation=str(res.get("explanation") or "Evaluated in context by terminology model."),
        )
