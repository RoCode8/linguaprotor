# ADR 0001: Keyless authentication with managed identities

**Status:** Accepted · **Date:** 2026-11-04

## Context
The original templates read service keys with `listKeys()`, passed them to the
container apps as secrets, and published them as deployment outputs. Any keys in
outputs end up in deployment history and in `azd` environment files, and they
never rotate. The ACR admin user was enabled for image pulls.

## Decision
- Disable local (key) authentication on Azure OpenAI, AI Speech and Cosmos DB
  (`disableLocalAuth: true`). Disable the ACR admin user.
- Give each workload its own user-assigned managed identity, with
  least-privilege roles:

  | Identity | Roles |
  |---|---|
  | API | Cognitive Services OpenAI User, Cognitive Services Speech User, a custom Cosmos "Audit Appender" role, AcrPull |
  | Dashboard | AcrPull only |

- The application uses `DefaultAzureCredential` for every client. In Azure,
  `AZURE_CLIENT_ID` selects the managed identity. Locally it uses the
  developer's `az login` session, which gets the same data-plane roles through
  the `principalId` parameter.
- CI/CD authenticates with GitHub OIDC federated credentials, so no Azure
  secrets are stored in GitHub.

## Consequences
- No secret exists to leak or rotate. The Bicep linter rule
  `outputs-should-not-contain-secrets` is enforced as an error, and
  `tests/test_infrastructure.py` asserts the whole baseline.
- Speech requires a custom subdomain and the `token_credential` API of the
  Speech SDK (available in 1.53+).
- The async Azure SDKs require `aiohttp`, now a declared dependency. The
  infrastructure tests caught its absence.
- Role assignments take minutes to propagate. The first container start after
  provisioning can briefly return 403 (see the deployment guide,
  troubleshooting section).
- Deploying requires permission to create role assignments (Owner, or
  Contributor + Role Based Access Control Administrator).
