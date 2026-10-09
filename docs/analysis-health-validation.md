# Progress and stalled-work test plan

Persist accepted checkpoint/publication timestamps independently of updates,
claims and retries. Observe admitted execution and verified idle capacity;
exclude manual/settings pauses, intentional provider delays and foreground
waiting. One bounded monitor slice owns one pipeline through short control
capacity, independent of the analysis worker. Keep unavailable evidence unknown,
one durable incident per unresolved episode, and resolve only after the affected
work accepts forward progress. Notifications read persisted, paginated changes;
resume notifications and toasts occur once per device/episode. Diagnostic reads
use indexed state counters and bounded cached kind summaries with explicit age.

Risks: heartbeat/replacement mistaken for recovery, blocked or paused work falsely
alerted, crash/replay duplication, reset of failure debt, global scans or locks
that interfere with study, fake health during outages, leaked request payloads,
stale reads and repeated toasts. Start with named frozen-clock SQLite regressions
for boundary/exclusion/progress semantics, followed by producer/consumer and
component contracts. Require native PostgreSQL contention, rollback, restart,
replay, actual monitor/control routing, populated diagnostic read budget and
real activity/notification browser workflow. Typecheck/lint and affected pinned
visuals apply. CI owns final current-head/current-base complete validation.
No migrations retry historical failures or delete study data.

Local candidate uses schema 49 and the ordered activity-history branch based on
main 2dd998b8df9e099c1fa39eec70c522b4db84863e (unchanged on freshness review).
MacOS ARM64, Python 3.14.8; disposable native PostgreSQL 18.6 and Redis 7.

- Initial missing implementation regression failed during import. Engine lease
  reclamation and terminal identical-timeout cases separately failed before repair.
- Full `make python`: 1,701 passed in 204.57s before the later terminal-error and
  capacity refinements. This is subsystem evidence, not a final full-gate pass.
- After those refinements, six affected backend files: 134 passed in 11.86s;
  four affected Vitest files: 51 passed in 2.26s. Typecheck/lint passed (ten existing
  warnings). The full activity health file covers 31 controlled cases.
- Fresh native proof with schema49: cached read 3.874ms, real foreground review
  27.54ms while monitor encountered a 25ms row-lock deadline; restart/replay,
  accepted-checkpoint recovery, 2,143 tasks and bounded engine debt passed in 0.89s.
- `make ui-file FILE=activity-tray.spec.ts`: 13 passed, 21.8s browser execution,
  22.43s browser stage, 8.49s cleanup, project tempo-pg-regressions-91103-014b9e9b.
  This precedes the independent engine capacity refinement; the UI and incident
  protocol are unchanged. The first new-fixture run failed before browser actions
  because the schema container has only its guarded admin URL; the fixture now
  binds that verified disposable URL locally. All owned runner resources removed.
- A first focused selection misspelled two existing unit paths; its 35 cases
  do not substitute for the later correct four-file 51-case run. An earlier
  bootstrap fixture lacked a required next_attempt_at; corrected fixture retains
  32-row page and inter-pipeline turn assertions.

Analysis worker stages distinguish control capacity. Engine availability and
idle samples come from actual accepted claims/control probes, independently of
an idle Python worker, in ephemeral 15-second Redis evidence. Missing or expired
samples mean unknown. PostgreSQL parent envelopes cannot compete with the
independently claimable child stage. Bootstrap and eligibility reconciliation
write bounded 32-row pages; one monitoring turn and one bounded cached counter
summary commit per slice. Regular durability, affected activity/training browser,
pinned visual checks and current-candidate CI remain pending on this candidate.

Related #39–#44 and coverage #3/#6; this completes progress/activity portions of
the approved repair plan. Provider OAuth live-service verification and broader
fan-out/engine coverage criteria remain on their issues. No closing keywords
are used for partial or unverified acceptance criteria. No deployment performed.
