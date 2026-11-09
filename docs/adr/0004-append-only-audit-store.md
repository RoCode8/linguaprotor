# ADR 0004: Cosmos DB audit trail with an append-only data-plane role

**Status:** Accepted · **Date:** 2026-11-04

## Context
Licensing decisions are appealed. The audit trail (scoring, triage, approvals,
overrides) is the evidence. It was in process memory: lost on restart and not
shared. Cosmos DB was provisioned but unused, and its `audit` container was
partitioned on `/id` while the code writes and queries by `partitionKey`. That
mismatch would have made every trail query return nothing.

## Decision
- When `COSMOS_ENDPOINT` is set, `AuditLogger` writes to the Cosmos `audit`
  container, partitioned on `/partitionKey` (the assessment id). Retrieving a
  trail is then a single-partition query.
- The API identity holds a **custom data-plane role** scoped to that container,
  with `items/create`, `items/read`, `executeQuery`, `readChangeFeed` and
  `readMetadata`. It has **no** replace, upsert or delete permission, so the
  application can't alter history even if compromised.
- Serverless capacity: audit traffic is bursty and low volume. Free tier was
  removed, because only one free-tier account is allowed per subscription and
  provisioning fails if one exists.

## Consequences
- Operators with the built-in Data Contributor role can still modify data.
  Production should grant humans read-only roles and consider continuous
  backup (point-in-time restore) for the account.
- A Cosmos outage fails audited operations (HTTP 500) rather than silently
  dropping audit records. That is intentional.
