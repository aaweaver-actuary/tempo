# Read-only selected-policy opening-card valuation

Issue #174 integrates the merged #154, #170, #153, #149 and #155 contracts.
`app.services.opening_card_valuation.evaluate_opening_card_valuation(snapshot)`
returns an actual ranked candidate comparison with a digest-bound #155 snapshot.
It is a pure Python call: no database, source fetching, live capture, admission,
queue modification, background work, API/UI, or scheduler mutation.

## Input and identity boundary

`OpeningCardValuationSnapshot` is frozen and versioned
`selected-policy-card-valuation-v1`. Supply:

- One #170 `LearnerPolicy` (policy ID/version, repertoire ID, color, explicit
  deterministic actions), `PolicyReachContext`, finite `PolicyReachRoute` tuples,
  and caller-selected #154 snapshots wrapped in `SelectedOpponentEvidence`.
- `DecisionReadinessBinding` tuples identifying the selected policy/version,
  versioned decision ID, real #153 `ReadinessSnapshot`, and decision index.
  Exact duplicate bindings collapse; conflicting sources for the same decision
  require an explicit caller selection rather than an invented aggregation.
- `ValuationCandidate` tuples containing #155 `OpeningRankingCandidate`, policy
  version, card revision, unique affected-decision IDs, exact route references
  `(publication_id, line_id)`, optional #149 `CardLearningEffect`, and an explicit
  versioned effect source/reference. The affected set is required independently
  of available projections, so omitted effects cannot silently shrink the score.
- A fixed timezone-aware evaluation timestamp and explicit study date. Complete
  production ranks cover the supplied candidate set consecutively from one.

Collections are detached into immutable tuples and canonical order. Readiness
evidence collections are detached recursively; opponent evidence is already
detached by #170's #154 serialization. Returned JSON is a detached projection.

The caller supplies the candidate set; the adapter does not assert eligibility.
Existing #151 `OpeningFrontier` / publication evidence may be carried in candidate
metadata. Reuse `project_opening_frontier` upstream when candidates need deriving;
never infer exposure, maturity or eligibility from valuation. Metadata can retain
the existing #155 memberships/routes for concentration reports, but foreign
repertoire metadata is rejected. Do not label unavailable metadata as observed.

Physical canonical positions may be shared. Learner decisions include repertoire,
color, required action and policy version; selected policy identity is separately
fenced and may differ from repertoire identity. Card IDs/revisions identify
interventions; publication/line IDs identify route representations, not new
probability events. The adapter validates all supplied identities before scoring.

#153's presentation decision identity currently uses the existing decision identity
version, whereas #170 uses the selected policy version. The adapter validates the
presentation geometry and selected action using `presentation_occurrences` and
`decision_identity`, then binds the existing readiness estimate to the versioned
reach decision. Both IDs remain in provenance. It does not relabel or manufacture
decision-specific observation evidence. A different action at the same position
is a different decision and cannot donate readiness.

## Computation and uncertainty

1. Validate all policy/context/presentation/effect/candidate/publication inputs.
2. Call `propagate_policy_reach` once. Learner actions are deterministic; opponent
   factors retain their absolute probabilities and outside/unassigned mass.
3. Call `decision_readiness(..., decision_index=...)` for each bound unique reached
   decision. Use the decision-level readiness score only; prefix `card_estimate`
   remains correlated card context. Missing memory is `ProbabilityEvidence.unknown`.
4. Pass #170's `UniqueReach.kernel_routes()` to #149 `DecisionReadiness` and
   `preparedness`. Unresolved actions cannot be exported as valid kernel routes.
   The baseline explicitly covers resolved unique decisions; overall completeness
   also requires exact baseline recall/reach and no unresolved required actions.
5. Evaluate each supplied intervention through #149 `marginal_card_value`, once
   per card with its unique affected decisions. Missing projections become unknown
   projected-readiness inputs with named reasons, including empty/missing effects.
6. Pass exact values or explicit unscorable reasons through #155 `ScoreResult`,
   `rank`, and `compare`. Ties use production rank then card ID, as #155 defines.

Scores are marginal **expected correctly recalled unique decisions**, not the
probability of surviving the entire opening or measured learning improvement.
Candidates are independent interventions against one fixed baseline. No greedy
top-K reselection, inferred repertoire weights or global allocation is performed.

Bounded/unknown evidence retains kernel bounds, contribution diagnostics and
source reasons. It receives `score=null`, `alternative_rank=null`; no zero or
midpoint fallback. Missing reach and unresolved actions retain explicit incomplete
results. Invalid identities or regressive-only effects fail rather than become a
successful ranking. #153 conservative failure can legitimately supply exact zero;
that differs from absent evidence.

## #155 replay and Agent 5 interface

The result is #155's schema-2 selected-policy comparison with `snapshot_id`, fixed
time, source versions, scorer configuration and an embedded `snapshot`. The
snapshot is produced by `snapshot_document`; `load_snapshot` verifies its digest.
Candidate `metadata.valuation` contains the value or incomplete result, kernel
bounds/contributions, reach paths/opponent factors, readiness estimates, effect
reference, revisions, and diagnostic reasons. Common inputs, reach diagnostics,
baseline preparedness and all model/contract versions are in scorer configuration.

`PrecomputedValuationScorer` implements #155's `OpeningCardScorer` protocol.
`replay_opening_card_valuation(document, top_k=(1, 5, 10))` reproduces the comparison
without recomputing memory, fetching evidence, or accessing a database. This is
precomputed-score replay; changing projected learning assumptions requires a new
adapter evaluation and digest. The inherited #155 historical limitations remain.

For the independent harness, `scorer_configuration` also exports:

```text
baseline_bundle: production-order@1
precomputed_score_bundles:
  [{name, version, axis, cohort_id, assumption, provenance,
    scores: {card_id: {score: finite_number_or_null, reason: required_if_null}}}]
```

The production bundle uses #155 `ProductionOrderScorer`; the valuation bundle
uses the actual #149 result. Both cover precisely the same cards. Agent 5 has
communicated this extension independently; no direct interface agreement is
claimed. Agent 1 owns valuation and its direct tests. Agent 5 owns the corpus,
comparison/sensitivity harness, harness test file and existing CLI extension.
No Agent 5 files are changed here, and no unmerged harness import is required.

Agent 5's eligible-new-card corpus additionally requires explicit captured
candidate metadata `state=new`, `introduced_at=null`, `archived=0`,
`pending_validation=0`. The adapter preserves supplied metadata rather than
inventing those facts. Its direct synthetic fixture supplies them explicitly.
Real-data consumers must verify that metadata upstream before corpus import.

## Hand-calculated end-to-end proof

The direct regression uses real #154 synthetic opponent evidence and real #170
Italian reach. Opponent factors `.42 × .50 × .25` give deep reach `.0525`;
an earlier learner decision has reach `.21`. Real #153 unresolved-failure study
evidence on independently bound one-decision presentations supplies current recall
zero. Projected readiness is explicitly supplied hypothetical fixture evidence.

| Candidate | Effect | Marginal expected correct decisions | Production rank | Alternative rank |
| --- | --- | --- | --- | --- |
| multi | Earlier recall .6; deep recall .8 | `.21 × .6 + .0525 × .8 = .168` | 1 | 2 |
| single | Earlier recall .9 | `.21 × .9 = .189` | 2 | 1 |

Spearman correlation is -1 and top-1 overlap is zero. These are comparisons of
rankings, not observed training outcomes. Adding Ruy does not change Italian,
and adding Italian does not change Ruy. Duplicated/shared incoming representations
do not duplicate readiness or marginal contributions.

The next real-data step is a bounded read-only exporter of verified selected-policy
routes, decision bindings, eligibility/publication metadata and explicitly sourced
learning effects into this interface. When effects are unavailable, export honest
unscorable candidates. Agent 5 can then run offline comparison/sensitivity trials;
production scheduling remains outside this milestone.
