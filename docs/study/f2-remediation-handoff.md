# F2: Study adoption of remediation-aware Learning

## F2 integration ownership agreement

The F2 integration owner owns Study composition, authenticated Learning routing,
session/storage authority transitions, HTTP registration, settings authority,
migration registration and native assembly qualification. Contributors may supply
isolated settings presentation against these fixtures; they must not register
competing routes or change the migration manifest independently.

Study is a production build of the existing training UI, with a separate explicit
Workbench launch. Its connection screen accepts Learning origin, workspace and
the Study credential. The credential is retained only in session storage; the
non-secret authority registry binds each retained store to its origin/workspace. No
credential is stored in a durable command or passed to Workbench.

Authenticated `/api/study/bootstrap` returns `schema_version:1`, `workspace_id`,
`timezone`, `local_date` and `settings`. `/api/study/settings` reads and updates
only study-only mode, opening/Study/tactics introduction limits, defense inclusion
and the light first interval. Timezone is read-only. These shapes are independent
of the mixed-domain legacy Settings schema. All existing command envelopes remain
legacy Learning HTTP. Teaching retains `{revision,ply}` and camel-case facts.

One integration owner edits shared composition, client/schema registration,
component/build inputs and migration manifests. Existing `legacy-protocol.json`
is the shared fixture owner; fixtures contain placeholders, never credentials.
No live profile/database conversion or cutover is authorized by F2.

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

## F2 implementation and remaining transition work

`npm run build:study` now builds the existing Home/training application at `/`.
The connection boundary validates bootstrap before mounting queue consumers.
`app/lib/study-connection.ts` owns authenticated delivery, strict Study bootstrap
and settings schemas, timezone, and origin/workspace storage authority. Legacy
`API_URL` remains unchanged. Optional Workbench views are lazy and Study has no
provider initialization, repertoire preparation, game synchronization or optional
startup preloads. `TEMPO_WORKBENCH_URL` configures a separate explicit link.

Learning now owns teaching GET/POST through its existing command gateway and
borrowed transaction. The card edit lock serializes new facts against retirement;
active accepted opening revision and learner-turn ply are required. Duplicate
facts retain their original `taughtAt`; completed receipts and historical reads
survive retirement. Teaching cannot create review, scheduling, issuance or
remediation effects. Accepted move-membership reads replace the opening UI's
Content lookup, including every accepted executable alternative.

Bootstrap/settings/progress use dedicated Learning schemas. Settings accept only
`study_only_mode`, `new_cards_per_day`, `study_new_per_day`,
`tactics_new_per_day`, `include_defensive_cards_in_daily_stack`, and
`light_first_interval_days`. The public tactics spelling maps to the existing
`tactic_new_per_day` column. No Learning migration was added; all facts use
existing tables. Provider/account/analysis/coverage settings remain Workbench-owned.

The existing intent store/session coordinator now also adapts tactical discovery,
endgame attempt creation, burial and settings. Authentication is attached only at
delivery; retained identities, bodies, endpoints and recorded times remain frozen.
Receipt identity is checked before confirming success. Unconfirmed 202 teaching
responses remain pending. New offline databases and queue caches are authority
scoped while keeping their existing schemas. Read-only inventory precedes every
connection; foreign unresolved work, ambiguous legacy records and incompatible
storage block switching. Inspection/export preserves original envelopes. Recovery
requires the original service; no automatic retargeting, clearing or live conversion
is provided. Terminal history remains inspectable without resurrecting it.

Candidate bootstrap does not rebind the active client. Authority inventory is
rechecked after bootstrap to catch an older writer arriving in flight; only then
is the connection persisted. Successful connection reloads the shell so existing
store handles, queue caches and coordinator owners bind the new origin/workspace.
Allowed browser origins can observe actual 401/403 responses through CORS and
reconnect; Host checks still cover preflight. Native acceptance rotates the real
service credential, connects through a second Learning origin, preserves the
original completed receipt and verifies the new command's authority. Its real
forwarding delay also proves cancelled old queue bytes cannot replace the older
tab's unfinished foreground attempt or cancel the newer acquisition.

Independent offline practice retains only the already issued head. It creates no
local repeat or authoritative successor. Reconnect/reload replays the original
answer and requires Learning confirmation before advancement. Cached typed
specifications use offline grading only in an explicitly offline queue; connected
snapshots still submit through Learning. Uncovered defense and uncached unavailable
tablebase results remain ungraded.

The registered `learning-full-study` suite invokes
`services/learning/runtime/integration/run_study.py` with the production UI,
installed Learning, explicit accepted fixtures and restricted disposable PostgreSQL.
Its native board/control scenarios and the regular owning regressions preserve
#285/F1 receipt, transaction, claim, generation and cancellation invariants. Exact
results, source inputs and limitations are recorded in
[the F2 acceptance map](../validation/f2-study-learning.md).

Historical v1 obligation conversion, authored-content/history import, production
publication scale beyond the existing 128-definition bound, frontend artifact
extraction, release/cross-browser offline qualification and deployment remain
separate work. Do not close broad #194/#224 or frontend parent issues from this
bounded runtime integration. No live profile/database is converted by F2.

## Evidence available to F2

Learning's regular native suite owns the real installed producer -> authenticated
HTTP -> separate PostgreSQL database tests. It drops an acknowledgement after
Learning commits, replays after process restart, and delivers an actual source
correction with an exact withdrawal. Its browser case stops the producer, restarts
Learning alone, performs durable-outbox response-loss/reload recovery, settles the
accepted obligation and saves ordinary introduced work while honoring normal
reinforcement. Cross-database connection attempts by the two restricted runtime
roles are denied. The Learning image has no producer/backend/broker/model modules.

F1 demonstrates the selected client/API path. F2 adds actual Home/Study and the
production cached shell in its separate registered assembly suite; live external
providers remain outside that proof.
Source-bound commands/results and limitations are recorded in the F1 validation
record. No production deployment or architecture-issue closure is implied.

## Recovery guarantees added to F1

Same-day refreshes preserve an unfinished remediation cursor and schedule a durable
follow-up pass for arrivals behind it. Stale compatibility transport receipts bind
their original payload; they cannot authorize evidence acceptance or be reused for
changed contents. Producer HTTP-401 failures retry the original delivery after
credential correction and restart. The [producer recovery instructions](../../services/game-evidence/README.md#authentication-recovery)
cover the explicit command for old 401-blocked rows. No new Study wire fields or
browser authority are introduced. F2 now owns full routing and teaching; live cutover remains separate work.
