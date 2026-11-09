"""Tests for written translation scoring engine and meaning units."""

from __future__ import annotations

import pytest

from src.scoring.meaning_unit_decomposer import MeaningUnitDecomposer
from src.scoring.schemas import ScoringRequest
from src.scoring.written_scorer import ScoringError, WrittenTranslationScorer


@pytest.mark.asyncio
async def test_meaning_unit_decomposition(mock_openai_client):
    mock_openai_client.chat_completion_json.return_value = {
        "meaning_units": [
            {
                "id": "mu_1",
                "unit_type": "main_clause",
                "source_text": "The licensee shall notify the authority",
                "source_span": [0, 39],
                "importance_weight": 1.0,
            }
        ]
    }

    decomposer = MeaningUnitDecomposer(mock_openai_client)
    units = await decomposer.decompose("The licensee shall notify the authority")
    assert len(units) == 1
    assert units[0].id == "mu_1"
    assert units[0].importance_weight == 1.0


@pytest.mark.asyncio
async def test_written_scorer_flow(mock_openai_client):
    mock_openai_client.chat_completion_json.side_effect = [
        # Decomposer output
        {
            "meaning_units": [
                {
                    "id": "mu_1",
                    "unit_type": "main_clause",
                    "source_text": "Notify the authority",
                    "source_span": [0, 20],
                    "importance_weight": 1.0,
                }
            ]
        },
        # Aligner output
        {
            "alignments": [
                {
                    "meaning_unit_id": "mu_1",
                    "candidate_text": "إخطار الهيئة",
                    "alignment_confidence": 0.95,
                    "is_omitted": False,
                }
            ]
        },
        # Rubric Evaluator output
        {
            "criteria": [
                {
                    "criterion": "accuracy",
                    "score": 5.0,
                    "max_score": 5.0,
                    "summary": "Flawless semantic transmission.",
                    "evidence": [],
                },
                {
                    "criterion": "completeness",
                    "score": 5.0,
                    "max_score": 5.0,
                    "summary": "No omissions.",
                    "evidence": [],
                },
            ],
            "terminology_verdicts": [],
            "valid_alternatives_detected": 0,
            "confidence": 0.95,
            "flags": [],
        },
    ]

    scorer = WrittenTranslationScorer(mock_openai_client)
    req = ScoringRequest(
        exam_id="ex-1",
        candidate_id="cand-1",
        source_text="Notify the authority",
        candidate_translation="إخطار الهيئة",
    )
    result = await scorer.score(req)

    # The model only returned 2 of the 4 rubric criteria: the missing ones are
    # scored 0, flagged, and confidence is capped so the item goes to a human.
    assert result.overall_score == 10.0
    assert result.overall_max == 20.0
    assert result.percentage == 50.0
    assert {"criterion_missing:terminology", "criterion_missing:fluency"} <= set(result.flags)
    assert result.confidence == 0.5
    assert len(result.meaning_units) == 1


# --- Units the aligner model forgets to report must not count against the candidate ---

_FULL_RUBRIC = {
    "criteria": [
        {"criterion": c, "score": 5.0, "summary": "ok", "evidence": []}
        for c in ("accuracy", "completeness", "terminology", "fluency")
    ],
    "terminology_verdicts": [],
    "confidence": 0.95,
    "flags": [],
}


def _units(*weights):
    return {
        "meaning_units": [
            {
                "id": f"mu_{i}",
                "unit_type": "main_clause",
                "source_text": f"unit {i}",
                "source_span": [0, 1],
                "importance_weight": w,
            }
            for i, w in enumerate(weights, start=1)
        ]
    }


def _aligned(unit_id):
    return {
        "meaning_unit_id": unit_id,
        "candidate_text": "نص",
        "alignment_confidence": 0.9,
        "is_omitted": False,
    }


def _request():
    return ScoringRequest(
        exam_id="ex-1",
        candidate_id="cand-1",
        source_text="unit 1. unit 2. unit 3. unit 4.",
        candidate_translation="نص",
    )


@pytest.mark.asyncio
async def test_unreported_unit_is_flagged_not_penalised(mock_openai_client):
    # The aligner silently skips mu_1 (the heaviest unit); the other three are rendered.
    mock_openai_client.chat_completion_json.side_effect = [
        _units(1.0, 1.0, 0.5, 0.5),
        {"alignments": [_aligned("mu_2"), _aligned("mu_3"), _aligned("mu_4")]},
        dict(_FULL_RUBRIC),
    ]
    result = await WrittenTranslationScorer(mock_openai_client).score(_request())

    completeness = next(c for c in result.criteria if c.criterion == "completeness")
    assert result.scoring_metadata["weighted_coverage"] == 1.0
    assert completeness.score == 5.0  # not capped to 3.5 for the model's failure
    assert "alignment_incomplete_1_units" in result.flags
    assert not any(f.startswith("omissions_detected") for f in result.flags)
    assert result.confidence <= 0.74  # still routed to a human reviewer

    # The rubric model is told the unit is unknown, not that the candidate omitted it.
    rubric_prompt = str(mock_openai_client.chat_completion_json.call_args_list[2])
    assert "NOT ALIGNED" in rubric_prompt


@pytest.mark.asyncio
async def test_explicit_omission_still_caps_completeness(mock_openai_client):
    mock_openai_client.chat_completion_json.side_effect = [
        _units(1.0, 1.0, 0.5, 0.5),
        {
            "alignments": [
                {"meaning_unit_id": "mu_1", "candidate_text": None,
                 "alignment_confidence": 0.9, "is_omitted": True},
                _aligned("mu_2"), _aligned("mu_3"), _aligned("mu_4"),
            ]
        },
        dict(_FULL_RUBRIC),
    ]
    result = await WrittenTranslationScorer(mock_openai_client).score(_request())

    completeness = next(c for c in result.criteria if c.criterion == "completeness")
    assert completeness.score == 3.5  # 5 x 2.0/3.0, half-point grid
    assert "omissions_detected_1_units" in result.flags


@pytest.mark.asyncio
async def test_alignment_reporting_no_units_is_an_error(mock_openai_client):
    mock_openai_client.chat_completion_json.side_effect = [
        _units(1.0, 0.5),
        {"alignments": []},
        dict(_FULL_RUBRIC),
    ]
    with pytest.raises(ScoringError):
        await WrittenTranslationScorer(mock_openai_client).score(_request())
