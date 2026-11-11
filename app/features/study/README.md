# Study review controller

Home uses `useStudyReviewController` from this public index for the existing online review and queue progression workflow. Its snapshot is read-only; named actions retain and submit the original review, capture receipt confirmation, retry progression, and coordinate Home's local presentation work. Home supplies queue, notification and attempt capabilities; the existing outbox and Study session remain the durable authorities.

A pending or failed save retains its original identity and does not advance the attempt. A superseded or unmounted presentation cannot acquire a successor, and recovery remains bound to the original workspace and intent store. Offline and demo saves continue through Home's existing adapters. This controller creates no session or scheduler.

Focused checks: `npm run test:unit -- tests/unit/study-controller-boundary.test.ts tests/unit/study-controller-review.test.ts tests/unit/study-controller-progression.test.ts tests/unit/study-shell-composition-regressions.test.tsx tests/unit/review-attempt-confirmation-regressions.test.tsx`.
