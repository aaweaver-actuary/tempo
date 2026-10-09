# Shared opponent-position inventory (#108)

The internal PostgreSQL inventory records every opponent-to-move position on the
canonical imported routes, including terminal positions, and every legal reply.
It does not use the coverage horizon, recursively expand unauthored routes,
fetch sources, calculate probabilities, create cards, or alter admissions.

## Identity and coverage

`inventory_positions.fen_key` uses the existing canonical first four FEN fields.
Castling rights and *legal* en-passant rights matter; move counters do not.
`inventory_legal_replies` records the exact legal set and resulting position key.
The legal-reply count is not a probability denominator or an estimated distribution.
Unfetched evidence remains absent, never zero.

Route identity hashes the inventory version, starting FEN, saved move-content
fingerprint, trained color, scope boundary and certified origin. Duplicate routes
share immutable calculations, while generation membership retains each line ID.
Absolute ply offsets preserve custom-FEN origins without replaying the origin.
Only positions at or after the verified canonical boundary are emitted.

For each legal reply, `route_coverage` returns independent authored and response
facts. `coverage` is `route` when a matching learner answer on that route has a
published linked opening card, `transposition` when another supported learner
answer exists at the resulting position in the same generation, and `missing`
otherwise. An authored terminal reply does not imply a learner answer. An
unintroduced but published card is structural support; recall is outside #108.
Another repertoire never provides coverage. Staged, archived, deleted, or
content-mismatched cards cannot establish support.

## Storage and lifecycle

Existing `repertoire_coverage_*` tables retain their behavior. They are owned by
individual runs/repertoires and cascade on deletion, so they cannot own reusable
source evidence. The additive schema separates shared positions, legal edges and
cohort metadata from immutable route artifacts, generation memberships and
published learner-response projections. Route artifacts store JSONB moves so a
slice reads at most 16 moves without repeatedly parsing a complete text array.

Graph publication and canonical-prefix saves request inventory work in the same
transaction. On upgrades with existing repertoires, a compact durable sweep visits one repertoire per delivery;
startup and foreground reads do not calculate inventories. Inputs are fenced by
published graph generation, raw source revision, canonical-prefix revision and
its relevant preview identity. Repeated identical requests preserve task leases
and cursors. Unchanged route artifacts are reused, including across repertoires.

A delivery reads one route/card segment, closes the reader before python-chess
computation, then commits through the existing foreground admission gate. Board
FEN and ply checkpoints commit atomically with results. Replay checks both task
lease and artifact cursor. Publication swaps an independent inventory publication
pointer only when all routes are complete and versions still match. Changed input supersedes
the build; the previous publication remains readable with `stale=true`.

Cleanup removes old memberships/responses in 64-row slices before their generation
headers. Publication metadata lives outside `repertoires`, so inventory work does
not alter existing prefix-transition snapshots. Unreferenced route occurrences are
removed in 64-row slices, with the route locked against new memberships; shared positions and cohort evidence are
retained for future repertoires. Repertoire deletion cascades only its generation
records. Orphan route artifacts are collected by subsequent inventory cleanup.

## Internal read contract and downstream handoff

`postgres_position_inventory` exposes `inventory_progress`, `published_positions`,
`published_routes`, `route_occurrences`, `legal_replies`, and `route_coverage`.
Position and route pages include publication identity and stale status; cursors
are canonical FEN, line ID, absolute ply, or UCI as appropriate. Page limits are
1–256, default 64 for route/position pages. Consumers must pin the publication ID
while reading route detail and restart if it changes; old details may disappear
after bounded retention. There is no new public endpoint or UI.

`source_cohort_key` identifies canonical position, source, model/API version,
evidence schema version, actual supported rating cohort and optional supported
speed cohort. Empty speed means the source does not support that dimension.
`position_cohort_evidence` reserves query parameters, evidence status, fetched and
validity times and response fingerprint independently of repertoire membership.
#109/#110 own acquisition, source capability verification, payload/move storage,
write ordering, retries and validity policy. They must preserve all legal replies
and source-native totals, validate moves against the shared legal set, and never
map unsupported cohorts inside the raw cache. #112 can consume route membership
and response support without confusing legal count with probability mass.

## Selected validation

Risk: additive PostgreSQL persistence, worker dispatch, graph/scope publication
and internal read contracts. Focused tests prove chess behavior and worker
boundaries; the disposable PostgreSQL checker proves upgrade, rollback, restart,
replay, source fencing, cache retention, transpositions, pagination and long-route
bounds. The regular durability runner invokes this checker. Existing coverage,
canonical-prefix, graph, durable-task and schema tests protect callers. CI owns
final required current-head/current-base validation; no UI rendering changed.
