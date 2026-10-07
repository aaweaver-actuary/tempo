# Structural prefix evaluation

## Implementation validation plan

Issue #77 adds a pure structural evaluator and read-only PostgreSQL API. Risks are
incorrect card deduplication, learner/ply confusion, shared aliases, saved split
overrides, mixed saved depths, stale publications, accidental writes, and foreground
contention. The smallest proof is deterministic evaluator fixtures followed by API
and route-contract tests. Existing graph, split and segmentation snapshot files
provide caller coverage. The settled candidate runs disposable PostgreSQL durability
with a paused-traversal foreground review and source-change proof. CI owns required
current-head/current-base validation; no local full gate or rendering suite is
planned. Tests never target the live study database.

Implementation starts at main `698d50e8c0e4c15b683c537612e76c0b32db6fcd` in
`.dev-copies/issue-77-structural-prefix-evaluator`. Existing checkouts have unpreserved
work, open PRs or no verified release of ownership and are retained. The live checkout
and ten live Docker containers are outside this work.

## API contract (version 1)

`GET /api/repertoires/{id}/prefix-evaluation/source` returns `snapshot_id`, the
published `graph_generation`, source `lines` (IDs, names, starting FENs, moves,
trained colors and saved depths), and the current depth distribution.

Send one explicit alternative to
`POST /api/repertoires/{id}/prefix-evaluation/evaluate`:

```json
{
  "snapshot_id": "<token from source>",
  "selected_line_ids": ["source-line-a", "source-line-b"],
  "candidate_depths": {"source-line-a": 2, "source-line-b": 3}
}
```

Omit `candidate_depths` or send null to evaluate saved current depths. A supplied
map must cover exactly the selected line IDs. Depths are strict integers from 1
through 20; zero, booleans, numeric strings and unknown fields are rejected.
Duplicate or foreign IDs are invalid. An empty selection succeeds with
`status: empty_selection`; identical structure returns `status: no_change`.
`depth_configuration_changed` is independent of `structure_changed`: changing
depth beyond a short route's length can leave every presentation unchanged.

Both `selected` and `whole_repertoire` contain current/proposed `metrics`, `cards`
and source graph `steps`, signed metric `delta`, additional/reduced board starts,
and sorted unchanged/added/removed card IDs. These identity classifications describe
presentations, not persistence or history treatment. They are not transition plans.
Unselected aliases can retain a card removed from the selected scope.

Each selected line reports saved, requested and current/proposed effective prefix
depth. Production saved splits still apply when their source card ID matches the
requested prefix; shortening or lengthening can change which saved overrides match.
No split is discarded or globally changed by this diagnostic.

An abbreviated response for the two-route counting fixture is:

```json
{
  "version": 1,
  "preview_only": true,
  "estimate_basis": "Structural counts only; not evidence of better learning or measured time savings.",
  "selected_line_count": 2,
  "status": "changed",
  "depth_configuration_changed": true,
  "structure_changed": true,
  "selected": {
    "current": {"metrics": {"distinct_cards": 2, "learner_decision_occurrences": 6, "board_starts": 2}},
    "proposed": {"metrics": {"distinct_cards": 3, "learner_decision_occurrences": 4, "board_starts": 3}},
    "delta": {"distinct_cards": 1, "learner_decision_occurrences": -2, "board_starts": 1},
    "additional_starts": 1,
    "reduced_starts": 0
  }
}
```

The full response also contains the snapshot identity, whole-repertoire comparison,
all metric fields, card identities/payloads/roles, graph steps and line depths.
Failure responses contain no comparison, for example:

```json
{"detail": {"code": "stale_snapshot", "message": "This structural snapshot is stale. Refresh the source and compare again."}}
```

## Metrics and identities

Distinct cards use production starting-position/move-sequence IDs; opponent setup
moves remain part of identity. Card roles are scoped graph roles, independent of
the global stored card kind. Prefix and descendant role sets can overlap; total
cards are their union. Full graph steps preserve each alias and parent relationship.

A pass exercises every distinct card once. `learner_decision_occurrences` counts
only moves by that card's trained color, including repeated decisions within a
cycle. Decision identity reuses opening segmentation's position/color/repertoire/
expected-UCI/policy contract. `repeated_decisions_across_cards` sums, per decision,
the number of distinct containing cards minus one. `repeated_decisions_within_cards`
counts additional occurrences inside each card separately. Their sum equals total
decision occurrences minus distinct decisions. `board_starts` equals distinct
cards, not distinct starting positions.

For two Black routes sharing two decisions and diverging at the third, depth three
produces two cards and six decision occurrences. Depth two produces one shared
prefix and two descendants: three starts and four occurrences. Keeping an unselected
alias at depth three retains its original card in the whole repertoire: four cards.
These are structural counts, not evidence of better learning or measured time savings.

## Freshness, bounds and recovery

The token binds source IDs/content/names, saved depths, published graph generation
and shape, card content/revisions/membership/archive status, split definitions and
child revisions, and contract/policy/position-identity versions. Production currently loads a global
split map, so an unrelated saved split can conservatively invalidate the token.
Reviews, scheduling, queues and priority epochs are excluded. No adaptive
recommendation is required. Missing/zero saved depths are unsupported rather than
replaced with the current global setting.

All source reads use the API's configured PostgreSQL reader pool; the checked-in
reader URL targets the authoritative PostgreSQL service, without API writer credentials.
Repeatable reads are explicitly READ ONLY before the existing background transaction/lock
budgets are set. Worker callers using `authoritative=True` retain their writer-pool
selection. JSON interpretation,
fingerprinting, chess traversal and counting occur after closing the connection.
Isolation is selected before the timeout configuration queries. The API rechecks
the source token after calculation and never returns partial results.

Limits are 2,000 source lines, 80,000 total source plies, **512 plies per source line**,
40,000 graph steps, a 4 MiB source
move-JSON transfer budget, at most 40,000 saved split entries, and a ten-second
request computation deadline. Foreground activity is checked between line/card
calculations. Both endpoints are secondary work even without a work-class header;
the POST is also query-only. They enqueue no work and persist no result or receipt.

The per-line ceiling applies only to prefix evaluation; PGN imports, saved repertoire
content and normal study are unaffected. Shared source validation checks every line,
including unselected lines, before any current or proposed graph construction. A line
with 512 plies is accepted; a line with 513 or more makes both endpoints return HTTP
413 with `detail.code: limit_exceeded` and a message identifying the line, its ply
count and the maximum. Nothing is truncated, skipped or returned as partial metrics.
This bounds the input to each uninterrupted single-line graph build; it does not
provide a hard real-time guarantee for every request phase.

Errors contain `detail.code` and `detail.message`: `stale_snapshot`, `graph_not_ready`,
`unsupported_source` and `unsupported_backend` use HTTP 409; `invalid_selection`
uses 422; `limit_exceeded` uses 413; `evaluation_busy` uses 503 with `Retry-After: 1`.
A missing repertoire uses 404. Refresh after stale source/publication changes; retry
busy work when study is idle. Every retry is a fresh, deterministic read against
its supplied snapshot, with no history transfer or application authority.

## Issue #78 implementation validation plan

The read-only Repertoire comparison dialog selects literal starting-FEN/color/UCI
routes and auditable source IDs. Its risks are implicit transposition expansion,
mixed-depth misreporting, shared-card double counting, obsolete async callbacks,
accidental commands and responsive overflow. Focused selection/component tests,
Python-to-Zod parity and existing evaluator/API tests provide the first proof.
Disposable PostgreSQL verifies multiple candidate reads leave product tables
unchanged; the real browser workflow covers 390/1280px. Pinned visual/performance
runs once stable. CI owns the complete required current-head/current-base gate.
No tests use the live study stack.

## Branch-scoped comparison UI (#78)

In a backend-backed Repertoire card, open **More actions → Compare prefix depths**.
Choose a saved starting position/color, then successive route moves such as
`1.e4 c6`. Matching is exact starting-FEN, trained-color and ordered-UCI prefix
matching against the displayed authoritative snapshot. A matching board position
or opening name never expands the selection. The source-line checklist is final:
users can explicitly include/exclude IDs. Names and SAN are display aids only.
The snapshot token, graph generation, exact routes and selected IDs are inspectable.
Filtering/paging never silently changes selection; source rows are paged by 50 and
long routes show an excerpt with the complete UCI source available in details.

No scope or candidate depth is chosen automatically. Enter one to four distinct
uniform candidate depths, 1–20, separated by commas. Numeric ordering is only for
comparison and is not a recommendation. Current depths come from each source
line's saved state. Each candidate uses the existing read-only evaluate POST with
an explicit complete selected-ID depth map. Candidates run sequentially, followed
by an authoritative source re-read. Only a complete matching batch is published;
a failed/stale batch returns no partial metrics. There are no command, preference,
transition, receipt, save or application calls.

Each candidate shows selected-scope and whole-repertoire current/proposed metrics,
signed deltas, additional/reduced starts and added/removed/unchanged card identities.
Whole-repertoire values are server results, not sums of selected and unselected
counts. Selected-scope removals that remain unchanged in the whole repertoire are
shown as retained by unselected lines. Newly selected presentations may also reuse
identities already present in the whole repertoire. These classifications grant
no persistence/history-transfer authority. Requested/effective line depths distinguish
structural no-change from saved-depth no-change. Counts imply neither better
learning nor measured time savings.

Closing, repertoire changes, local refresh revisions, source reloads and selection
or candidate edits abort obsolete work and revoke response ownership. Snapshot,
graph, repertoire, selected IDs and requested/saved depths are checked on acceptance.
A final source check prevents publication across candidates from different snapshots.
While visible, the dialog rechecks source every 30 seconds and on focus. External
changes are detected at the next check; the last successful check time is visible.
On becoming hidden it clears comparisons; on return it rechecks freshness. Source
changes require explicit Refresh source and reselection. Failed freshness checks
hide unverified metrics. Busy service responses are shown explicitly for user-initiated
retry; polling never writes or queues work. Empty selection, no-op, unsupported,
stale, graph-not-ready, input limits and service failures remain distinct.
