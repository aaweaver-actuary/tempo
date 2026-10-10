# Opening decision readiness, version 1

`decision_readiness(snapshot, as_of, *, decision_index=None)` answers what existing
evidence supports about a card or a particular learner decision now. It returns
a bounded score or explicit unknown. It does not schedule reviews, admit cards,
order queues, introduce material, split prefixes, persist derived data, or call
Explorer/Maia. This is an independent input adapter for future [#112
preparedness calculations](https://github.com/aaweaver-actuary/tempo/issues/112),
not that calculation or an admission policy.
It depends on neither PR #116 nor PR #117.

## Evidence audit and chosen definition

| Existing evidence | What it supports | Limit |
| --- | --- | --- |
| `cards.fsrs_card_json` | FSRS stability, difficulty, memory reference and card-level retrievability | A memory-model estimate, not calibrated chess/game performance |
| `reviews` | Saved `correct`/`again`, guided status, actual review instant, invalidation and source | Legacy rows have no presentation revision or move-level response |
| Card state, due date and reinforcement | Context and evidence classification | Admission, maturity and lateness alone do not establish recall |
| Opening evidence presentations/attempts/observations | Immutable presentation identity, first response, assistance, manual failure and response time | Optional delivery; missing observations remain unknown; partial attempts are not whole-card passes |
| Published graph and legacy mappings | Which decisions belong to a card, including shared/transposed cards | A link does not establish independent practice |
| `opening_card_schedule_seeds` | Provenance of memory inherited from an older card | Seeded decision cards have not necessarily been independently reviewed |
| Eligible current repertoire decision events | Correctly played or missed canonical decisions, including misses inside prefixes | Games are observations, not synthetic FSRS reviews |

Tempo pins `fsrs==6.3.2`. The adapter calls the existing scheduler factory's
`get_card_retrievability` with the explicit instant. It does not duplicate the
forgetting curve, tune parameters or convert stability directly into a
probability. The library uses whole elapsed days, returns `1.0` during the first
day after either `Good` or `Again`, and uses lower failure stability to decay
faster later. See [the pinned FSRS source](https://github.com/open-spaced-repetition/py-fsrs/blob/v6.3.2/fsrs/scheduler.py).

Therefore v1 distinguishes:

- **FSRS basis:** the readiness score equals valid FSRS retrievability at the
  card's observed granularity. This is a model estimate, not a calibrated
  next-game probability.
- **Conservative failure basis:** unresolved failure gives score `0`. This is a
  versioned remediation policy, not a claim that true recall probability is zero.
  Raw card retrievability remains visible when valid.
- **Unknown basis:** no supported number. Unknown must not silently become zero,
  a fixed prior, maturity-based certainty or a fabricated success rate.

Raw FSRS state is sufficient for a card memory estimate with valid practice or
seed provenance. It is insufficient for immediate failure handling and for
independent recall probabilities inside a cumulative prefix. No second memory
model or empirical success-rate estimator is introduced.

## Pure snapshot contract

The implementation and frozen types live in
[`decision_readiness.py`](../backend/app/services/decision_readiness.py).

`ReadinessSnapshot` describes one saved opening presentation: card/revision,
repertoire, learner color, starting FEN, and immutable UCI move tuple. Optional
inputs are serialized FSRS state, state/reinforcement context, study reviews,
persisted decision observations, eligible game events and an inherited seed's
source card/time. `decision_index=None` requests the card; an index identifies
one learner occurrence, not an opponent move or just a repeated canonical ID.

`ReadinessEstimate` contains:

- `version`, presentation identity and requested decision identity/index;
- nullable `readiness_score` and raw `fsrs_retrievability`;
- `basis`, `availability` (`available`, `coarse`, `unknown`, `unavailable`),
  `confidence` (`model_based`, `limited`, `coarse`, `none`) and `evidence_scope`;
- normalized UTC `as_of`, latest evidence instant and FSRS memory reference;
- immutable evidence source/reference tuples, reasons and `evidence_group_id`;
- `latest_observation_outcome` for a requested occurrence (`clean`, `failed`,
  `unproven`, or absent), without converting it into a recall probability;
- for decision requests, a separate `card_estimate` containing the owning card's
  model context. Prefix raw retrievability is never a decision-level field.

Confidence labels describe evidence provenance; they are not statistical
confidence intervals. Learning cards and pending reinforcement are limited;
directly reviewed mature cards are model-based without a probability floor.
Legacy review rows whose revision cannot be proven can support card memory with
limited confidence, but cannot establish independently reviewed decision memory.

The caller owns bounded snapshot acquisition and current-publication/safety
checks. There is no database loader, route or startup/background handler. Map
persisted response timestamps and first-response/assistance facts directly;
do not reconstruct attempts or call the reducer's wall-clock validation from
this adapter. Supply a review's revision only when immutable saved context or
equivalent existing evidence proves it; do not attach today's revision to
unattributed legacy rows. Supply `attempt_id` when a review is linked to optional
observations, so assisted completion cannot be mistaken for a clean aggregate.

Game `eligible` must be explicitly supplied from current evidence satisfying
existing publication/source fences, game exclusion, current card ownership and
repertoire integrity rules. The adapter further checks repertoire/color/card
and canonical position/expected-move attribution. Do not use accumulated game
summaries, stale queue priority metadata, or expired generation rows as events.
Do not derive readiness eligibility from `has_outstanding_real_game_miss`: its
queue-obligation predicate consumes misses after any saved study attempt. Load
current safe event facts and let this adapter apply its clean-recall recovery.
The caller must provide all relevant unresolved-failure and recovery facts in
its bounded snapshot; absence from a truncated window is not proof of recovery.

## Failures, recovery and cumulative prefixes

An aggregate `again` or guided review is card failure. An incorrect first
response or manual failure is directly attributed decision failure. An eligible
game miss affects the owning card and its matching canonical decision. Game
success is retained as context and does not recover the conservative score.
Unverified responses cannot prove clean recall or an incorrect recall judgment.

A later nonguided successful whole-card study review clears aggregate failure.
A clean unassisted response to the same occurrence, or a later proven clean
whole-card pass covering it, clears direct decision failure. Assistance before
response, reveal/correction after an incorrect response and guided completion
do not constitute clean recovery. Linked invalidated review observations are
excluded. Equal cross-source timestamps retain failure; timestamps are compared
as UTC instants, not calendar dates or lexicographic strings.

A miss anywhere in a prefix fails the card. That aggregate failure is not a
failure observation on every included move. Clean predecessors, the evidenced
miss and unreached successors remain distinct. A prefix decision ordinarily has
unknown numeric readiness; direct failure can support conservative zero.
Clean prefix observations report recovery/freshness, not new per-move FSRS
state. A one-decision card needs its own revision-proven study review before its
FSRS estimate can become decision-level memory. A seeded card remains coarse
until its own practice establishes that provenance.

Repeated decision IDs inside a looping presentation retain their occurrence
indexes. A canonical game event can match multiple occurrences, but retains one
event reference and one shared evidence group; this is not multiple independent
observations. Linked cards do not pool, multiply or average their memory states.
An inherited estimate shares `fsrs-card:<source_card_id>`; directly practiced
cards use `fsrs-card:<card_id>`.

## Edge cases and examples

| Snapshot at a fixed instant | Result |
| --- | --- |
| Introduced/locked/new card without study or inherited memory | Unknown; `never_studied`, regardless of admission or maturity flags |
| First clean practice, same day | Card FSRS score `1.0`, limited confidence and pending-reinforcement reason |
| First failed practice, same day | Score `0`, conservative failure; raw FSRS can still be `1.0` |
| The same first success/failure one day later | Raw FSRS is approximately `0.947` / `0.766`; the unresolved failed readiness score stays `0` |
| Studied three-decision prefix, failure on decision 1 | Card score `0`; decisions 0 and 2 unknown; decision 1 conservative zero |
| Clean aggregate prefix review without optional observations | Card estimate available; per-decision scores unknown; no invented first responses |
| Mature overdue card | Ordinary FSRS decay; no maturity floor, due-date cliff or added overdue multiplier |
| Seeded decision card with no own review | Coarse inherited card estimate; independent decision readiness unknown |
| Missing/malformed/nonfinite/invalid scheduler state | Raw model unavailable with reason; reliable observed failure can still yield conservative zero |

Both evidence freshness and FSRS memory reference are exposed because Tempo may
clamp effective review time for overdue scheduling. Actual review freshness must
not be replaced by the earlier model reference. `as_of` requires a timezone;
offset-equivalent instants produce equal results. Historical evaluation needs a
coherent historical snapshot. Newer scheduler state or a newer actual review
cannot be used to reconstruct earlier readiness. Future observation/game events
are excluded from the projection. No wall clock is consulted.

For example, after constructing a bounded snapshot from owned records:

```python
from datetime import datetime, timezone
from app.services.decision_readiness import decision_readiness

as_of = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
card_estimate = decision_readiness(snapshot, as_of)
move_estimate = decision_readiness(snapshot, as_of, decision_index=1)
# On a cumulative prefix, move_estimate.readiness_score can be None while
# move_estimate.card_estimate.fsrs_retrievability is available.
```

Future marginal-preparedness scoring supplies these bounded snapshots outside
long transactions, preserves unknown mass and shared evidence groups, and makes
an explicit modeling choice about raw FSRS probabilities versus conservative
scores. It must not multiply a repeated prefix estimate once per included move,
interpret a zero policy score as calibrated impossibility, inherit parent
maturity as descendant competence, or normalize away unknown readiness.

## Regression and validation scope

The named regular regressions are in
[`test_decision_readiness.py`](../backend/tests/test_decision_readiness.py) and
registered in [`tests/REGRESSIONS.md`](../tests/REGRESSIONS.md). They cover recent
failure, clean recovery, scheduler-semantic differences, overdue decay,
never-studied/admitted states, real-game misses, prefix uncertainty, repeated
occurrences, inherited and legacy provenance, assistance/corrections,
invalidation, corrupt/future state, timezone ties and input nonmutation.

The smallest local proving scope is `make python-file
FILE=backend/tests/test_decision_readiness.py`, followed by the existing scheduler
tests, opening-decision evidence tests and prefix-diagnostics tests. No database
query, migration, workflow or rendering change requires an additional local
Docker/browser run. CI owns required current-candidate qualification; focused
local passes are not a full-gate claim. This adapter remains separate from
[PR #142](https://github.com/aaweaver-actuary/tempo/pull/142)'s obligation recovery:
an ordinary saved study attempt can consume queue obligation evidence, while
readiness requires clean recall. It also preserves
[PR #134](https://github.com/aaweaver-actuary/tempo/pull/134)'s practice-based
progression. Related #112/#118 remain incomplete and open.
