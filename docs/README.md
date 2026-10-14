# Documentation guide

This directory holds durable explanations of Tempo's architecture, storage,
background-work rules, product contracts, and generated-content workflows.

## Start here

- [Component reconstruction guide](architecture/component-reconstruction.md) —
  adopted component boundaries, current implementation limits, and the
  [12-phase migration index](https://github.com/aaweaver-actuary/tempo/issues/192).
- [Code organization audit](CODE-ORGANIZATION-AUDIT.md) — historical findings
  and proposals from its September 20, 2026 baseline, not the current
  architecture or migration policy.
- [Background work](BACKGROUND-WORK.md) — foreground priority, durable slices,
  leases, retries, and idempotent publication.
- [Storage](STORAGE.md) — PostgreSQL and SQLite storage, Docker volumes, backup, restore,
  and test-instance safety.
- [UI contract](UI-CONTRACT.md) — responsive board and workspace behavior.
- [Tactics catalog](TACTICS-CATALOG.md) and [tactical opportunities](TACTICAL-OPPORTUNITIES.md)
  — curriculum data and game-derived tactic flows.

Historical or feature-specific guides remain at the repository root when they
describe a cross-cutting implementation rather than a single directory.
