# Committed merge-conflict repair

## Cause and selected scope

`191fd838bcf927e4a59cf583041a99922d4a0e9f` committed unresolved conflict blocks while merging `c45d0e8` into the daily-study branch at `ddf5a94`. PR #93 then brought that tree onto main at `48d7df1`. Neither parent contains these artifacts. The planner failed to parse before downstream verification could run; passing historical feature CI is not proof of the broken merged tree.

This repair removes 130 genuine regions from 36 tracked files. The initial inventory was regenerated from freshly fetched main; report separators and WASM binary matches were excluded. Every right-hand region together reproduces the clean second-parent file exactly, but selecting all of them would discard PR #93. The reviewed resolution retains 14 first-parent regions, 115 second-parent regions, and both sections in one regression-registry region. No entire PR is reverted.

## Resolution and semantic preservation

- PR #93 (`2d11e05`, `3f18205`, `ddf5a94`): retain published-graph/mature-parent filtering before bounded sparse unlocking and the update recheck; execution-time lease acquisition and shared fenced execution; legacy queued delivery compatibility; cutover direct-execution assertions; daily-study PostgreSQL checker/Compose identity; the real backlog browser proof; and complete offline projection readiness. Four clean added files remain unchanged. Pause, priority, generation fencing, rollback, restart, replay, and commit-before-ack contracts retain their named tests.
- PR #98 (`29a9e61`, merged `187a9b2`): retain dismissal snapshots across temporary resolution until material evidence qualifies reopening; newest current-scope attempt selection and source provenance; independent provider completion/failure; transaction-local coalesced refresh checkpoints; stale calculation rejection and shared cleanup qualification. Retain updated partial-source query counts and all new tests.
- PR #99 (merged `3a86338`): retain optional bounded raw-row snapshot capture, read-only transition route registration/background classification, route contracts, immutable transition planning, and deployed foreground/read-only/stale-state proofs. Existing evaluator callers still return the original snapshot when no capture callback is supplied.
- PR #100 (`4c0c4c4`, merged `c45d0e8`): retain prefix-diagnostics router/admission, bounded reader-only endpoints, selected reporting-window validation, UI wiring, and indexes. POSTGRES_SCHEMA_VERSION is 36: migrations 001–036 are contiguous and unique, and 036 records version 36. Migration SQL, schema semantics, startup readiness and the stopped-writer maintenance path are unchanged.
- PR #101 (`7ff67fe`, merged `0106364`): retain branch-scoped comparison UI, structural transport parity, bounded multi-depth/read-only rehearsals, reviewed visual cases and the lightweight game-sync serializer with coordinator re-export compatibility. Comparison freshness recovery remains untouched.
- PR #95 (`0c48e71`): retain source-mapping validation and rationale, six global smoke invariants, complete affected families and demoted cases, exact collection/ID/hash semantics, fixture isolation, runner optimizations and complete-event fallbacks. Both initial-ready and complete-offline Studies checks are retained. PR #94 backup INT/TERM handling also remains.
- Regression inventory: union the complete daily-study section with newer backup/planner/freshness/comparison/diagnostics entries. Updated family descriptions are retained; no regression is discarded.

## Original conflict inventory

Line ranges identify the original `48d7df1` tree, before editing. L retains the daily-study parent, R retains the newer-main parent, and B combines both.

| File | Original regions and selected resolution |
| --- | --- |
| `app/globals.css` | 4104–4145 (R) |
| `app/views/repertoire_view.tsx` | 29–33 (R), 93–99 (R), 358–363 (R), 379–384 (R), 454–458 (R) |
| `backend/app/coverage_maia_commands.py` | 15–23 (R), 42–54 (R), 66–77 (R), 137–140 (R), 184–197 (R) |
| `backend/app/main.py` | 426–435 (R), 726–737 (R), 1268–1284 (L), 1294–1300 (L), 1307–1324 (L), 4215–4218 (R), 4228–4238 (R), 4341–4345 (R), 4361–4365 (R), 6517–6521 (R), 6526–6531 (R) |
| `backend/app/prefix_evaluation_api.py` | 72–81 (R), 133–136 (R), 156–160 (R) |
| `backend/app/schema_version.py` | 3–7 (R) |
| `backend/app/services/canonical_scope_freshness.py` | 51–70 (R) |
| `backend/app/services/game_sync_coordinator.py` | 23–26 (R), 649–667 (R) |
| `backend/app/services/postgres_coverage_explorer.py` | 11–30 (R), 58–64 (R), 126–129 (R), 143–151 (R), 205–214 (R) |
| `backend/app/services/repertoire_coverage.py` | 21–25 (R), 357–361 (R), 542–546 (R), 570–574 (R), 641–654 (R), 665–669 (R), 679–691 (R), 701–705 (R), 814–821 (R), 856–860 (R), 891–895 (R), 901–910 (R) |
| `backend/app/services/repertoire_opportunities.py` | 17–25 (R), 124–128 (R), 137–142 (R), 161–176 (R), 521–562 (R), 592–613 (R), 630–634 (R), 670–679 (R), 779–789 (R), 809–814 (R), 830–845 (R), 855–865 (R), 907–916 (R), 963–973 (R), 978–982 (R), 1005–1019 (R), 1030–1034 (R) |
| `backend/app/tasks.py` | 235–243 (L), 248–260 (L), 271–282 (L), 319–323 (L) |
| `backend/tests/test_game_sync.py` | 19–35 (R) |
| `backend/tests/test_postgres_cutover.py` | 4630–4639 (L), 5387–5400 (L), 5627–5640 (L), 6088–6092 (R), 6404–6409 (R), 6428–6432 (R) |
| `backend/tests/test_queue_attempt_recovery.py` | 373–377 (R) |
| `backend/tests/test_repertoire_opportunities.py` | 973–977 (R), 1441–1778 (R) |
| `docker-compose.postgres.test.yml` | 17–22 (R) |
| `docs/PREFIX-EVALUATION.md` | 148–213 (R) |
| `docs/postgres-route-contract.json` | 3–12 (R), 1271–1287 (R) |
| `docs/testing.md` | 133–228 (R) |
| `scripts/check_postgres_canonical_freshness.py` | 1173–1177 (R) |
| `scripts/check_postgres_opening_evidence.py` | 1074–1078 (R) |
| `scripts/check_postgres_opening_segmentation.py` | 12–16 (R), 73–91 (R), 99–143 (R), 153–159 (R), 169–173 (R), 178–190 (R), 220–237 (R), 262–266 (R), 274–278 (R), 286–417 (R), 426–436 (R), 451–454 (R), 469–485 (R), 491–495 (R), 500–504 (R), 627–630 (R) |
| `scripts/ci-verification-inventory.json` | 7–11 (R), 19–60 (R) |
| `scripts/ci-verification-plan.mjs` | 30–43 (R), 78–84 (R), 89–102 (R), 177–181 (R) |
| `scripts/test-postgres-docker.mjs` | 609–627 (R), 878–882 (L), 1017–1020 (L) |
| `tests/REGRESSIONS.md` | 1–12 (R), 95–99 (R), 1435–1439 (R), 1470–1474 (R), 1496–1500 (R), 2153–2384 (B) |
| `tests/browser/README.md` | 8–16 (R) |
| `tests/browser/settings-repertoire-limits.spec.ts` | 1–5 (R) |
| `tests/browser/studies.spec.ts` | 70–80 (R), 110–119 (L) |
| `tests/browser/training-queue-contention.spec.ts` | 3–47 (L) |
| `tests/browser/visual.spec.ts` | 309–341 (R) |
| `tests/runner/ci-reliability.test.mjs` | 3–7 (R), 14–39 (R), 179–196 (R), 201–204 (R), 269–441 (R) |
| `tests/runner/postgres-test-speedups.test.mjs` | 333–350 (R) |
| `tests/unit/api-schema-parity-regressions.test.ts` | 33–37 (R), 93–107 (R) |
| `tests/unit/ci-reliability-regressions.test.ts` | 4–14 (R) |

## Regression guard

`npm run check:conflicts` invokes a dependency-free Node tracked-file scanner. It detects ordinary/diff3/variable-width conflicts and malformed labeled boundaries, ignores binary/quoted/separator lookalikes, and never follows symlinks. Deliberate full examples require exact path plus SHA-256 block binding; the production exemption list is empty. Named detector regressions run through the regular CI-reliability Vitest wrapper. The standalone check runs in the existing CI plan job before `npm ci` and before browser collection, and at the local gate entry without changing stage definitions.

## Validation ownership

Risk spans shared task execution, partial-source freshness, API/router contracts, schema readiness, browser readiness and verification inventory. Start with guard/syntax/structured-file checks, focused runner and producer/consumer tests, then affected backend files. Run settled disposable PostgreSQL durability and focused Studies/training-contention browser cases once. CI owns required current-candidate/current-base complete validation, including all browser families and pinned rendering. Do not infer complete validation from historical CI or `make plan`.

Exact candidate commands, counts, durations, resource ownership, failures and final CI links are recorded in the repair PR and dated local evidence under the root checkout’s `test-results/2026-10-08-merge-conflict-repair/`. No live study data, migrations, queues, receipts, services or shared Docker resources are changed.
