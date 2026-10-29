# Study recombination owner — #503

Current delivery: the bounded R11 feature is assembled on integration `adfc8de8`,
with reserved normal-Study hooks and additive migration 053 after schema 52.
Frozen #502 policy is unchanged. Current encounter fields, deferred intake,
guarded priority writes and #547 bounded reads are retained. The actual-board
A08 case passed at `3a3317261c352f6faec4399f2d5e91db6908dc27`; native, UI and
required Games→Study boundary evidence is recorded below. This is source and
disposable assembled acceptance, not merge qualification or installation.

Study-owned implementation for SPEC v0.2 C01–C04 / A08. It consumes
[#502's pure policy](POLICY.md) at `86debb32` and #482's immutable
[`Presentation` / `JoinPlan`](../../contracts/product_v02/presentation.py).
The retained normal-Study save, queue recovery and confirmed-split paths now
consume `CurrentStudyRecombination` in the owning database adapter. New rows require
additive source migration 053; this is not a claim of live schema or deployment.
Migration/readiness coordination and browser acceptance are recorded below. There
is no additional service, scheduler, producer call or source content mutation.

## Public application and transaction contract

`Recombination(store, study, clock=..., next_local_study_day=..., new_id=...)`
borrows one existing Study writer transaction. The caller commits/rolls back all
current-owner, queue, schedule and private-store writes together. A returned
`applied` receipt is provisional until commit. Any exception requires rollback.
Do not catch an initialization/storage error and commit an earlier partial write.

- `record_review(route_id, ReviewEvent) -> CleanStreak`: accepts an exact saved
  owner fact only after `StudyBoundary.saved_review` proves its immutable
  presentation/revision/epoch, attempt/review, completion, assistance and failure
  facts. Unknown historical assistance never qualifies. The exact #502 reducer
  handles chronology and deduplication. An old pending completion remains tied
  to its original presentation; the new composite receives no inherited event.
- `propose(route_id) -> JoinDecision | None`: reloads current facts, overlays only
  this owner's persisted streaks and calls #502 `plan_join`. `None` means current
  authority/bounded locks were unavailable; an ineligible decision retains the
  policy's explicit holds. No recursive same-pass merging.
- `apply_join(RouteJoinPlan) -> PresentationChangeReceipt`: exact shared wire
  plan plus #502's immutable local route proof. Replans from current facts under
  nonblocking route/evidence/current-owner locks. Rechecks source and Study
  revision, conflict scope, memberships, current content, active/pending attempts,
  earned eligibility and unused result identity. Changed command payload under a
  reused command ID raises `command_identity_conflict`. Exact replay returns its
  original receipt even after subsequent split/rejoin generations.
- `accept_split(route_id, presentation_id, expected_revision, command_id,
  confirmed=True)`: the learner must confirm; three Again outcomes among the
  latest five **distinct completed Study attempts** are required. Isolates the
  final learner decision, preserving the legal opponent cue in continuation
  moves. Both fragments have fresh identities/epochs, introduced=true and 0/3.
  No targeted interior split is claimed. The existing UI carries the exact
  displayed queue entry through its persisted command intent. An explicit
  confirmation may retire only that same current, untouched issued head in the
  split savepoint. Its original origin/manifest and any empty checkpoint metadata
  remain intact. Any response/assistance, another pending attempt, wrong head,
  changed definition or busy source holds the operation. Failure rolls retirement
  back with the split; unrelated active attempts remain unchanged. Callers without
  attempt context retain the active-attempt guard and their original wire payload.

`ports.py` is the narrow composition contract. `StudyBoundary.try_current`
must reread/fence actual retained source/publication/conflict/membership rows,
current revisions, issued and pending attempts, due state and unresolved
obligations in the **same** writer transaction. Its `CurrentRoute` is a capability
result, not authorization from a producer snapshot. Unproved/ambiguous routes
return unavailable. `saved_review` must read actual owning immutable receipts and
hold their existing locks. `identity_available` checks all retained physical and
historical presentation IDs, including old generations.

`CurrentStudyRecombination.apply(PresentationChange)` initializes an actual fresh
`cards` row: learning, zero repetitions/stability/interval, no FSRS state or first
correct/review history, explicit already-introduced local date in the retained
queue's date representation, generated source
provenance (`canonical_route_source=0`). It uses the existing
`ensure_card_queued_after` writer with review admission. Source cards and their
memberships are retained. Only verified unissued duplicate queue occurrences are
blocked; no original queue identity or saved review is reassigned. The normal
`scheduling_review_application` remains the sole review transition authority.

The saved-review wrapper runs after immutable evidence completion and prepared
continuation issuance. A member that is issued/pending is held. It persists its
actual review-derived streak, then attempts one join inside a savepoint of the
same writer: an initialization failure rolls back the entire join while keeping
the genuine saved review and evidence. The response reports the hold/error; it
does not call a partial join applied. Direct application callers retain their
existing all-or-nothing outer transaction contract.

The application takes a nonblocking route lock and sorted evidence identity
locks. Current-owner capture must use nonblocking locks when it cannot follow
the existing card-before-day order after a completed review. Busy work defers;
it must never wait for another card while holding the current review/day locks.
Evidence locks also serialize one shared presentation used by different routes.
Release all locks promptly by ending the caller transaction; perform one change
per pass. No long-running database transaction or optional worker is introduced.

## Finite initial schedule and obligations

The exact default is:

`due = min(next local study-day boundary, each participating current due,
           each unresolved participating decision's original available_by)`.

`next_local_study_day(now)` is supplied by the existing owner with the user's
timezone/day boundary; it must be an aware instant strictly after now and within
26 hours (including DST changes). Every supplied deadline must be aware. Missing
component due state declines the join. An overdue deadline stays in the past and
is immediately eligible. This is a complete verification request by the next
local day, not a copied/averaged FSRS stability or a fabricated combined review.
Initialization owns no past review count and requests already-introduced work
without writing an introduction charge. Actual admission readers exclude its
explicit generated identity from new-card reconciliation/counts and preserve its
introduced status across day rollover.

The explicit `regrouped_card_sql(database, qualified_card_id)` read seam proves
that a physical ID belongs to this owner's persisted presentation history; it
returns `FALSE` when the optional schema is absent. The current admission reads,
stale reset, queue classifier and presentation mapping now consume that identity;
the native test uses the actual QueueAdmissionAdapter and a lost projection/day
rollover. No backdated date, invented review or copied FSRS state is used. The current `product_current_queue_facts` and `product_encounter_budget_facts`
readers retain this same explicit exclusion. This verifies R11's zero charge,
without claiming the separate T01 ranking feature's full acceptance.

`Obligation(logical_id, decision_id, available_by)` carries #487's original
unresolved effect identity/deadline unchanged. Only obligations whose existing
decision occurs in the combined sequence are included. The adapter updates a
route/decision → current-presentation projection; the immutable obligation row,
its lapse receipt, occurrence/detection time and settlement history stay with
#487. The existing #487 store now explicitly supplies `GameFeedbackTargets` for
current queue targeting and genuine settlement. It resolves the exact original
decision ID/repertoire to one current presentation; zero/ambiguous mappings retain
the original target. Effect `card_id`, original lapse review/provenance and
`available_by` never change. A genuine combined review settles matching effects
through #487, and invalidating that settlement reopens the same owed effect.
Combining itself neither settles nor creates a lapse. A newly admitted lapse
invalidates current joining eligibility once under its stable effect identity.
Applied-obligation recovery promotes the current target through the same Study
queue writer without a producer. Original effects are never resubmitted just to
change their queue target. #487 `994499f3` remains the authority/maintenance
reference, with its exact-UTC-introduction limitations unchanged.

The existing split donor reassigns `reviews.card_id`, rekeys old queue entries,
deletes memberships and archives the source globally. Those mutations are not
reused. R11 retains original immutable attempt/review identities and route-local
lineage; fresh split/rejoin identities cannot resurrect old schedules, receipts
or introductions. No source repertoire edit is made.

## Implemented source boundary (deployment unverified)

```mermaid
flowchart LR
    Saved["Retained Study saved review / between-attempt hook\nsource implemented"] --> App["Recombination application"]
    App --> Policy["#502 reduce_clean_streak / plan_join\nimmutable shared #482 values"]
    App --> Boundary["StudyBoundary\nCurrentStudyRecombination\nactual current authority + normal schedule/queue"]
    App --> Store["PostgresRecombinationStore\nborrowed writer; private facts/projection"]
    Boundary --> Owned["Existing Study owner\ncard/day locks; immutable attempts/history"]
    Store --> Private[("Study recombination tables\nsource migration 053; live schema unverified")]
    Obligations["#487 unresolved identity + deadline\nread-only injected facts"] --> Boundary
```

```mermaid
sequenceDiagram
    participant Hook as Retained Study hook
    participant App as Recombination
    participant Current as CurrentStudyRecombination
    participant Policy as #502 pure policy
    participant Store as Private store
    Hook->>App: after saved review, same writer
    App->>Store: try route + sorted evidence locks
    App->>Current: prove saved review/current route, no wait
    App->>Policy: reduce or replan exact immutable inputs
    alt member active/pending, stale, ambiguous or busy
        App-->>Hook: hold, no change
    else one verified route-local change
        App->>Current: initialize fresh schedule, route-only queue projection
        App->>Store: zero streak + lineage + current map + receipt
        App-->>Hook: applied (provisional)
        Hook->>Hook: commit all, or rollback all on error
    end
```

## Private storage shape

[`recombination.py`](../../../../database/tempo_legacy_database/adapters/study/recombination.py)
contains source DDL `SCHEMA`, also frozen in
[`053_study_recombination.sql`](../../../../database/tempo_legacy_database/ddl/migrations/053_study_recombination.sql).
The store/adapter runs no DDL, opens no connection and commits nothing. 053 follows
#491's published `f137d970` migration 052; source readiness is 53. Complete ordered
DDL through 053 is exercised in the native fixture. Central registration remains
with the serialized integration owner. Columns are source, not a live-schema claim.

```mermaid
erDiagram
    study_recombination_streaks {
        text presentation_id PK
        bigint revision PK
        text evidence_epoch PK
        jsonb state_json
    }
    study_recombination_events {
        text event_id PK
        jsonb event_json
    }
    study_recombination_completions {
        bigint sequence PK "generated identity"
        text attempt_id UK
        text review_id UK
        text presentation_id
        bigint revision
        text evidence_epoch
        timestamptz completed_at
        jsonb event_json
    }
    study_recombination_changes {
        text command_id PK
        text digest
        jsonb change_json
        jsonb receipt_json
    }
    study_recombination_presentations {
        text presentation_id PK
        text command_id FK
        jsonb result_json
    }
    study_recombination_current {
        text route_id PK
        text decision_id PK
        text presentation_id FK
    }
    study_recombination_changes ||--|{ study_recombination_presentations : creates
    study_recombination_presentations ||--o{ study_recombination_current : targets
    study_recombination_streaks ||..o{ study_recombination_completions : "logical identity, no FK"
    study_recombination_events ||..o| study_recombination_completions : "logical original event, no FK"
```

All stored facts belong to Study. There are no FKs to source cards, original
attempts, reviews or #487 effects: those are explicit logical provenance
references in immutable JSON, not ownership transfers or inferred mappings.
`changes.change_json` retains exact retired route occurrences, result moves,
lineage, fresh evidence epochs, conservative initialization and obligation refs.
Unique attempt/review IDs prevent retries becoming extra split failures; the
indexed completion query reads at most five distinct completed facts. The
current projection is keyed by route and decision, so another branch/repertoire
is untouched. Original events and historical changes/presentations are append-only.

## Focused verification and remaining integration

Run the assigned commands with an existing Python runtime/source paths:

```
make python-file FILE=backend/tests/test_product_recombination_application.py
make python-file FILE=backend/tests/test_product_recombination_postgres.py
```

The native file requires an explicitly task-owned database named
`tempo_task503_*` via `TEMPO_RECOMBINATION_TEST_DSN`; unavailable infrastructure is
a failed required check, never a skip/pass. The six original private-store cases
use explicit fake current/scheduling capabilities over real PostgreSQL. They are
retained evidence, not ordinary Study proof. The new `-k current_owner` cases use
the actual retained source, source/publication/integrity fences, actual queue
issuance, immutable manifest/evidence completion, normal review controller,
existing FSRS scheduler, saved review/attempt receipts, current queue writer,
#487 target/settlement adapter and confirmed split command. Only the task database
connection/policy read is injected. The synthetic source fixture includes retained
unknown-assistance history; every qualifying success is performed through the
normal controller. There is no Games/provider implementation in the Study path.

The concrete adapter materializes at most 64 authored routes, 256 graph steps,
128 physical/current generated members and 128 obligations. It refuses larger,
unproved, changed-content, unsupported and ambiguous scopes with diagnostics.
Current client evidence supports at most 40 moves / 20 learner decisions; larger
proposed results are held without interrupting the saved review. Each clean event
requires complete persisted evidence, all actual expected first responses and no
assistance/reveal/correction/manual-failure signal anywhere in that attempt.
Counted original review/receipt/origin rows are rechecked against invalidation and
held nonblockingly. Legacy aggregate correct ratings with unknown assistance do
not become clean evidence. Explicit retained split parent/lineage is projected;
new fragments always use fresh identities/epochs.

The old queue cannot represent all shared route occurrences independently.
Concrete shared/ambiguous members stay unjoined (`shared_occurrence_queue_unavailable`)
and retain their separate original memberships and normal Study. Private policy
and application ports still preserve retained uses for a future richer queue;
there is no global archive/delete fallback. Foreground Study priority is retained:
all added source/evidence/member lock acquisition is nonblocking, and join errors
are isolated by the savepoint after the actual review has saved.

Serving an existing generated presentation locks its own definition under the
same current route fence; a busy join neighbour only defers the later proposal.
The current R11 guard checks generated identity/source availability before
recovery, failure marking and review lookup. Current source/integrity fences
remain authoritative. The separate unmerged #487 scoped-conflict extension is
preserved on the earlier R11 branch rather than imported as a prerequisite.
Shared or ambiguous occurrences remain unjoined with an explicit diagnostic.


The external task runner bounds the existing disposable image, uses a loopback
ephemeral port, verifies exact container ownership, removes its container/anonymous
volumes, and releases the nontruncating validation lock. It installs nothing.
Deployment/resource isolation on the live host remains unverified.

The A08 browser file uses the real Study board and HTTP endpoints, then accepts
the retained split control. A guarded disposable source/readback fixture stages
explicit extra same-day practice through the existing queue writer; it never
seeds qualifying reviews, clean streaks or settlements. Another actually issued
unrelated attempt and all original card/history identities are checked throughout.
All three original cards retain synthetic old history with explicitly unknown
assistance, so ordinary refresh does not reclaim them as abandoned, unreviewed
introductions. That history earns no R11 evidence. The native recorded-review /
prepared-head case runs actual queue refresh between each new qualifying review.
No Study API is intercepted. The current bounded browser result is recorded below.
The retained UI's first teaching pass on a fresh composite is an actual assisted
review and earns no clean streak. A08 then requires three fresh whole-sequence
clean reviews; it seeds no combined first-correct or prior-review facts.

Central regression/schema/diagram registration links this owner. Current-base
application and public-import files pass 17 and three cases; #547's unchanged
bounded-reader file passes ten. The native requirement is covered without
repeating unchanged cases: six private transaction and two adapter cases pass at
`4a4793db`, eleven current-owner cases pass at `00e10f3c`, and the repaired real
callback/obligation/settlement case passes at `8f388b19`. The latter proves that
translated nested repertoire paths retain the generated target's priority label.
The current legacy and product admission readers both exclude explicit generated
identities from introduction charges. Split-intent units pass three cases and
the scoped TypeScript check passes. The existing required Games→Study selection
`python backend/app/study/qualify_priority.py` passes all 15 cases at `3a331726`.

The single corrected current-base A08 run at
`3a3317261c352f6faec4399f2d5e91db6908dc27` passed: **1 passed**, browser stage
99.09 seconds. It saved genuine A/C/B reviews, created AB then ABC only after
fresh whole-sequence reviews, applied the visible confirmed split, and passed
original-card/history, fresh-lineage, unrelated-attempt and retired-ABC queue
assertions. Its guarded fixture preserves the image's declared pure-library
source paths instead of replacing PYTHONPATH. No completed clean review or
combined first-correct fact is seeded. The earlier bootstrap failure is retained
as fixture evidence, not counted as a pass.

Runner cleanup completed in 4.13 seconds and released the nontruncating validation
lock. Existing unchanged pure policy and scheduling evidence is reused. No broad
suite was run. Hosted merge qualification and live resource isolation remain
unverified. This task performs no live join/review, history reset, graph migration
resumption, deployment or merge.
