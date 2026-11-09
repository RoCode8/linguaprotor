# Architecture Decision Records

Significant, hard-to-reverse decisions, recorded in the lightweight ADR format
(Context, Decision, Consequences) recommended by the
[Microsoft Code-With Engineering Playbook](https://microsoft.github.io/code-with-engineering-playbook/design/design-reviews/decision-log/).
ADRs are immutable once accepted; a later ADR supersedes an earlier one.

| # | Decision | Status |
|---|---|---|
| [0001](0001-keyless-authentication.md) | Keyless authentication with managed identities | Accepted |
| [0002](0002-chat-model-gpt-5-1.md) | GPT-5.1 as the default chat model | Accepted |
| [0003](0003-revision-based-container-deployment.md) | Revision-based Container Apps deployment with azd | Accepted |
| [0004](0004-append-only-audit-store.md) | Cosmos DB audit trail with an append-only data-plane role | Accepted |
| [0005](0005-single-replica-api.md) | Single-replica API until review state is persisted | Accepted |
| [0006](0006-provision-only-what-runs.md) | Provision only resources the application uses | Accepted |
