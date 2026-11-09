# 📡 LinguaProctor — REST API Specification

Base URL: `http://localhost:8000/api/v1` (or your Azure Container App endpoint)

Interactive OpenAPI Documentation: `http://localhost:8000/docs`

---

## Content Generation

### `POST /content/generate`
Generates CEFR-calibrated examination passages with model translations and terminology glossaries.

**Request Body:**
```json
{
  "source_language": "en",
  "target_language": "ar",
  "domain": "legal",
  "difficulty": "C1",
  "reference_texts": ["UAE Federal Decree Law No. (33) of 2021..."],
  "num_passages": 1,
  "include_model_answer": true,
  "specific_terminology": ["arbitration tribunal", "jurisdiction", "discretionary power"]
}
```

---

## Written Translation Scoring

### `POST /scoring/written`
Performs explainable four-pillar scoring anchored in meaning units and valid alternative detection.

**Request Body:**
```json
{
  "exam_id": "exam_uae_legal_c1",
  "candidate_id": "cand_0842",
  "source_text": "The licensee shall notify the competent licensing authority in writing within thirty days.",
  "candidate_translation": "يجب على المرخص له إخطار سلطة الترخيص المختصة كتابةً خلال ثلاثين يوماً.",
  "source_language": "en",
  "target_language": "ar",
  "rubric_version": "v1"
}
```

**Response Output (Snippet):**
```json
{
  "id": "c7a8b492-31fa-4f28-9273-df289a0c102a",
  "overall_score": 19.5,
  "overall_max": 20.0,
  "percentage": 97.5,
  "confidence": 0.94,
  "criteria": [
    {
      "criterion": "accuracy",
      "score": 5.0,
      "max_score": 5.0,
      "evidence": []
    }
  ],
  "valid_alternatives_detected": 1,
  "flags": []
}
```

---

## Oral Examination & Speech

### `POST /oral/assess`
Accepts candidate audio recording, transcribes with word timestamps, aligns with source text, and grades oral interpretation.

**Form Data:**
- `exam_id`: string
- `candidate_id`: string
- `mode`: `consecutive` | `simultaneous` | `dialogue`
- `source_language`: `en`
- `target_language`: `ar`
- `source_text`: string (**required**: the passage the candidate interpreted)
- `audio_file`: **required**, PCM WAV (any sample rate; normalised to 16 kHz mono before STT; max 100 MB)

Errors: `422` missing/unreadable audio or unsupported language · `502` speech-to-text failed (the attempt is **not** scored) · `503` Azure Speech/OpenAI not configured.

---

## Human-in-the-Loop & Calibration

Every written and oral result is automatically triaged into the review queue
(see `docs/scoring_methodology.md` §3). Review items carry `ai_criterion_scores`,
`required_approvals` (2 for the dual-assessor panel), `approvals` and `final_score`.

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/review/queue?status=&priority=` | – | Most urgent first, oldest first within a tier |
| GET | `/review/{item_id}` | – | 404 if unknown |
| POST | `/review/{item_id}/assign` | `{"reviewer_id"}` | |
| POST | `/review/{item_id}/approve` | `{"reviewer_id"}` | Dual-review items need a second, *different* assessor; 409 on repeat or closed item |
| POST | `/review/{item_id}/escalate` | `{"reviewer_id", "reason"}` | Raises to CRITICAL, requires dual sign-off |
| POST | `/review/{item_id}/override` | `{"reviewer_id", "overridden_scores": {"terminology": 3.0}, "justification"}` | Baseline taken from stored AI scores; scores must be within [0, 5]; justification ≥ 10 chars (422 otherwise) |
| GET | `/review/overrides?reviewer_id=` | – | |
| GET | `/review/calibration/report` | – | Cohen's κ (bands), ICC(2,1), mean AI−human difference per criterion, override rate |

## Governance

| Method | Path | Notes |
|---|---|---|
| GET | `/admin/audit-trail/{assessment_id}` | Append-only events: `scoring_completed`, `review_queued`, `review_assigned`, `review_approved`, `review_escalated`, `override_applied`, … |
| GET | `/admin/usage` | Token usage & cost of every LLM call |
| GET | `/admin/budget` | Monthly budget utilisation |
| GET | `/health` | Includes `backends.azure_openai` / `backends.azure_speech` configuration status |
