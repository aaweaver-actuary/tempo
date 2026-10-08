# Phone recovery and messaging validation

Source base: 2dd998b8df9e099c1fa39eec70c522b4db84863e. Isolated checkout:
`.dev-copies/phone-recovery-messaging`, branch `codex/phone-recovery-messaging`.

Selected scope: pending receipt confirmation and recovery, discovery admission reload,
notification incident ownership and phone layout. Risks: false success, duplicate
mutation after lost response/reload, lost retained evidence, retry storms, duplicate
popups and unusable phone controls. Smallest proof: named outbox/state/notification
regressions, then affected unit files and callers. Typecheck/lint for interface changes;
real cross-browser phone workflows and pinned visual checks for UI. CI owns required
current-candidate validation, including PostgreSQL durability. No live study changes.

The original discovery/training implementations failed both initial named regressions
(accepted intent discarded on reload; pending state missing inline status). The repaired
outbox, discovery, opening-recovery and phone files passed 85 cases in 44.58 seconds.
Caller/board/debug/notification files passed 117 cases in 151.88 seconds before the
final passive-refresh refinement; affected caller files are rerun for that refinement.
The recovery file passed all six cases, including increasing backoff, held-pointer
release, blocked receipt suspension, and missing-receipt replay. Typecheck passed;
full lint passed with existing warnings and an unused import subsequently removed.

Local browser attempts: `make ui-file FILE=phone-opening-study.spec.ts` twice,
with elevated permissions from the initial command. Both exited at capability
preflight (`docker info` timed out at the runner's five-second deadline), before
creating any disposable stack or running tests. Read-only `docker ps` succeeded
and showed only the unchanged live Tempo and Excel stacks. No test resources
were created, so no teardown or image removal was needed. Pinned visual and
PostgreSQL durability evidence must come from the current PR's required CI plan.

Local execution uses the bundled Node 24.19 runtime; the host Node 26 process
crashed during Vitest startup. CI retains its configured runtime. No live study
files, services, queues, browser data, or database records were modified.

Issues #29 and #39 were reviewed against current main and their acceptance criteria.
They remain open: this patch repairs browser recovery identities and messaging;
it does not implement the background admission/dispatch roadmap. Stockfish engine
timeouts are a separate reported symptom and are outside this patch.

Follow-up candidate proof: an in-flight receipt crossing a foreground-readiness
change initially failed `phone receipt confirmed during a foreground pause refreshes
once after resume` (0 callbacks after resume). Confirmations now survive the effect
boundary and wait for safe readiness. The complete pending-review recovery file
passed all 8 cases in 21.36 seconds, including readiness toggles retaining one
in-flight request/callback. Focused ESLint passed. The phone browser fixture now
models the queue becoming available only after authoritative receipt completion.

Final follow-up development candidate: browser review storage failures now suspend
receipt recovery without discarding the retained result; passive initial queue reads
retain a compact manual-check banner. Training preserves storage warning severity
and raw details. Existing opening-policy tests now assert actionable prose with raw
errors in Details, and the conflict UI asserts exact attempt ownership. CI's prior
head failed those ten outdated assertions; no retry, suspension or persistence
assertions were removed.

Exact final focused commands (bundled Node 24.19 first in PATH):
- `npm run test:unit -- tests/unit/pending-review-recovery-regressions.test.tsx tests/unit/phone-recovery-messaging-regressions.test.tsx tests/unit/review-outbox-regressions.test.ts`: 63 passed, 32.84s, before the subsequent warning-renderer refinement.
- `npm run test:unit -- tests/unit/phone-recovery-messaging-regressions.test.tsx tests/unit/opening-evidence-recovery-policy.test.tsx tests/unit/review-conflict-ui-regressions.test.tsx`: 53 passed, 57.90s, including that refinement.
- `npm run lint`: passed, 0 errors and 10 existing warnings. `npm run typecheck`: passed before the last renderer/test refinement and rerun for the settled candidate. Static checks were untimed; no performance claim.

These runs tested the dirty development candidate based on 40ab450. Fresh required
CI will certify its committed head and merge candidate; earlier CI successes do
not certify the follow-up. Local browser/visual/durability remain unavailable for
the Docker preflight reason above. CI owns these required scopes.
