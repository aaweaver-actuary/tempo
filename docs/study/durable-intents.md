# Durable Study intents and legacy Learning adapter

This is the bounded #219/#221 reliability slice and the affected #13 exercise
adapters, based on main `f43b5f7fb4d7ddafeb3124a95f4fa3d64556abc4` (#278).
It consumes the installed legacy HTTP application. It does not adopt production
wire-v1, move the Learning database, or finish independent Study/Workbench
application packaging.

## Ownership and transactions

`@tempo/browser-intents` owns the dedicated `tempo-study-intents` IndexedDB
schema: immutable command envelopes, transactional delivery claims, retained
settlement records, keyed teaching facts and migration/progression checkpoints.
The workspace includes the configured Learning origin. Identities bind the
attempt, request, endpoint, content revision, timestamp and frozen payload.
Incompatible reuse fails. Terminal records are retained; replaying an old import
cannot resurrect them. A browser transaction ends before network delivery.
Notifications and expiring leases coordinate clients; neither establishes a
Learning result. Only confirmed Learning responses settle reviews.

`@tempo/study-session` owns delivery, settlement and bounded progression. The
application's `study-session-runtime.ts` creates one coordinator per store and
registers protocol adapters. The coordinator exposes snapshots/subscriptions,
start, submit, check-save, retry-save, retry-next and disposal. Submit can capture
a completion durably while deferring delivery until the existing feedback pause
ends. It reports pending only after that capture commits. Review/outbox
presentation, failure/teaching adapters and specialized exercise views use the
same durable boundary. The training store remains the board projection.

The committed review is durable before a progression checkpoint is written.
Both authorized prepared-continuation installation and live acquisition go
through that progression operation. Acquisition has at most two automatic
attempts, each bounded by 15 seconds, then an actionable retry. Reload acquires
the issued queue head; committed envelopes remain settled and are not posted
again. A later response can settle its original intent but cannot install a
board with an older session/attempt/revision/request generation.

Failure facts must commit locally before guided completion can retain a review.
A later correct move preserves Again. Unknown/unverified moves and unavailable,
stale or needs-analysis responses do not become invented failures. Defense
recognition prompts and grading policy remain the existing ones. Connected
answers and self-assessments retain their first immutable payload; receipts must
identify the original Study attempt. Startup recovers pending specialized commands
even when their original exercise has disappeared from the issued head.

Teaching uses card/revision/ply facts, with the fact and its delivery intent in
one transaction. Demo and offline-only facts are retained. Ordinary moves no
longer serialize the complete historical teaching set. Storage absence, denial,
quota and aborts remain explicit failures; no required commit is replaced with
an in-memory success.

## Cutover policy

Finish or close older tabs before acknowledging conversion in the new client.
The gate requires the explicit “Older tabs are closed — recover study work”
action. Migration retains every legacy localStorage source. It imports keyed
records and progress checkpoints idempotently; interrupted initialization
reimports all retained sources. Checkpoints describe progress and never authorize
skipping a possibly changed source. A committed/discarded tombstone wins over a
repeated source import. Malformed or incompatible data blocks conversion and is
kept for repair.

A storage event or changed source observed during import pauses conversion and
recovery, clears the acknowledgement and requests close-and-reopen. Already
loaded legacy JavaScript cannot be forcibly stopped by a new IndexedDB/browser
lock. Mixed-version writing is unsupported; the explicit acknowledgement is a
user assertion that those older writers have finished or closed. This branch
contains the conversion code and disposable fixture conversions, not a migration
of any live browser profile.

## HTTP fixture requirements for A/F

Fixtures must validate exact method/path, query, identity header and original
body. They must model absent, pending, blocked, failed and complete receipts
separately. Client interception demonstrates adapter behavior, not exactly-once
database effects. A/F own the database transaction proof.

| Operation | Legacy request / authority |
| --- | --- |
| Review | `POST /api/cards/{id}/review`, `Idempotency-Key: review-attempt:{attempt_id}`; body `{outcome, guided, queue_entry_id, attempt_id, recorded_at, expected_revision?, prepared_next?, opening_evidence_completion?}`. Only `{persisted:true}` is completion. |
| Receipt | `GET /api/operations/{encoded original key}`. `{state:"complete",response:{...}}` returns the original result; queued/executing/retrying/pending are unresolved. `unknown` or 404 permits replay of the retained original command. Blocked/failed remain actionable. A 202 without an operation ID is still pending. |
| Reconcile | `POST /api/cards/{id}/review/reconcile`, `review-reconcile:{attempt_id}:{sequence}`. Original result/time remain unchanged. `{persisted:false,conflict:{code,message,retryable:false}}` retains a conflict; it is not a saved grade. |
| Opening fallback | The existing protocol may authorize aggregate review after an opening-evidence rejection. Persist the rejection before sending the separate `:aggregate-only` operation. Quota fallback persists the compact aggregate intent before delivery. Separate `review-delivery` envelopes preserve each exact derived body/header/endpoint and link to the original review. |
| Failure | `POST /api/queue/entries/{id}/fail`, stable original operation key, `{card_id,expected_revision}` for modern markers. A definitive rejection uses `/state` to distinguish head/queued/completed/unavailable; only the existing authorized retry rule creates a new operation. |
| Teaching | `POST /api/cards/{id}/teaching`, `teaching:{card}:{revision}:{ply}`, `{revision,ply}`. A successful direct response or completed command receipt confirms it; unconfirmed 202 does not. |
| Connected Study | `POST /api/studies/{study}/exercises/{exercise}/attempts`, header equals `attempt_id`; frozen revision, answer, displayed choice order, hints and review queue context. The response must echo that exact attempt ID. |
| Self assessment | `POST .../attempts/{attempt_id}/self-assess`, key `{attempt_id}:self-assess`; original `{rating}` and parent attempt identity are retained. |
| Defense | `POST /api/defense-exercises/{candidate}/recognition` or `/attempt`; original answer, exercise revision, queue ID and linked recognition ID are retained. Recognized statuses include correct, incorrect, ready_for_move, ambiguous, illegal and needs_analysis. Needs-analysis remains unresolved and reuses the authorized original command. |
| Queue | `GET /api/queue/window?limit=20` (and the existing evidence query). Only the returned head is issued. Filtering a local result or a projection generation cannot issue the next prefetched item. |
| Prepared continuation | Only the successful Learning review receipt can authorize the exact frozen card, entry, revision, cycle and local date. Later local failure evidence cannot be erased by an older receipt. |

Specialized recovery is a bounded foreground pass of up to 20 retained commands;
another queue retry continues unresolved work. It does not fetch optional game
providers or start unrelated analysis. Game synchronization's automatic hooks
are enabled through Workbench navigation, outside ordinary Study navigation and
settlement. Existing optional board tools remain user-invoked.

## Regression ownership and retired implementation details

The package inventories own their installed tests. Root
`study-intent-storage-regressions.test.ts` owns interrupted/repeated migration,
legacy writers, unavailable/aborted storage, atomic teaching and specialized boot
recovery. Existing review, failure, teaching, lifecycle, conflict and Home suites
continue to own their prior invariants. Their asynchronous barriers now observe
committed IndexedDB transactions, rather than a synchronous localStorage write.
Native cases are registered in the existing controlled lane; E retains its
host/runner and protected pilot identities.

The former #191 synchronous whole-array publication order is replaced by
transactional per-key publication. The owning waiter regression still proves
that each waiter receives its own terminal outcome and earlier partial
settlements survive a later failure. Failure delivery can run alongside queue
acquisition after earlier review settlement; hydration still uses the retained
failure fact and never permits a clean attempt on that basis.

The feedback-completion regression formerly treated removal from the pending
array as an absence of evidence and required a fresh receipt read. Its replacement
checks the retained committed receipt: later pending/unconfirmed transport state
cannot undo a known committed result. The missing-identity regression continues
to reject advancement without a retained completion. Storage-fault diagnostics
still identify the original attempt and expose the browser's original cause.

The protected phone pilot formerly asserted deletion of a legacy array. Its
replacement assertion inspects the committed native intent/receipt, because this
cutover intentionally retains legacy sources. The original receipt-only retry,
true reload, native interactions and no-duplicate-review assertions remain. Both
original and controlled copies carry the same replacement, with the old proof
fingerprint and reason retained in the pilot ledger.

Controlled tests block service workers. They make no offline-shell, service-worker
or production exactly-once qualification claim. The PostgreSQL progression spec
is a separate real integration proof; neither it nor focused client suites is
the full hosted merge/release gate.

## PR #285 correctness repairs

Connected Study distinguishes uncertain delivery from authoritative validation
rejection. Direct validation HTTP 422 and a matching failed operation receipt
with status 422 (unless explicitly retryable) retain a `discarded` intent with a
structured rejection receipt. Its frozen command and validation details remain
inspectable. A discarded validation request is never a grade and is not replayed
on reload. Correcting the answer requires an explicit submission with fresh
attempt/request IDs. A rejected self-assessment retains its parent identity; an
explicit replacement assessment uses a fresh operation key. Other conflicts,
transport failures, pending/blocked receipts and needs-analysis keep their
existing recovery semantics. An uncertain answer/rating keeps its exact original
payload, endpoint, revision, timestamp and key. Mismatched operation/attempt
identities cannot settle the original. Rejection is reported only after its
terminal storage transaction commits; a storage abort leaves recovery pending.

Initialization promises cover only an opening in progress. Readiness requires
current-generation migration and complete hydration. A later refresh failure or
connection invalidation blocks Study; the retry button reopens and rehydrates the
backing database. Cached promises cannot establish readiness. Unsupported database
versions or a blocked upgrade remain errors with an explicit reload action.
Superseded openings/refreshes cannot publish into a replacement connection.

Legacy conflict state is installed atomically with first insertion. Reimport
validates immutable identity and preserves every mutable field of an existing
record, including reconciliation sequence, receipts, claims and suppression.
Legacy arrays are retained and the explicit old-tab cutover policy remains.
`study-legacy-validation.ts` owns the shared runtime validation used by migration
and diagnostic archives, including review evidence, prepared identities, conflict
shape and positive failure identifiers. Malformed sources remain retained and
block readiness before delivery.

Practice answer and self-assessment scopes share their parent's sequence boundary.
A transaction checks all relevant work is settled and advances that one marker.
An answer awaiting self-assessment is incomplete until a matching committed
assessment exists. New insertion checks the displayed generation in the same
transaction as retention: an earlier pending insert blocks reset; an earlier
reset rejects stale insertion before delivery. Boundary reads are consistent with
the selected records. Old requests remain retained; restoration also verifies the
assessment's parent attempt. Cross-tab reset notifications invalidate stale UI
callbacks. Existing retained envelopes are never rewritten to add a generation.

Specialized recovery persists a last-visited sequence cursor, examines at most
20 commands per pass, and wraps to older unresolved work. Unresolved checks and
dependent-command deferrals advance the cursor. Compare-and-set cursor writes
prevent a stale pass from overwriting another pass's progress; overlapping local
passes coalesce. Interrupted/unvisited commands remain retained and eligible.
Self-assessment and linked defense recovery require their retained predecessor to
be committed; independent commands continue. This is foreground recovery, not a
new background scheduler.

Teaching transactions notify subscribers with affected keys only after commit.
Other tabs reread those keys from IndexedDB; notification payloads are advisory.
The application coalesces targeted reads and Home subscribes to incremental
teaching updates. Initial hydration reads history once; ordinary delivery claims,
checkpoint changes and teaching moves do not enumerate or rewrite historical
teaching facts. Both seen and first-clean facts use this path.
Home unions retained legacy and server snapshots with committed facts, so a stale
snapshot cannot erase newer evidence. Modern demo completion writes its durable
fact without writing the watched legacy cutover source.

A terminal discard that wins against a delayed review response cannot produce
saved-grade UI, persisted-attempt IDs or failure-evidence clearing. Those effects
require the actual durable committed settlement and its `persisted:true` receipt.
Failed-operation validation detail is retained in the structured rejection receipt
alongside its original identity, HTTP status and authoritative message.
