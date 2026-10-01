# Adaptive opening segmentation

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

## Evidence and adaptation contracts reserved for later PRs

Capture first responses, assistance before response, corrections, partial attempts,
ordering and original plan revisions. Setup/unreached moves earn no credit. Failure
at decision five does not change earlier clean evidence. Revealed corrections do not
become clean attempts. Journal locally; deduplicate delivery, not legitimate retries.
Distinct-day reliability counts study days, not retries. Legacy inference, personal
games, teaching exposure and observed training recall remain separate.

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

PR 2 continuation: versioned manifests; move-boundary journal; durable checkpoint/
completion envelopes; strict validation/receipts; affected decision summaries;
aggregate-review atomicity and old-client/outbox contracts; AS-08–11,15–16,19.
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
