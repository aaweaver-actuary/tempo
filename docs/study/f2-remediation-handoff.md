# F2: Study adoption of remediation-aware Learning

## Boundary supplied by F1

The independent Learning service owns accepted Game Evidence, remediation,
eligibility/admission, issuance and settlement. Study sends the same legacy HTTP
queue/review/operation envelopes used by its existing strict schemas and durable
intent adapters. The example Learning wire-v1 package is not newly activated.
The browser does not compute obligations, settle them locally or contact Game
Evidence to decide whether studying is available.

Game Evidence's private worker credential targets `/internal/evidence`; it must
never be given to Study. Study uses the distinct workspace study credential.
Publication staging/activation uses a third operator credential. Configure exact
Learning Host and browser Origin allowlists. Optional `X-Tempo-Workspace` must
match the configured database workspace; CORS permits that header,
`Authorization`, `Content-Type` and `Idempotency-Key`.
The exact environment-variable names and private-file rules are in the
[runtime configuration](../../services/learning/runtime/README.md#install-and-run).

## Selection, completion and receipts

| Operation | Study contract |
| --- | --- |
| Acquire cards | `GET /api/queue/window?limit=20&include_opening_evidence=true`; only the returned head is authoritatively issued. `/api/queue/today` and `/api/queue/prepared` retain their current envelopes. |
| Render priority | Use the existing nullable `gameplay_priority_reason`. Ordering is supplied by Learning. Preserve the full queue identity, revision, cycle, local day and opening manifest; never reorder/admit from game counts or a local cache. |
| Save review | `POST /api/cards/{card_id}/review`, original `Idempotency-Key`, `attempt_id`, `queue_entry_id`, `expected_revision`, outcome/guided state, original recorded time and any frozen opening-evidence completion. Keep the exact durable body/header/endpoint on uncertain delivery. |
| Reconcile | `POST /api/cards/{card_id}/review/reconcile` retains the existing competing-review/history semantics. A persisted result can contain reconciliation/warning information. It does not authorize a replacement board by itself. |
| Read receipt | `GET /api/operations/{operation_id}`; 202 retains `operation_id`, state and Location. Only a matching authoritative successful result with `persisted:true` settles a review intent. A 503 means retain the original identity and retry. |
| Retry command | `POST /api/operations/{operation_id}/retry`, preserving the original accepted intent. Do not regrade a committed review when its successor load fails. |
| Mark failure | `POST /api/queue/entries/{entry_id}/fail`, stable original operation key and `{card_id, expected_revision}`. `/api/queue/entries/{entry_id}/state` distinguishes current/queued/completed/unavailable recovery. |
| Opening evidence | Existing checkpoint and attempt-status routes remain required where used. Teaching/checkpoint completion is not remediation settlement. |
| Continue | Install a prefetched continuation only when the committed review receipt explicitly authorizes its exact card, queue entry, revision, cycle and local date. Projection generation and a filtered local list never issue a card. |

Learning internally binds `queue:<entry_id>:revision:<revision>` on first issuance.
Study does not create or submit that remediation identity. Reload reuses the same
frontier. A miss accepted after issuance may require another authoritative attempt;
the old answer cannot settle it retrospectively. Unaided correct controlled study
qualifies; Again, guided/failed, stale, invalidated or unbound work does not.
Same-day reinforcement remains under the existing scheduler and may precede the
next ordinary card. No new remediation-specific review submission is needed.

Exact retained request/response examples are in
[`legacy-protocol.json`](../../services/learning/runtime/fixtures/legacy-protocol.json).
The native [review fixtures](../../services/learning/runtime/tests/test_runtime.py)
and [checkpoint/prepared-next fixtures](../../services/learning/runtime/tests/test_transactions.py)
exercise those shapes against the actual application. The
[remediation cases](../../services/learning/runtime/tests/test_remediation.py)
add current priority, prepared issuance, receipt replay and unchanged-frontier
assertions. These executable fixtures use real server-issued identities; copy the
shape and preserve the actual response values instead of inventing identifiers.

## Work F2 must implement or validate

1. Route the full Study application to independent Learning with the existing
   `@tempo/browser-intents`, `@tempo/study-session` and exercise adapters. Do not
   introduce another persistence framework or post one operation to both backends.
   Changing Learning origin changes the browser workspace identity: preserve old
   unresolved envelopes and define explicit receipt reconciliation/cutover rather
   than silently abandoning them.
2. The full legacy teaching adapter uses GET/POST
   `/api/cards/{card_id}/teaching`. **Those routes are currently absent from the
   independent runtime.** The retained implementation is in
   `backend/app/teaching_commands.py` and the HTTP adapter in `backend/app/main.py`.
   Port/adapt that narrow command and read shape through Learning's existing
   gateway/roles/revision checks, or deliberately adapt the existing durable
   teaching envelopes to an equivalent supported Learning protocol. Preserve
   exact intent identity, incremental facts and honest pending/failure states.
   This is an explicit remaining #224/#194 dependency, not proof that full Study
   is already portable. Audit other full-application initialization calls against
   the runtime route inventory before switching the application.
3. Preserve #285's receipt-first recovery, competing-tab claims, cancellation
   ownership, generation fences, pending/conflicted/committed states and bounded
   saved-next-load recovery. Await durable intent insertion before delivery or
   claiming a saved state. Retain guidance/failure evidence on every exercise
   adapter; an old successful callback cannot erase newer failure state.
4. Add the actual full Study screen path to the native PostgreSQL assembly proof:
   acquire, answer, save once, advance/reinforce, reload, retry after response loss,
   two tabs and ordinary deterministic introduction with Game Evidence,
   Content/Intelligence/model workers and broker absent. Controlled frontend
   fixtures remain useful UI evidence; they cannot establish database persistence.
5. Keep historical v1 obligation conversion, authored-content import/activation
   and live study-history migration as explicit later cutover work. F1 retains old
   receipts/rows without manufacturing missing provenance and performs no live
   conversion. Do not close #194/#224 based on the existence of the libraries or
   this focused browser adapter proof.

## Evidence available to F2

Learning's regular native suite owns the real installed producer -> authenticated
HTTP -> separate PostgreSQL database tests. It drops an acknowledgement after
Learning commits, replays after process restart, and delivers an actual source
correction with an exact withdrawal. Its browser case stops the producer, restarts
Learning alone, performs durable-outbox response-loss/reload recovery, settles the
accepted obligation and saves ordinary introduced work while honoring normal
reinforcement. Cross-database connection attempts by the two restricted runtime
roles are denied. The Learning image has no producer/backend/broker/model modules.

This demonstrates the selected client/API path, not the entire Home/Study screen,
service-worker/offline shell, production routing or live external providers.
Source-bound commands/results and limitations are recorded in the F1 validation
record. No production deployment or architecture-issue closure is implied.

## Recovery guarantees added to F1

Same-day refreshes preserve an unfinished remediation cursor and schedule a durable
follow-up pass for arrivals behind it. Stale compatibility transport receipts bind
their original payload; they cannot authorize evidence acceptance or be reused for
changed contents. Producer HTTP-401 failures retry the original delivery after
credential correction and restart. The [producer recovery instructions](../../services/game-evidence/README.md#authentication-recovery)
cover the explicit command for old 401-blocked rows. No new Study wire fields or
browser authority are introduced. Full routing, teaching and cutover remain F2 work.
