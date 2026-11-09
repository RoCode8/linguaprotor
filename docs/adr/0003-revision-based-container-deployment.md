# ADR 0003: Revision-based Container Apps deployment with azd

**Status:** Accepted · **Date:** 2026-11-04

## Context
The container apps were defined in `main.bicep` with the placeholder image
`containerapps-helloworld` and no "exists" handling. This caused three problems:
1. Every `azd provision` reset the live image to the placeholder, an outage.
2. The placeholder listens on port 80 while ingress targets 8000/8501, so the
   first revision is unhealthy.
3. Health probes can't target the real app port without failing on the
   placeholder.

`azd` documents two strategies ([Container Apps workflows](https://learn.microsoft.com/azure/developer/azure-developer-cli/container-apps-workflows)):
image-based (with an upsert/"exists" pattern) and revision-based.

## Decision
Use the **revision-based** strategy. `infra/main.bicep` provisions the platform
only. Each app is defined in `infra/app/<service>.bicep` with a parameters file
and is deployed by `azd deploy` together with the image built for it
(`SERVICE_<NAME>_IMAGE_NAME`). Images are built in ACR (`remoteBuild: true`), so
no local Docker is needed.

## Consequences
- `azd provision` can't touch running apps. Image, configuration and probes ship
  together as one revision, and rollback is per revision.
- AcrPull role assignments exist before the first app deployment, so there's no
  race.
- The app modules reference resources that exist only after provisioning, so
  ARM what-if can validate `main.bicep` but not `infra/app/*.bicep` beforehand.
  Their contract with the platform is checked by `tests/test_infrastructure.py`
  instead.
- Requires `azd >= 1.22.1` (enforced in `azure.yaml`).
