"""Validation engine for generated examination passages.

Validates linguistic complexity, lexical diversity, terminology coverage,
and deduplication against historical exam banks using embeddings.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import numpy as np

from src.content_generation.schemas import (
    ComprehensiveValidationResult,
    DuplicationResult,
    ExamDifficulty,
    GeneratedPassage,
    ValidationResult,
)
from src.infrastructure.azure_openai_client import AzureOpenAIClient

logger = logging.getLogger(__name__)


class ContentValidator:
    """Validates generated passages for difficulty, terminology, and duplication."""

    def __init__(self, openai_client: AzureOpenAIClient | None = None) -> None:
        self.openai_client = openai_client or AzureOpenAIClient()

    def validate_difficulty(self, passage: GeneratedPassage) -> ValidationResult:
        """Validate whether the passage complexity matches the target CEFR level."""
        text = passage.source_text
        words = re.findall(r"\b\w+\b", text.lower())
        sentences = [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]

        if not words or not sentences:
            return ValidationResult(
                is_valid=False,
                score=0.0,
                issues=["Passage is empty or malformed."],
                details={},
            )

        # Lexical diversity (Type-Token Ratio)
        unique_words = set(words)
        ttr = len(unique_words) / len(words)

        # Average sentence length (words per sentence)
        avg_sentence_len = len(words) / len(sentences)

        # Average word length (characters per word)
        avg_word_len = sum(len(w) for w in words) / len(words)

        # Approximate readability index / CEFR mapping score
        # B2: avg_sentence_len ~ 15-20, avg_word_len ~ 4.5-5.2
        # C1: avg_sentence_len ~ 20-28, avg_word_len ~ 5.2-6.0
        # C2: avg_sentence_len > 25, avg_word_len > 5.8
        complexity_score = min(
            1.0,
            (avg_sentence_len / 35.0) * 0.4
            + (avg_word_len / 7.0) * 0.3
            + ttr * 0.3,
        )

        issues: list[str] = []
        target = passage.difficulty
        if target == ExamDifficulty.B2:
            is_valid = 0.35 <= complexity_score <= 0.70
            if complexity_score < 0.35:
                issues.append("Passage text may be too simple for B2 level.")
            elif complexity_score > 0.70:
                issues.append("Passage text may exceed B2 complexity expectations.")
        elif target == ExamDifficulty.C1:
            is_valid = 0.50 <= complexity_score <= 0.85
            if complexity_score < 0.50:
                issues.append("Passage vocabulary and syntax appear below C1 standard.")
        else:  # C2
            is_valid = complexity_score >= 0.65
            if complexity_score < 0.65:
                issues.append("Passage lacks the syntactic complexity typical of C2 texts.")

        if passage.word_count < 100:
            issues.append(f"Passage word count ({passage.word_count}) is below recommended 100 words.")
        elif passage.word_count > 500:
            issues.append(f"Passage word count ({passage.word_count}) exceeds recommended 500 words.")

        return ValidationResult(
            is_valid=is_valid and len(issues) == 0,
            score=round(complexity_score, 3),
            issues=issues,
            details={
                "type_token_ratio": round(ttr, 3),
                "avg_sentence_length": round(avg_sentence_len, 2),
                "avg_word_length": round(avg_word_len, 2),
                "total_words": len(words),
                "total_sentences": len(sentences),
            },
        )

    def validate_terminology(
        self,
        passage: GeneratedPassage,
        required_terms: list[str] | None = None,
    ) -> ValidationResult:
        """Ensure specified domain terms and general glossary items are present."""
        text_lower = passage.source_text.lower()
        issues: list[str] = []

        # Check required terms if provided
        missing_required = []
        if required_terms:
            for term in required_terms:
                if term.lower() not in text_lower:
                    missing_required.append(term)
            if missing_required:
                issues.append(f"Missing required terms: {', '.join(missing_required)}")

        # Check glossary consistency
        glossary_count = len(passage.terminology_glossary)
        if glossary_count == 0:
            issues.append("No terminology glossary was extracted for the passage.")

        score = 1.0 if not issues else max(0.0, 1.0 - (len(issues) * 0.3))
        return ValidationResult(
            is_valid=len(issues) == 0,
            score=round(score, 2),
            issues=issues,
            details={
                "glossary_terms_count": glossary_count,
                "missing_required": missing_required,
            },
        )

    async def check_duplication(
        self,
        passage: GeneratedPassage,
        existing_passages: list[dict[str, Any]],
        threshold: float = 0.85,
    ) -> DuplicationResult:
        """Check cosine similarity against existing passages in database using embeddings."""
        if not existing_passages:
            return DuplicationResult(
                is_duplicate=False,
                max_similarity=0.0,
                similar_passage_ids=[],
            )

        # Get embedding for candidate passage
        candidate_embeddings = await self.openai_client.embeddings([passage.source_text])
        candidate_vec = np.array(candidate_embeddings[0])

        similar_ids: list[str] = []
        max_sim = 0.0

        for item in existing_passages:
            item_vec_data = item.get("embedding")
            if not item_vec_data:
                continue
            item_vec = np.array(item_vec_data)
            # Cosine similarity
            sim = float(
                np.dot(candidate_vec, item_vec)
                / (np.linalg.norm(candidate_vec) * np.linalg.norm(item_vec) + 1e-9)
            )
            if sim > max_sim:
                max_sim = sim
            if sim >= threshold:
                similar_ids.append(item.get("id", "unknown"))

        return DuplicationResult(
            is_duplicate=len(similar_ids) > 0,
            max_similarity=round(max_sim, 4),
            similar_passage_ids=similar_ids,
        )

    async def validate_all(
        self,
        passage: GeneratedPassage,
        existing_passages: list[dict[str, Any]] | None = None,
        required_terms: list[str] | None = None,
    ) -> ComprehensiveValidationResult:
        """Run all validators and synthesize into a single report."""
        diff_res = self.validate_difficulty(passage)
        term_res = self.validate_terminology(passage, required_terms)
        dup_res = await self.check_duplication(passage, existing_passages or [])

        overall = diff_res.is_valid and term_res.is_valid and not dup_res.is_duplicate

        return ComprehensiveValidationResult(
            difficulty=diff_res,
            terminology=term_res,
            duplication=dup_res,
            overall_valid=overall,
        )
