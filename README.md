# 🌍 LinguaProctor — AI-Powered Multilingual Examination Platform

[![CI/CD Pipeline](https://github.com/your-username/linguaproctor/actions/workflows/ci-cd.yml/badge.svg)](https://github.com/your-username/linguaproctor/actions/workflows/ci-cd.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com)
[![Azure OpenAI](https://img.shields.io/badge/Azure-OpenAI-0078D4.svg)](https://azure.microsoft.com/en-us/products/ai-services/openai-service/)
[![Azure AI Speech](https://img.shields.io/badge/Azure-AI%20Speech-0078D4.svg)](https://azure.microsoft.com/en-us/products/ai-services/speech-services/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> **Enterprise-grade, explainable assessment platform for Written Translation and Oral Interpretation (English ↔ Arabic), featuring atomic Meaning-Unit decomposition, valid regional alternative recognition, Azure Speech processing, and confidence-gated Human-in-the-Loop review.**

---

## 🌟 Key Differentiators & JD Alignment

| Requirement in Upwork Job Description | LinguaProctor Solution |
|---|---|
| **Multilingual AI Content Generation** | Generates CEFR-calibrated (B2/C1/C2) translation passages from legislation/reference texts with metadata, model answers, glossary extraction, and automated difficulty/duplication validation. |
| **Rubric-Based Written Translation Scoring** | Evaluates **Accuracy, Completeness, Terminology, and Fluency** using **Meaning-Unit Decomposition** rather than string-matching, providing verbatim evidence citations for every deduction. |
| **Semantic Equivalence & Valid Alternatives** | Recognizes accepted regional synonyms (e.g. UAE vs GCC legal terminology variants) using embeddings + contextual LLM reasoning to prevent unfair point deductions. |
| **Oral Interpretation & Speech Pipelines** | Supports **Consecutive, Simultaneous, and Dialogue** interpretation via **Azure AI Speech (STT/TTS)** with word-level alignment metadata and audio quality screening. |
| **Human-in-the-Loop & Calibration** | Confidence-gated triage routing ($\ge90\%$ auto-approved, borderline prioritized, $<60\%$ mandatory dual-review), assessor override workflows, and **Cohen's Kappa & ICC** calibration reporting. |
| **Azure Cloud & Governance** | Keyless Azure deployment (managed identities, no secrets) via `azd up`: Azure OpenAI (gpt-5.1), Azure AI Speech, Cosmos DB append-only audit trail, Container Apps, OpenTelemetry tracing to Application Insights. Bicep IaC validated against ARM. |

---

## 🏛️ System Architecture

```
                  ┌────────────────────────────────────────┐
                  │      Reviewer Workspace (Streamlit)    │
                  │  (Exam Builder, Evidence, Audio Review)│
                  └───────────────────┬────────────────────┘
                                      │ REST API / SSE
                                      ▼
                  ┌────────────────────────────────────────┐
                  │       LinguaProctor API (FastAPI)      │
                  │                                        │
                  │ ┌────────────────┐  ┌────────────────┐ │
                  │ │Content Generator│  │ Written Scorer │ │
                  │ └────────────────┘  └────────────────┘ │
                  │ ┌────────────────┐  ┌────────────────┐ │
                  │ │ Oral Pipeline  │  │ HITL & Calib.  │ │
                  │ └────────────────┘  └────────────────┘ │
                  └───────┬────────────────────────┬───────┘
                          │                        │
         ┌────────────────┴──────┐          ┌──────┴────────────────┐
         ▼                       ▼          ▼                       ▼
┌──────────────────┐    ┌──────────────┐ ┌──────────────┐ ┌───────────────────┐
│  Azure OpenAI    │    │ Azure Speech │ │ Azure Cosmos │ │ App Insights /    │
│ (gpt-5.1 + emb.) │    │    (STT)     │ │ (Audit trail)│ │ Log Analytics     │
└──────────────────┘    └──────────────┘ └──────────────┘ └───────────────────┘
        All data-plane access: Microsoft Entra ID (managed identity) — no keys
```

See [docs/infrastructure.md](docs/infrastructure.md) for the full Azure
architecture, resource inventory and identity model.

---

## 🚀 Quick Start

### 1. Prerequisites
- Python 3.11+
- Azure Subscription (or run in mock development mode)

### 2. Installation
```bash
git clone https://github.com/your-username/linguaproctor.git
cd linguaproctor

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies with dashboard and dev tools
pip install -e ".[dev,dashboard]"
```

### 3. Environment Configuration
```bash
cp .env.example .env
# Edit .env with your Azure OpenAI and Azure Speech credentials
```
All Azure settings are optional. Without them the API still starts. The review
queue, audit and admin endpoints work, and AI endpoints return **HTTP 503**
rather than inventing results. `GET /health` shows which backends are configured.

### 4. Running the Application
Launch both the backend API and the interactive reviewer workspace:

```bash
# Terminal 1: Launch FastAPI Backend (Port 8000)
uvicorn src.main:app --reload --port 8000

# Terminal 2: Launch Streamlit Reviewer Workspace (Port 8501)
streamlit run dashboard/app.py
```

Or run everything using **Docker Compose**:
```bash
docker-compose up --build
```

---

## ☁️ Deploy to Azure

```bash
azd auth login
azd env new linguaproctor-dev
azd env set AZURE_LOCATION uaenorth
azd up
```

Requires the Azure Developer CLI 1.22.1+. Docker isn't needed, because images
build in Azure Container Registry. No keys or secrets are created: the
application authenticates with managed identities. Prerequisites, options,
CI/CD (GitHub OIDC) and troubleshooting are in the
**[deployment guide](docs/deployment-guide.md)**.

---

## 📚 Documentation

| Document | Contents |
|---|---|
| [Problem statement](docs/problem_statement.md) | Users, requirements, acceptance criteria |
| [Architecture](docs/architecture.md) | Components and data flow |
| [Scoring methodology](docs/scoring_methodology.md) | Meaning units, rubric safeguards, HITL triage, calibration statistics |
| [API reference](docs/api_reference.md) | REST endpoints and error semantics |
| [Infrastructure reference](docs/infrastructure.md) | Azure resources, identity and access, configuration contract, validation |
| [Deployment guide](docs/deployment-guide.md) | `azd` deployment, CI/CD, troubleshooting |
| [Architecture decision records](docs/adr/README.md) | Key design decisions and their trade-offs |
| [Validation report](docs/validation_report.md) | Codebase and infrastructure validation findings |

---

## 🧪 Testing & Validation Suite

Run unit and integration tests:
```bash
pytest tests/ -v
```

The test suite needs no Azure credentials. It drives the full API, from scoring
through triage, review/override, audit and calibration, using scripted AI fakes.

Execute regression testing against expert benchmark translations (requires Azure OpenAI;
exit code 0 = pass, 1 = fail, 2 = skipped because not configured):
```bash
python -m evaluation.regression_tests
```

Run inter-rater reliability calibration analysis (Cohen's Kappa & ICC(2,1)):
```bash
python -m evaluation.inter_rater_agreement
```

See [docs/problem_statement.md](docs/problem_statement.md) for the problem
statement and acceptance criteria, and [docs/validation_report.md](docs/validation_report.md)
for the codebase validation findings.

---

## 📂 Project Structure

```
linguaproctor/
├── src/
│   ├── main.py                          # FastAPI application entrypoint
│   ├── config.py                        # Pydantic settings & Azure configuration
│   ├── content_generation/              # AI Passage & Exam generation
│   │   ├── generator.py                 # LLM generation with model answers
│   │   ├── validator.py                 # Difficulty, terminology, dedup validation
│   │   ├── schemas.py                   # Pydantic schemas
│   │   └── prompts/                     # Versioned YAML prompts
│   ├── scoring/                         # Written translation evaluation
│   │   ├── written_scorer.py            # Main scoring orchestrator
│   │   ├── meaning_unit_decomposer.py   # Atomic semantic unit extractor
│   │   ├── semantic_aligner.py          # Cross-lingual meaning alignment
│   │   ├── alternative_detector.py      # Regional synonym & alternative verifier
│   │   ├── rubric_evaluator.py          # Grounded 4-pillar rubric evaluator
│   │   └── consistency_checker.py       # Cohort variance and outlier detection
│   ├── oral/                            # Oral interpretation & speech pipeline
│   │   ├── interpretation_pipeline.py   # Consecutive, simultaneous, dialogue flows
│   │   ├── audio_processor.py           # Audio format & quality screening
│   │   ├── transcript_aligner.py        # Word-level timestamp alignment
│   │   └── oral_scorer.py               # Spoken fluency & accuracy evaluation
│   ├── hitl/                            # Human-in-the-Loop & Calibration
│   │   ├── review_engine.py             # Uncertainty-based priority queue
│   │   ├── override_handler.py          # Score overrides with audit justification
│   │   └── calibration.py               # Cohen's Kappa, ICC & bias detection
│   ├── governance/                      # Enterprise audit, privacy & safety
│   │   ├── audit_logger.py              # Immutable decision trail
│   │   ├── cost_tracker.py              # Token usage & budget alert monitor
│   │   ├── privacy.py                   # PII redaction engine
│   │   └── guardrails.py                # Scoring consistency guardrails
│   └── infrastructure/                  # Azure cloud SDK wrappers
│       ├── azure_openai_client.py       # Async Azure OpenAI client
│       ├── azure_speech_client.py       # Azure Speech STT/TTS wrapper
│       ├── cosmos_client.py             # Azure Cosmos DB client
│       ├── blob_storage.py              # Azure Blob client (not wired; no storage provisioned)
│       └── telemetry.py                 # OpenTelemetry & App Insights
├── dashboard/                           # Streamlit Reviewer Workspace
│   ├── app.py                           # Multi-page dashboard
│   ├── pages/                           # Exam builder, Score explorer, Calibration
│   └── components/                      # Evidence viewers, Heatmaps, Gauges
├── evaluation/                          # Regression & psychometric evaluation
│   ├── datasets/                        # Expert-scored translation corpora
│   ├── regression_tests.py              # Continuous automated regression
│   └── inter_rater_agreement.py         # Statistical agreement metrics
├── infra/                               # Bicep IaC (azd): see docs/infrastructure.md
│   ├── main.bicep                       # Platform (azd provision)
│   ├── app/                             # Container apps (azd deploy)
│   └── modules/                         # OpenAI, Speech, Cosmos, ACR, monitoring, identity
├── .github/workflows/ci-cd.yml          # GitHub Actions CI/CD pipeline
└── docs/                                # Technical & methodology documentation
```

---

## 📄 License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
