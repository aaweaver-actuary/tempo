# Probability-based opening-card value

## Purpose and mathematical definition

`app.services.opening_preparedness` is a pure, deterministic computational kernel
with no production callers. Its model version is `expected-correct-decisions-v1`.
It does not change queue admission/order, FSRS, graph eligibility, or the UI.
It has no database, clock, source provider, chess, or scheduler dependency.

For a unique learner decision `d`, let `w[d]` be the probability of reaching it
conditioned on following the explicitly selected learner `policy_id`, and `r[d]`
the estimated probability of recalling its expected move. For card `c`, the caller supplies
projected recall `r_after[c,d]` for the decisions that studying it changes:

```text
Preparedness = sum_d w[d] * r[d]
MarginalValue(c) = sum_d_in_card w[d] * (r_after[c,d] - r[d])
RouteProbability = root_context_probability * product(conditional_opponent_replies)
```

Units are **expected correctly recalled unique learner decisions**. Totals can
exceed one when a game tests several decisions. Reach, recall and individual reply
probabilities remain in `[0,1]`. The corresponding readiness of a card is its
set of decision estimates; the module does not invent an aggregate probability
of passing the whole card or multiply marginal recall estimates as if independent.
Linearity of expectation needs no independence between learner recalls.

Reach is `P(reach decision d | learner follows policy R)`. It excludes
the learner's probability of deviating earlier, formal maturity, exposure gates,
depth bonuses, and learning cost. Thus a useful descendant can have positive
prospective gain before a parent becomes mature. Eligibility and actual gameplay
survival are separate future policies/metrics. There is no whole-horizon success
claim. Projected study effects are explicit non-regressive scenarios, not promises
that actual study always helps, and never implicitly set recall to one.

## Input/output contract

All public input/result types are frozen dataclasses; input collections are
copied to tuples. Collection ordering is canonical; move-prefix sequence remains
meaningful. Functions accept finite in-memory snapshots, perform no traversal
of PGNs or the repertoire graph, and never update their inputs.

| Type/function | Contract |
| --- | --- |
| `ProbabilityEvidence.exact(value)` | Fully specified input number; **not** statistically certain or calibrated truth |
| `.bounded(lower, upper, reason)` / `.unknown(reason)` | Explicit sensitivity interval / unknown `[0,1]`; reason required |
| `OpponentReply` | Move key, absolute conditional probability evidence, explicit `in_repertoire` membership |
| `OpponentReplyDistribution` | Listed replies plus explicit `unassigned_mass`; bounds must permit total mass one |
| `RouteReach` | Required keyword `policy_id`, context, canonical root key, complete incoming move prefix, policy-conditioned absolute probability evidence |
| `DecisionReadiness` | Required keyword `policy_id`, decision ID, incoming routes all belonging to that policy, current recall evidence |
| `CardLearningEffect` | Required keyword `policy_id`, card ID and `(decision_id, projected_recall)` pairs; unspecified decisions stay unchanged |
| `route_probability(root_probability, opponent_replies)` | Multiply sequential conditional evidence without a floor or normalization |
| `decision_reach(routes, *, policy_id)` | Union distinct incoming prefixes; deduplicate repeats and absorb later revisits |
| `preparedness(readiness, *, policy_id)` | Total bounds, sorted decision contributions and evidence diagnostics |
| `marginal_card_value(readiness, effect, *, policy_id)` | Gain bounds computed directly from changed decisions, with current/projected evidence |
| `rank_card_values(readiness, effects, *, policy_id)` | Exact-input scores descending, card ID for equal computed scores; incomplete results separately by ID |

`PreparednessResult` and `CardValueResult` expose `lower`, `upper`, `contributions`,
`diagnostics`, `model_version`, `policy_id`, and `value`. `value` is `None` when
relevant evidence is incomplete, even when the numerical interval collapses to zero. Card results
also include `card_id`. Ranking returns `CardValueRanking.ranked`, `.incomplete`,
and the selected `.policy_id`.
No incomplete card is silently assigned a numeric zero or put last in one ranking.
Ranks compare independent one-card interventions against the same baseline;
they are not a budget allocator or a greedy multi-card plan.

Duplicate identities with identical evidence collapse; conflicting evidence
raises `ValueError`. Missing decision references, absent reach events, invalid
probabilities, impossible mass totals and impossible non-regressive projections
also raise. Represent absent reach evidence as an explicit unknown route rather
than an empty route list. Empty decision snapshots/effects have zero value but
still require an explicit selected policy.

## Explicit learner-policy boundary

`policy_id` is a nonempty opaque identity for a persisted repertoire, hypothetical
policy, or shadow experiment. It requires no database lookup. There is no optional
policy-less mode. Every reach, preparedness, marginal-value, and ranking call
selects exactly one policy through its required keyword argument. All supplied
routes, readiness snapshots and card effects must match it. A foreign identity
raises `ValueError` before deduplication, scoring or projection lookup, even for
empty projections or rankings. Iterables are materialized once before validation.
Results retain the selected identity; decision IDs alone cannot establish policy
isolation. Route identity is `(policy_id, context_id, root_key, move_prefix)`.

At learner nodes, the prescribed move is an intended policy action. Its reach
factor is not inferred from repertoire counts, imported PGN frequencies, card
counts, repertoire size, or alternative learner moves. Root/context evidence
must already be conditioned on following this policy and must not contain an
implicit repertoire-selection weight. Scenario weights describe disjoint
opponent/profile contexts within the selected policy.

At opponent nodes, replies retain their modeled conditional probabilities.
Selecting Italian `e4 e5 Nf3 Nc6 Bc4` does not force `1...e5`: if its probability
is `0.42` and `2...Nc6` has conditional probability `0.5`, reach of the defining
learner decision is `0.42 * 0.5`, with root mass one. A later opponent reply adds
its own factor. Selecting Ruy `Bb5` instead, or storing both policies, introduces
no additional learner-branch factor. Repertoire conditioning is not opponent
conditioning.

Adding, removing or duplicating another policy leaves the selected policy's
reach, preparedness contributions, marginal values and ranking unchanged.
Identical canonical positions can appear in different policies with independent
route mass, including when decision/card IDs coincide. Within each policy,
duplicate prefixes, transpositions and overlapping revisits retain existing
unique-decision behavior. Learner recall evidence may eventually be intentionally
shared by another layer; sharing that evidence does not merge policy reach mass.

Cross-policy weighting is deferred. A future optimizer could explicitly compute
`sum_R P(R) * preparedness(R)` outside this within-policy kernel. Selection
weights must be explicit user/configured/learned inputs, never inferred from
imported lines, cards, repertoire size or the existence of other repertoires.
This version does not implement `P(R)`. The v1 count metric is not
`P(fully prepared for the game/opening horizon)`; a whole-horizon probability
would be a separate future metric.

## Routes, sharing and current Tempo concepts

- Reuse `opening_segmentation.opening_position_key`, `decision_identity`, and
  `presentation_occurrences`. Decision identity includes repertoire, trained
  color, canonical position, expected UCI response and identity/policy versions.
  Full-move/half-move counters and ineffective en-passant targets are not new
  knowledge. Different expected responses are distinct knowledge, not independent
  attempts at the same selected decision.
- Prefix, response and checkpoint cards can cover multiple learner decisions.
  Read the actual presentation's learner occurrences; do not use card length or
  assume one decision per card. A descendant affects its own presented decisions,
  not all ancestors leading to it. Shared card/repertoire memberships remain intact.
- The incoming prefix is from the **common scenario root to the decision**, not
  from an arbitrary card boundary and not the whole terminal line. Repeating an
  authored line or splitting a card therefore cannot add another reach event.
- Within one policy/context/root, caller-provided prefixes must follow one selected
  learner response per position; divergent opponent continuations are mutually
  exclusive. Distinct root/context pairs must represent disjoint weighted scenarios,
  with weights already included in absolute reach. Never use overlapping card
  roots as different scenarios. Canonicalization, legal moves, scenario partition
  and distribution consistency across positions remain caller responsibilities.
- Different move orders may reach the same decision. Their distinct, disjoint
  incoming masses add; duplicate prefixes do not. A later revisit beneath an
  already represented incoming prefix is a subset of that earlier event and is
  absorbed. This counts recalled **unique knowledge**, not repeated move attempts.
- Current FSRS fields (`state`, `stability`, serialized card, review timestamps),
  introductions and clean/assisted decision observations are potential inputs to
  a later readiness adapter. Neither maturity nor FSRS stability is itself a recall
  probability. No review/FSRS estimator or after-study gain predictor ships here.
- Existing introduction scoring remains `0.7 * completion_mass + 0.3 * frontier_reach`
  plus its gameplay evidence; its path floor/source fallbacks are not reused as
  truthful absolute probability evidence. Admission and daily queue mixing remain
  separate, unchanged consumers of that existing scorer.

## Missing mass and uncertainty

An example distribution has authored `e5=0.60`, authored `c5=0.20`, known outside
`e6=0.10`, and unassigned `0.10`. Policy selection preserves all three buckets.
Authored mass stays `0.80`; `e5` does **not** become `0.75`. The unassigned bucket's size is known here but its allocation is unknown:
it is neither proven outside coverage nor an authored move. No source data can
be represented by no listed replies and unassigned mass one. Uncertain individual
replies retain their own intervals. No Explorer/Maia fetch or blend occurs.

Intervals describe conservative sensitivity, not statistical confidence levels.
Their components may be coupled; no independence of uncertainty is assumed.
Distribution bounds must admit a total of one. For a union of disjoint events,
lower bounds add and upper bounds add up to the logical maximum one. This upper
bound cap does not redistribute mass. Exact mass exceeding one is rejected,
apart from `1e-12` arithmetic tolerance; supplied scalar probabilities are strictly
bounded. Stable sorted summation avoids order-dependent accumulation.

For `current=[a,b]`, `after=[c,d]` and `reach=[l,u]`, the non-regressive scenario
must be feasible (`d >= a`). The gain envelope is:

```text
gain_lower = l * max(0, c - b)
gain_upper = u * (d - a)
```

This preserves the shared before/after baseline; subtracting separately bounded
preparedness totals would lose that relationship. Bounds can be loose when
probability or recall inputs are correlated. Known zero reach yields exactly
zero immediate gain, with missing-evidence diagnostics still visible.
Matching non-point before/after intervals alone do not prove unchanged underlying
recall; their gain remains bounded rather than silently assumed zero.

## Ranking examples

| Card decision | Reach | Current recall | After recall | Marginal value |
| --- | ---: | ---: | ---: | ---: |
| Frequent deeper decision | 0.60 | 0.20 | 0.80 | 0.36 |
| Rare shallow decision | 0.05 | 0.20 | 0.80 | 0.03 |
| Mostly ready frequent decision | 0.60 | 0.75 | 0.80 | 0.03 |

The frequent deeper decision wins despite depth; an already ready frequent card
offers less remaining benefit. A two-decision prefix with reaches `1.0, 0.6`,
current recalls `0.2, 0.4` and after recalls `0.8, 0.9` gains `0.9`.
Binary floating-point may distinguish mathematically equal decimal examples by
tiny rounding differences; card-ID ties apply to equal computed values. There
is no hidden score rounding or cost/depth normalization.

## Future integrations and remaining questions

Related [#156](https://github.com/aaweaver-actuary/tempo/issues/156),
[#106](https://github.com/aaweaver-actuary/tempo/issues/106),
[#112](https://github.com/aaweaver-actuary/tempo/issues/112),
[#113](https://github.com/aaweaver-actuary/tempo/issues/113), and
[#118](https://github.com/aaweaver-actuary/tempo/issues/118) remain broader work.
This PR closes none of them; #156 also covers future shadow and production integration.

- [#116](https://github.com/aaweaver-actuary/tempo/pull/116) can later supply profile
  context/weights, immutable version IDs, freshness and unsupported-context
  diagnostics to an adapter. Probabilities still need source fusion; profile
  weights alone are not reply probabilities.
- [#117](https://github.com/aaweaver-actuary/tempo/pull/117) can supply bounded route
  occurrences, canonical positions, legal replies and repertoire/card relationships.
  Its traversal/persistence are not imported, copied or required by this kernel.
- #112 can provide bounded input snapshots, calibrated/current recall and projected
  intervention estimates. #113/#118 can use scores in shadow evaluation/frontiers.
  Source metadata and freshness are owned by those adapters; this kernel's result
  retains its math version and per-decision evidence, without inventing a second
  profile/cache/persistence contract. No integration is wired in this PR.

Open modeling questions: how well recall estimates predict games; how much one
study action improves recall; transfer between different expected responses;
learning-effort adjustment across card sizes; correlated recall and whole-horizon
preparedness; and frontier/exploration policy. These do not affect this version's
explicit computational contract.

## Validation

Named regressions live in `backend/tests/test_opening_preparedness.py` and are
registered in `tests/REGRESSIONS.md`. They cover hand arithmetic, monotonicity,
zero reach, bounded/invalid probabilities, missing mass, sharing/transpositions,
revisits, incomplete ranking, deterministic permutations, identity mapping and
immutable/dependency-free computation, required policy identities, mixed-policy
rejection, bidirectional Italian/Ruy isolation, opponent noncooperation and
independent cross-policy transposition mass. Existing identity, introduction and
queue-order suites protect neighboring contracts. Run from the isolated root:

```sh
make plan
make python-file FILE=backend/tests/test_opening_preparedness.py
make python-file FILE=backend/tests/test_opening_segmentation.py
make python-file FILE=backend/tests/test_introduction_priorities.py
make python-file FILE=backend/tests/test_daily_queue_randomization.py
git diff --check
```

CI owns current-candidate qualification. No local Docker/browser/visual/full
run is required for a disconnected pure module; no production scheduling code,
migration, dependency manifest or UI changes are part of this patch.
