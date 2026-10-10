# Explicit policy-conditioned opening reach

`app.services.opening_policy_reach` bridges the opponent evidence contract (#154)
and the preparedness kernel (#149). It calculates
`P(reach position/decision | learner follows selected policy)` from finite,
caller-supplied snapshots. It has no production callers or storage, network,
clock, scheduler, eligibility, admission, or UI capability.

## Inputs

`propagate_policy_reach(policy, routes, opponent_evidence, *, context)` accepts:

- `LearnerPolicy`: one nonempty policy identity, repertoire identity, trained
  color, decision identity policy version and `LearnerPolicyAction` tuples.
  Actions prescribe one legal UCI move per canonical learner position. Policy
  identity can differ from repertoire identity for explicit experiments.
- `PolicyReachRoute`: selected policy identity, published line/publication
  identity and complete UCI prefix from the common root. These are finite route
  snapshots, not graph/database objects. A later adapter can reconstruct complete
  prefixes from existing graph segments while retaining publication provenance.
- `PolicyReachContext`: one context ID, root FEN, root `ProbabilityEvidence`
  (default exact one), and canonical JSON for the already-selected opponent
  target context. Root mass must already be conditioned on this policy and must
  not encode repertoire-selection probabilities. Multiple roots or overlapping
  profile scenarios must be evaluated separately; this API does not aggregate
  them or assert their exclusivity.
- `SelectedOpponentEvidence`: the selected #154 `OpeningMoveEvidence` or
  `FusedMoveDistribution`, context identity, evidence identity, and optional
  `(move_uci, ProbabilityEvidence)` sensitivity bounds. For a source the ID must
  match its evidence ID; fused outputs receive an explicit caller ID. A fused
  target context must match the selected context. Source cohort metadata is
  preserved, not reinterpreted as a profile selection rule. The caller owns
  selection of source/cohort/profile and freshness suitability.

Every supplied route/action must belong to the selected policy, including
otherwise unused inputs. Foreign routes fail before chess replay or propagation.
Conflicting prescribed actions, line/publication representations or selected
opponent snapshots fail rather than overwriting evidence. Exact duplicate inputs
collapse. Illegal routes fail. No learner action is inferred from authored routes.

Nested source/provenance/cohort/quality/version metadata is detached at evidence
selection through #154 canonical serialization. `snapshot()` returns a new
validated projection; mutating that projection cannot change the input/result.
Collections owned by this library are tuples; public records are frozen.

## Probability semantics

At a learner position the prescribed action contributes weight one. Italian
`Bc4` does not receive a factor based on the existence, size or frequency of Ruy
`Bb5`, Scotch `d4`, cards or imported lines. The caller selects one policy before
constructing inputs; passing a mixed-policy collection is an error.

Opponent moves retain their absolute conditional probabilities. Selecting
Italian does not force `...e5` or `...Nc6`. Sequential factors are multiplied by
#149 `route_probability`; there is no normalization, floor or independence
assumption about uncertainty. These are conditional probabilities along the
observed history, modeled by the supplied position/context evidence.

#154 numeric assigned replies remain exact supplied estimates, not claims of
calibration. Missing/unavailable source evidence is unknown. A `None` reply is
an unknown allocation of #154's `unknown_mass`, conservatively bounded between
zero and that mass. Diagnostics retain the original unknown entry and bucket;
a narrowed interval is still incomplete evidence, including a collapsed interval.
A fully unknown reply retains `[0,1]` input evidence; #149 expresses derived
incomplete products as bounded evidence with the contributing reasons.

Optional sensitivity intervals constrain only unknown replies, are explicitly
linked to the selected snapshot and move, and must permit allocating its total
unknown mass. They cannot overwrite an assigned number or turn an unknown into
an exact estimate. Unknown allocations share a mass budget; conservative interval
products/unions may be loose, and no independence between intervals is asserted.
Opaque #154 quality metadata is retained but never parsed as numerical bounds.

The strict #149 probability domain rejects a #154 tolerance-edge assigned scalar
above one. There is no silent clipping of supplied probabilities. Disjoint exact
unions must also satisfy #149's mass tolerance; inconsistent evidence is an error.

For each traversed opponent position, diagnostics retain every legal reply,
assigned covered mass, assigned outside-route mass and unassigned mass separately.
For `.60 + .20` represented replies, `.10` outside and `.10` unassigned, covered
mass remains `.80`. A deviation's assigned mass remains present even when there
is no authored continuation; no downstream position or learner action is invented.
Coverage is membership in the supplied route snapshot, not all persisted material.

A missing current learner action is flagged at the visited position. Reach of
that position is still identifiable from previous actions. Terminal positions have
no required action and create no learner decision. Crossing the missing
action makes downstream paths unresolved. Later decisions have unknown union
reach and `kernel_routes()` raises rather than exporting an unjustified sum.
The missing action itself has no invented decision identity or prescribed move.

## Paths, positions and decisions

`PolicyReachResult` retains selected policy/context/root, detached target-context
JSON (including caller-supplied profile versions), model version, every
visited `PathReach`, unique canonical-position summaries, unique learner-decision
summaries and opponent diagnostics. Path records contain actual #149 `RouteReach`,
policy-action/decision identity, opponent factors, unresolved policy positions,
and sorted line/publication representation provenance.

Reach summaries describe the union of the supplied finite route events, not an
exhaustive probability of visiting a position over an unlimited game. Unrepresented
legal replies remain outside/unassigned diagnostics; this module does not assume
that no unrepresented continuation can later transpose into a decision.

A prefix is an event from the common root, never a card-local root. Identical
prefixes collapse even under different imported line IDs. A longer revisit under
an earlier prefix is a subset of that earlier event and is absorbed in the union.
These rules apply independently to canonical positions and decision identities;
path diagnostics still expose the revisit.

For two non-nested complete legal prefixes, replay gives an identical board at
the first differing move. A resolved deterministic learner policy permits only
one learner action there. Consequently their first difference must be an opponent
move and their events are mutually exclusive. Only after that invariant is
established by action validation can #149 `decision_reach` add their masses.
The transposition regressions exercise both mutually exclusive opponent paths
and ambiguous paths crossing missing learner actions. Unresolved unions stay
unknown; they are neither summed nor treated as independent.

Decision identity reuses `opening_segmentation.decision_identity` with repertoire,
canonical position, trained color, expected UCI and policy version. Event identity
additionally retains explicit selected `policy_id`; equal decision IDs in two
policy experiments never combine their reach mass.

## Preparedness integration and hand arithmetic

For a resolved decision, use its `identity` and `kernel_routes()` to construct
#149 `DecisionReadiness` with caller-supplied recall and the result's `policy_id`.
Unknown opponent probabilities are valid incomplete kernel inputs; unresolved
learner-policy paths refuse conversion. This module does not estimate recall or
copy preparedness/marginal-value calculations.

The regular integration fixture follows learner `e4`, opponent `...e5=.42`,
learner `Nf3`, opponent `...Nc6=.50`, learner `Bc4`, opponent `...a6=.25` to the
next learner decision `d3`. Its conditioned reach is `.0525`. Current recall
`.20` contributes `.0105` preparedness; projected recall `.80` yields marginal
value `.0525 * (.80-.20) = .0315` through the actual #149 kernel and ranking API.

## Validation and next adapter

Named tests in `backend/tests/test_opening_policy_reach.py` are collected by the
regular backend suite and registered in `tests/REGRESSIONS.md`. This new capability
has no previous propagation implementation against which to reproduce a failing
baseline. Tests cover policy isolation in both directions, learner weight one,
known opponent deviations, missing mass/evidence/actions, intervals, transpositions,
revisits, ambiguous unions, black learner/custom root, provenance detachment,
foreign identities and deterministic results plus actual #149 scoring/ranking.

After #155 lands, the smallest adapter joins these policy-scoped decision routes
with readiness and explicit projected learning effects, invokes #149, and supplies
scores/diagnostic provenance to its selected-policy evaluator. #153 readiness is
separate evidence, not a reach factor. #151 eligibility and game obligations remain
separate. #116 may select future cohorts before this API; #117 may supply future
position/reply identities. Neither fetching nor either unmerged implementation is
required here. Related #169, #156, #112 and #113; #156's broader shadow/production
requirements remain open. No cross-repertoire `P(R)` or learning-priority weights
are computed by this layer.
