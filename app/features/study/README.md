# Study review controller

Home uses `useStudyReviewController` from this public index for the existing online review and queue progression workflow. Its snapshot is read-only; named actions retain and submit the original review, capture receipt confirmation, retry progression, and coordinate Home's local presentation work. Home supplies queue, notification and attempt capabilities; the existing outbox and Study session remain the durable authorities.

A pending or failed save retains its original identity and does not advance the attempt. A superseded or unmounted presentation cannot acquire a successor, and recovery remains bound to the original workspace and intent store. Offline and demo saves continue through Home's existing adapters. This controller creates no session or scheduler.

Focused checks: `npm run test:unit -- tests/unit/study-controller-boundary.test.ts tests/unit/study-controller-review.test.ts tests/unit/study-controller-progression.test.ts tests/unit/study-shell-composition-regressions.test.tsx tests/unit/review-attempt-confirmation-regressions.test.tsx`.

Queue acquisition uses the flat queue and the original pending issuance receipt; held and unavailable outcomes remain distinct. Only an explicit `foreground_wait` denial receives foreground-wait handling. There is no optional prepare-next request.

## Frozen ordinary exercise transport (grader v1)

Published linked `study_exercise` cards remain supported ordinary Study work. Existing attempts and self-assessments with their original owner and receipt remain recovery work. New issuance excludes exact `defense`/`endgame` content and `defense:`/`endgame:` admission sources; schema 64 records specific retired producer operations without erasing their identities. Unknown kind, endpoint, or authority is held; a generic `study-command` tag alone is not a retirement decision. Queue selection, prepared snapshot, PracticeCard mapping and TrainingView reach this retained runner. No authoring or producer restoration follows from this contract.

| Method and path | Existing request and successful result |
| --- | --- |
| GET `/api/studies/{study_id}/exercises/{exercise_id}/present` | Learner projection: `id`, `revision`, `type`, `prompt`, `hint`, `fen`, and interaction fields for that type. No accepted lines, required/optional answers, correct option IDs, explanation, or rubric. Owner verifies study/exercise and current available revision. |
| POST `/api/studies/{study_id}/exercises/{exercise_id}/attempts` | `StudyAttemptRequest`; foreground `studies.attempts.submit` payload `{study_id, exercise_id, attempt}` with original `Idempotency-Key`. Returns pending-self-assessment parent or completed envelope below; command dispatcher retains 202/receipt semantics. |
| POST `/api/studies/{study_id}/exercises/{exercise_id}/attempts/{attempt_id}/self-assess` | `{rating: "correct" | "again"}`; foreground `studies.attempts.self_assess` payload `{study_id, exercise_id, attempt_id, assessment}`. Same original operation semantics; returns completed envelope. |
| GET `/api/studies/{study_id}/exercises/{exercise_id}/attempts/{attempt_id}/feedback` | `{specification}` from the persisted attempt's revision, after verifying all path ownership. This legacy projection has no identity fields, so the route must enforce identity and revision before returning it. |

Pending is `{attempt_id, assessment, pending_self_assessment: true}`: the durable parent command has completed and can release only its dependent self-assessment, but no human review is complete and the queue must not advance. Completed is `{attempt_id, assessment, rating, requested_rating, review, persisted: true}`; `review` is null for practice and contains the review and requeue identities for a review. The writer owns the request hash, queue/card/revision fences, original attempts and scheduling transaction. Existing writer errors preserve their HTTP distinctions: 404 unavailable owner/attempt, 409 identity/revision/queue conflict or retired presentation, 422 invalid request/answer; dependency failures and pending commands must not be represented as completed success. B31 must preserve these distinctions and prove exact persisted revision ownership on feedback. `study-exercise-transport.ts` provides pure response decoders for C21 adoption; it does not itself activate these routes or establish assembled behavior.

## Independent serving and retained-work disposition

The SPEC retains ordinary monolith Study and retires the independent Learning
service/database. This source decision classifies the separate Study build as a
recovery-only hold/export surface or a removable unused serving target once
recovery is reachable elsewhere; it does not activate that disposition or
change current code. `static/src/main.tsx` mounts the supported monolith shell;
`static/src/study-main.tsx` instead mounts `StudyConnectionBoundary` for the
`TEMPO_TARGET=study` build. The latter is still buildable source, not evidence
of a supported independent deployment.

| Boundary and current consumer | Source disposition |
| --- | --- |
| `StudyConnectionBoundary` calls `connectStudy`; `study-client.ts` requests `/api/study/bootstrap` and validates origin, workspace and bootstrap. | The current `backend/app/main.py` has no bootstrap route. New independent connection/issuance is unsupported; do not restore Learning to satisfy it. Cached practice is not a settlement receipt. |
| `StudySettingsPanel` reads and submits `/api/study/settings` using `settings-command`. | The current monolith owns `/api/settings`, a different contract. Hold retained independent settings commands with original endpoint, body and ID; do not translate them. Remove the obsolete panel/control only after recovery is available. |
| `fetchAndInitializeQueue` reads `/api/queue/window`, follows `/api/operations/{id}` and rereads the authoritative queue; `main.py` exposes these monolith routes. | Supported new Study issuance belongs to the monolith queue/command owner, including issued identities and frozen exercises. A receipt is not a playable successor. |
| `study-command-client.ts`, `offline-training.ts`, browser intent and prepared-training stores retain attempts and original receipts. | Preserve original authority, request IDs, bodies, attempts and parent/child dependencies. Recover only against the positively bound original authority; ambiguous or unavailable work remains held. C21's scoped retained-work holds do not authorize relabeling. |
| `StudyConnectionBoundary` calls `retainedStudyData()` to inspect and export; `study-connection-storage.ts` validates registry bindings and inspects existing stores. | Preserve read-only inspection/export and independent client factories needed for original-authority recovery even if the serving entry is removed. Export is not a receipt or delivery. |

FE31 adopts the existing `study-command-client` / Study session retained
command owner for the current `tactics_view.tsx` attempt in the monolith,
retaining the exact tactic operation and validated receipt before local
progress. FE32 adopts that same owner for `training-bury-command.ts` and
`home_view.tsx`, with a bounded reader for existing burial markers and their
original operation/body through receipt and queue refresh. Neither should
create a second lifecycle or redirect an old independent request. FE36 owns
removing the separate entry and its connection, settings and build-mode
controls after these adoptions and a reachable recovery/export path are
reviewed. A newly discovered supported consumer requires the delivery owner to
resolve its exact backend contract before removal.

### Prepared-storage authority handoff

The current `database/browser/offline-training-storage.ts` caches one
`databasePromise`, although `studyOfflineDatabaseName()` selects the locator
from the current origin and workspace. `readPreparedTraining` and
`updatePreparedTraining` use that cached handle. The connection boundary's
normal connect/disconnect controls reload the application, but the public
connection client also permits a same-lifetime authority change. Source
inspection therefore identifies a possible stale handle across such a change;
it is not a demonstrated live incident. The connection registry and retained
inspector attribute offline records by the exact authority-derived database
name, and cutover blocks unresolved foreign work, but an empty or terminal
snapshot does not itself require a hold.

The recovery owner should bind each opened handle to the captured exact locator
and fence delayed prepared-queue writes at their transaction before put; an
in-flight old operation must retain its old authority or hold. Verify same-
authority recovery, an allowed A-to-B transition, delayed writes, and retained
bytes in both stores. Do not move, delete or infer the provenance of existing
records. This is an owner handoff, not evidence that the repair or a real browser
transition has been executed.
