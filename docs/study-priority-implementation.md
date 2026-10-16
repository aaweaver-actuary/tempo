# Study priority v1 — implementation and qualification

Release candidate: `study-priority-v1.0.0-rc.1`. Base: `0edf566a` (schema 48).
No live settings, services, study records, or deployment were changed.

## Behavior and ownership

Study input synchronously stops active optional browser computation. Pointer,
touch, keyboard, scroll and navigation contribute activity. Held contact and
pending study operations retain priority; three quiet seconds permit optional
work again. Quiet windows and throttles use elapsed time. Study-only remains a
persistent pause for optional application work.

The legacy activity adapter owns expiring per-session leases and generation-fenced
control snapshots. Control polling reads Redis rather than PostgreSQL. A missing,
stale, superseded or unavailable snapshot cannot authorize optional work. Essential
study commands retain their existing transaction and receipt ownership. The
independent Learning runtime acquires no Redis dependency; its existing PostgreSQL
admission remains its server-side owner, with local browser cancellation shared
by its client.

Stateless bulk preparation has a disposable serial worker, separate from essential
queue/position decoding. Optional position/comparison owners preserve confirmed
initializers, serialize computation and retain idle confirmed indexes. Cancellation
kills actual computation and fences old replies. Current source versions, durable
job leases and completed analysis quality are retained. Stockfish runs in an owned
child and Maia uses its unchanged rating/full-legal-universe adapter in a cancellable
child. Unavailable control does not incur ordinary command retry backoff.

PostgreSQL unlock discovery reads at most 16 locked cards, writes at most eight,
advances through rejected candidates and repeats ready-publication/practiced-parent
eligibility at update. The native parent-wide query is retired from execution;
SQLite's useful sparse algorithm and historical extraction literals are retained.
Receipt recovery selects indexed due-state heads rather than sorting terminal
history. Setup timeouts and closed connections defer maintenance with bounded
backoff, retaining durable work and transaction/lock budgets (250/25 ms).

Review/history/scheduling/required-evidence/receipt transactions are unchanged.
Current-window repertoire enrichment is restricted to the eligible window before
ranking. Pool acquisition, setup, SQL fingerprints, evidence/handler stages, commit,
response delivery, browser worker work and UI progression have timing evidence.
Non-opening moves do not request opening membership or start opening journals.
Opening membership remains essential when interaction pauses an optional index.
Routine recovery status reuses its record; genuine queue timeouts remain visible.
Exports distinguish unresolved failures and resolved history.

`config/resource-budgets.json` owns new managed work: development 2 CPUs/2 GiB;
optional application services together 1 CPU/2 GiB, including scheduled backup.
One heavy invocation owns the shared host lease. A rootless builder, with one
execution lane, confines actual build processes to the same capped group. Merely
capping a privileged builder container was tested and rejected as insufficient.
Runners defer new heavy stages during study activity, preserve lightweight checks,
and retain ownership when cleanup is uncertain. Component-local tests get a Docker
adapter through the managed entry point; standalone runtime artifacts gain no
Node or broker dependency. Existing resources from other tasks are untouched.

## Measured finite workloads

The six-case native rehearsal uses private PostgreSQL/Redis, 1.4 million published
graph steps plus an eligibility fixture, 2,048 locked candidates, 415,000 terminal
receipts, 5,000 future receipts and 60 reviewed/replayed attempts. Its report records
source hashes. It also checks independent sessions, expired control, database-paused
control and data-preserving index rollback. A fresh process resumes a fenced,
persisted rejected-candidate checkpoint.

The real Chromium workload includes 12,000 eight-move lines, 500 retained diagnostic
notifications, 20 input samples per idle/loaded lane and ten prepared confirmations.
Native browser frame callbacks are presentation proxies, not physical display timing.
Server request, transport/client and browser measurements are reported separately.
Local runs establish only these finite workloads; production p99 guarantees and
phone network variability require the separate release pilot.

Evidence and exact commands are recorded in the task's preserved validation report.
See [study-priority-validation.md](study-priority-validation.md).
Focused passes are not full-system qualification. Existing failing checks must be
reported, not skipped or weakened. The release procedure is in
[study-priority-release.md](study-priority-release.md).
