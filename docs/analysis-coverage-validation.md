# Coverage source recovery test plan

Persist source-specific failure codes and provider retry deadlines. Recover one
node in the newest current-scope attempt only, without changing completed Maia
or Explorer probabilities, cache, candidates, or source counts. Authentication
waiting must distinguish missing registration, rejected credential, unavailable
provider, rate limiting and invalid response. Source preparation/network activity
runs with the database closed; commit remains fenced by scope, run and task lease.

Risks: retry loops for rejected credentials, retrying obsolete/imported failures,
429 delays ignored, invalid counts silently accepted, partial results erased,
source failure overwritten by a successful independent source, foreground delay,
stale lease publication and replay duplicates. Start with provider boundary and
SQLite/source recovery cases, then the affected cutover/coverage files. Prove
latest-scope recovery, retained partial source data, real PostgreSQL restart,
replay and foreground admission in the regular disposable durability scenario.
API consumers/typecheck are required if serialized fields change. CI owns the
complete current-head/current-base gate. Related issues #3/#6, following #128.

Primary provider behavior was checked against current Lichess OpenAPI files in
lichess-org/api/doc/specs: OAuth2 on explorer.lichess.org/lichess, integer outcome
counts, 429 wait guidance (normally one minute, longer for some endpoints).
HTTP Retry-After will be honored when present; unknown/unavailable responses
remain errors. Validation rejects negative counts as unusable coverage evidence.

Evidence on the settled source-recovery candidate: the provider baseline fails
five cases (rate delay and four invalid-evidence variants) in 0.26 s; the usable
empty/valid control passes. Final affected Python files (provider recovery,
repertoire coverage, PostgreSQL cutover and canonical prefix) pass 374 cases in
20.34 s. The source-specific file passes nine cases. A fresh PostgreSQL 18.6
schema47 and dedicated Redis7 proof passes in 0.54 s including setup; actual
foreground review during paused network preparation takes 156.363 ms. Completed
Explorer/Maia probabilities are compared exactly; another invalid source remains
failed with its explicit error after accepted publication. The first native run
failed at a test-only unsupported row slice; the corrected assertion and both
subsequent stronger native proofs pass. Logs remain preserved in root
`test-results/analysis-activity-2026-10-09/coverage-recovery-native*.log`.

Commands: `PYTHONPATH=backend backend/.venv/bin/python -m pytest
backend/tests/test_coverage_provider_recovery.py backend/tests/test_repertoire_coverage.py
backend/tests/test_postgres_cutover.py backend/tests/test_canonical_repertoire_prefix.py
-q --rootdir=.`; `scripts/check_postgres_coverage_recovery.py` under explicitly
disposable PostgreSQL/broker/session fixture URLs (no real credentials). Tested
isolated branch based on the corrected session PR, Python 3.14.8. Migration047
only adds source metadata/indexes and does not retry any historical failures.
The native scenario is registered in the regular durability runner. No frontend
serialized shape or rendering changes in this PR. Complete PostgreSQL durability,
browser and full current-head/current-base CI remain pending; focused native
checks are not claimed as a complete gate. No merge or deployment is requested.
