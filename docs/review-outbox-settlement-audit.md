# Review-outbox flush settlement audit

Independent audit of PR #172 on merged main
`d4dbf28da5b6e95db8da1acfd9fecd4655821296`, October 11, 2026.
Baseline tree: `cc2ed24e7114ed755b7caea481f2a7c1caca6cfb`.
The isolated checkout is owned by PR #191; the live study checkout is untouched.

## Verdict

**Three CONFIRMED DEFECTS**, reproduced before changing production:

1. A bounded owner followed by an unlimited caller drops both generations'
   prepared-continuation maps while retaining their confirmed attempt IDs.
2. A failing follow-up drops earlier confirmed IDs and continuation evidence
   from unlimited and submitted-attempt callers' error results.
3. Saving can synchronously publish a public event before `activeFlush` is
   assigned. A reentrant submission can lose ownership, allowing concurrent
   authoritative receipt recovery followed by an incorrect submitted rejection.

The corrections combine all three settlement fields on success/failure, copy
aggregate errors without mutating shared observers, and reserve ownership before
saving can publish synchronously. Public signatures, storage formats, ranking,
FSRS, scheduling, backend locking and reconciliation are unchanged. Existing
synchronous publication and low-latency target settlement are preserved.

No false persistence confirmation, terminal-conflict authorization, advancement
to an unissued successor, duplicate advancement, or lost known failed-attempt
marker was reproduced. The evidence does not establish an actual study incident.
Qualification remains pending until current-candidate required CI passes.

## Ownership and settlement invariants

The corrected transition is idle → reserve active ownership → save → publish
cumulative progress → complete/reject → release ownership → waiter callbacks or
follow-up. A reservation forwards the actual flush settlement to synchronous
reentrant observers; ordinary observers still receive the original flush.

- Persistence requires an authoritative `persisted: true` POST or receipt.
  Storage absence, another attempt's success and ambiguous delivery are insufficient.
- Terminal reconciliation conflict retains the envelope and settles its own ID;
  it does not confirm persistence or authorize a prepared successor.
- Progress listeners require their requested attempt ID in a settlement list and
  copy arrays/maps for an immutable early result. Unrelated later delivery need
  not finish before a submitted attempt becomes usable.
- Continuation evidence must belong to the confirmed attempt and match its frozen
  issued successor. Advancement also rechecks the current attempt/generation,
  queue-entry/card/revision/cycle/date, readiness and known failure evidence.
- Advancement changes logical attempt ownership, preventing duplicate observers
  from advancing the completed attempt twice.
- A caller's aggregate error preserves earlier settlement plus the later failure's
  identity, classification, message, cause, prototype and stack. Direct observers'
  error evidence remains unchanged.

**Flush-generation isolation is not an invariant.** For both completion and
rejection, controlled public outbox callbacks and two explicit microtasks reach:
A terminates → A's `finally` releases the slot → B starts and synchronously
publishes its target conflict → A's old waiter callbacks run. The old listener
can observe B, but resolves with genuine B terminal settlement and no persistence
or continuation. Cross-generation observation alone is SAFE — VERIFIED in those
executions; no generation abstraction was added for it.

## Classified scenario matrix

All 39 new unit cases have the `flush_settlement_audit` prefix in the four regular
regression files listed below. Controlled fetch/body/receipt promises and explicit
microtasks establish ordering; tests do not write private flush state or sleep.

| Interleaving / outcome | Classification and observation |
| --- | --- |
| A completes/rejects; B publishes before A callbacks | SAFE — VERIFIED; genuine B conflict, no fabricated evidence |
| Subscription immediately before/after publication; duplicate observers | SAFE — VERIFIED; target attribution, immutable early snapshots |
| Synchronous subscriber during target storage change | SAFE — VERIFIED; target usable before held unrelated delivery |
| Reentrant submitted caller during initial synchronous conflict | CONFIRMED DEFECT; recovered target subsequently rejected by its original caller |
| Same/different submitted IDs; limits 1, 2, Infinity | SAFE — VERIFIED for target settlement; coalesced delivery and caller follow-up settings |
| Different explicit retry IDs/settings; receipt-first versus POST-first | SAFE — VERIFIED; exact original identities, suppression retained for other attempts |
| Bounded owner followed by unlimited drain | CONFIRMED DEFECT; confirmed IDs survive but both continuation maps disappear |
| Unlimited/submitted follow-up fails after earlier confirmation | CONFIRMED DEFECT; earlier IDs/maps disappear from partial failure evidence |
| Success; terminal or retryable conflict; missing confirmation | SAFE — VERIFIED; persistence and terminal settlement remain distinct |
| Network failure before persistence; lost response after simulated commit | SAFE — VERIFIED; ambiguous until authoritative receipt recovery |
| Same-card persisted/suppressed/conflicted predecessor | SAFE — VERIFIED; dependency ordering and retained unrelated work |
| Unrelated reviews before/after target; reviews appended during delivery | SAFE — VERIFIED by new and retained cases; early settlement survives later work |
| Missing/mismatched issuance; replacement attempt/generation | SAFE — VERIFIED; no stale or unissued advancement |
| Changed queue entry/revision/cycle/date; newly known failure flag/marker | SAFE — VERIFIED; no advancement, retained failure guidance |
| Home foreground with overlapping recovery/reconnect | SAFE — VERIFIED; one prepared advance or actionable authoritative recovery |
| Idle receipt observer plus later failure | SAFE — VERIFIED after correction; confirmed target and capability retained |
| Real PostgreSQL confirmation held during reconnect | SAFE — VERIFIED on baseline and first correction; native successor, restart and exact durable replay |

## Reproductions and regression protection

`tests/unit/review-outbox-regressions.test.ts`:

- `flush_settlement_audit bounded %s then unlimited preserves all continuation capabilities`
  fails on baseline for limits 1 and 2. Hold A, join its bounded flush with an
  unlimited caller, confirm A, then drain B/C. The caller loses both maps.
- `flush_settlement_audit %s follow-up failure retains earlier settled evidence`
  fails for unlimited and submitted callers. A confirms; follow-up B fails before
  C. A disappears from error evidence. Duplicate aggregate and direct B observers
  guard against shared error mutation and duplicated evidence.
- `flush_settlement_audit reentrant submitted owner cannot reject after concurrent authoritative recovery`
  fails on baseline. An initial same-card conflict event synchronously starts
  target C's held POST. The first owner's assignment/cleanup discards C's active
  ownership. Another receipt-first caller confirms C and removes its envelope;
  the held POST then fails and rejects the original submitted caller. The ordered
  trace confirms that recovery precedes rejection. With reserved ownership,
  recovery cannot overtake the POST: ambiguous rejection remains truthful and
  the explicit retry subsequently confirms C through its original receipt.

The other audit cases live in this file and
`tests/unit/prepared-review-continuation-regressions.test.ts`,
`tests/unit/review-attempt-confirmation-regressions.test.tsx`, and
`tests/unit/pending-review-recovery-regressions.test.tsx`.
Exact expanded case names, baseline artifacts and corrected results are retained
in `exact-audit-cases.json` in the local evidence bundle. Coverage is registered in
`tests/REGRESSIONS.md` and remains discoverable by the regular suite.

`tests/browser/post-review-progression.spec.ts` adds
`flush_settlement_audit overlapping reconnect preserves issued successor and one durable review`.
It holds actual POST or 202/receipt confirmation during reconnect, exercises native
successor input after authoritative queue recovery, restarts the owned services,
and proves original review/receipt/scheduling equality and idempotent replay.

## Execution evidence and qualification limits

- Baseline unit matrix: **39 cases; five failing assertions reproducing the three
  defects, 34 passes**. Production was unchanged; tests were dirty additions.
  Ordered traces and source hashes distinguish baseline from correction runs.
- Final affected unit run: **269 passed, ten files, 18.10 s**, including all 39
  audit cases. Runtime source is the final correction, tested while dirty on
  `3394cd45`; it was then committed as `a35abbf0`. Final outbox file: **90 passed,
  3.63 s**. Typecheck and lint passed (ten existing warnings); fresh static-command
  duration was not captured. Earlier typecheck/lint timings are preserved separately.
- Baseline real browser probe: **one passed, 81.66 s** browser stage. First
  correction's full browser file: **four passed, 287.61 s** on clean runtime
  `c46bc94d`. Fresh final ownership-correction browser evidence is pending.
- Draft CI [38142379629](https://github.com/aaweaver-actuary/tempo/actions/runs/38142379629)
  for `3394cd45` passed **1,629 frontend tests, 159 files, 270.59 s**, build and
  pinned visual. Browser: **280/282 passed, 1,138.56 s**. Two existing startup
  fixtures failed before review submission: `ERR_NETWORK_CHANGED` and a queue
  probe dereferencing `cards` on pending-issuance HTTP 503. This run is failed
  evidence and cannot qualify the new correction.
- Both CI fixture failures were deterministically reproduced with controlled
  inputs on unchanged production (**two failures, 100.62 s**). Test-only fixes
  use one explicit initial UI Retry and bounded non-OK queue polling. Native
  progression, durability/replay, deadlines and branch-deletion assertions remain.
  Workspace passed its corrected probe. The large training fixture then exposed
  separate failures: a 60 s cleanup timeout (isolated cleanup 25.42 s), followed
  in a focused run by historical checkpoints remaining retrying beyond their
  original 30 s deadline. Worker/lease/receipt traces are retained. Their cause
  is unproven; no timeout, backend budget or product assertion is weakened.
- Two complete local durability attempts did **not** qualify: unchanged operation
  recovery hit a transaction timeout (focused unchanged case passed in 11.21 s);
  the next attempt passed it but hit a daily-study refresh lock timeout (focused
  unchanged case passed in 18.17 s). These are retained failures, not attributed
  to the client fix. Fresh settled-correction durability and final CI remain pending.

Exact proving commands (reporter/output options are retained in bundle logs):

```sh
npm run test:unit -- tests/unit/review-outbox-regressions.test.ts -t 'flush_settlement_audit reentrant submitted owner'
npm run test:unit -- tests/unit/review-outbox-regressions.test.ts tests/unit/prepared-review-continuation-regressions.test.ts tests/unit/review-attempt-confirmation-regressions.test.tsx tests/unit/pending-review-recovery-regressions.test.tsx -t flush_settlement_audit
npm run test:unit -- tests/unit/review-outbox-regressions.test.ts tests/unit/prepared-review-continuation-regressions.test.ts tests/unit/review-attempt-confirmation-regressions.test.tsx tests/unit/pending-review-recovery-regressions.test.tsx tests/unit/desktop-queue-regressions.test.ts tests/unit/phone-queue-preparation-regressions.test.ts tests/unit/queue-readiness-diagnostics-regressions.test.ts tests/unit/offline-training-regressions.test.ts tests/unit/attempt-lifecycle-regressions.test.ts tests/unit/opening-evidence-review-deadlines.test.ts
npm run typecheck
npm run lint
make ui-file FILE=post-review-progression.spec.ts
make docker-durability
```

macOS development: Node 26.10.0, npm 11.19.1, Vitest 5.0.1/jsdom, locked
Node dependencies installed once. Docker invocations use fresh disposable
project-isolated state. CI owns complete current-head/base/integration validation;
[PR #191 checks](https://github.com/aaweaver-actuary/tempo/pull/191/checks) record
its candidate qualification separately. No local `make full` duplicates CI.

## Remaining uncertainty and resource ownership

Mocked commit/receipt cases prove client semantics, not PostgreSQL durability.
This finite matrix is not exhaustive model checking or cross-tab serialization.
Raw errors without `ReviewReplayError.flushResult` keep their existing behavior;
aggregate retention for that exceptional path is INCONCLUSIVE. No unsafe
advancement was reproduced; this is bounded evidence, not a universal proof.

Logs, traces, assertions, patches, timing, source provenance and owned-resource
teardown records are retained outside the clone at root
`test-results/audits/2026-10-11-review-outbox-settlement/`.
Task-owned disposable runners are torn down after capturing diagnostics. The
checkout remains owned by this audit while PR #191 is unmerged. Existing issue
#186's broader requirements remain open; this PR references #172 without claiming
that issue is completely resolved. No merge or deployment was performed.
