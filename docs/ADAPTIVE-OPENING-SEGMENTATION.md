# Adaptive opening segmentation

## PR 2 implementation and validation scope

PR #69's command transport follow-up preserves optional JSON-native HTTP failure
detail in durable receipts (at most 16 KiB of serialized JSON), while retaining
the existing status and message for deferred operation-status callers. Immediate
dispatch reconstructs that detail; old receipts continue using their message.
Unsafe or oversized detail retains the legacy envelope. Named backend transport,
PostgreSQL rollback/replay and frontend immediate/deferred archival regressions
prove the two timings converge without changing scheduling ownership. Current
main reverted PR #67 and retains schema 29; shadow evidence is additive migration 30.

Phase 2 starts from main `92f9aeacdf07bd1b61931ec1ca90be40c3a1619d` in the
isolated `opening-decision-shadow-evidence` checkout. Existing unfinished work
and live study resources are preserved. This change captures shadow observations
only; cards remain scheduling owners.

Validation starts with legal immutable manifests, ordered/idempotent event
reduction, then journal/lifecycle/offline replay and review transaction contracts.
Failure risks include assisted recall mislabeled clean, unreached moves credited,
conflicting retries, late evidence, stale card revisions and accidental scheduling
writes. Producer/consumer parity, real training/browser interaction and disposable
PostgreSQL migration/recreation/concurrent replay/backup tests cover those boundaries.
Typecheck and lint cover the changed TypeScript contracts. CI owns final required
candidate validation; focused local runs do not establish merge readiness.

## Baseline and validation plan

Implementation starts from main `86daf0053cdf7cf523956a723e5b309031ba09bd`
in `.dev-copies/adaptive-opening-segmentation`. The original checkout and live
study instance are outside this work. Other `.dev-copies` and worktrees are retained.

Import stores source routes and enqueues graph publication. PostgreSQL graph slices
prepare a source outside the read transaction, stage cards, link them, publish,
classify, refresh integrity, and remove obsolete links. Integrity completion queues
daily materialization. Daily queue admission owns prerequisites, per-repertoire new
card allowances, priority exceptions, and ordering. Review commands lock the card
and queue day, persist receipts, and call the existing FSRS adapter. Prefix splitting
transfers aggregate history and replaces presentations; it is not used for adaptation.
Edits/archive can request integrity without rebuilding source lines. Offline exports
and online outboxes submit aggregate outcomes; runtime move handling also has
assistance, wrong-answer, teaching, and generation information. Personal-game
observations are separate evidence, not clean training recall.

Smallest proof first: legal-position and pure segmentation fixtures, durable worker
phase/contended replay tests, producer/consumer contracts, component regressions,
then disposable PostgreSQL durability and the real preview browser workflow.
Typecheck/lint cover frontend contracts. CI owns the full gate on each candidate.
Do not run the full gate locally without a concrete reason. Record tested revision,
commands, counts, durations and unavailable checks in each PR.

## Ownership and compatibility

PR 1 is advisory only. Cards, reviews, FSRS state, introductions, and active queue
entries retain their existing owners and identities. New PostgreSQL projections
and preference metadata are additive; SQLite/static demo do not expose this feature.
Decision identity uses opening-position v1 (board-normalized first four FEN fields,
legal en-passant), trained color, repertoire scope, prescribed UCI response and
response-policy v1. Legacy card hashes remain untouched. Full FEN and route aliases
remain available for replay. A generation is not part of decision identity.

PR 2 captures observed evidence in shadow mode; whole cards still own scheduling.
PR 3 introduces opt-in, immutable plans and a scheduling ownership epoch. Decision
FSRS state owns adaptive reviews; one attempt cannot schedule legacy and decision
owners. Keep existing per-repertoire card admission allowance. Finish the active
attempt at cutover; late legacy work is retained as history only. Opt-out preserves
adaptive evidence and needs real whole-card verification, never inferred successes.
PR 4 changes presentation at attempt boundaries, not identities or active plans.

## Projection and recommendation semantics

Analyze distinct published legal presentations, retaining their bounded graph
source occurrences. Prefixes have at most the configured twenty learner decisions;
one-decision descendants preserve their opponent cues. Shared-trunk keys preserve
move order; compatible suffix keys identify transposition joins. Counts are distinct
presentation counts, never PGN leaf probabilities. Source aliases remain distinct
for provenance but cannot inflate savings. Cycles are bounded source occurrences.

Each recommendation compares the same covered presentations before/after. A common
trunk is tested once, with branch suffixes beginning at the trunk's ending position.
Transposition previews retain separate incoming bridges and share only compatible
continuations. Existing sharing is deducted. Positive savings must cover additional
board starts at one avoided learner decision per extra start. Rank by savings,
additional starts, segment count and deterministic identity. These are structural
estimates, not measured time savings or proof of improved learning.

Analysis is event-driven after current integrity publication, never startup, queue
GET, review processing, or input handling. One slice prepares one bounded presentation
or eight indexed group members after closing the read connection, then checkpoints
short writes and yields. Publication checks task lease, graph generation and content
version. Unchanged reads never traverse chess or enqueue work. Invalidated previews
are hidden. Dismissal/keep-current preferences survive unchanged semantic rebuilds;
changed pinned content is reported for review rather than silently reapplied.

## PR 2 shadow evidence contracts

Migration 30 records immutable saved presentations (actual revision, starting FEN,
exact move JSON and saved trained color), seeds current presentations, and captures
content changes with a narrow transactional trigger. Queue contexts preserve the
proven repertoire and effective learner color. Initial binding without admission
requires an unambiguous eligible owner. A review-generated repeat can instead use
its unique immutable context copied from the validated parent completion. Explicit
admission must match that context; multiple valid contexts remain ambiguous.
Unknown color, illegal content and ambiguous scope produce an actionable capture
diagnostic; ordinary cards remain playable.

Live/window/prepared queues expose manifest v1 only with
`include_opening_evidence=true`; default response shapes remain compatible. Manifest
identity hashes exact saved content, policy and repertoire scope. Decision identity
reuses PR #50's legal en-passant normalization. Reads close before chess traversal.
Setup/opponent plies never become learner decision indices. Legacy aggregate history
creates no inferred observations. SQLite and the practice demo remain aggregate only.

One logical attempt UUID survives rerenders, history navigation, queue refresh and
review retries, independently of the callback generation token. Restart, replacement
and genuine reinforcement get new identities; observed abandoned work is partial.
The shared offline IndexedDB version 2 stores incremental ordered events before the
board advances, without awaiting storage or transport in move handling. Committed
transactions alone count as locally durable. Storage failures retain the original
in-memory events, report a capture gap and allow repair without changing feedback.
Browser leases protect other tabs; orphaned sessions retain committed work as partial.
A sealed orphan without an aggregate outbox checks server completion before recovery
and never invents an aggregate review.

First response, assistance, manual failure, reveal and correction remain separate
facts. Assistance categories are teaching/hint/revealed/guided/other; no assistance
is an empty set. Actual displayed arrows are observed at the board boundary. Pre-first
assistance is frozen; duplicate exposure at the same decision is deduplicated. Submitted
UCI and existing grading disposition are separate, including valid alternatives and
illegal/unverified context. Manual Again fabricates no move. Corrections cannot become
clean first recall. Unreached decisions earn no observations.

Checkpoint deliveries use immutable keys and at most 256 events. `(attempt_id,sequence)`
is an immutable event identity. Exact duplicates succeed, changed context/content
returns a structured conflict, and out-of-order input advances projections only through
a contiguous valid prefix. Terminal checkpoints seal their final sequence; missing
events remain recoverable and contradictory terminals/beyond-seal events fail.
Offline attempts freeze their timezone and original timestamps, retain independent
repeat IDs/parent provenance, and resolve temporary queue entries through existing
parent-review reconciliation before submission.

Optional `ReviewRequest.opening_evidence_completion` combines final evidence with the
unchanged aggregate review transaction. Chess validation happens outside the write;
immutable references, semantic reduction, aggregate reconciliation and persisted
completion commit together. Stale reviews or conflicts roll everything back. Receipt
replay validates the evidence binding. An absent completion preserves the legacy
payload and command fingerprint exactly. A definitive evidence rejection retains the
journal/diagnostic, then submits the original aggregate body under a separate
`:aggregate-only` key. Ambiguous results retry the original envelope and key.

Only affected decision summaries update. Internal paginated provenance reads report
first responses, clean successes, incorrect/assisted responses, manual failures,
corrections, distinct clean study days, latest observation/failure and the latest
20 outcomes. Original observation time and stable attempt/index tie-breakers order
history; late replay cannot overwrite newer facts. Shadow provenance survives ordinary
presentation replacement and deletion. **Shadow evidence only. No scheduling owner
uses this projection.**

## Adaptation reserved for PR 3/4

Default adaptation: isolate after two first-response failures in the last three
attempts; reliable after three distinct clean study days with no later failure.
Recombine isolated targets only after three subsequent clean days and seven days.
A compatible connected/alternate-route diagnostic probe occurs every ten completed
adaptive exercises; extra not-due probes do not schedule or count toward mastery.
These are versioned engineering defaults with no scientific optimality claim.

## Migration, rollback and continuation

Use additive PostgreSQL migrations and schema-readiness versions. Rehearse only on
disposable databases; do not upgrade or activate the live study instance. PR 1 can
be disabled by hiding the advisory UI; preference/projection data can remain. Old
application images are not a schema rollback strategy. Recovery uses the supported
current-schema image and verified PostgreSQL backup/restore.

PR 2 supplies shadow manifests, journals, checkpoint/completion receipts and internal
summaries. It does not activate adaptive scheduling or replace teaching/alternative
move persistence and lookup (issues #35 and #36). PRs #66/#67 are not dependencies.
PR 3 continuation: activation dry run and epochs; quota reservations; immutable plans;
existing-queue adapter; tested-only FSRS effects; legacy historical replay and opt-out;
disposable activation/restart/restore; AS-01–04,12–18,20–21.
PR 4 continuation: bounded assembly, thresholds/cooldowns/pins, probes and diagnostics;
held-drag/opponent callbacks; AS-02–03,13,17–18,22.

Acceptance coverage is recorded in `tests/REGRESSIONS.md`. Future criteria stay
explicitly pending until the corresponding phase has executable coverage.

## PR 1 validation record

Code revision `73dab6c3f7146bb0cbf79c266a844a4512b6574d`, macOS host,
isolated Docker PostgreSQL/Redis and Chromium. Python uses the clone's backend
uv environment; no live study resources were used. Commands below ran at the
clone root with `TEMPO_PYTHON=backend/.venv/bin/python` for Python targets.

| Command | Result | Test duration |
|---|---|---|
| `make plan` | Coverage inspected | static |
| `make python-file FILE=backend/tests/test_opening_graph.py` | 24 passed; legacy assertions preserved | 3.83s |
| `make python-file FILE=backend/tests/test_opening_segmentation.py` | 9 passed | 0.06s |
| `make python-file FILE=backend/tests/test_postgres_opening_segmentation.py` | 4 passed | 0.13s |
| `make python-file FILE=backend/tests/test_postgres_opening_graph.py` | 12 passed | 0.57s |
| `make python-file FILE=backend/tests/test_repertoire_integrity.py` | 15 passed | 3.92s |
| `make python-file FILE=backend/tests/test_postgres_route_contract.py` | 3 passed | 0.55s |
| `make python-file FILE=backend/tests/test_postgres_upgrade_regressions.py` | 3 passed | 0.40s |
| `make python-file FILE=backend/tests/test_postgres_cutover.py` | 187 passed | 1.71s |
| `make unit-file FILE=tests/unit/opening-segmentation-regressions.test.tsx` | 4 passed | 1.26s |
| `npm run typecheck` | passed | unmeasured |
| `npm run lint` | passed; six existing warnings | unmeasured |
| elevated `make docker-durability` | passed all planned stages | per-stage artifact |
| elevated `make ui-file FILE=opening-segmentation.spec.ts` | 2 passed, phone/desktop | 16.9s |

The durability runner recorded the pre-commit HEAD `86daf005` with working changes
that became `73dab6c`; the browser runner recorded clean `73dab6c`. Durability
artifacts are `test-results/performance/postgres-scenarios-durability-tempo-pg-regressions-97966-fb2c6890.json`
and `test-results/performance/postgres-scenarios-browser-tempo-pg-regressions-99407-9109d766.json`.
The isolated legal three-route rehearsal used 26 bounded slices, performed zero
traversals for three unchanged reads, and retained schedules/queue identities.
An intentionally concurrent foreground FSRS review took 4.58ms while traversal
was paused; this single observation is a concurrency proof, not a speedup claim.
Background writes used the pre-existing 50ms transaction budget. An initial
durability invocation selected a container without the source mount; the runner
was corrected and the complete command subsequently passed.

CI full validation remains required on the actual PR candidate. Local `make full`
was not run because CI owns that gate. Decision evidence, executable exercises,
activation/opt-out and adaptation are not delivered by PR 1.

### PR #50 preview snapshot repair

Every list recommendation and detail carries an opaque `snapshot_id`, hashing contract/policy versions, repertoire/recommendation IDs, publication ID, content version, graph generation and source fingerprint. Initial reads may omit the binding for older readers; pagination must name it. Missing or superseded pagination returns actionable HTTP 409. Bounded read-only repeatable-read PostgreSQL transactions prevent mixed publications within a response. New preference commands submit the token; old durable payloads retain their existing source/version checks and receipts. No schema migration is needed.

The client checks the response against both its current list and displayed snapshot before appending. List/detail/command generations invalidate late successes, failures and cleanup after closure, repertoire changes or newer work. Stale/missing/changed previews cannot submit preferences. Pagination preserves original metadata. HTTP 409 on a command removes its obsolete preview. Recovery: refresh recommendations, load a fresh preview, then retry the choice; uncertain transport retries retain the idempotency key.

Validation uses the named component/API regressions and disposable PostgreSQL concurrent-publication rehearsal. Evidence capture, activation and scheduling remain unavailable.

Repair evidence (dirty snapshot patch over `26bbf2a`, macOS ARM64, Node 26.3.0, Python 3.14.5): `npm run test:unit -- tests/unit/opening-segmentation-regressions.test.tsx tests/unit/api-schema-parity-regressions.test.ts` passed 20 cases in 1.36s; `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_opening_segmentation_snapshot_api.py backend/tests/test_opening_segmentation.py backend/tests/test_postgres_opening_segmentation.py backend/tests/test_postgres_route_contract.py -q -o cache_dir=.pytest_cache --rootdir=.` passed 29 in 0.56s. Typecheck passed; lint passed with existing warnings and ref-cleanup warnings. Elevated `make docker-durability` passed every planned stage (158.21s scenario sum; includes setup/cleanup). The original component failed the new segment rebuild regression in 5.70s. These focused results are not a complete candidate gate; CI remains pending.

Discovery synchronization evidence: CI trace from run 36871513621, quality job 110400178010, shows response 2 still completing when clock jump 2 starts. Original local `26bbf2a` exact case passed (1.9s); the defect was not reproduced locally. The repaired case passed (2.0s), the complete discovery file passed 19 cases (14.8s), and the real segmentation file passed 2 cases (16.6s). Commands: elevated `make view VIEW='late removed preview cannot be reused when the discovery returns'`, then `make ui-file FILE=discovery-viewer.spec.ts` and `make ui-file FILE=opening-segmentation.spec.ts`. These ran on the snapshot commit `c914d01` plus the discovery synchronization patch. A feed-count attribute on the existing tray exposes completed feed processing without changing its visible layout. The next interval waits for response completion and the removal/return count. Existing obsolete-preview assertions remain.

Typecheck and lint were rerun after both repairs; passing focused evidence does not claim merge readiness. The final PR and merge revision still require the complete CI gate. No local full gate was run because CI owns that boundary.

Broad CI run [36883869217](https://github.com/aaweaver-actuary/tempo/actions/runs/36883869217) passed for `e3856e7` and its synthetic merge. Final API review then extended the snapshot guard to explicitly empty pagination cursors; named tests cover both cursor kinds and values. Focused backend/API files passed 31 cases on that follow-up. SQL statements and normal initial/page query arguments remain identical. This follow-up requires a fresh current-head CI gate; the earlier successful run is not relabelled as validation of it.

Final compatibility audit: adding the optional snapshot field would insert `snapshot_id: null` into legacy dispatch payloads and change their durable receipt fingerprint. `test_preference_dispatch_preserves_legacy_receipt_payload_and_new_snapshot` failed for the legacy request before the serialization fix (0.46s; new-token case passed). Dispatch now excludes absent optional fields, preserving the original payload/key exactly; new tokens remain present. Focused backend/API/route files passed 33 cases in 0.47s on this patch over `2486a14`. This changes command serialization only; SQL and scheduling remain unchanged. Fresh complete CI owns the final candidate gate.

Current-main reconciliation: full CI run 36890587332 passed at `0888e9e` against main `86daf005` (440 frontend, 745 backend, 8 Rust, 154 browser and 49 pinned cases). Main then advanced to `6edf704` with priority migrations 21–23. Its merge into this PR (`7556189`) left the unpublished segmentation migration numbered 21 while schema readiness became 24; run 36897266117 failed the existing startup-readiness regression. Segmentation is renumbered to 24 and records 24. `test_segmentation_migration_has_unique_number_and_matches_schema_readiness` reproduced the collision before the repair and guards unique numbers, readiness and the recorded version. No live schema is modified; disposable upgrade/restore and current-candidate CI must validate this integration follow-up. The snapshot repair itself adds no tables or migration.

## PR 2 focused validation record

The implementing checkout is `opening-decision-shadow-evidence`, branch
`codex/opening-decision-shadow-evidence`, based on main
`92f9aeacdf07bd1b61931ec1ca90be40c3a1619d`. Local results below exercised the
working candidate over that base, not clean base HEAD. Environment: macOS ARM64,
Node 26.3, backend Python 3.14.5, disposable PostgreSQL 18.6/Redis and real
Chromium/Firefox/WebKit. The live study checkout/stack and the dirty
`adaptive-opening-segmentation` checkout were preserved.

| Command | Result | Observed duration |
| --- | --- | --- |
| `make plan` | selected scope inspected before edits | static |
| `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_opening_decision_evidence.py backend/tests/test_opening_evidence_contracts.py backend/tests/test_postgres_route_contract.py -q -o cache_dir=.pytest_cache --rootdir=.` | 22 passed | 1.28s |
| `npm run test:unit -- tests/unit/opening-evidence-regressions.test.ts tests/unit/review-outbox-regressions.test.ts tests/unit/shared-board-shell-training-regressions.test.tsx tests/unit/training-store-regressions.test.ts tests/unit/offline-training-regressions.test.ts` | 24 passed | 3.21s |
| `npm run test:unit -- tests/unit/api-schema-parity-regressions.test.ts tests/unit/shared-board-shell-mount-regressions.test.tsx tests/unit/shared-board-shell-endgames-regressions.test.tsx tests/unit/shared-board-shell-tactics-regressions.test.tsx tests/unit/teaching-state-outbox-regressions.test.ts` | 15 passed | 3.35s |
| `node --test tests/runner/postgres-test-speedups.test.mjs` | 37 passed | 0.78s |
| `node --test tests/runner/ci-reliability.test.mjs` | 17 passed; new spec/critical case registered | 1.82s |
| elevated `make ui-file FILE=opening-evidence.spec.ts` | initial five workflows passed | 20.8s |
| elevated `make view VIEW='AS-08\|AS-09\|AS-15\|AS-16\|held training drag survives\|across browser engines\|tablet menu Escape\|WebKit desktop prepared queue\|prepared phone queue survives API outage'` | 26 passed, including all six final evidence cases, held drag, legacy phone and three browser engines | 58.9s tests; 101.55s including setup/cleanup |
| elevated `make docker-durability` | every planned stage passed; scheduling parity, migration, concurrent replay, recreation and every-table restore | 198.06s including setup/cleanup |
| `npm run typecheck` | passed | not separately timed |
| `npm run lint` | passed, nine existing warnings | not separately timed |
| `git diff --check` | passed | static |

Durability artifact:
`test-results/performance/postgres-scenarios-durability-tempo-pg-regressions-18015-4331f0b2.json`.
Browser artifact:
`test-results/performance/postgres-scenarios-browser-tempo-pg-regressions-19693-7fee18c2.json`.
The final SQLite unsupported-evidence guard was added after these Docker builds;
its named focused regression passed, and the PostgreSQL branch is unchanged.
The final scope-query rehearsal additionally proves explicit admission and omission
of ambiguous unbound ownership on fresh PostgreSQL 18.6. CI will execute the expanded
regular rehearsal on the actual candidate and applicable merge revision.

During implementation, the orphan-recovery browser assertion failed because an
acknowledged older envelope discarded a newer partial seal. The repair compares the
acknowledged terminal with the current committed terminal; exact retry and recovery
cases now pass. The first parity fixture failed because FSRS generated time-based
internal card IDs; only the test fixture fixes that ID for identical comparisons.
The first complete durability attempt with retained active shadow cards then blocked
an unrelated foreground queue assertion. The fixture now retains shadow tables while
removing its active cards/repertoire before the next scenario. Subsequent complete
durability passed; no product scheduling rule was changed to repair the harness.

Each runner removed its own project containers, disposable volumes, network, built
service images and separately built maintenance image. Final inventory contained only
the original live `tempo-*` stack. Safe base images and caches were retained. Logs,
traces, screenshots and timing provenance are preserved outside the clone under the
root checkout's dated `test-results/2026-10-02-opening-shadow-evidence/` directory.
This checkout remains retained until its work is merged or otherwise preserved.

No local complete gate was run: CI owns final required validation. PR 3 remains
responsible for opt-in/epochs, immutable adaptive plans, reservations/quota/prerequisite
protection, scheduling ownership and decision FSRS, historical replay, cutover and
opt-out recovery. PR 4 remains responsible for adaptive assembly, thresholds,
cooldowns/pins, probes and diagnostics. Issues #35/#36 remain open with their original
teaching-history and alternative-index implementation requirements.

PR 2 parent-recovery audit: `test_offline_repeat_reconciles_parent_aggregate_only_fallback`
failed on `7fd6042` plus the new test because it required parent evidence completion,
even after the original aggregate review had confirmed its compatible fallback.
Parent binding now uses the confirmed aggregate receipt, immutable presentation scope
and original/requeue entry, and rejects missing parents and unrelated queue cycles.
Evidence completion also requires a resolved actual queue entry. The final named
PostgreSQL rehearsal passed on a fresh disposable 18.6 container in 2.10s of execution
(`bash /private/tmp/tempo-shadow-run-focused.sh`, logs retained); the focused manifest,
contract and route files passed 23 cases in 0.62s. This backend follow-up requires fresh
current-candidate CI durability; the earlier local Docker artifact is not relabelled.

PR 2 rejected-journal retention audit: `AS-16 confirmed aggregate-only fallback
retains rejected journal even when IndexedDB is unavailable` failed on the prior
implementation (0.99s). The online outbox now archives the full rejected completion
and diagnostic before using its separate fallback key. A later prepared-phone queue
refresh retains rejected offline attempts instead of discarding them as ordinary
acknowledged records. The four affected journal/outbox/lifecycle/offline unit files
passed 23 cases in 1.88s; typecheck and lint passed (same nine existing warnings).
Elevated `make ui-file FILE=opening-evidence.spec.ts` passed all seven final cases,
including both successful and parent-rejected offline replay; original move/context,
independent repeat IDs, separate fallback key and post-refresh diagnostics are
asserted. The complete final candidate still requires fresh CI; run 37065687058
passed all required jobs on the earlier `7fd6042` and is not evidence for these repairs.

PR 2 storage-recovery follow-up: `AS-15 restart retains an uncommitted partial
journal for storage recovery` reproduced lost in-memory partial work before the
repair (1.37s). A partial journal remains reachable until its local transaction
commits; recovery retries failed terminal writes even when all event rows were
already committed. `AS-15 denied IndexedDB open can retry after access is restored
without reloading unsaved work` reproduced a cached synchronous denied-open failure
(1.58s). Failed opens now release their cached promise. The four affected unit files
passed 25 cases in 2.64s; typecheck and lint passed. Elevated
`make view VIEW='AS-15 crash recovery|AS-15 orphaned completion'` passed both real
browser recovery paths in 4.7s before the synchronous-open guard; that error branch
is proved by the named unit. Fresh CI owns the final committed candidate.

Run 37067036437 passed the complete plan for `7b78187` on synthetic merge
`126785f1e95c0bd4152366741d7997dc8b060095`, whose parents are main
`5976bce6e782b8fb22ec1bd79a8ed8bacd35dac9` (merged PR #68) and the PR head.
The plan required frontend, backend, build, PostgreSQL, all 189 browser cases and
53 pinned visual/performance cases; only the inapplicable quarantine and PR
publishing jobs skipped. This successful older run is not relabelled as validation
of the subsequent storage-recovery patch. PR #66/#67 remain unmerged dependencies
that this work does not consume.
