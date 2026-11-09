# ADR 0006: Provision only resources the application uses

**Status:** Accepted · **Date:** 2026-11-04

## Context
The templates provisioned a Storage account (three containers) and two Cosmos
containers (`exams`, `results`) that no code path used. They also passed the
storage connection string to the API. Unused resources add cost, attack surface
and drift.

## Decision
Remove them. Every provisioned resource is listed in
[infrastructure.md](../infrastructure.md) with the code path that requires it.

## Consequences
- Audio retention for appeals (Blob Storage) isn't available yet. When the oral
  pipeline persists recordings, add a storage module with
  `allowSharedKeyAccess: false` and a Storage Blob Data Contributor grant for
  the API identity.
