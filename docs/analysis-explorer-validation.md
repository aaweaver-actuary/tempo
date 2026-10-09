# Explorer session recovery validation plan

Use a dedicated memory-only Redis store (persistence disabled, 24-hour credential
expiry), safe missing/rejected/available status, browser re-registration after
session loss and rejection fencing. Resume only the latest current-scope coverage
attempt while keeping completed Explorer/Maia data. Provider retries and response
semantics remain a following coherent change in this planned coverage step.

Risks: secrets reaching PostgreSQL, diagnostic output or persisted broker storage;
a stale HTTP rejection deleting a replacement credential; repeated registration
of a rejected credential; expired/backend-lost sessions never recovering; store
outages presented as healthy; historical coverage attempts replacing current work;
partial Maia/Explorer results lost during recovery; foreground interference.
Start with named credential/store, endpoint and browser helper regressions plus
affected coverage/cutover tests. Expand to actual separate Redis persistence and
restart proof with fresh PostgreSQL, real admission and retained source results.
API producer/consumer contracts, typecheck/lint and the relevant real browser
workflow are required. Changed Docker topology needs actual disposable-stack
validation; CI owns final required current-head/current-base candidate proof.

Official semantics were checked against the current Lichess OpenAPI primary
specification: explorer.lichess.org/lichess uses OAuth2 bearer authentication;
counts are integers; 429 normally calls for waiting a minute, with
some endpoint limits longer. Relevant primary specifications:
https://raw.githubusercontent.com/lichess-org/api/master/doc/specs/lichess-api.yaml
https://raw.githubusercontent.com/lichess-org/api/master/doc/specs/tags/openingexplorer/lichess.yaml
https://raw.githubusercontent.com/lichess-org/api/master/doc/specs/schemas/OpeningExplorerLichess.yaml

Related issues #3/#6, following #127. No live credentials or study data are used
by tests. No historical failures are broadly retried, and no deployment is part
of this change.

Credential/storage baseline failed in 0.19 s because the former implementation
wrote to the durable broker. The final storage/endpoint file passes eight cases
in 0.48 s. Browser helper and existing refresh-pending/service panel tests first
passed 18 cases in 1.48 s; the added independent-Maia case passes with the helper
and pending file (seven cases, 0.878 s). Typecheck passes; lint passes with ten
pre-existing warnings. Runner contracts pass 48 cases in 1.08 s.
Fresh PostgreSQL schema46 and the separate Redis 7 store pass the actual API,
separate-process, TTL, late-rejection CAS and missing-registration proof in
0.38 s (0.977 s including setup). A real restart of the exact task-owned
`tempo-analysis-explorer-session-proof` also proves the session disappears and
the unchanged synthetic browser credential registers successfully. The runner
now includes this proof around its container recreation, with no provider worker
able to read the proof namespace. Logs are preserved in root
`test-results/analysis-activity-2026-10-09/explorer-session-native.log` and
`explorer-store-restart.log`. Actual Compose topology and the broader browser/
durability gates remain pending current-candidate CI; no full pass is claimed.

Settled candidate whole affected files: `PYTHONPATH=backend
backend/.venv/bin/python -m pytest backend/tests/test_explorer_session_recovery.py
backend/tests/test_repertoire_coverage.py backend/tests/test_postgres_cutover.py -q
--rootdir=.` passes 218 cases in 2.22 s. The three affected browser-helper,
refresh-pending and service-panel unit files pass 19 cases in 1.22 s. These are
focused passes, not the complete gate. Tested dirty branch based on
7d17c56a972eaea58c30671cd2a437c8f8f7e7e0, Python 3.14.8, local PostgreSQL 18.6,
Redis 7 and Node runtime from the installed checkout.

Late rejection follow-up: a rejected newer connection also fences a delayed
rejection of the older connection when no credential remains registered. The
named fingerprint regression failed before this fix; nine storage/endpoint cases
pass in 0.52 s. The actual Redis native proof asserts this CAS boundary too.
