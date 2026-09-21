# Documentation guide

This directory holds durable explanations of Tempo's architecture, storage,
background-work rules, product contracts, and generated-content workflows.

## Start here

- [Code organization audit](CODE-ORGANIZATION-AUDIT.md) — current structure,
  service clusters, duplication signals, and a staged simplification plan.
- [Background work](BACKGROUND-WORK.md) — foreground priority, durable slices,
  leases, retries, and idempotent publication.
- [Storage](STORAGE.md) — SQLite authority, Docker volumes, backup, restore,
  and test-instance safety.
- [UI contract](UI-CONTRACT.md) — responsive board and workspace behavior.
- [Tactics catalog](TACTICS-CATALOG.md) and [tactical opportunities](TACTICAL-OPPORTUNITIES.md)
  — curriculum data and game-derived tactic flows.

Historical or feature-specific guides remain at the repository root when they
describe a cross-cutting implementation rather than a single directory.
