# Legacy game → normal Study source checkpoint

First recorded start: **2026-10-18 20:21:08 UTC**. The parent review target was
21:35 UTC, with a21:50 UTC hard rescope. This source checkpoint returns early so
the repaired policy and finite artifact plan can be reviewed before resources
are released. Task evidence `STUDY-CHECKPOINT.json` records the final timestamp
and exact pushed head on `codex/legacy-game-study-bridge`; base remains
`67a11752097147816fc8090caa10bb821265eb4e`, draft PR311.

## Authority repair first

Commit `706af736` repairs the reviewed P1. An Italian route ending exactly after
Bb5 remains in the complete prefix-policy proof at black ply5, even though it
has no executable candidate there. The receiver formerly discarded it and
accepted a forged Ruy miss. The new regression failed against `c2111aa5`.
Backend and shared remediation now require the owner-derived exclusive policy
anchor at or before the encounter. Paired controls prove an earlier-ended route
still permits genuine later separation, while Game Evidence returns unknown
when no anchor exists. No comparison/frontier policy was forked.

## Concrete normal Study bridge

`legacy_study_remediation.py` is a backend owner adapter over the unchanged
12-table remediation module. It does not adopt runtime core, another scheduler,
a second frontier store or Content activation. Its current-fact projection
checks retained typed legacy authority against target/workspace, source and
catalogue revisions, current graph, exact original physical revision/full
executable/color/route, single repertoire membership, introduction chronology,
deletion/blocking and the existing current completed integrity certificate.
No legal replay, catalogue walk or graph construction happens in issuance.

The current canonical imported-game source must still match. All active current
game heads for the card must be coherent, with a bound of three; a corrected,
excluded, missing or larger mixed scope defers the whole card. Another valid
game cannot launder a stale incident through a card-level predicate. The normal
game owner hashes root, ordered moves and time; color/result are checked too.

Writes obtain card then day locks before the shared remediation lock. Source
share locks use nonblocking graph admission and `SKIP LOCKED`, then re-read the
projection; busy source grants no binding/settlement while the ordinary Study
action remains possible. Native locking and query semantics remain unqualified.

Two explicit commands keep their outcomes separate:

| Surface | Behavior |
| --- | --- |
| POST `/api/game-evidence/legacy/deliveries` | Typed immutable v3 envelope, required Idempotency-Key, existing background Celery command, outside-transaction preparation, durable operation and module receipt. No direct HTTP write. |
| POST `/api/game-evidence/legacy/cards/{card_id}/queue` | Required Idempotency-Key; one accepted card through shared admission and existing prelocked `ensure_card_queued_after`. Buried, issued/protected and invalid frozen proposals defer. No introduction or quota consumption. |
| GET `/api/game-evidence/legacy/cards/{card_id}` | Read-only actual target/workspace/card revision, played/expected move, current/stale, accepted, actual queue membership/deferred result, unsettled/settled/inactive, protected-head wait and fresh-attempt requirement. No acceptance, issuance or review. |

The exact routes join the existing Celery cutover guard. Unimplemented writes
remain blocked, GET retains query-only scope, and delivery uses background
request admission to avoid waiting on its own foreground lease. No CORS,
authentication, credential, provider or security setting changes. Earlier
checkpoint speculation that this required new evidence-token provisioning is
superseded: these are explicit local API commands using its existing boundary.

`queue_commands.issue_queue_head` binds the accepted incident frontier **before**
the durable origin stamp, after actual-head and revision checks. Already-issued
origins permanently skip binding. Prefetched later entries, offline/recovered
origins and completion cannot manufacture a retrospective frontier.

The normal review adapter settles using the real saved review ID, exact request
source reference, issued origin/failure marker and bound authority. The normal
review-receipt and real reconciliation paths call the shared qualifying-review
settlement; history-only reconciliation has no review ID and grants nothing.
The invalidation port requires the actual review owner's invalidation write in
the same transaction, then calls shared invalidation. Legacy has no general
opening-review invalidation endpoint; the existing defensive-rubric invalidator
does not provide an opening obligation path and is unchanged.

Retained accepted scope ownership still excludes old legacy misses even after
withdrawal. Old date-based review clearing cannot settle new bridge work or
erase its current priority label. Backend label normalization delegates ordering
to the existing shared presenter, preserving protected slots, ordinary order and
original label/hash identity. Stale bridge labels cannot grant presentation
priority. Physical schedules/review histories remain with normal Study.

## Source evidence and remaining gates

The synthetic original black card still executes
`e4 e5 Nf3 Nc6 Bb5 a6` in full, including its opponent prelude. Its game plays
`...Nf6`, expected `...a6`, at physical offset5. No new card, suffix, engine or
sample expansion is involved.

`test_legacy_study_remediation.py` connects real shared acceptance/replay, shared
admission, the actual legacy queue writer, actual head issuance, normal Study
scheduling/review receipts, reconciliation, shared settlement and invalidation.
It also covers protected head, prefetch, already-issued/stale/busy/corrected
source fences, guided/failure outcomes, old clearing coexistence and read-only
status. **PostgreSQL rows/effects are scripted**; the real SQLite compatibility
queue/review writer and shared module application execute. This is source
integration evidence, not native persistence, lock, query-plan or live delivery
proof. Worker receipt/migration and existing queue/legacy tests run alongside it.

Source results: **502 passed** across253 backend,114 GE,83 remediation,37 A bridge
and15 v3 model cases. Two JSON Schema cases remain deselected because the reused
source environment lacks `jsonschema`. Installed boundaries, wheel builds,
backend native SQL/migration/HTTP durability/concurrency, full v1/v2 exchange,
browser and TypeScript checks remain unqualified. SQL parsing, AST/Node syntax
and whitespace checks pass. Detailed commands/results are retained in task
evidence. No native/build/browser window or live action occurred.

The finite artifact runner now uses `--maxfail=1`; failed/incomplete inventory is
failure. Its255-case selection includes four new paired policy controls. The
[qualification plan](legacy-game-study-qualification.md) gives the exact command
and hash-pinned uv0.9.3 bootstrap inside the existing20-minute caps. A reviewed
already-local Python image ID and explicit artifact-only grant are still needed.
Native remains a separately granted five-case module window, which does not
qualify this new backend projection or actual HTTP/Study wiring.

Workbench UI and automatic deferred-admission draining are intentionally absent.
For this minimal slice a caller accepts, inspects, explicitly requests queue
admission, and the user later reviews through normal Study. A prior-issued target
requires a fresh later attempt; a deferred result is not a delivery claim. The
parent separately owns actual repertoire readiness. No live/provider activation,
priority delivery, deployment or merge is authorized here.

No wholesale Content/history migration or weaker provenance proved necessary.
Remaining estimate is roughly7–11 working hours plus separately owned readiness,
resource and reviewer waits, conditional on native qualification; this is not a
deployment promise. Next useful gate is artifact-only qualification, independent
review of these owner SQL/transport seams, then a separately bounded native
backend acceptance → normal Study proof before small Workbench wiring.
