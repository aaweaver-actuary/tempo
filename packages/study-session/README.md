# Study session 0.1.0

`@tempo/study-session` owns durable submission, settlement and bounded next-attempt
loading. It receives an intent store, a narrow `LearningClient`, clock and delivery
owner. It imports no application stores, React components or optional services.

`createStudySession` exposes snapshot/subscription, start, submit, check-save,
retry-save, retry-next and dispose operations. Explicit phases distinguish active,
persisting, pending, conflicted, committed, next-attempt-loading, next-load-failed
and storage-failed states. Pending transport acceptance never implies commitment.
Late delivery can settle its original record but cannot change a newer session
snapshot. Disposal preserves durable work.

Next loading records a progression checkpoint and makes at most two bounded
acquisitions. Retrying next loading requires an already settled review and never
submits its grade again. The application adapter alone validates issued heads and
prepared continuations; a projection counter is not authority.

Build the declared browser-intents dependency, then run this package's `npm test`
or use `node tools/component-build/test-lane.mjs --component study-session` from
the repository root. The legacy frontend consumes the installed workspace export.
The package is a library, not a separately deployed Study Web application.

Adapters may return terminal `discarded` settlement, including authoritative
validation rejection. The coordinator commits that result before publishing it,
returns the actual retained terminal winner in a race, and never advances from a
discarded command. Its caller owns rejection messages and corrected identities.
Submission can forward an expected storage boundary for atomic new retention.
