# Browser intent regression ownership

The regular owning command is `npm test`. Named cases in
`tests/browser-intents.test.mjs` cover #221 independent two-tab write orders,
immutable grade/time/revision/request identity, concurrent claims, expired claims,
settlement tombstones, interrupted updates, migration checkpoints, incremental
teaching facts, denied storage and workspace isolation. Native browser tests
provide the separate real-storage and navigation proof.

The legacy whole-array race was characterized at main
`f43b5f7fb4d7ddafeb3124a95f4fa3d64556abc4`: both tabs read before either write,
then each write order retained only the final writer. This is controlled native
localStorage reproduction, not evidence of historical user data loss.

PR #285: `pr285_legacy_conflict_*` covers atomic concurrent initial-state import,
full preservation of newer mutable state/claims/receipts, identity conflicts and
both terminal tombstones. `pr285_practice_generation_*` covers both reset/insertion
orders and an actual metadata transaction abort retaining the answer/assessment
pair. `pr285_recovery_cursor_stale_compare_cannot_overwrite_newer_progress` covers
fenced progress and authorized wraparound. `pr285_teaching_cross_tab_*` verifies
keyed notifications and durable targeted reads through two store connections.
