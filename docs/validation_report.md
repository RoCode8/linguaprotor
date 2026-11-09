# LinguaProctor — Codebase Validation Report

**Date:** 2026-11-03 (code), 2026-11-04 (infrastructure) · **Scope:** `src/`, `tests/`, `evaluation/`, `dashboard/`,
Docker/Compose, `infra/`, CI · **Baseline:** working tree before this validation
(no commits).

Requirements and acceptance criteria are in
[problem_statement.md](problem_statement.md).

## 1. Summary

The original codebase read like a complete product but **was not runnable**.
It could not be installed, the API crashed on import, and the existing tests
never exercised the API, so they couldn't catch either problem.

Beyond that, several defects violated the product's core promise of
trustworthy, human-governed, explainable marking:

* The oral pipeline **scored a hard-coded fake transcript** whenever audio was
  missing or speech-to-text failed. Because of an attribute mismatch,
  speech-to-text *always* failed against real Azure.
* AI results **never reached the human review queue**.
* Override baselines were **client-supplied**, so they could be spoofed.
* LLM scores were **trusted unvalidated**; for example, `score: 9` out of 5 was
  accepted.
* The offline regression suite **reported PASSED with no AI at all**.

All of these are fixed and covered by tests.

| | Before | After |
|---|---|---|
| `pip install -e .` | ❌ invalid build backend | ✅ |
| `import src.main` / `uvicorn` | ❌ `TypeError` at import | ✅ boots with or without Azure |
| Test suite | 6 pass / 2 fail, 0 API tests | **39 pass**, 16 end-to-end API tests |
| `ruff check src tests` | 123 errors | 0 (B008/UP042/E501 deliberately ignored) |
| Regression suite without AI | reports **PASSED** (fake) | exit 2 "SKIPPED" |

## 2. Defects found and fixed

Severity key: **C** = critical (integrity or service down) · **H** = wrong
results or broken feature · **M** = misleading or operational.

### Platform and configuration

| # | Sev | Defect | Fix |
|---|---|---|---|
| 1 | C | `pyproject.toml` build backend `setuptools.backends._legacy:_Backend` doesn't exist, so the package can't be installed. | `setuptools.build_meta` |
| 2 | C | `AuditLogger()` is called without its required argument in `routes_admin` and `OverrideHandler`, so **the API crashes on import**. | In-memory append-only audit container as the default; Cosmos container still supported |
| 3 | C | `main.py` reads `settings.app.env`, which doesn't exist (the field is `environment`), so startup crashes. | Corrected |
| 4 | H | Every Azure credential is *required*, so the documented mock/dev mode doesn't exist. | Credentials optional. `is_configured` flags. AI endpoints → 503 |
| 5 | H | Sub-settings are built by `default_factory`, so **`.env` was never read**. | `env_file=".env"` on every settings group (verified) |
| 6 | H | Most `.env.example`, Bicep and CI variable names were never read by the config (`AZURE_OPENAI_DEPLOYMENT_GPT4O`, `COSMOS_DATABASE`, `AZURE_STORAGE_*`, `APP_ENV`, unprefixed thresholds). | Renamed to the names config reads |

### Human-in-the-loop and governance

| # | Sev | Defect | Fix |
|---|---|---|---|
| 7 | C | Scoring and oral routes created a **new `ReviewEngine` per request**, and `/review/queue` read another instance, so **no AI result ever reached reviewers**. Audit and cost trackers had the same problem. | One `AppState` shared through FastAPI dependencies |
| 8 | C | Override "original scores" came from the request body, so the AI baseline, audit trail and calibration could be spoofed. Overridden scores had no range check. Closed items could be changed again. | Baseline from the stored AI result. Range and criterion validation. 409 on closed items |
| 9 | H | The documented **dual-assessor panel** (< 0.60) and **5% random audit** were not implemented. | Implemented: two *distinct* approvals; seeded/injectable sampling |
| 10 | H | Calibration only received *overrides*, which biased κ toward disagreement. `exam_id` was hard-coded to `"exam_override"`. | Confirmations and overrides both recorded, with real IDs |
| 11 | H | The "ICC(2,1)" was actually ICC(1,1), in two places. Kappa ignored its `num_bins` argument. | Correct ICC(2,1). Reproduces Shrout & Fleiss 0.29. One shared implementation |
| 12 | M | LLM token cost was never recorded, so `/admin/usage` always showed zero. | Every chat and embedding call is cost-tracked |

### Written scoring

| # | Sev | Defect | Fix |
|---|---|---|---|
| 13 | H | The rubric prompt never specified the JSON output it is parsed into. | Explicit output contract in `v1_scoring_rubric.yaml` |
| 14 | H | LLM criterion scores, `max_score` and criterion set were trusted as given: a score of 9/5 or 2 criteria out of 20 points passed straight through. | Exactly 4 criteria, clamped, fixed max. Repairs flagged and confidence capped |
| 15 | H | `AlternativeDetector` was constructed but **never called**. Glossary keys didn't match (`source_term` vs `source`), and the glossary type rejected `alternatives` lists. | Three-tier detector wired in. Typed `GlossaryTerm` accepts both shapes |
| 16 | H | Importance weights were unused, so a model could award full completeness for an omitted unit. | Completeness capped by importance-weighted coverage |
| 17 | M | An out-of-range weight or confidence from the LLM caused a `ValidationError` (500). Empty decomposition scored 0/20. Units the model forgot to align counted as candidate omissions. | Values clamped. Empty decomposition → 502. Unreported units flagged rather than blamed on the candidate |
| 18 | M | `rubric_version` (user input) built a file path, a path-traversal vector introduced during this refactor. | Constrained to `^v\d+$` |

### Oral pipeline

| # | Sev | Defect | Fix |
|---|---|---|---|
| 19 | C | No audio, or STT failure → the pipeline **scored a hard-coded Arabic sentence** and reported invented audio metrics (15 s, 25 dB SNR). | Removed. 422 for bad input, 502 for STT failure; nothing scored or queued |
| 20 | C | The pipeline read `res.duration_seconds` and `res.overall_confidence`, which the speech client doesn't return, so **every real STT call fell into the fake transcript**. | Client returns `confidence` and `duration_seconds` |
| 21 | H | `TranscriptAligner()` was built without its client and called the raw SDK on the wrapper; this caused the 2 original test failures. | Uses the project client wrapper |
| 22 | H | `recognize_once()` transcribes only the first utterance, which truncates interpretations. Raw WAV bytes, header included, were pushed as PCM. Audio was never normalised. | Continuous recognition. Header stripped and real format declared. 16 kHz mono normalisation |

### Evaluation, deployment and CI

| # | Sev | Defect | Fix |
|---|---|---|---|
| 23 | C | `regression_tests.py`: on any scoring failure, AI score = expert score, so with no AI it reported MAE 0, r = 1, PASSED. | Failures fail. Unconfigured → exit 2 (skipped). Adds ICC and κ gates |
| 24 | H | Compose built the last Dockerfile stage (the dashboard) for the `api` service, so the API never listened, the healthcheck failed and the dashboard never started. The dashboard pointed at its own `localhost`. | `target:` per service. `LINGUAPROCTOR_API_URL=http://api:8000` |
| 25 | H | Dockerfile ran `pip install -e` before `README.md` existed in the image. There was no `.dockerignore`, so `.venv` and `.env` secrets would be baked into the image. | Copy, then install. `.dockerignore` added |
| 26 | H | The CI workflow lived at `infra/.github/…`, which GitHub never runs. It set the wrong env names, ran the wrong script as the "regression suite", and built the wrong image. | Moved to `.github/workflows/`. Fixed steps. Regression runs only with secrets |
| 27 | M | `azd` needs an `AZURE_CONTAINER_REGISTRY_ENDPOINT` output, which was missing. | Added. `az bicep build` passes |
| 28 | H | Dashboard: every page called wrong paths or fields (`/calibration/report`, `/oral/assessments/{id}`, `candidate_text`, wrong enums). Failures **silently fell back to mock data**. `dashboard.*` imports failed under `streamlit run`. | See §4 |

## 3. Validation evidence

```
$ pytest -q                      → 39 passed
$ ruff check src tests           → All checks passed!
$ python -m evaluation.regression_tests   (no Azure)   → "SKIPPED", exit 2
$ python -m evaluation.inter_rater_agreement           → κ 1.000, ICC(2,1) 0.996
$ az bicep build --file infra/main.bicep               → OK (linter warnings only)
Live: uvicorn src.main:app with an empty environment
  GET  /health                   → 200, backends {azure_openai: false, azure_speech: false}
  GET  /api/v1/review/queue      → 200 []
  POST /api/v1/scoring/written   → 503 "Azure OpenAI is not configured…"
  GET  /docs                     → 200
```

The end-to-end suite (`tests/test_api_e2e.py`) drives the real FastAPI app
through each acceptance criterion: scoring → triage → dual approval and
override → audit trail → calibration report. It replaces only the two AI
backends, with deterministic fakes.

## 4. Dashboard

All four pages were aligned to the live OpenAPI contract: paths, payloads,
enum values and response field names. Shared helpers are in
`dashboard/components/api.py`, `mock_data.py` and `review_panel.py`.

* **No silent mock fallback.** Demo data appears only when the sidebar
  "Demo data" toggle is on, and it is clearly labelled. A failed request shows
  the backend's error (503/502/422/404/409).
* Review actions (approve, override, escalate) call the real endpoints. The old
  buttons said "Override recorded" without calling anything.
* Imports work under `streamlit run dashboard/app.py`. The Streamlit floor was
  raised to 1.46 for `width="stretch"`.

Verification:

* `py_compile` passes on all files.
* Streamlit `AppTest` passed on all 4 pages in demo mode and with the API
  unreachable: no exceptions, and errors shown correctly.
* **Live round-trip:** Score Explorer was run against a real `uvicorn` backend
  with only the LLM swapped for the scripted fake. The result was 19.5/20,
  97.5%, status "approved", and the item appeared in `/review/queue`.

## 5. Infrastructure validation (2026-11-04)

A second pass reviewed `infra/`, `azure.yaml`, the Dockerfile and CI against
what the application actually needs. It also checked live Azure data: model
lifecycle, quota and role definitions in the target subscription. The rebuilt
infrastructure is documented in [infrastructure.md](infrastructure.md), with
design decisions in [adr/](adr/README.md).

### Defects found and fixed

| # | Sev | Defect | Fix |
|---|---|---|---|
| I-1 | C | Chat model gpt-4o `2024-08-06` on regional `Standard` is **blocked for new customers**. The subscription's Models API reports it as `Deprecating`, and regional Standard gpt-4o is retired. **Provisioning would fail.** | gpt-5.1 `2025-11-13` GlobalStandard ([ADR 0002](adr/0002-chat-model-gpt-5-1.md)) |
| I-2 | C | GPT-5.x rejects `temperature`/`max_tokens`, which the app sent on every call. Switching models alone would break all scoring. | Reasoning-model request shape (`max_completion_tokens`, no temperature) |
| I-3 | C | Every `azd provision` reset the live images to the hello-world placeholder (no "exists" handling). The placeholder's port 80 didn't match ingress 8000/8501. | Revision-based deployment: apps in `infra/app/*.bicep` ([ADR 0003](adr/0003-revision-based-container-deployment.md)) |
| I-4 | C | The API ran with `maxReplicas: 3` while the review queue is in-process, so state would split or be lost. | Pinned to 1 replica ([ADR 0005](adr/0005-single-replica-api.md)) |
| I-5 | H | Keys everywhere: OpenAI, Speech, Cosmos and storage keys in container secrets and in **deployment outputs** (persisted in deployment history and `.azure/`). The ACR admin user was enabled. | Local auth disabled on every service. Per-workload managed identities with least-privilege RBAC. Zero secrets ([ADR 0001](adr/0001-keyless-authentication.md)) |
| I-6 | H | The Cosmos `audit` container was partitioned on `/id` while the code writes and queries `partitionKey`, so trail queries would return nothing. Cosmos wasn't used by the app at all. | `/partitionKey`. Audit trail wired to Cosmos with an append-only custom role ([ADR 0004](adr/0004-append-only-audit-store.md)) |
| I-7 | H | `enableFreeTier: true` on Cosmos fails if the subscription already has a free-tier account. | Serverless capacity mode |
| I-8 | H | Async Azure SDKs need `aiohttp`, which wasn't a dependency. Every keyless call would raise `ModuleNotFoundError` in Azure. | Declared dependency; caught by new tests |
| I-9 | H | A new OpenAI client (connection pool) was built **per request**, and never closed. | Process-wide clients, closed on shutdown |
| I-10 | M | Storage account and two Cosmos containers were provisioned but unused. | Removed ([ADR 0006](adr/0006-provision-only-what-runs.md)) |
| I-11 | M | No health probes, no diagnostic settings, the API publicly exposed, and the App Insights connection string unused (telemetry never initialised). | Probes, `allLogs` diagnostics to Log Analytics, internal API ingress, OpenTelemetry wired |
| I-12 | M | The Dockerfile ran as root, with build tools in the runtime image and an unpinned Debian release (Speech SDK libraries are release-specific). | Multi-stage, non-root, `bookworm` pinned, `--proxy-headers` |
| I-13 | M | Copying `.env.example` as documented enabled placeholder endpoints. | Endpoints commented out: the copy is offline by default |
| I-14 | M | CI regression used an API key the new infra rejects. There was no deploy pipeline. | GitHub OIDC login. `azure-dev.yml` manual deploy |

### Evidence

All checks were read-only. The subscription was verified unchanged afterwards.

| Check | Result |
|---|---|
| `az bicep build` with lint rules promoted to errors (`infra/bicepconfig.json`) | Pass, all templates |
| ARM preflight (`az deployment sub validate`), `uaenorth` | **Succeeded** |
| ARM what-if | 22 creates. 5 role assignments "Unsupported" (names depend on identities created in the same deployment). |
| Models API / quota (`uaenorth`, `swedencentral`, `eastus2`) | Chosen models GA; quota available |
| `tests/test_infrastructure.py` | 12 pass; 5 of 5 injected defects caught |
| `tests/test_azure_auth.py` | 6 pass |
| Full suite | **57 passed**, ruff clean |

## 6. Not validated / residual risks

* **No `azd up` was run.** It creates billable resources. The app modules
  (`infra/app/*.bicep`) can't be what-if'd before the platform exists, and the
  container images weren't built (no Docker locally; azd builds in ACR). Run
  the [deployment guide](deployment-guide.md) in a sandbox subscription first.
* **Live model quality is unverified.** Scoring now targets gpt-5.1, a
  reasoning model with no `temperature=0`. Agreement with experts must be
  re-established with `python -m evaluation.regression_tests`. The bundled
  5-sample expert set is far too small to establish validity; a defensible
  claim needs hundreds of double-marked scripts. The continuous-recognition
  STT code follows the SDK's documented pattern but hasn't run against the
  service.
* **Single-replica state.** Results, the review queue and calibration
  datapoints are in-process and lost on restart. The audit trail is durable in
  Cosmos. See [ADR 0005](adr/0005-single-replica-api.md).
* **No authentication.** Any caller who can reach the dashboard can approve or
  override as any `reviewer_id`. Enable Container Apps authentication
  (Entra ID) before use with real candidates.
* **Data residency.** GlobalStandard deployments may process prompts outside
  the UAE. Confirm with the regulator; alternatives are in
  [infrastructure.md](infrastructure.md#model-lifecycle).
* **Unused modules.** `governance/privacy.py` (Presidio),
  `governance/guardrails.py` (its 0–10 scale doesn't match the 0–5 rubric),
  `governance/prompt_registry.py` and the Blob client aren't wired into any
  request path. `/admin/prompts` returns a static list.
* **Source audio** transcription for oral exams is not supported; source text
  is required.
* `README.md` references a `LICENSE` file that doesn't exist.
