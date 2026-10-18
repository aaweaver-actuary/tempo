# Startup lifecycle and introduced-review admission repairs

These are source-only repairs after the failed artifact attempt at pinned head
`6c2eb502`. No qualification retry, Docker launch, native test or live action is
part of this repair checkpoint. The previous artifact grant is consumed.

## What the retained startup evidence establishes

The approved attempt started20:48:29 UTC and failed20:49:01 UTC. Its image inspect
succeeded. The managed `docker run` wrapper reached its30-second startup deadline
without a verified running helper. There is no inside/tool report, artifact or
test result. The logs do **not** establish why Docker creation stalled; no uv,
Python, dependency or test failure should be inferred.

The confirmed runner defect is lifecycle handling after that failure. The
wrapper exited nonzero, but its Docker CLI PID24071 survived in the runner-owned
process group24069. `Deadline.run` previously killed the group only on a timeout
exception, not when the wrapper itself returned failure. The retained resource
journal therefore outlived its owner. Manual cleanup terminated only that proven
group and independently verified owned resource/budget-lock absence; see task
evidence `ARTIFACT-CLEANUP.json` and `ARTIFACT-RESULT.json`.

The narrow repair kills any surviving descendants of that failed, explicitly
created process session before container cleanup, and records the group ID.
It does not extend startup/work deadlines or retry creation. A second repair
recognizes identity-specific absence diagnostics regardless of capitalization;
the previous literal `No such` check could reject the observed lowercase form.
An unrelated identity or connection error remains unverified.

For a startup that left only a journal, cleanup now requests removal of the
exact helper even when absent. The existing managed Docker adapter independently
rechecks absence and can reconcile that explicitly requested owned journal after
nonzero `rm`. It preserves other journaled identities. Existing successful-removal
behavior and ownership/capacity checks stay intact. No general harness/runtime
replacement is introduced. Underlying Docker startup remains a diagnostic
unknown until a separately reviewed finite attempt can observe it.

## Frozen introductions do not retire existing review cards

The new P2 reproducer uses a current, introduced mature physical card, active
frozen run and an old completed migrated origin with `legacy=1`. There is no
burial/protected attempt today. `annotate` intentionally marks that history
uncertain for **new introduction selection**; the bridge incorrectly passed its
review admission through `proposal_valid`, permanently deferring it as
`frozen_order_changed`.

Existing owner semantics distinguish this from review eligibility:
`real_game_feedback.promote_real_game_card` sends introduced learning/mature
cards directly to the normal queue writer, and the ordinary due-review predicate
does not apply introduction rank/legacy-origin validity. The bridge already
requires introduced, current learning/mature physical-card authority.

The review-only adapter therefore preserves the frozen activation-token read,
locked recheck and normal protected-slot presenter, while removing its call to
introduction-only annotation/validity. It does not modify frozen introduction
selection, ranks or quotas. The repro now admits behind the protected head;
a fresh issued target still defers as `issued_head_preserved`, its old completed
origin is untouched, and an activation change still defers. Existing frozen
introduction tests retain their rejecting legacy-origin behavior.

## Lightweight validation

114 focused Python cases pass: bridge vertical/qualifier tests, frozen owner,
queue extraction and old feedback. The broader Node resource tests have28 passes,
including the new exact absent-journal regression and owned-process cleanup.
Two cases remain blocked at import by missing `typescript`; nothing was installed.
An initial sandbox-only process-inspection failure passed with the required own
process inspection access. AST/Node syntax and whitespace checks pass.

All Docker calls in these Node tests use synthetic executables. These results
qualify source behavior only, not the uncompleted255-case artifact selection or
native startup/cleanup. A new head requires review and a fresh finite grant.

## Smallest useful delivery plan

The earlier7–11-hour range included a fuller finish. The minimal explicit
one-game slice is roughly4–6 additional working hours, conditional on review and
qualification, plus resource/readiness/reviewer waits:

| Essential item | Working estimate |
| --- | ---: |
| Review fixes and intake operating-path decision | 0.5–1h |
| Read-only one-game observation and generated v3 envelope | 0.75–1.25h |
| Native backend fixture, focused proofs and repairs | 2–3h |
| Explicit accept/queue procedure and evidence | 0.25–0.75h |

Workbench polish (about1.5–3h) and automatic deferred draining (about1–2h) are
optional. Explicit preview, accept, queue and status are sufficient for a first
useful slice. A later explicit queue request uses a new operation key; no loop
or scheduler is required. Unknown/no-miss ends the selected game without sample
expansion. A real user's later Study review is not a synthetic test assertion.

Two essential operating gaps remain. The current intake is a background command
and study-only mode pauses it; a separately authorized intake window could avoid
an allowlist expansion. Simply flipping to foreground would also require adapting
its background-admitted capture reader. The GE payload builder remains v2-only,
so a small v3 producer adapter must construct real envelopes from owner capture
and GE findings; operators must not hand-edit the synthetic fixture.

The smallest new native backend scenario uses one synthetic completed game,
one introduced original black card and an unrelated protected head, real backend
migrations through50, actual source/graph/integrity rows, real capture/comparison,
HTTP dispatch, real Celery/Redis and durable command receipts. Accept/replay,
explicitly queue, prove preview/prefetch has no binding, complete the protected
head normally, fetch the target through normal issuance, then submit/replay a
correct unaided normal review. Require one contribution/frontier, one scheduling
review and one settlement. A sibling prior-issued-attempt control must not settle.
Reconciliation/invalidation and stale-source controls remain focused native gates.

Prerequisites: qualified artifacts; independent review disposition; a **separate**
backend-native resource grant and pinned local PostgreSQL/Redis/backend images;
disposable DB roles and Redis namespaces; finite supervised command workers with
no beat, engine or provider workers. The existing five module-native cases alone
do not qualify this backend path. Parent owns actual repertoire readiness and any
later authorization for real priority delivery/deployment.
