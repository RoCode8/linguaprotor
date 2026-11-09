"""Tests for content generation and validation modules."""

from __future__ import annotations

import pytest

from src.content_generation.generator import ContentGenerator
from src.content_generation.schemas import (
    ContentGenerationRequest,
    ExamDifficulty,
    ExamDomain,
    GeneratedPassage,
    ModelAnswer,
)
from src.content_generation.validator import ContentValidator


@pytest.mark.asyncio
async def test_generate_passage(mock_openai_client):
    mock_openai_client.chat_completion_json.return_value = {
        "source_text": "The competent licensing authority shall review any corporate alteration.",
        "model_answer": {
            "translation": "تقوم سلطة الترخيص المختصة بمراجعة أي تغيير مؤسسي.",
            "key_terminology": [
                {
                    "source_term": "licensing authority",
                    "target_term": "سلطة الترخيص",
                    "alternatives": ["هيئة الترخيص"],
                }
            ],
            "translation_notes": "Standard UAE administrative phrasing.",
            "difficulty_justification": "C1 level administrative legislation.",
        },
        "terminology_glossary": [
            {
                "source_term": "licensing authority",
                "target_term": "سلطة الترخيص",
                "alternatives": ["هيئة الترخيص"],
            }
        ],
    }

    generator = ContentGenerator(mock_openai_client)
    req = ContentGenerationRequest(
        domain=ExamDomain.LEGAL,
        difficulty=ExamDifficulty.C1,
        num_passages=1,
    )
    passages = await generator.generate_passages(req)

    assert len(passages) == 1
    p = passages[0]
    assert p.domain == ExamDomain.LEGAL
    assert p.difficulty == ExamDifficulty.C1
    assert "licensing authority" in p.source_text
    assert p.review_status == "pending_expert_review"


def test_validator_difficulty():
    validator = ContentValidator()
    passage = GeneratedPassage(
        id="test-1",
        source_language="en",
        target_language="ar",
        source_text="The commercial tribunal shall exercise exclusive judicial authority over the dispute arising from the international maritime concession agreement.",
        model_answer=ModelAnswer(
            translation="تختص المحكمة التجارية حصرياً بالنزاع.",
            key_terminology=[],
            translation_notes="",
            difficulty_justification="",
        ),
        domain=ExamDomain.LEGAL,
        difficulty=ExamDifficulty.C1,
        cefr_level="C1",
        word_count=16,
        terminology_glossary=[],
        metadata={},
        version=1,
        created_at="2026-01-01T00:00:00Z",
    )

    res = validator.validate_difficulty(passage)
    assert res.score > 0.0
    assert "avg_sentence_length" in res.details


@pytest.mark.asyncio
async def test_passage_generation_uses_large_token_budget_and_timeout(mock_openai_client):
    # A full passage + model translation + glossaries exceeds 4,096 tokens for
    # some domain/difficulty/language combinations (truncated JSON otherwise).
    mock_openai_client.chat_completion_json.return_value = {"source_text": "x"}
    await ContentGenerator(mock_openai_client).generate_passages(
        ContentGenerationRequest(domain=ExamDomain.FINANCIAL, difficulty=ExamDifficulty.C2)
    )
    kwargs = mock_openai_client.chat_completion_json.call_args.kwargs
    assert kwargs["max_tokens"] >= 16_000
    assert kwargs["timeout"] > 60
