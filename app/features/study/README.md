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
