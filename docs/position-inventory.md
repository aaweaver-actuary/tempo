# Shared opponent-position inventory (#108 / #117)

The PostgreSQL inventory contains opponent-to-move positions on currently
relevant authored routes, including their terminal positions, and every legal
opponent reply. It does not recursively expand beyond those routes.

## Identity and provenance

`inventory_positions.fen_key` reuses Tempo's canonical four-field FEN: piece
placement, side to move, castling rights and legal en passant. Move counters are
excluded. Replies use lowercase UCI, including promotion suffixes, and retain
the resulting canonical position key. `legal_reply_count` and
`legal_moves_version` identify the complete legal support. Terminal positions
have zero replies and do not define a probability distribution.

Positions and replies are shared independently of repertoire identity.
Immutable route artifacts retain authored move occurrences and ply offsets;
generation memberships retain repertoire and line identity. Route identity
includes the inventory version, starting FEN, saved move-content digest,
trained color and certified canonical-prefix scope/origin. Equal positions on
different routes share legal support without merging repertoire policy.

`route_coverage` distinguishes an authored reply, a published learner response
on that route, a supported response through transposition in the same repertoire
generation, and missing coverage. Authored terminal moves do not imply a learner
response exists. These labels describe repertoire support, never probabilities.

Deleting a repertoire cascades its generations/memberships/responses. Shared
positions/replies remain; bounded cleanup collects unreferenced route artifacts
without removing another repertoire's membership or shared legal universe.

## Durable publication

The current graph publication boundary and accepted canonical-prefix saves
request inventory construction atomically. The input fence includes graph
generation, source revision, prefix revision and certificate identity. Unchanged
requests preserve the existing generation, lease and cursor.

One delivery registers a route, traverses at most 16 plies, examines one bounded
card segment, publishes, or performs bounded cleanup. Source readers close before
chess computation. Fenced short background writes commit results and checkpoint
advancement together using current durable-task primitives and admission.

Publication requires all registered routes complete. Repertoire/task locks and
source identity are revalidated before accepting output. Graph publication takes
the repertoire lock before its task lock where the inventory handoff requires
it. Inventory requests/slices use repertoire then inventory task ordering;
terminal failure locks the task before updating generation state. Deterministic
PostgreSQL contention proofs protect these boundaries.

Inventory publication is separate from queue publication and never changes
admission, FSRS, ranking, canonical-prefix retention or scheduling policy.
Readers expose stale status when current inputs differ from accepted generation
metadata. Failed construction retains actionable task/error diagnostics.

## Internal readers and evidence boundary

`inventory_progress`, `published_positions`, `legal_replies`, `published_routes`,
`route_occurrences` and `route_coverage` provide bounded internal reads.
Pagination allows 1–256 rows; position/route pages default to 64. There is no new
public HTTP API or UI.

The #154 `PositionMoveUniverse` accepts the complete canonical legal set for
nonterminal positions. An unobserved legal reply remains present. Its downstream
probability is unknown/unassigned; inventory does not assign zero or any weight.
The unused source-cohort cache scaffolding from the original branch is removed.
Future evidence persistence belongs to the #154 boundary. No Explorer/Maia
fetching, profile weighting, policy reach or probability fusion occurs here.

#116 profile rating/speed publications are not inventory inputs. Repertoire
selection weights are never inferred from stored lines/cards/repertoires.

## Migration and qualification

Migration 045 adds inventory tables and queues one bounded reconciliation task
only when upgrading a database with existing repertoires. Readiness is 45.
Published migrations 001–044 remain unchanged. The normal migration driver owns
idempotent application; no study/history rebuild is performed.

Named Python and real PostgreSQL cases are registered in `tests/REGRESSIONS.md`.
`scripts/check_postgres_position_inventory.py` owns isolated databases and runs
through the regular `background_workloads` durability stage. Current main's
historical migration rehearsals and complete qualification remain required.
