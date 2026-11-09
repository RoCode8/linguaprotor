# ADR 0002: GPT-5.1 as the default chat model

**Status:** Accepted · **Date:** 2026-11-04

## Context
The templates deployed `gpt-4o` version `2024-08-06` on the regional `Standard`
SKU. As of 2026-11-04:
- Microsoft's [model retirement schedule](https://learn.microsoft.com/azure/foundry/openai/concepts/model-retirement-schedule)
  lists every gpt-4o version as Deprecated or Legacy. All of them retire on
  **2027-04-14**, and the named replacement is **gpt-5.1**.
- "Deprecated" means [blocked for new customers](https://learn.microsoft.com/azure/foundry/openai/concepts/model-retirements):
  a subscription that has never deployed the version cannot create it. The
  target subscription's Models API reported `2024-08-06` as `Deprecating`, so
  provisioning would have failed. Regional Standard gpt-4o offers are also
  retired, and `uaenorth` offers no regional Standard gpt-4o at all.

## Decision
- Default chat deployment: **gpt-5.1 `2025-11-13`, GlobalStandard, 30K TPM**.
  It is GA, retires 2027-07-13, and is available in the target regions with
  quota. Default embeddings: **text-embedding-3-small `1`, GlobalStandard**
  (GA, retires 2028-02-09).
- Deployments use model-agnostic names (`chat`, `embedding`), so a model change
  needs no application configuration change.
- `versionUpgradeOption: OnceCurrentVersionExpired`. The service never swaps the
  grading model silently, because that would invalidate calibration.
- The application supports reasoning models. When
  `AZURE_OPENAI_CHAT_REASONING_MODEL=true` it sends `max_completion_tokens` and
  omits `temperature`. GPT-5.x rejects `temperature` and `max_tokens`
  ([reasoning models](https://learn.microsoft.com/azure/foundry/openai/how-to/reasoning)).
  GPT-5.1 defaults to `reasoning_effort: none`.

## Consequences
- Scoring is **not** bit-for-bit deterministic. Reasoning models don't accept
  `temperature=0`. Agreement with experts must be re-established on gpt-5.1
  with `python -m evaluation.regression_tests` before release. The HITL triage
  thresholds absorb residual variance.
- GlobalStandard may process prompts in any Azure region. If candidate data
  must stay in-country or in-zone, switch to `DataZoneStandard` (where offered),
  a regional `Standard` deployment, or provisioned throughput. Data Zone isn't
  offered in `uaenorth`.
- Model lifecycle is now an operational concern. Subscribe to Azure Service
  Health advisories for "Azure OpenAI Service", and plan the next migration
  before 2027-07-13.
