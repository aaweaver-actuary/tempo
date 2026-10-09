# Activity/history test plan

Use authoritative current PostgreSQL task generations/publications, group game
parents and stages, and retain obsolete legacy work in history. Separate active,
attention, manual pause, settings disablement and finished/history groups. Clear
finished archives only successful completions through the displayed snapshot's
server-issued cutoff. An additive workspace preference is durable across devices;
a signed cutoff prevents a future or fabricated snapshot from hiding later work.
No job, result, diagnostic, study record, failure or pause is deleted.

Risks: legacy rows appearing running, duplicated logical games, grouping hiding an
active or failed stage, stale snapshot hiding concurrent/new-generation results,
false success from pending/failed receipts, unavailable reads presented healthy,
foreground read cost and lost history. Start with native/SQLite projection and
command boundary regressions plus clear-command and panel unit cases. Require
actual PostgreSQL persistence, concurrent completion, receipt restart/replay,
large queue counts and publication reconciliation; real activity/training browser
workflow and stable pinned panel visuals; typecheck/lint. CI owns the complete
current-head/current-base gate. Following #129; related analysis issues #39–#44.

Initial actual behavior: all three new SQLite/API regressions failed (missing
clear snapshot, missing endpoint, and duplicated game stages). After wiring:
`make python-file FILE=backend/tests/test_activity_history.py`: 3 passed, 0.66s;
`make python-file FILE=backend/tests/test_background_activity.py`: 7 passed, 1.15s;
`make python-file FILE=backend/tests/test_postgres_cutover.py`: 198 passed, 1.70s
(before adding the explicit clear dispatch case); focused panel/clear Vitest:
17 passed, 0.80s. Typecheck passed; lint passed with ten existing warnings.
Disposable PostgreSQL 18.6/schema48/Redis7 native proof: 2,143 logical games,
57.064ms activity read, 0.73s including helper setup; receipt restart/replay,
concurrent completion and new generations passed. Full Docker durability,
activity/training browser and pinned visual evidence remain pending on the settled
candidate; these focused results do not imply full-gate or release readiness.

Follow-up evidence on 13a4c957 plus the recorded working changes: all 71 cases in
status-polling, service-status-panel and activity-clear-command passed (1.52s).
Activity/history Python 4 passed (0.84s), background activity 7 passed (1.14s),
and PostgreSQL cutover 199 passed (1.83s). Typecheck passed. The actual two-device
clear browser case passed in 8.8s (stage 9.48s, cleanup 8.70s), disposable project
79659-6cba3c0b; its containers and volumes were removed by the owning runner.
The timestamp regression first raised the previous naive/aware comparison error;
the foreground-wait regression first produced an outage notification. The regular
render-identity regression caught a redundant state update and now passes.
Evidence: root test-results/analysis-activity-2026-10-09/activity-clear-browser-timestamps.log.
Pinned visual and settled Docker durability remain pending.
