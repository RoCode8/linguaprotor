# Deploy LinguaProctor to Azure

This guide provisions the infrastructure described in
[infrastructure.md](infrastructure.md) and deploys the API and dashboard with
the Azure Developer CLI (`azd`). It takes about 15 minutes.

## Prerequisites

| Requirement | Details |
|---|---|
| Azure Developer CLI | **1.22.1 or later** (`azd version`). [Install azd](https://learn.microsoft.com/azure/developer/azure-developer-cli/install-azd). |
| Azure CLI | Optional; used for the validation and provider commands below. |
| Docker | **Not required.** Images build in Azure Container Registry (`remoteBuild: true`). |
| Azure permissions | **Owner**, or **Contributor + Role Based Access Control Administrator**, on the subscription. The deployment creates role assignments. |
| Resource providers | `Microsoft.App`, `Microsoft.ContainerRegistry`, `Microsoft.OperationalInsights`, `Microsoft.CognitiveServices`, `Microsoft.DocumentDB`, `Microsoft.ManagedIdentity` must be registered. See below. |
| Azure OpenAI quota | GlobalStandard quota for **gpt-5.1** (30K TPM) and **text-embedding-3-small** (30K TPM) in the region you choose. azd lists only regions with enough quota. |

Register the resource providers once per subscription. On 2026-11-04 the target
subscription had `Microsoft.App`, `Microsoft.ContainerRegistry` and
`Microsoft.OperationalInsights` unregistered.

```bash
for p in Microsoft.App Microsoft.ContainerRegistry Microsoft.OperationalInsights \
         Microsoft.CognitiveServices Microsoft.DocumentDB Microsoft.ManagedIdentity; do
  az provider register --namespace "$p"
done
```

## Deploy

1. Sign in.

   ```bash
   azd auth login
   ```

2. Create an environment. The name becomes part of every resource name
   (`rg-<name>`).

   ```bash
   azd env new linguaproctor-dev
   azd env set AZURE_LOCATION uaenorth          # app, data and Speech region
   azd env set AZURE_OPENAI_LOCATION uaenorth   # optional; azd prompts with quota-filtered regions
   ```

3. Optional settings:

   | Variable | Default | Effect |
   |---|---|---|
   | `DASHBOARD_ALLOWED_IP_RANGES` | *(empty: open)* | Comma-separated CIDRs allowed to reach the dashboard |
   | `API_EXTERNAL_INGRESS` | `false` | Expose the API outside the environment. Requires an auth layer. |
   | `AZURE_PRINCIPAL_ID` | your signed-in user | Grants your account data-plane roles for local development. Unset it for production. |
   | `AZURE_PRINCIPAL_TYPE` | `User` | `ServicePrincipal` when a pipeline deploys |

4. Preview the platform changes (read-only).

   ```bash
   azd provision --preview
   ```

5. Provision and deploy.

   ```bash
   azd up
   ```

   `azd up` runs two phases:
   - **provision** (`infra/main.bicep`): identities, monitoring, Azure OpenAI
     and its model deployments, Speech, Cosmos DB, the registry and the
     Container Apps environment.
   - **deploy**: for each service, builds the image in ACR, then deploys
     `infra/app/<service>.bicep` as one revision.

6. Verify. Open `DASHBOARD_URI` from `azd env get-values`, then check the API
   from inside the environment:

   ```bash
   az containerapp exec -g rg-linguaproctor-dev -n "$(azd env get-value SERVICE_API_NAME)" \
     --command "python -c \"import urllib.request;print(urllib.request.urlopen('http://localhost:8000/health').read().decode())\""
   ```

   The response should report `"azure_openai": true`, `"azure_speech": true`
   and `"audit_store": "cosmos"`.

7. Run the expert regression suite against the deployed models before
   assessing candidates (see [Run locally against Azure](#run-locally-against-azure)):

   ```bash
   python -m evaluation.regression_tests   # exit 0 = pass, 1 = fail
   ```

## Update

| Change | Command |
|---|---|
| Application code | `azd deploy` (or `azd deploy api`) |
| Platform (`infra/main.bicep`, modules) | `azd provision`. Running apps aren't touched. |
| Container app settings (`infra/app/*.bicep`) | `azd deploy <service>` |

## Run locally against Azure

`azd provision` grants your account the same data-plane roles as the API, and
writes the non-secret settings to `.env.azure`:

```bash
az login                     # DefaultAzureCredential uses this session
cp .env.azure .env
uvicorn src.main:app --reload
```

`docker compose` is for **offline** use. A container can't use your `az login`
session, and the provisioned resources reject keys.

## Set up CI/CD

```bash
azd pipeline config
```

This creates a service principal with a GitHub OIDC federated credential and sets
the repository variables `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`,
`AZURE_SUBSCRIPTION_ID`, `AZURE_ENV_NAME` and `AZURE_LOCATION`. Also set
`AZURE_OPENAI_LOCATION` (and `AZURE_OPENAI_ENDPOINT` to run the regression
job). No secrets are stored.

- `.github/workflows/ci-cd.yml` runs on every push and PR: ruff, Bicep build and
  lint, and pytest, including the infrastructure contract tests. It runs the live
  regression suite when OIDC is configured.
- `.github/workflows/azure-dev.yml` provisions and deploys on manual dispatch.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `MissingSubscriptionRegistration` | Resource provider not registered | Register the providers (see [prerequisites](#prerequisites)) |
| `InsufficientQuota` or "model not available" during provision | No quota for the model and SKU in `AZURE_OPENAI_LOCATION` | Choose another region (`azd env set AZURE_OPENAI_LOCATION …`), request quota, or lower `capacity` |
| Model creation fails for a gpt-4o version | Deprecated versions are blocked for subscriptions that never deployed them | Use a GA model ([ADR 0002](adr/0002-chat-model-gpt-5-1.md)) |
| `RoleAssignmentExists` / `AuthorizationFailed` on role assignments | Missing permission to create role assignments | Deploy with Owner, or Contributor + Role Based Access Control Administrator |
| API logs show 401/403 from OpenAI, Speech or Cosmos right after the first deployment | Role assignments still propagating (can take several minutes) | Wait, then restart the revision: `az containerapp revision restart …` |
| `TasksOperationsNotAllowed` during deploy | ACR Tasks aren't available for the subscription type (some trial/sponsored offers) | Install Docker and set `remoteBuild: false` in `azure.yaml`; azd falls back to a local build |
| `CustomDomainInUse` or `FlagMustBeSetForRestore` when re-creating an environment | A deleted Cognitive Services account is soft-deleted (48 h) | Use `azd down --purge`, or purge it: `az cognitiveservices account purge …` |
| Dashboard shows "Cannot reach the API" | API revision unhealthy, or ingress mode changed without redeploying the dashboard | Check the API revision health and logs; run `azd deploy dashboard` after changing `API_EXTERNAL_INGRESS` |
| `/health` reports `"audit_store": "in-memory"` in Azure | `COSMOS_ENDPOINT` not set on the API | Redeploy the API: `azd deploy api` |

## Remove

```bash
azd down --purge
```

`--purge` also purges the soft-deleted Azure OpenAI and Speech accounts so the
names can be reused. **This deletes the audit trail.** Export it first if it's
needed for appeals.
