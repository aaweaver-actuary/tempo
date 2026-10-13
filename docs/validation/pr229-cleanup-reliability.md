# PR #229 cleanup reliability

## Scope and ownership

Original cleanup implementation `ae9b621523eddbe748aa2b48a40a48cbec218dff` was incorporated by the fixture owner into PR #229 commit `ef26a6d9c19969baa3f1b1974dd58bcb27a66032`. PR #233 now preserves this report and cleanup regression registry against that advanced base; it does not overwrite the fixture owner's changes.

Original validation: test infrastructure only, stacked on PR #229 head `d49efd20d595c5c288426c82a4ee6338afcaf29c` and main `d82f78e9b764fbba5b48e9ea6de8abcca5d133e7`. No production, admission, queue, review or replay semantics changed. `scripts/check_postgres_redis_admission.py` is unchanged.

The test-specific cleanup lifecycle owns outstanding Redis stop/pause restoration obligations, per-scenario response listener removals, explicit page closure and the PostgreSQL fixture cleanup callback. Each cleanup is attempted independently; one synchronous throw or asynchronous rejection cannot skip another. Obligations are set before fault commands and cleared only after successful restoration. Failed listener detachments are retried before final resource cleanup. The original thrown value is rethrown unchanged when cleanup succeeds; otherwise it is the first aggregate entry and cause, followed by resource-labelled failures whose causes preserve their original errors.

The evidence directory, `native-recovery.json`, Playwright attachments and runner traces are retained diagnostics. This test creates no temporary files of its own. Disposable stack secrets, containers, volumes, networks and images belong to the outer PostgreSQL runner, not this helper. Project/disposable-health guards, the recovery deadline, existing assertions and evidence publication remain intact.

## Deterministic cleanup failure matrix

| Failure | Attempted cleanup / report |
| --- | --- |
| Redis restart | Listener removal, restart, page closure, fixture cleanup; original assertion plus restart error |
| Redis unpause | Listener removal, unpause, page closure, fixture cleanup; original assertion plus unpause error |
| Fixture cleanup | Earlier eligible cleanup completes; fixture error fails the test |
| Assertion during refusal | Listener removal, restart, page closure and fixture cleanup; original assertion retained |
| Assertion during nonreply | Listener removal, unpause, page closure and fixture cleanup; original assertion retained |
| Listener removal | Removal is retried at finalization; page and fixture cleanup still attempted; each failure retained |
| Page closure | Fixture cleanup still attempted; page error retained |
| Multiple cleanup failures | Every eligible resource attempted; original failure first, then all labelled cleanup failures |
| Stop/pause command rejects after possible side effect | Restoration obligation remains and finalization attempts start/unpause |
| In-scenario restoration rejects | Finalization retries the outstanding obligation, without dropping either failure |
| Evidence write/attachment | Publication failure retained and remaining resource cleanup attempted |
| Committed seed output lost | Page closure and identity-based fixture cleanup attempted; the fixture owner's updated interface recovers the persisted snapshot |
| Successful scenario restoration | No redundant final Redis restoration or listener detachment |

## Selected validation on the original cleanup overlay

- Baseline: the two named restart/unpause regressions failed in 0.600 s Vitest duration against the extracted original finalizer; logs showed no page or fixture cleanup calls. Fault injection was deterministic, not an injected Docker failure in the real browser run.
- `make unit-file FILE=tests/unit/redis-admission-cleanup-regressions.test.ts`: 21 passed; Vitest duration 1.14 s, command wall time 2.25 s.
- `npm run typecheck`: passed in 19.89 s.
- `npm run lint`: passed in 33.81 s with 10 existing unrelated warnings and zero errors.
- `TEMPO_DOCKER_URL=http://127.0.0.1:1 npx playwright test tests/browser/redis-admission-recovery.spec.ts --list`: exactly one Chromium test collected. This is collection only; no service was contacted. Initial collection without the required URL was rejected by the existing config guard.
- `make ui-file FILE=redis-admission-recovery.spec.ts`: passed: one Chromium test, Playwright total 46.2 s; browser stage 48.33 s. Setup: Compose configuration 1.17 s, cached image build 28.31 s, startup 23.62 s and health 0.20 s. Cleanup passed in 4.40 s. These are measured stage spans, not an independently measured total command wall time.
- `node --test --test-name-pattern foreground_redis_admission_native_regression_is_registered_and_selected tests/runner/ci-reliability.test.mjs`: one passed, 2.769 s runner duration / 2.86 s command wall time.
- `git diff --check`: passed before commit. No broad local suite; CI owns selected current-candidate qualification.

Local environment: macOS ARM64, Node v26.10.0, locked Node dependencies from `npm ci --ignore-scripts`, invocation-owned Docker PostgreSQL/Redis/browser stack. Local execution uses the dirty implementation overlay on the pinned dependency head; the implementation commit preserves that source. Logs and runner ownership/timing/trace evidence are preserved outside the development checkout under `test-results/pr229-cleanup-reliability-20261013/` in the root study checkout. CI links and exact committed head belong in the PR handoff.

## Remaining limitations

- Cleanup attempts cannot guarantee successful Docker or PostgreSQL restoration. Failures remain actionable test failures; no live services or global resources are repaired or pruned.
- The original token-loss limitation was resolved independently by the fixture owner in `ef26a6d9`: cleanup now retrieves a persisted restoration snapshot by identity, including after committed seed output is lost. This task did not edit the fixture script. Visible restoration conflicts still fail and retain the snapshot rather than overwriting newer state.
- The fixture's internal cleanup atomicity and completeness remain its responsibility. This repair guarantees its invocation when a seed token exists.
- Forced process termination or worker shutdown can interrupt cleanup. Hanging operations remain subject to existing command/test deadlines.
- Detaching a response listener prevents future callbacks; it does not cancel callbacks already processing a response.

## Advanced-base reconciliation

All execution layers in CI run `38382213750` passed for the original cleanup candidate. Aggregation rejected it after PR #229 advanced: its pinned base/integration candidate was stale. This is not a successful aggregate gate. Rebase preserves the fixture owner's complete browser spec and adjusted identity-cleanup regression, byte-for-byte. The helper itself is identical to the original implementation. Fresh focused checks and current-head CI are required; no previous pass is attributed to the advanced candidate.
