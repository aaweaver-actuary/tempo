# Review-outbox flush settlement audit

Independent audit of merged PR #172, October 11, 2026. Baseline:
`d4dbf28da5b6e95db8da1acfd9fecd4655821296` (tree
`cc2ed24e7114ed755b7caea481f2a7c1caca6cfb`). The live study checkout was untouched.

## Verdict

**CONFIRMED DEFECTS:** follow-up aggregation loses evidence in two ways:

- A bounded owner followed by an unlimited caller discards both generations'
  `preparedContinuations` maps on success, while preserving their confirmed IDs.
- If the follow-up fails, both unlimited and submitted-attempt callers receive
  only the follow-up error's partial result, losing the earlier settled IDs and
  continuation map.

The correction combines the same three result fields on success and failure.
Failure aggregation copies the error for the caller, preserving its original
identity/classification/message/cause/stack and leaving shared observers intact.

No false persistence confirmation, incorrect conflict classification, duplicate
advancement, unissued-successor advancement, or lost known failure evidence was
reproduced. This does not establish a production study incident: Home's targeted
submission path already preserves continuation maps; queue initialization ignores
the aggregate result. The reproduced defect is loss of returned settlement/authorization
evidence, not fabrication of authorization.

## Ownership and settlement model

`activeFlush` serializes ordinary asynchronous delivery. Each save constructs a
fresh cumulative result. Persistence removes the exact local envelope and adds
its attempt ID only after an authoritative `persisted: true` response. A terminal
reconciliation conflict instead retains the envelope and adds its ID to the
conflicted list. Retryable conflicts and ambiguous transport outcomes reject with
the failing attempt identity and any earlier settled evidence.

Publication sets `currentFlushProgress` and synchronously calls listeners.
Targeted listeners resolve only when their own ID appears in either settlement
list, copying the cumulative arrays/map for early completion. The shared promise's
`finally` clears `activeFlush`; promise reactions subsequently remove listeners
or start additional work. Starting a new flush clears the current snapshot.

**Generation isolation is not an invariant.** Controlled public outbox-event
callbacks and two explicit microtasks reproduced this ordering for both A's
completion and rejection:

1. A changes its stored envelope and publishes/terminates.
2. A's `finally` releases the active slot.
3. B starts and synchronously settles its target as an earlier-review conflict.
4. A's promise callbacks run; the old target listener has already observed B.

The old listener resolves with B's genuine terminal settlement, without crediting
A or supplying a continuation. This is **SAFE — VERIFIED** for those executions.
Promise resolution is one-shot; no generation wrapper is added solely because
cross-generation observation is possible.

Persistence confirmation, terminal settlement, and authorization remain distinct.
A persisted result without matching issued-successor metadata cannot advance a
cached successor. Home rejects prepared advancement for conflicts. Cached
advancement rechecks attempt ID/generation, queue-entry/card/revision/cycle/day,
queue readiness, pending/conflicted reviews, and known failure evidence. Advancing
changes the logical attempt, preventing duplicate observers from advancing again.

## Adversarial scenario matrix

All new unit cases use the `flush_settlement_audit` prefix in four existing regular
suite files. Fetch/receipt gates and explicit microtasks control ordering; no
private flush-state writes or wall-clock sleeps are used.

| Scenario | Classification / observable proof |
| --- | --- |
| A completes/rejects; B publishes before A callbacks | SAFE — VERIFIED; genuine B conflict, no false persistence/capability |
| Subscribe before/after publication; duplicate target observers | SAFE — VERIFIED; exact target attribution and immutable early snapshot |
| Different submitted IDs; limits 1, 2, Infinity; differing retry/receipt settings | SAFE — VERIFIED for settlement; original delivery is coalesced and follow-up uses caller settings |
| Bounded owner followed by unlimited drain | CONFIRMED DEFECT; both capability maps disappear despite confirmed IDs |
| Unlimited/submitted follow-up rejects after earlier confirmation | CONFIRMED DEFECT; earlier IDs and capability map disappear from partial error evidence |
| Terminal conflict, including supplied continuation metadata | SAFE — VERIFIED; conflicted only, no prepared authorization |
| Retryable conflict; missing persistence confirmation | SAFE — VERIFIED; retained, unconfirmed target, no continuation |
| Network failure before commit; response loss after simulated commit | SAFE — VERIFIED; both reject as ambiguous, receipt recovery alone confirms the latter |
| Same-card persisted, suppressed, or conflicted predecessor | SAFE — VERIFIED; ordering/dependency protection, unrelated reviews retained |
| Appended review; later unrelated success/failure | SAFE — VERIFIED by new and retained overlap cases; early target confirmation survives |
| Replacement attempt/generation; changed entry/revision/cycle/date | SAFE — VERIFIED; no cached advancement |
| Missing/mismatched issuance; new failure flag/marker | SAFE — VERIFIED; no cached advancement and failure evidence retained |
| Home foreground plus another flush observer | SAFE — VERIFIED; prepared card advances once before later held delivery |
| Idle recovery shares a flush with a later failing receipt | SAFE — VERIFIED; confirmed target/capability survives in partial error evidence |
| Real PostgreSQL response/receipt held during reconnect | SAFE — VERIFIED on baseline; native successor interaction, restart and original receipt/replay |

## Reproduction and smallest correction

Enqueue A, B, C; A and C have frozen prepared identities. Hold A's response, start
`flushPendingReviews(1)` (also tested with `2`), then call unlimited
`flushPendingReviews()`. Release A with confirmed persistence and continuation.
The bounded owner returns its evidence, then the unlimited caller drains the
remainder. Its merge copies only the two ID arrays, dropping both maps.

`flush_settlement_audit bounded %s then unlimited preserves all continuation capabilities`
fails on unchanged production for both limits with `preparedContinuations ===
undefined`. A second named case, `flush_settlement_audit %s follow-up failure retains earlier
settled evidence`, fails for unlimited and submitted callers: A confirmed in the
bounded owner, B rejects before C delivery, and A disappears from the caller's
error evidence. Duplicate callers and a direct B observer ensure the correction
cannot mutate the underlying shared error or duplicate earlier evidence.

Two small private helpers combine result fields and include earlier settlement
in success or a caller-specific error. Both follow-up branches use them. No public API, persistence, queue policy, scheduling,
backend locking, generation mechanism, or reconciliation behavior changes.

## Evidence and limitations

Baseline final unit matrix: **29 passed, 2 failed**, 157 unselected existing cases,
11.48 s. Both failures are the named capability-loss regression. The baseline
has unchanged production and new tests; it is not a clean-HEAD test result.
A separate final marker-retention assertion also passed on unchanged production.
After the public callback/type correction, all 13 prepared/Home audit cases passed
on baseline; the two added explicit-retry cases also passed. The two later
follow-up-error cases failed in 1.94 s with their earlier evidence missing.

The final baseline browser probe passed: 1 case, 81.66 s browser stage. Runtime
production was built from unchanged baseline before the corrected source was
restored on the host; its source hashes and test patch are retained. The fixture
proves both direct and 202/receipt confirmation, bounded queue recovery, native
B interaction, restart, and original review/receipt/scheduling equality.

Early exploratory runs contained two fixture mistakes (a receipt GET treated as
a POST, and a Home call attempting to grade an already queued result). Corrected
fixtures use missing-receipt recovery and the public retry callback. Browser
fixture iterations corrected an invalid ready-queue expectation, restarted the
fixture proxy after the API, bounded temporary restart transport failures, and
handled real 202/receipt confirmation. Those errors
are preserved as diagnostics and are not classified as production defects.

Local environment: macOS, Node 26.10.0, npm 11.19.1, Vitest 5.0.1/jsdom; locked
dependencies installed once. CI uses its configured runtime and owns complete
candidate qualification. Focused execution, actual PostgreSQL execution, and CI
qualification are recorded separately; previous #172 CI is not audit evidence.

Post-correction affected tests: **266 passed in 22.94 s**, ten files covering the
four audit files plus desktop queue, phone preparation, readiness, offline
training, attempt lifecycle and opening-evidence deadlines. Smallest four
previously failing cases pass in 9.42 s. These runs have dirty task source on the
baseline commit, not clean HEAD.

After explicit cleanup was added to the new held-response tests, both affected
Home/recovery files passed all 69 cases. Two follow-up-error cases also passed
with explicit prototype, message, stack, cause and shared-observer assertions.
Typecheck passed (30.10 s), lint passed (54.35 s, ten pre-existing warnings), and
conflict/migration/whitespace guards passed. Subsequent changed-test lint passed
with one pre-existing warning.

Browser-file, PostgreSQL durability, tested clean revision, successful CI, and
cleanup records will be added before handoff. Evidence is retained outside
the clone under root `test-results/audits/2026-10-11-review-outbox-settlement/`.
Mocked commit/receipt cases establish client semantics, not PostgreSQL durability.
This finite scenario set is not exhaustive model checking and does not claim
cross-tab serialization. No merge or deployment is authorized.
