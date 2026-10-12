# PR #191 current-main integration qualification

Head: `2940aeffdc670a7bb8ae5031122e9ba0a2f547f6`. Main integrated: `3f7332399796366eb32f9fcb5e1b5c678a5b1562`. GitHub integration: `229b7ee03fbce556da9db9478e6c0d945a255190`. Original PR head: `3b2e9ec262bb2427a805af46711bbe1de1f59df8`; common base: `d4dbf28da5b6e95db8da1acfd9fecd4655821296`.

## Conflict and preservation

The only conflict was both branches appending to tests/REGRESSIONS.md. Resolution retains the entire main registry and the entire PR addition. Production source merged cleanly. Static byte comparisons verify Home is exactly current-main source, and review-outbox exactly the audited PR source. No newer main behavior was replaced and no other unmerged branch code was imported. Public/persisted contracts remain unchanged.

Main since the common base introduced study-only mode, foreground saved-repertoire move verification while optional preparation is paused, opening capacity admission, migration 47, and emergency CI classification/provenance. Preserve these intact. Relevant newer Home tests are part of the selected 313-case run. The current workflow is included through ancestry, not independently modified.

## Invariants and named regular-suite coverage

| Invariant | Evidence |
| --- | --- |
| Both generations' continuation maps survive joined success | `flush_settlement_audit bounded %s then unlimited preserves all continuation capabilities` (limits 1,2) |
| Earlier confirmed evidence survives failed follow-up; direct errors are not mutated | `flush_settlement_audit %s follow-up failure retains earlier settled evidence` (unlimited,submitted) |
| Original error identity/attribution and retry suppression retained | `flush_settlement_audit unrelated rejection attributes its own attempt and explicit retry=%s`; `different explicit retry identities retain suppression after %s owner` |
| Reentrant callers share active ownership before publication | `flush_settlement_audit reentrant submitted owner cannot reject after concurrent authoritative recovery`; `synchronous outbox subscriber observes subsequent %s publication` |
| No false persistence or missing immutable identity | `flush_settlement_audit overlapping submitted outcome remains truthful: %s`; native receipt replay compares original body/key and exact review/receipt/scheduling result |
| No duplicate or unauthorized advancement | `flush_settlement_audit submitted identities coalesce with limit %s and retain immutable early snapshots`; continuation authorization boundary cases; Home foreground/idle/reconnect cases |
| Existing failure/guidance markers and same-card dependencies protected | continuation authorization boundary cases; `target cannot bypass same-card %s predecessor`; whole training-failure outbox and Home caller files |

Exact expanded 39 passing audit identities are in exact-audit-cases.json. Whole-file/caller results are in affected-units.json. Existing audit baseline reproductions are retained at the original audit source; this integration did not rerun unchanged established baseline failures.

## Fresh local execution

macOS arm64, Node 26.10.0, npm 11.19.1; locked npm ci once (18s). No Python environment needed by these selected local unit files. All source/test execution uses the clean committed integration tree; generated secrets/build outputs are task-owned runner artifacts.

- 39 selected audit cases: 3.22s Vitest. Selection excludes 157 other cases; the subsequent unfiltered run executes all selected files.
- 313 cases / 14 whole files: 12.81s Vitest, 13.59s invocation.
- Typecheck: 12.50s; lint: 22.14s, ten existing warnings and zero errors. Conflict and diff guards pass.
- Native progression: 4/4 Chromium cases; prepared abort 35.6s, fallback abort 34.4s, fallback timeout 48.7s, overlapping confirmation 41.8s. Browser stage 162.88s, image build 17.62s, startup 12.80s, health 0.19s, cleanup 5.45s. Stage timings are disjoint; per-case times are nested inside browser and not additional wall time. Exact overall invocation is in native-browser-command.json.
- Native overlap proves one durable review/receipt/scheduling update, issued successor input, restart and original immutable idempotency replay. Cleanup independently verifies no owned containers, volumes, networks, or tagged images remain. Resource IDs/mounts/creation/start times and exact teardown are in native-resource-provenance.json; no secrets are published.

## CI qualification

Run https://github.com/aaweaver-actuary/tempo/actions/runs/38281438557 is selected FULL via ci:full. CI owns complete qualification including PostgreSQL durability and native queue-attempt recovery/prepared-transition proofs. No redundant local full/durability execution. Plan binds exact head and integration above, qualification/complete scope, ready state. GitHub base metadata retains the original common-base SHA; independent main-ref and integration-parent verification establishes actual main 3f733239. Integration tree matches the locally tested head tree.

Final results pending. No merge readiness is claimed until mandatory layers, quality, merge-gate, fresh PR identity and mergeability are verified.

## Scope and remaining limits

This is narrow integration/qualification. No architecture refactoring, queue redesign, session extraction, cross-tab migration, specialized recovery or live study modification. Finite deterministic cases are not exhaustive model checking. Raw errors lacking ReviewReplayError.flushResult retain existing behavior; no additional defect is asserted. Broader #186/#193/#217/#219/#221/#222 work remains separate.
