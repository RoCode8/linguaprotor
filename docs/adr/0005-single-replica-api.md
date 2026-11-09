# ADR 0005: Single-replica API until review state is persisted

**Status:** Accepted · **Date:** 2026-11-04

## Context
Scoring results, the review queue and calibration datapoints live in process.
The template allowed 1–3 API replicas, which would split the queue across
replicas. Scale-to-zero would erase it.

## Decision
Pin the API to `minReplicas: 1`, `maxReplicas: 1`. This is asserted by
`tests/test_infrastructure.py`.

## Consequences
- No horizontal scaling and no zero-downtime restarts. A restart loses the
  queue and results; the audit trail survives in Cosmos.
- Lifting the constraint requires moving results, review items and calibration
  datapoints to Cosmos containers. The audit store shows the pattern. After
  that, revisit replicas and zone redundancy.
