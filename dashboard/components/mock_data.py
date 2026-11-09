"""Static demo payloads, shaped exactly like the backend's response schemas.

Only used when the user explicitly enables the sidebar "Demo data" toggle.
Shapes follow the OpenAPI spec (ScoringResult, OralAssessmentResult,
CalibrationReport, GeneratedPassage, ReviewItem, ComprehensiveValidationResult)
so the same rendering code handles demo and live data.
"""

from __future__ import annotations

_NOW = "2026-11-03T10:00:00Z"

_SOURCE = (
    "The parties hereby agree to submit any dispute arising out of or in connection with "
    "this contract to binding arbitration in accordance with the rules of the "
    "International Chamber of Commerce."
)
_CANDIDATE = (
    "يتفق الأطراف بموجب هذا على إحالة أي نزاع ينشأ عن هذا العقد أو يتصل به "
    "إلى التحكيم الملزم وفقاً لقواعد غرفة التجارة الدولية."
)


def _mu(uid: str, text: str, start: int, weight: float = 1.0) -> dict:
    return {
        "id": uid,
        "unit_type": "proposition",
        "source_text": text,
        "source_span": [start, start + len(text)],
        "importance_weight": weight,
    }


_MEANING_UNITS = [
    _mu("MU1", "The parties hereby agree", 0),
    _mu("MU2", "to submit any dispute", 25),
    _mu("MU3", "arising out of or in connection with this contract", 47, 1.2),
    _mu("MU4", "to binding arbitration", 98, 1.5),
    _mu("MU5", "in accordance with the rules of the International Chamber of Commerce", 121),
]

MOCK_SCORING_RESULT: dict = {
    "id": "demo-written-001",
    "exam_id": "demo-exam",
    "candidate_id": "demo-candidate",
    "overall_score": 17.0,
    "overall_max": 20.0,
    "percentage": 85.0,
    "confidence": 0.91,
    "criteria": [
        {
            "criterion": "accuracy",
            "score": 4.5,
            "max_score": 5.0,
            "summary": "Meaning transferred faithfully; one minor simplification.",
            "evidence": [
                {
                    "meaning_unit_id": "MU4",
                    "source_span": "binding arbitration",
                    "candidate_span": "التحكيم الملزم",
                    "issue": None,
                    "severity": None,
                    "is_positive": True,
                    "explanation": "Correct rendering of the legal concept.",
                },
                {
                    "meaning_unit_id": "MU5",
                    "source_span": "in accordance with the rules",
                    "candidate_span": "وفقاً لقواعد",
                    "issue": "Simplification",
                    "severity": "minor",
                    "is_positive": False,
                    "explanation": "Acceptable simplification of the prepositional phrase.",
                },
            ],
        },
        {
            "criterion": "completeness",
            "score": 4.0,
            "max_score": 5.0,
            "summary": "All meaning units present; MU3 partially rendered.",
            "evidence": [
                {
                    "meaning_unit_id": "MU3",
                    "source_span": "arising out of or in connection with",
                    "candidate_span": "ينشأ عن … أو يتصل به",
                    "issue": "Partial transfer",
                    "severity": "minor",
                    "is_positive": False,
                    "explanation": "Both alternatives present but condensed.",
                }
            ],
        },
        {
            "criterion": "terminology",
            "score": 4.5,
            "max_score": 5.0,
            "summary": "Standard legal terminology used throughout.",
            "evidence": [
                {
                    "meaning_unit_id": "MU5",
                    "source_span": "International Chamber of Commerce",
                    "candidate_span": "غرفة التجارة الدولية",
                    "issue": None,
                    "severity": None,
                    "is_positive": True,
                    "explanation": "Official institutional name used.",
                }
            ],
        },
        {
            "criterion": "fluency",
            "score": 4.0,
            "max_score": 5.0,
            "summary": "Natural legal register.",
            "evidence": [],
        },
    ],
    "meaning_units": _MEANING_UNITS,
    "alignments": [
        {"meaning_unit": _MEANING_UNITS[0], "candidate_text": "يتفق الأطراف بموجب هذا", "candidate_span": [0, 22], "alignment_confidence": 0.97},
        {"meaning_unit": _MEANING_UNITS[1], "candidate_text": "على إحالة أي نزاع", "candidate_span": [23, 40], "alignment_confidence": 0.95},
        {"meaning_unit": _MEANING_UNITS[2], "candidate_text": "ينشأ عن هذا العقد أو يتصل به", "candidate_span": [41, 69], "alignment_confidence": 0.78},
        {"meaning_unit": _MEANING_UNITS[3], "candidate_text": "إلى التحكيم الملزم", "candidate_span": [70, 88], "alignment_confidence": 0.99},
        {"meaning_unit": _MEANING_UNITS[4], "candidate_text": None, "candidate_span": None, "alignment_confidence": 0.0},
    ],
    "terminology_verdicts": [
        {"source_term": "binding arbitration", "expected_translation": "التحكيم الملزم", "candidate_translation": "التحكيم الملزم", "is_correct": True, "is_valid_alternative": False, "explanation": "Exact match."},
        {"source_term": "dispute", "expected_translation": "نزاع", "candidate_translation": "نزاع", "is_correct": True, "is_valid_alternative": False, "explanation": "Exact match."},
        {"source_term": "contract", "expected_translation": "عقد", "candidate_translation": "العقد", "is_correct": True, "is_valid_alternative": True, "explanation": "Definite form is a valid alternative."},
    ],
    "valid_alternatives_detected": 1,
    "flags": [],
    "scoring_metadata": {"rubric_version": "v1", "demo": True},
    "created_at": "2026-11-03T10:00:00Z",
}


def _words(text: str, step_ms: int = 300) -> list[dict]:
    return [
        {"word": w, "start_ms": i * step_ms, "end_ms": i * step_ms + step_ms - 20, "confidence": 0.95}
        for i, w in enumerate(text.split())
    ]


_ORAL_SRC = "The contract shall be governed by the laws of the United Arab Emirates."
_ORAL_CAND = "يخضع العقد لقوانين دولة الإمارات العربية المتحدة"

MOCK_ORAL_RESULT: dict = {
    "id": "demo-oral-001",
    "exam_id": "demo-exam",
    "candidate_id": "demo-candidate",
    "mode": "consecutive",
    "source_language": "en",
    "target_language": "ar",
    "source_transcript": {"text": _ORAL_SRC, "language": "en", "words": _words(_ORAL_SRC), "duration_seconds": 3.9, "overall_confidence": 1.0},
    "candidate_transcript": {"text": _ORAL_CAND, "language": "ar", "words": _words(_ORAL_CAND, 360), "duration_seconds": 2.5, "overall_confidence": 0.93},
    "alignments": [
        {"source_segment": "The contract", "source_start_ms": 0, "source_end_ms": 580, "candidate_segment": "العقد", "candidate_start_ms": 360, "candidate_end_ms": 700, "alignment_score": 0.98},
        {"source_segment": "shall be governed by", "source_start_ms": 600, "source_end_ms": 1780, "candidate_segment": "يخضع", "candidate_start_ms": 0, "candidate_end_ms": 340, "alignment_score": 0.88},
        {"source_segment": "the laws of the United Arab Emirates", "source_start_ms": 1800, "source_end_ms": 3880, "candidate_segment": "لقوانين دولة الإمارات العربية المتحدة", "candidate_start_ms": 720, "candidate_end_ms": 2500, "alignment_score": 0.95},
    ],
    "criteria": [
        {"criterion": "accuracy", "score": 4.5, "max_score": 5.0, "evidence": ["All propositions rendered."], "summary": "Faithful rendition."},
        {"criterion": "completeness", "score": 4.5, "max_score": 5.0, "evidence": [], "summary": "No omissions."},
        {"criterion": "fluency", "score": 3.5, "max_score": 5.0, "evidence": ["Pause of 1.2s before final clause."], "summary": "Minor hesitation."},
        {"criterion": "delivery", "score": 4.0, "max_score": 5.0, "evidence": [], "summary": "Clear delivery."},
    ],
    "overall_score": 16.5,
    "overall_max": 20.0,
    "audio_quality": {"duration_seconds": 2.5, "sample_rate": 16000, "estimated_snr_db": 32.5, "is_acceptable": True, "issues": []},
    "confidence": 0.88,
    "flags": [],
    "metadata": {"demo": True},
    "created_at": _NOW,
}

MOCK_CALIBRATION_REPORT: dict = {
    "report_id": "demo-report",
    "period_start": "2026-08-01T00:00:00Z",
    "period_end": _NOW,
    "total_assessments": 240,
    "human_reviewed": 60,
    "overrides": 9,
    "override_rate": 0.15,
    "mean_ai_human_difference": {"accuracy": 0.3, "completeness": 0.1, "terminology": -0.2, "fluency": -0.8},
    "cohens_kappa": 0.78,
    "icc": 0.84,
    "recommendations": [
        "Review fluency prompt: AI rates systematically lower than human assessors.",
        "Override rate within tolerance; continue routine sampling.",
    ],
    "created_at": _NOW,
}

MOCK_PASSAGES: list[dict] = [
    {
        "id": "demo-passage-001",
        "source_language": "en",
        "target_language": "ar",
        "source_text": _SOURCE,
        "model_answer": {
            "translation": _CANDIDATE,
            "key_terminology": [{"source_term": "binding arbitration", "target_term": "التحكيم الملزم", "domain": "legal", "alternatives": [], "notes": None}],
            "translation_notes": "Keep the formal legal register.",
            "difficulty_justification": "Dense legal clause with nested prepositional phrases.",
        },
        "domain": "legal",
        "difficulty": "C1",
        "cefr_level": "C1",
        "word_count": len(_SOURCE.split()),
        "terminology_glossary": [
            {"source_term": "binding arbitration", "target_term": "التحكيم الملزم", "domain": "legal", "alternatives": [], "notes": None},
            {"source_term": "dispute", "target_term": "نزاع", "domain": "legal", "alternatives": ["خلاف"], "notes": None},
            {"source_term": "International Chamber of Commerce", "target_term": "غرفة التجارة الدولية", "domain": "legal", "alternatives": [], "notes": None},
        ],
        "metadata": {"demo": True},
        "version": 1,
        "created_at": _NOW,
        "review_status": "pending_expert_review",
    }
]

MOCK_VALIDATION: dict = {
    "difficulty": {"is_valid": True, "score": 0.82, "issues": [], "details": {}},
    "terminology": {"is_valid": True, "score": 1.0, "issues": [], "details": {}},
    "duplication": {"is_duplicate": False, "max_similarity": 0.12, "similar_passage_ids": []},
    "overall_valid": True,
}


def mock_review_item(assessment_id: str, assessment_type: str, criteria: list[dict]) -> dict:
    """Build a ReviewItem-shaped dict for a demo assessment."""
    return {
        "id": f"demo-review-{assessment_id}",
        "assessment_id": assessment_id,
        "assessment_type": assessment_type,
        "candidate_id": "demo-candidate",
        "exam_id": "demo-exam",
        "ai_score": sum(c["score"] for c in criteria),
        "ai_confidence": 0.9,
        "priority": "medium",
        "status": "pending",
        "assigned_reviewer": None,
        "flags": [],
        "created_at": _NOW,
        "reviewed_at": None,
        "ai_criterion_scores": {c["criterion"]: c["score"] for c in criteria},
        "criterion_max": 5.0,
        "required_approvals": 1,
        "approvals": [],
        "final_score": None,
    }
