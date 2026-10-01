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
