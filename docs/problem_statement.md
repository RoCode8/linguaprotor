# LinguaProctor — Problem Statement

## 1. Context

Government and professional bodies that license legal and court translators and
interpreters (e.g. ministries of justice and translator accreditation boards in
the UAE/GCC) run high-stakes **English ↔ Arabic** examinations in two formats:

* **Written translation**: a candidate translates a statutory or contractual
  passage of 150–400 words.
* **Oral interpretation**: consecutive, simultaneous or dialogue interpretation,
  recorded as audio.

A pass grants a legal right to practise, so results are routinely **appealed**.
Every mark has to be defensible to an appeals panel and an accreditation auditor.

## 2. The problem

| Pain point | Consequence today |
|---|---|
| Manual marking takes a certified assessor 20–40 min per written script and longer per recording. | Results are released weeks late. Assessor capacity caps how often exams can run. |
| Marks are inconsistent between assessors and across sessions (drift, fatigue, leniency/severity bias). | The same script can pass or fail depending on who marks it, which is unfair and invites appeals. |
| Arabic has legitimate regional and register variation (UAE vs. GCC vs. Egyptian statutory phrasing, e.g. *سلطة الترخيص* vs. *هيئة الترخيص*). | Candidates are penalised for valid alternatives that are not the "model answer". |
| Generic MT metrics (BLEU, chrF, BERTScore) give one opaque number. | They can't tell a fatal legal omission ("within thirty days") from a harmless synonym, and they provide no evidence for appeals. |
| Oral exams need transcription before they can be marked. | It is slow, and quality problems in the recording are found only after marking. |
| Using AI naively in this process is dangerous. | An LLM can invent a score, a transcript or a justification. In a licensing exam that is worse than a slow human. |

## 3. Goal

Build an **AI-assisted, human-governed assessment service**. It produces an
explainable, evidence-backed provisional score for every written and oral
attempt. It releases routine high-confidence results automatically, sends
everything uncertain to certified assessors with the evidence they need, and
keeps the AI measurably calibrated against expert judgement.

The AI never has the last word on a doubtful case. It never fabricates an input
or an output. Every decision can be traced.

## 4. Users

| Persona | Needs |
|---|---|
| **Exam author** | Generate CEFR-calibrated (B2/C1/C2) passages with model answers and glossaries, validate them, and approve them before use. |
| **Assessor** | A prioritised review queue that shows meaning-unit evidence. They approve, override with a justification, or escalate. |
| **Chief examiner / QA** | Calibration statistics (κ, ICC) between AI and humans, cohort consistency, and a full audit trail. |
| **Platform / IT** | Runs on Azure, with costs visible and deployable from IaC. Runs locally and in CI without cloud credentials. |
| **Candidate** (indirect) | A fair mark. Not penalised for valid variants. An appeal is answered with evidence. |

## 5. Functional requirements

* **FR-1 Written scoring.** Decompose the source into weighted atomic *meaning
  units*, align them to the candidate translation, and score four criteria
  (Accuracy, Completeness, Terminology, Fluency; 0–5 each, 20 total). Cite
  source and candidate spans as evidence.
* **FR-2 Valid alternatives.** Recognise accepted regional or official variants
  from the reference lexicon before any model judgement, and never deduct for
  them.
* **FR-3 Oral scoring.** Screen audio quality, transcribe with word timestamps
  (Azure Speech), align to the source, and score Accuracy, Fluency,
  Comprehension and Delivery.
* **FR-4 HITL triage.** Every AI result enters a review queue, routed by
  confidence:

  | Confidence | Route |
  |---|---|
  | ≥ 0.90 | Auto-approve, with a 5% random audit sample |
  | 0.75–0.89 | Standard review |
  | 0.60–0.74 | Priority review |
  | < 0.60 | Dual-assessor panel |

  Any flag forces review.
* **FR-5 Overrides.** Assessors can override per-criterion scores within the
  scale. A justification is mandatory, the AI baseline comes from the system
  and not the client, and closed items are immutable.
* **FR-6 Audit.** Append-only trail of scoring, triage, assignment, approval,
  escalation, override and content decisions, retrievable per assessment.
* **FR-7 Calibration.** Cohen's κ (grade bands) and ICC(2,1) (absolute
  agreement) between AI and human scores, computed from both confirmations and
  overrides.
* **FR-8 Content generation.** Generate passages with model answers and
  glossaries, validate them, and approve them through an expert review
  workflow.
* **FR-9 Cost governance.** Record token usage and cost for every LLM call and
  check it against a budget.

## 6. Non-functional requirements

* **NFR-1 Integrity.** No fabricated transcripts, scores or audio metrics. When
  the system cannot assess, it says so (HTTP 4xx/5xx), and the attempt is not
  scored.
* **NFR-2 Untrusted model output.** LLM output is validated, clamped and
  repaired. Every repair is flagged and lowers confidence, so a human sees it.
* **NFR-3 Operability.** Boots with zero cloud configuration. AI endpoints
  return 503 until configured. CI runs without secrets.
* **NFR-4 Reproducibility.** Deterministic parts (coverage cap, lexicon tiers,
  triage, statistics) are unit-tested against hand-computed or published
  values.

## 7. Acceptance criteria and traceability

| AC | Criterion | Verified by |
|---|---|---|
| AC-1 | The service starts without Azure credentials. Health reports which backends are configured. Non-AI endpoints work. AI endpoints return 503. | `test_ac1_*`; live `uvicorn` smoke test |
| AC-2 | A written score always has exactly 4 criteria in [0, 5], max 20. Completeness ≤ 5 × importance-weighted coverage. Malformed model output is clamped/filled, flagged, and confidence capped. An empty decomposition is an error, not a score. | `test_ac2_*`, `test_written_scorer_flow` |
| AC-3 | Glossary alternatives are recognised deterministically with no LLM call and counted in `valid_alternatives_detected`. Unknown renderings go to the contextual LLM tier. | `test_ac2_ac3_*`, `test_ac3_*` |
| AC-4 | Oral: missing or unreadable audio → 422. STT failure → 502 and nothing scored or queued. Poor audio is flagged and routed to review. A successful run returns a transcript, 4 criteria and audio metrics. | `test_ac4_*`, `test_pipeline_never_fabricates_a_transcript` |
| AC-5 | Triage follows FR-4, including the random audit sample and dual review (two *distinct* assessors). Overrides are validated against the stored AI scores. Closed items return 409. | `test_ac5_*` |
| AC-6 | Each transition is in the audit trail of the assessment, in order. | `test_ac5_high_confidence_*`, `test_ac5_dual_*`, `test_ac5_override_*` |
| AC-7 | ICC(2,1) reproduces Shrout & Fleiss (1979) = 0.29. κ matches the hand computation. The regression suite fails (never passes) when items can't be scored or agreement is poor; the thresholds are MAE ≤ 12 pts, r ≥ 0.85, ICC ≥ 0.75. | `tests/test_calibration.py`, `tests/test_evaluation.py` |
| AC-8 | Every LLM call is cost-tracked. Invalid model JSON raises a typed error (→ 502). | `test_every_llm_call_is_cost_tracked`, `test_invalid_json_raises_typed_error` |

## 8. Out of scope (current release)

* Transcribing **source** audio. Oral exams take the source passage as text.
* Durable multi-replica persistence of results and the review queue. These are
  in-process, single replica ([ADR 0005](adr/0005-single-replica-api.md)). The
  **audit trail** is durable: it is persisted to Cosmos DB in Azure
  ([ADR 0004](adr/0004-append-only-audit-store.md)).
* Authentication and authorisation of assessors. Deploy behind the institution's
  identity provider.
* Live-model quality claims. Agreement with experts must be established by
  running `python -m evaluation.regression_tests` against the real Azure
  deployment, on a much larger expert-scored set than the 5 bundled samples.
