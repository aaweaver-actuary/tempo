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
backend/.venv/bin/python database/tooling/frozen-maia-order.py plan \
  --config scripts/frozen-maia-order.json \
  --docker-container tempo-postgres-1 \
  --foreground-url http://127.0.0.1:3000/api/system/foreground-requests-active \
  --allow-unscored-tail \
  --output /Users/andy/tempo/test-results/frozen-maia-order
```

Omit `--allow-unscored-tail` to keep strict probability completeness as the
default planning policy.

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
  inclusion/exclusion/unknown status, final disposition, current gates, ranks,
  scoring status and marginal scores.
- `preview.json`: global/family ranks, current introduction allowances and eligible
  admission previews. High-ranked locked cards stay locked. No published graph
  route is a genuine gate, not a successful introduction.
- `inference-cache/`: resumable, atomically written, identity-checked results.
- `ranks.csv`: includes scoring status, final disposition, defer reasons and
  diagnostic references. `deferred-cards.csv` lists each unscored card, its
  memberships, reason codes, tail rank, null score and source-line references.
- `diagnostics.json`: counts source-line diagnostics separately from the
  distinct affected card IDs. Its `summary` (also `diagnostic_summary` in the
  plan) lists affected scenarios and dispositions, cards with unknown route
  mass, and any affected scored cards. `excluded-cases.csv`, `unknown-cases.csv`, prior
  ordering, checksums and measured execution counts accompany the plan.

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
1; unweighted valid alternatives use a reported mixture over distinct moves
at the same canonical position within each repertoire policy. That mixture is
consistent across transposed arrivals; an unrepresented continuation retains its
missing mass rather than borrowing a different route history.
For example, opponent branches 0.60 and 0.50 give reach 0.30 regardless of
Maia's likelihood for the intervening prescribed learner move.
The supplied reference-prefix teaching decisions retain the original plan's
probability-1 convention; that convention does not restrict arrival order or
assign probabilities to alternative pre-anchor histories. `positions.csv`
contains only the conditional event and subsequent represented positions.

These are represented-path estimates. Missing probability is unknown, never
zero. A FEN alone cannot establish its encounter probability. The utility uses
legal stored lines and existing card/published graph prefixes; it does not
discover new chess routes.

## Completeness and the optional unscored tail

Planning is strict by default. Without `--allow-unscored-tail`, unresolved
probability evidence or any lineage diagnostic leaves `probability_complete`
and its compatibility alias `complete` false; apply refuses the plan.
`complete` means probability completeness, not activation safety.

With explicit tail mode, safely scorable cards retain the existing Maia
calculation and greedy marginal-coverage order. Valid, never-studied cards with
localized evidence gaps follow in stable `baseline_order`, then card-ID order.
They receive contiguous ranks, `scoring_status: unscored_deferred`,
`marginal_score: null`, original reason codes and affected source-line
references. Unknown estimates are not assigned zero and cannot change scored
card ranks.

Only missing exact card lineage, missing represented source ancestry / unknown
route mass, and localized unknown decision reach may enter the tail. Archived,
deleted, superseded, pending-validation, invalid, exposed, and uncertain
legacy-exposure cards remain excluded or protected by existing safeguards.
An uncertain legacy admission remains protected even after queue trimming or
an introduction-date reset; a missing current admission does not prove that
the card was never presented.
Invalid source evidence, failed Maia inference, a scenario with no effective
scoring evidence, invalid anchors/configuration, missing model provenance,
incoherent capture, and target-identity mismatch remain fatal. Lineage
diagnostics may remain nonblocking only when their effects are localized to
deferred cards or they affect no card in the frozen target scope.

`activation_safe` is separate from `probability_complete`. It requires every
captured card to have an explicit scored, deferred, excluded, or protected
disposition; complete unique ranks for the safe introduction scope; no fatal
scenario/system failure; and a deferred fraction at or below 1% of the
otherwise rankable backlog. The plan records numerator, denominator, fraction,
threshold, failed scenarios and diagnostic-to-card relationships. If the
threshold is exceeded, stop and generate no activation-safe plan.

Unknown arrival mass affects every answer at the same policy-specific position
and its represented descendants, including transpositions. Propagation stops at
the conditioning anchor, whose mass is fixed at 1. The plan records
`lineage_uncertainty_policy: represented_position_descendants_v1`; older plans
must be regenerated before apply. Existing inference results remain reusable
when their cache identity still matches. A scenario with no represented route
reaching its anchor is fatal, even if prefix probabilities or zero-valued
decision placeholders exist. Apply independently checks the actual deferred
fraction, deterministic tail order, card definitions and dispositions, and
effective evidence for every configured scenario.

## Deployment and explicit apply

Planning works against deployed schema 47. Serving this override requires schema
48 and this reader patch in every API/queue writer image. Deployment, migration,
restart and live apply require separate authorization. A short quiet maintenance
window is appropriate; no live stack is restarted by this utility.

Use the existing stopped-writer migration/deployment procedure in
`STARTUP-BACKGROUND-RECOVERY.md`, including backup, role/readiness verification
and coherent API/foreground/background reader deployment. The shared Maia image
must include `scripts/maia-inference.mjs`. Do not enable paused analysis systems.
Before applying, inspect the plan's scoring policy, scored/deferred/excluded/
protected counts, diagnostics, complete scope and exact target identifier.

```sh
backend/.venv/bin/python database/tooling/frozen-maia-order.py apply \
  --plan /Users/andy/tempo/test-results/frozen-maia-order/plan.json \
  --target <target-from-plan-command> \
  --reader-url http://127.0.0.1:3000

backend/.venv/bin/python database/tooling/frozen-maia-order.py verify \
  --plan /Users/andy/tempo/test-results/frozen-maia-order/plan.json \
  --target <target-from-plan-command> \
  --reader-url http://127.0.0.1:3000
```

Writes use the existing configured writer/read connections, not the Docker
bridge. Apply checks strict probability completeness, or `activation_safe` for an
explicit deferred-tail plan; it also checks checksum, exact PostgreSQL
cluster/database identity, reader compatibility, definitions, exposure,
membership, lifecycle and source inputs. It exports `pre-apply-order.json`,
stages inactive rows in bounded batches, then activates the pointer and requests
the ordinary durable queue refresh in one short transaction. Pending-apply guards
refuse protected input changes during staging. Mutations after activation remain
fenced by current gates, content fingerprints and issued-attempt protection. No
inference occurs in apply.
Reapplying the active plan changes no admissions, allowances or study state.

Before each bounded write slice, the CLI waits up to 30 seconds for foreground
admission. Waiting holds no database connection, and a started write body is
never replayed. Persistent activity still stops the command with its original
admission error and preserves inactive staging for inspection or explicit abort.
Use a short quiet window for activation: study or source writes during staging
still invalidate the pending run, including writes completed between slices.

Mandatory real-game remediation remains separate and higher priority. Ordinary
boosts, generated/legacy scores, breadth, depth and import order cannot displace
eligible frozen cards. Existing quotas and shared-card single admission remain.
Optional capacity expansion keeps its existing parent gates. Already admitted
membership, quotas and frozen opening selection are retained. The versioned
daily presentation policy spreads eligible, unissued new and review work across
the movable slots. Selected new openings retain their frozen relative rank,
with uncaptured ordinary openings afterward; their slots may move across other
categories. This replaces the earlier fixed-other-slots presentation promise.
Issued attempts, teaching/presentation checkpoints, explicit entries and
positioned repeats retain their positions. Content changes hold the affected
unstarted card and require explicit
rescoring/replacement or retirement. Re-enabling analysis services, refreshing a
generated score, or rebuilding priorities does not automatically retire an
active frozen order. It remains authoritative until an explicit, verified
replacement or rollback retires its pointer; retirement is separate from
publishing the replacement.

Verify reports the installed pointer, stale cards and ranked unstarted queue
order. Inspect the real queue after its durable refresh. An installed pointer or
empty preview does not establish a live introduction; report applied/awaiting
refresh separately from actual reader consumption.

## Rollback

```sh
backend/.venv/bin/python database/tooling/frozen-maia-order.py rollback \
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
backend/.venv/bin/python database/tests/test-frozen-postgres.py
make ui-file FILE=frozen-opening-order.spec.ts
```

The native script owns a fresh PostgreSQL container/volume, tests schema,
selection/admission, refresh, day boundary, restart, stale activation, an issued
head during preparation, stale-content serving and rollback preserving later
study, then removes its resources. It also checks indexed 16-row access over
35,400 static ranks. That server query measurement is not whole-study latency
or a before/after speedup claim. The browser proof checks actual displayed study
and preserves an issued board attempt across replacement.

## Runtime protection and reader compatibility

Frozen rank publication does not turn a legacy-origin card into an eligible
fallback. Runtime annotation, candidate selection, final writer admission, queue
serving, and rollback restoration consult current `queue_attempt_origins`
evidence. Valid post-snapshot cards without legacy origins can still use the
ordinary fallback. Deferred cards remain after scored ranks, and existing
issued attempts, explicit/manual admissions and positioned repeats retain their
positions. Unissued reviews participate in daily presentation. Verified mandatory
real-game remediation keeps priority in the remaining movable slots; a failed
remediation repeat keeps its existing positioned retry behavior. This presentation
change does not introduce a retry cooldown or change scheduling intervals.

The plan checksum binds `required_reader_version: frozen-maia-v2`. Model and
inference-cache identity remain `frozen-maia-v1`; a reader capability change
does not invalidate Maia results. Apply checks API health before staging and
again verifies the active target/run. Deploy the v2 reader before following the
activation steps below. A v1 reader or a plan missing this marker is refused.

Capture uses ordered primary keys discovered through PostgreSQL catalogs for
base tables. Current views have explicit base-table key mappings, validated
against their projected columns. Capture fails with the relation name if a
stable key cannot be established; it does not fall back to a full table scan or
all-column ordering. Pages remain bounded at 128 rows, and two complete passes
must agree. The SELECT-only `tempo_reader` role is exercised by
`backend/.venv/bin/python database/tests/test-frozen-postgres.py`.

The full disposable command proof is
`backend/.venv/bin/python database/tests/test-frozen-cli.py`. It creates a unique
PostgreSQL/Redis Compose project, starts the API and foreground/background
workers, uses separate reader and writer roles, runs real Maia plan/apply,
repeats apply, waits for ordinary queue refresh, fetches the actual queue to
issue its head, verifies the publication, and rolls it back. It removes only
its own project, volumes, and temporary credentials. Do not point either proof
at the study installation.
