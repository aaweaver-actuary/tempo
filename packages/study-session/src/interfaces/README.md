# Study session public surface

Owner: #490 UI/session lane. `index.ts` explicitly exports the existing
`createStudySession`, `LearningClient`, `SessionPhase`, `SessionSnapshot`,
`Settlement`, `StudySession` and derived `StudySessionOptions` signature. It aliases
the canonical package implementation; no second state machine or DTO is defined.

The session owns delivery/progression state, timeout/cancellation and subscriptions.
Injected `IntentStore` owns durable commands/checkpoints; injected `LearningClient`
and the receiving Study owner establish actual settlement. Constructing a session
does not read/write storage or deliver learning. Disposing it retains durable work.
Existing pending/conflict/retry/late-response behavior and function identity stay
unchanged.

## Exact transition edges and adoption

`../index.ts` is the current library implementation, not application composition.
It still imports the canonical `IntentStorageError` and storage contract from
`@tempo/browser-intents`' existing package root. That public legacy dependency is
retained until its owner and #501 release a compatible interface/package entry;
this pass adds no storage writer or cross-owner mutation facade.

The package manifest currently exports only `@tempo/study-session`'s root. The new
source compiles to `dist/interfaces/index.js`, but an installed
`@tempo/study-session/interfaces` import is **not available** until #501 changes
the manifest/registry/packaging and migrates callers. No manifest, root index,
implementation or application adapter is edited under this first-wave lease.
Exact current application callers are `app/lib/study-session-runtime.ts` (factory
and client) and `app/lib/review-outbox.ts` (session type). After the subpath is
published, #501 can replace their package-root imports with the declared entry
without changing the constructor/callbacks; neither file is leased here.
Remove old import compatibility only after that adoption, semantic regressions
and #482's dependency/export negative controls pass.

Focused proof: `tests/unit/public-surface-study-games.test.ts` checks the canonical
identity and inert construction/start/dispose using supplied capabilities. The
existing package `test.mjs` retains settlement/retry/progression semantic ownership;
no native Study or release build is required by this mechanical surface.
