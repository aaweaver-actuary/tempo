# One-time frozen Maia opening order

This utility calculates a captured backlog once and publishes immutable ranks.
Runtime reads `frozen_opening_ranks`, indexed by `(run_id, ordinal)` and
`(run_id, card_id)`. It never invokes Maia, recalculates encounter probabilities,
or runs the marginal-coverage optimizer. This is a bounded hot fix, not the
component extraction or a recurring probability service. Related: #231, #110,
#156; none of those issues is completed by this change.

## Read-only planning

Work in an isolated checkout. Use the existing prescribed Python setup and
`npm ci` for the shared Maia helper. From the checkout root:

```sh
backend/.venv/bin/python scripts/frozen-maia-order.py plan \
  --config scripts/frozen-maia-order.json \
  --docker-container tempo-postgres-1 \
  --foreground-url http://127.0.0.1:3000/api/system/foreground-requests-active \
  --output /Users/andy/tempo/test-results/frozen-maia-order
```

The Docker bridge is explicitly read-only, opens short read transactions, and
keeps credentials inside the PostgreSQL container. Alternatively omit
`--docker-container` and use the installation's configured
`TEMPO_DATABASE_READ_URL` and passfile through Tempo's owning connection boundary.
`--snapshot-dir` replays an already exported capture. `--inventory-only` exports
an explicitly blocked inventory without running inference. Record a verified
running revision with `--application-revision <revision>` when available.

Outputs:

- `plan.json`: input checksums, exact cluster/database/schema identity, cutoff,
  definitions, lifecycle/membership inventory, model/runtime/encoder checksums,
  ordered rating inputs, route evidence, decision probabilities and frozen ranks.
- `decision-reach.csv`: FEN, expected answer, policy/scenario, conditional
  decision probability, scenario weight and actual prior-study coverage.
- `positions.csv`: every represented post-anchor position with its conditional
  first-hit probability, canonical FEN and representative complete input FEN.
  Position reach is tracked separately from answer-specific coverage, so repeated
  positions with different answers cannot inflate it.
- `inventory.csv`: physical cards, effective colors, memberships, lineage,
  inclusion/exclusion/unknown status, current gates, ranks and marginal scores.
- `preview.json`: global/family ranks, current introduction allowances and eligible
  admission previews. High-ranked locked cards stay locked. No published graph
  route is a genuine gate, not a successful introduction.
- `inference-cache/`: resumable, atomically written, identity-checked results.
- Separate `ranks.csv`, `excluded-cases.csv`, `unknown-cases.csv`, diagnostics,
  previous ordering, checksums and measured execution counts accompany the plan.

Keep captures private: they contain study history and presentation metadata.
Inference runs outside database transactions, uses one bounded headless helper,
and yields to the existing foreground-request signal. The model receives full
valid FENs. Cache matching uses the existing canonical first four FEN fields:
side, castling and relevant en passant remain significant; clock counters are
not inputs to this piece-plane encoder or its legal-move mask. Cache reuse never
merges policy-specific reach or coverage.

The ten scenarios have equal weight 0.10, including London conditioned only on
`1. d4`. Reference sequences construct anchor FENs; any represented legal arrival
order reaching the same canonical position qualifies. Arrival histories are
discarded and deduplicated suffixes start at probability 1. Canonical matching
preserves side to move, castling rights and legal en passant, ignoring clocks.
KID sidelines conditions
only the first White move against `e4`, retaining later `e4` and d4/c4 routes.
Opponent departures retain their mass. Prescribed learner moves have probability
1; unweighted valid alternatives use a reported mixture over distinct moves.
For example, opponent branches 0.60 and 0.50 give reach 0.30 regardless of
Maia's likelihood for the intervening prescribed learner move.
The supplied reference-prefix teaching decisions retain the original plan's
probability-1 convention; that convention does not restrict arrival order or
assign probabilities to alternative pre-anchor histories. `positions.csv`
contains only the conditional event and subsequent represented positions.

These are represented-path estimates. Missing prefixes or inference are UNKNOWN.
A FEN alone cannot establish its encounter probability. The utility uses legal
stored lines and existing card/published graph prefixes; it does not discover
new chess routes. A blocked plan may contain inspectable partial ranks, but
`apply` refuses it. It does not fall back to old scores to claim completeness.

## Deployment and explicit apply

Planning works against deployed schema 47. Serving this override requires schema
48 and this reader patch in every API/queue writer image. Deployment, migration,
restart and live apply require separate authorization. A short quiet maintenance
window is appropriate; no live stack is restarted by this utility.

Use the existing stopped-writer migration/deployment procedure in
`STARTUP-BACKGROUND-RECOVERY.md`, including backup, role/readiness verification
and coherent API/foreground/background reader deployment. The shared Maia image
must include `scripts/maia-inference.mjs`. Do not enable paused analysis systems.
Before applying, inspect the plan's complete scope and exact target identifier.

```sh
backend/.venv/bin/python scripts/frozen-maia-order.py apply \
  --plan /Users/andy/tempo/test-results/frozen-maia-order/plan.json \
  --target <target-from-plan-command> \
  --reader-url http://127.0.0.1:3000

backend/.venv/bin/python scripts/frozen-maia-order.py verify \
  --plan /Users/andy/tempo/test-results/frozen-maia-order/plan.json \
  --target <target-from-plan-command> \
  --reader-url http://127.0.0.1:3000
```

Writes use the existing configured writer/read connections, not the Docker
bridge. Apply checks completeness/checksum, exact PostgreSQL cluster/database
identity, reader compatibility, definitions, exposure, membership, lifecycle and
source inputs. It exports `pre-apply-order.json`, stages inactive rows in bounded
batches, then activates the pointer and requests the ordinary durable queue
refresh in one short transaction. Pending-apply guards refuse protected input
changes during staging. A mutation after activation is handled by current gates,
content fingerprints and issued-attempt protection. No inference occurs in apply.
Reapplying the active plan changes no admissions, allowances or study state.

Mandatory real-game remediation remains separate and higher priority. Ordinary
boosts, generated/legacy scores, breadth, depth and import order cannot displace
eligible frozen cards. Existing quotas and shared-card single admission remain.
Optional capacity expansion keeps its existing parent gates. Already admitted
membership is retained; only movable unstarted opening slots are reordered,
with uncaptured ordinary openings afterward. Reviews and issued attempts stay
protected. Content changes hold the affected unstarted card and require explicit
rescoring/replacement or retirement. Future ordering implementations must honor
an active snapshot; a priority rebuild cannot retire it.

Verify reports the installed pointer, stale cards and ranked unstarted queue
order. Inspect the real queue after its durable refresh. An installed pointer or
empty preview does not establish a live introduction; report applied/awaiting
refresh separately from actual reader consumption.

## Rollback

```sh
backend/.venv/bin/python scripts/frozen-maia-order.py rollback \
  --run-id <run-id-from-plan-command> \
  --target <target-from-plan-command>
```

Rollback restores this run's previous pointer and requests normal queue
refresh. Position receipts record only this run's queue permutations. Rollback
restores still-unstarted entries only when their content and positions match;
it reports conflicts and preserves a whole swap when one occupant is protected.
It refuses replacement conflicts, retains immutable publications as
provenance, and never restores a database backup or writes reviews, FSRS,
content, due dates or introduction timestamps. It can also abort this run's
inactive staging guard after failed apply. Subsequent study remains authoritative.

## Focused proof

Run the new `backend/tests/test_frozen_maia_*.py` and
`backend/tests/test_frozen_opening_order.py`, the affected queue/capacity/study
files, the Maia helper/parity unit files, and:

```sh
backend/.venv/bin/python scripts/test-frozen-postgres.py
make ui-file FILE=frozen-opening-order.spec.ts
```

The native script owns a fresh PostgreSQL container/volume, tests schema,
selection/admission, refresh, day boundary, restart, stale activation, an issued
head during preparation, stale-content serving and rollback preserving later
study, then removes its resources. It also checks indexed 16-row access over
35,400 static ranks. That server query measurement is not whole-study latency
or a before/after speedup claim. The browser proof checks actual displayed study
and preserves an issued board attempt across replacement.
