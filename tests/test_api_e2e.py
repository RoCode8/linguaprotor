"""End-to-end API tests: scoring → HITL triage → review/override → audit → calibration.

Each test is tagged with the acceptance criterion (AC-n) it validates;
see docs/problem_statement.md.
"""

from __future__ import annotations

import random

from fastapi.testclient import TestClient

from src.infrastructure.azure_speech_client import SpeechServiceError
from tests.conftest import (
    ALIGNMENT,
    CANDIDATE_TEXT,
    GLOSSARY,
    SOURCE_TEXT,
    FakeLLM,
    FakeSpeech,
    make_wav,
    rubric,
)

W = "/api/v1/scoring/written"


def written_payload(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "exam_id": "exam_uae_legal_c1",
        "candidate_id": "cand_0842",
        "source_text": SOURCE_TEXT,
        "candidate_translation": CANDIDATE_TEXT,
        "terminology_glossary": GLOSSARY,
    }
    body.update(overrides)
    return body


def queue_item_for(api: TestClient, assessment_id: str) -> dict:
    items = api.get("/api/v1/review/queue").json()
    return next(i for i in items if i["assessment_id"] == assessment_id)


# --------------------------------------------------------------------------- AC-1


def test_ac1_boots_without_azure_and_reports_unconfigured_backends(unconfigured: None) -> None:
    from src.api import dependencies as deps
    from src.main import app

    deps.reset_state()
    with TestClient(app) as client:
        health = client.get("/health").json()
        assert health["status"] == "healthy"
        assert health["backends"] == {"azure_openai": False, "azure_speech": False}
        assert health["audit_store"] == "in-memory"

        # Non-AI functionality works …
        assert client.get("/api/v1/review/queue").json() == []
        assert client.get("/api/v1/admin/usage").status_code == 200
        # … and AI endpoints fail loudly instead of fabricating output.
        r = client.post(W, json=written_payload())
        assert r.status_code == 503
        assert "not configured" in r.json()["detail"]


# --------------------------------------------------------------------------- AC-2 / AC-3


def test_ac2_ac3_written_scoring_full_packet_and_valid_alternative(
    api: TestClient, fake_llm: FakeLLM
) -> None:
    r = api.post(W, json=written_payload())
    assert r.status_code == 200, r.text
    res = r.json()

    assert [c["criterion"] for c in res["criteria"]] == [
        "accuracy", "completeness", "terminology", "fluency",
    ]
    assert all(c["max_score"] == 5.0 for c in res["criteria"])
    assert res["overall_score"] == 19.5 and res["overall_max"] == 20.0
    assert len(res["meaning_units"]) == 3
    assert all(a["candidate_span"] is not None for a in res["alignments"])

    # AC-3: regional variant recognised deterministically (tier 2, no LLM call)
    verdict = res["terminology_verdicts"][0]
    assert verdict["is_valid_alternative"] and verdict["is_correct"]
    assert verdict["candidate_translation"] == "هيئة الترخيص"
    assert res["valid_alternatives_detected"] == 1
    assert "written_scoring.terminology" not in fake_llm.calls
    assert res["scoring_metadata"]["weighted_coverage"] == 1.0

    # Retrievable by id
    assert api.get(f"/api/v1/scoring/{res['id']}").json()["id"] == res["id"]


def test_ac3_unknown_rendering_goes_to_contextual_llm_tier(api: TestClient, fake_llm: FakeLLM) -> None:
    glossary = [{"source": "licensing authority", "target": "سلطة الترخيص"}]  # short-form keys
    r = api.post(W, json=written_payload(terminology_glossary=glossary))
    assert r.status_code == 200, r.text
    assert "written_scoring.terminology" in fake_llm.calls
    v = r.json()["terminology_verdicts"][0]
    assert not v["is_correct"] and not v["is_valid_alternative"]


def test_ac2_completeness_capped_by_weighted_meaning_unit_coverage(
    api: TestClient, fake_llm: FakeLLM
) -> None:
    alignment = {"alignments": [dict(a) for a in ALIGNMENT["alignments"]]}
    alignment["alignments"][2] = {
        "meaning_unit_id": "mu_3", "candidate_text": None,
        "alignment_confidence": 0.9, "is_omitted": True,
    }
    fake_llm.responses["written_scoring.align"] = alignment
    # The model "forgets" the omission and awards full completeness.
    res = api.post(W, json=written_payload()).json()

    completeness = next(c for c in res["criteria"] if c["criterion"] == "completeness")
    # coverage = (1.0 + 0.6) / 2.6 = 0.615 → cap 5 × 0.615 = 3.08 → 3.0 on half-point grid
    assert completeness["score"] == 3.0
    assert "Capped" in completeness["summary"]
    assert "omissions_detected_1_units" in res["flags"]
    assert res["scoring_metadata"]["model_completeness_score"] == 5.0


def test_ac2_untrusted_llm_output_is_repaired_and_routed_to_dual_review(
    api: TestClient, fake_llm: FakeLLM
) -> None:
    fake_llm.responses["written_scoring.rubric"] = rubric(
        {"accuracy": 9.0, "fluency": 4.0}, confidence=0.99
    )
    res = api.post(W, json=written_payload()).json()

    scores = {c["criterion"]: c["score"] for c in res["criteria"]}
    assert scores == {"accuracy": 5.0, "completeness": 0.0, "terminology": 0.0, "fluency": 4.0}
    assert "score_clamped:accuracy" in res["flags"]
    assert "criterion_missing:completeness" in res["flags"]
    assert res["confidence"] <= 0.5
    item = queue_item_for(api, res["id"])
    assert item["priority"] == "critical" and item["required_approvals"] == 2


def test_ac2_empty_decomposition_is_an_error_not_a_score(api: TestClient, fake_llm: FakeLLM) -> None:
    fake_llm.responses["written_scoring.decompose"] = {"meaning_units": []}
    r = api.post(W, json=written_payload())
    assert r.status_code == 502
    assert api.get("/api/v1/review/queue").json() == []


def test_ac2_rejects_empty_submission(api: TestClient) -> None:
    assert api.post(W, json=written_payload(candidate_translation="")).status_code == 422


# --------------------------------------------------------------------------- AC-5 / AC-6


def test_ac5_high_confidence_is_auto_approved_and_audited(api: TestClient) -> None:
    res = api.post(W, json=written_payload()).json()
    item = queue_item_for(api, res["id"])
    assert item["status"] == "approved" and item["final_score"] == 19.5

    trail = api.get(f"/api/v1/admin/audit-trail/{res['id']}").json()
    assert [e["event_type"] for e in trail] == ["scoring_completed", "review_queued"]


def test_ac5_random_audit_sample_holds_auto_approvable_item(api: TestClient) -> None:
    class AlwaysSample(random.Random):
        def random(self) -> float:
            return 0.0

    api.state.review_engine._rng = AlwaysSample()  # type: ignore[attr-defined]
    res = api.post(W, json=written_payload()).json()
    item = queue_item_for(api, res["id"])
    assert item["status"] == "pending" and "random_audit_sample" in item["flags"]


def test_ac5_dual_assessor_panel_for_low_confidence(api: TestClient, fake_llm: FakeLLM) -> None:
    fake_llm.responses["written_scoring.rubric"] = rubric(confidence=0.40)
    res = api.post(W, json=written_payload()).json()
    item = queue_item_for(api, res["id"])
    assert item["priority"] == "critical" and item["required_approvals"] == 2

    url = f"/api/v1/review/{item['id']}/approve"
    first = api.post(url, json={"reviewer_id": "assessor_a"}).json()
    assert first["status"] == "in_review" and first["final_score"] is None
    assert api.post(url, json={"reviewer_id": "assessor_a"}).status_code == 409  # same person
    second = api.post(url, json={"reviewer_id": "assessor_b"}).json()
    assert second["status"] == "approved" and second["final_score"] == res["overall_score"]
    assert api.post(url, json={"reviewer_id": "assessor_c"}).status_code == 409  # closed

    events = [e["event_type"] for e in api.get(f"/api/v1/admin/audit-trail/{res['id']}").json()]
    assert events == ["scoring_completed", "review_queued", "review_approved", "review_approved"]


def test_ac5_override_uses_authoritative_ai_scores_and_feeds_calibration(
    api: TestClient, fake_llm: FakeLLM
) -> None:
    fake_llm.responses["written_scoring.rubric"] = rubric(confidence=0.80)  # standard review
    res = api.post(W, json=written_payload()).json()
    item = queue_item_for(api, res["id"])
    assert item["status"] == "pending"
    url = f"/api/v1/review/{item['id']}/override"

    bad = [
        ({"terminology": 7.0}, "Requires a score within range of the criterion maximum."),
        ({"style": 3.0}, "Unknown criterion is rejected with a justification."),
        ({"terminology": 3.0}, "short"),
    ]
    for scores, justification in bad:
        r = api.post(url, json={"reviewer_id": "a1", "overridden_scores": scores,
                                "justification": justification})
        assert r.status_code == 422, (scores, r.text)

    r = api.post(url, json={
        "reviewer_id": "a1",
        "overridden_scores": {"terminology": 3.0},
        # A spoofed baseline is ignored: originals come from the stored AI result.
        "original_scores": {"terminology": 0.0},
        "justification": "Candidate used a non-statutory term in the second clause.",
    })
    assert r.status_code == 200, r.text
    ov = r.json()
    assert ov["original_scores"]["terminology"] == 5.0
    assert ov["overridden_scores"] == {"accuracy": 5.0, "completeness": 5.0,
                                       "terminology": 3.0, "fluency": 4.5}

    closed = api.get(f"/api/v1/review/{item['id']}").json()
    assert closed["status"] == "overridden" and closed["final_score"] == 17.5
    assert api.post(url.replace("override", "approve"),
                    json={"reviewer_id": "a2"}).status_code == 409

    report = api.get("/api/v1/review/calibration/report").json()
    assert report["total_assessments"] == 1 and report["overrides"] == 1
    assert report["mean_ai_human_difference"]["terminology"] == 2.0

    trail = api.get(f"/api/v1/admin/audit-trail/{res['id']}").json()
    assert trail[-1]["event_type"] == "override_applied"
    assert trail[-1]["details"]["final_total"] == 17.5


def test_ac5_unknown_review_item_is_404(api: TestClient) -> None:
    r = api.post("/api/v1/review/nope/approve", json={"reviewer_id": "x"})
    assert r.status_code == 404


# --------------------------------------------------------------------------- AC-4


ORAL = "/api/v1/oral/assess"
ORAL_FORM = {
    "exam_id": "oral_c1",
    "candidate_id": "cand_7",
    "mode": "consecutive",
    "source_text": SOURCE_TEXT,
    "target_language": "ar",
}


def test_ac4_oral_assessment_end_to_end(api: TestClient, fake_speech: FakeSpeech) -> None:
    r = api.post(ORAL, data=ORAL_FORM, files={"audio_file": ("a.wav", make_wav(), "audio/wav")})
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["candidate_transcript"]["text"] == fake_speech.result.text
    assert res["overall_score"] == 17.0 and res["overall_max"] == 20.0
    assert res["confidence"] == 0.92
    assert res["audio_quality"]["sample_rate"] == 16_000
    assert fake_speech.calls[0][1] == "ar-AE"
    assert len(res["alignments"]) == 1
    assert queue_item_for(api, res["id"])["assessment_type"] == "oral"


def test_ac4_missing_or_unreadable_audio_is_rejected(api: TestClient) -> None:
    assert api.post(ORAL, data=ORAL_FORM).status_code == 422
    r = api.post(ORAL, data=ORAL_FORM, files={"audio_file": ("a.wav", b"not audio" * 200, "audio/wav")})
    assert r.status_code == 422
    assert api.get("/api/v1/review/queue").json() == []


def test_ac4_stt_failure_is_never_scored(api: TestClient, fake_speech: FakeSpeech, fake_llm: FakeLLM) -> None:
    fake_speech.result = SpeechServiceError("quota exceeded")
    r = api.post(ORAL, data=ORAL_FORM, files={"audio_file": ("a.wav", make_wav(), "audio/wav")})
    assert r.status_code == 502
    assert "NOT scored" in r.json()["detail"]
    assert "oral_scoring" not in fake_llm.calls
    assert api.get("/api/v1/review/queue").json() == []


def test_ac4_silent_recording_is_flagged_for_review(api: TestClient) -> None:
    silent = make_wav(freq=0.0)
    res = api.post(ORAL, data=ORAL_FORM, files={"audio_file": ("s.wav", silent, "audio/wav")}).json()
    assert "audio_quality_warning" in res["flags"]
    assert queue_item_for(api, res["id"])["status"] == "pending"
