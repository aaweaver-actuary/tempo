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
