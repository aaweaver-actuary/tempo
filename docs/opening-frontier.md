# Sequence-aware opening frontier (read-only)

## Original #151 test plan (historical)

Base: `4a222096f51f12d177687e97314f90a0d9063049`, October 10, 2026.
Checkout: `.dev-copies/opening-relaxed-frontier`; branch:
`codex/opening-relaxed-frontier`. These paths record the original task's
validation provenance; they are not current resource ownership records.

Change: a pure snapshot projection plus a runnable fixture diagnostic. Risks:
accepting disconnected/stale routes, skipping unexposed ancestors, combining
different transposed paths, duplicating shared cards, conflating introduction
with exposure, or treating a gameplay obligation as prerequisite knowledge.
Boundaries: published graph occurrences, existing card identity, saved study
reviews, lifecycle/safety metadata, and future scoring consumers. There are no
production consumers, database reads/writes, background handlers, or HTTP routes.

Smallest proof: `make python-file FILE=backend/tests/test_opening_frontier.py`.
Then run the existing opening graph and progression files, the fixture command,
and `git diff --check`. CI owns final required head/current-base qualification.
No local PostgreSQL, browser, visual, or full gate is required for this pure
feature; no runtime or performance claim is made from static inspection.

## Current gating audit

The audit reads current main, not the older premise in
[#118](https://github.com/aaweaver-actuary/tempo/issues/118) and
[#106](https://github.com/aaweaver-actuary/tempo/issues/106).
[PR #134](https://github.com/aaweaver-actuary/tempo/pull/134) merged on October 9,
2026 and already changed ordinary production unlocking to saved study exposure.

| Boundary | Current behavior |
| --- | --- |
| `opening_graph.decision_segments` / `build_graph` | One cumulative root prefix, then one learner decision per descendant segment. A descendant includes intervening opponent moves. Prefix overrides preserve explicit segment occurrences. |
| Published graph | Repertoire, generation, line, segment/learner indices, decision FEN keys and parent card identify route occurrences. Card identity hashes canonical starting FEN and moves; transposed paths may share a descendant with different parents. |
| `main._OPENING_UNLOCK_ELIGIBILITY_SQL` and sparse selectors | A current root or a child with **any immediate parent** having a non-invalidated study review may move from `locked` to `new`. No maturity check remains here. Candidate selection and bounded update recheck share `PRACTICED_OPENING_PARENT_SQL`. |
| `opening_progression.unlock_legacy_children_after_review` | Opening legacy pointers use completed study exposure only when no published graph owns the child. Non-opening pointers still use maturity. |
| `review_service` / `scheduler.unlock_ready` | FSRS maturity still uses stability >= 14, at least three successful review days, and two recent outcomes without `again`, including existing schedule-seed handling. This model neither reads nor changes those calculations. |
| `main._plan_prioritized_opening_admissions` / `postgres_queue_refresh` | Actual admissions, per-repertoire allowances, global shared-card selection, priority scoring, durable queue work and independent display ordering remain unchanged. |
| Real-game evidence | Current outstanding misses and approved weak-known-decision opportunities have separate admission paths, including locked cards. Misses are obligations, not fabricated reviews. This projection only carries their caller-qualified annotations. |

The projection is relaxed relative to the historical maturity gate. Its complete
route guard is intentionally more explicit than current production's immediate
parent predicate: a reviewed intermediate card cannot conceal an unseen earlier
ancestor. No production predicate is replaced by this PR.

## Contract and rationale

`project_opening_frontier(OpeningFrontierSnapshot)` consumes immutable records and
returns an immutable `OpeningFrontier`. It has no production callers and performs
no I/O, current-time lookup, admissions, ranking or probability acquisition.
The snapshot ID and repertoire publication generation/scope digest are preserved.

A real persisted review with `source_kind='study'` and no invalidation marks its
card presentation as exposed. Correct, failed and guided practice all count.
This binary historical fact establishes encounter, not reliable recall of every
move. Requiring success counts, stability, rating, recency or elapsed days would
introduce another readiness threshold. Difficulty belongs to future value/readiness
scoring. Introduction, pending/partial evidence, queue placement, `first_correct_at`
and schedule-seed estimates are not substitutes for a saved review.

For each current route occurrence, all preceding cards must have exposure.
One structurally valid, fully exposed route suffices; every sibling and every
alternative transposed route need not be exposed. All checks use actual snapshot
evidence, so marking a candidate eligible cannot expose its children in that call.
Actual reviews may be reused across current repertoires for the same card identity.
Exposure from different alternative routes is never spliced into a synthetic path.

| Classification | Meaning |
| --- | --- |
| `structurally_unreachable` | No valid current root-to-occurrence route; reasons identify missing roots/cards, malformed links, board/decision discontinuity, invalid moves, conflicts or stale scope/publication. |
| `prerequisite_unseen` | At least one valid route exists, but each is missing exposure for an earlier card. Per-route missing card IDs explain the block. |
| `eligible` | At least one valid route has all prerequisites exposed, or the card is a valid root with no prerequisites. |
| `already_introduced` | An active scoped card has a recorded introduction; later ancestor failure or invalidation cannot revoke it. Route warnings remain visible. It is absent from new-candidate IDs. |

Lifecycle/safety exclusions have `status=null` and explicit `exclusion_reasons`:
archived, deleted, superseded, pending validation, missing card, outside published
scope, or integrity blocked in every applicable repertoire. Per-repertoire
integrity blocks invalidate only those routes. Exclusions take precedence even
for previously introduced cards. An unavailable prerequisite invalidates that
route while other valid incoming routes may still qualify.

### Structural and edge-case behavior

- Validate legal segment content, existing card hashes, trained color and decision
  FEN keys. Root segment/learner indices begin at zero; subsequent segment indices
  and learner ranges are contiguous, and each parent is the preceding occurrence.
  Adjacent canonical board positions must agree; move counters do not affect identity.
- An imported route may explicitly start at a valid custom position. The model
  does not invent prerequisites before that declared root. Zero-card routes produce
  no candidates; short prefixes have no minimum length beyond a learner decision.
- Traverse finite ordered occurrences, not a global card-ID DAG. Legal repetitions
  may revisit an identity; shared cards are returned once with all route assessments.
  Repeated identical input copies collapse, independent of occurrence order.
- Changed generations/scope digests reject old routes. Re-imports may reuse genuine
  reviews for unchanged identities; shortened/replaced prefixes do not inherit
  synthetic exposure. Deleted-gap/reparented routes fail the complete-sequence guard
  unless the supplied current route actually contains continuous prerequisites.
  Existing deletion/unlocking behavior is unchanged.
- Conflicting publication, card or review records raise actionable `ValueError`.
  Conflicting representations/slots of a route are reported as structural blocks.
  An empty snapshot produces an empty frontier.
- Snapshot callers must supply coherent current scope, normalized card memberships
  (including applicable owner fallback), real saved review rows and qualified
  obligations. This pure function does not certify a live publication, resolve raw
  game evidence, or infer content-revision validity; invalidated evidence must be
  marked invalidated. Frozen types require tuple collections.

## Runnable diagnostic

From the checkout root, after setting up the documented backend environment:

```sh
backend/.venv/bin/python scripts/show_opening_frontier.py
```

The command constructs explicit fixture graph inputs with the existing pure graph
builder and prints deterministic JSON with labels, classifications, candidate
identities and route/review provenance. It connects to no database or service.
It is a demonstration, not a report about the live repertoire.

| Example | Expected result |
| --- | --- |
| Guided/failed short-prefix practice, still learning | `already_introduced`; its next continuation is `eligible` |
| Next unexposed prerequisite / deeper sibling | `prerequisite_unseen` |
| Shared descendant with one practiced transposed prefix | One `eligible` card with two assessed routes |
| Descendant whose root occurrence is missing | `structurally_unreachable` |
| Deep real-game miss with unseen prerequisites | Obligation annotation retained; `prerequisite_unseen` |

Stable output order is serialization order, not a pedagogical preference.

## Future integration boundary

A scorer takes `eligible_card_ids` and each card's `qualifying_routes`, including
publication, trained color, segment/learner decision indices, prerequisite
identities and review witnesses. Cumulative-prefix depth remains explicit in the
route's first/last learner decision indices.
It independently computes probability/marginal value and soft prerequisite recall.
Scoring must retain the selected learner repertoire-choice policy: shared card
identity does not imply a shared choice denominator or cross-repertoire weight.
This eligibility projection supplies scoped routes and never infers those weights.
The original `project_opening_frontier` has no score/probability fields or ranking
inputs. The policy-scoped API below carries supplied valuation evidence. Neither
API depends on PR #116/#117. A future live adapter must load a bounded coherent snapshot, close
its connection before computation, and separately enforce admissions/quota/attempt
contracts at publication time; this PR adds no adapter or background handler.

## Policy-scoped frontier contract (#118)

`project_policy_opening_frontier(snapshot, *, policy, route_bindings,
valuation_inputs=())` adds the missing policy boundary while retaining the
original projection unchanged. `policy` is the existing #170 `LearnerPolicy`.
Each `OpeningPolicyRouteBinding` selects an existing publication (repertoire,
generation, scope digest) and line. Bindings must resolve current snapshot routes.

Selection precedes complete-route exposure aggregation. A shared card eligible
through Italian cannot become eligible under Ruy through an unseen Ruy parent.
Within the selected policy, one valid fully exposed route still suffices; routes
cannot splice prerequisite exposure. Real saved failed/guided practice counts,
and introduction, queue placement, maturity and valuation never create exposure.

Every learner action in selected segments is checked against the explicit policy.
Missing actions, divergent actions and mismatched trained color create explanatory
route blocks that propagate to descendants. Already-introduced cards retain the
original classification and route warnings. Lifecycle, integrity and structural
checks continue to use the existing complete-route classifier.

The frozen `PolicyOpeningFrontier` retains the complete selected policy, source
snapshot, `version=policy-sequence-frontier-v1`, `exposure_version`, publications
and bindings. Each card has `eligibility` (the original route/review diagnostics),
`decisions` and `prerequisites`. Decision identities use the existing
`decision_identity` helper with the selected policy version. Stable card order
is serialization order; `eligible_card_ids` provides no preference score.

`OpeningFrontierValuationInput` carries snapshot ID, policy ID/version, card and
decision IDs, publication/line bindings, existing `ProbabilityEvidence` reach and
readiness, and immutable source-provenance strings. Values must be conditioned on
the selected policy before being supplied. They must bind a structurally valid
selected route, although its prerequisite exposure may still be incomplete.
Foreign/stale identities, missing source references, invalid bindings and conflicting
records raise `ValueError`; exact duplicates collapse. The caller explicitly
selects one input per card/decision when multiple evidence sources disagree.

Known values, uncertainty bounds and reasons pass through unchanged. Missing
inputs become `ProbabilityEvidence.unknown('missing_reach')` and
`ProbabilityEvidence.unknown('missing_readiness')`; no zero/midpoint fallback is
used. Candidate and prerequisite readiness remain separate from sequence eligibility.
Prerequisite rows retain their card and route bindings, so consumers must use the
qualifying route rather than treating their union as a synthetic exposed path.
Card-level cumulative-prefix recall is not copied into individual decisions.

Use #170's already propagated decision probabilities and #153's already derived
decision-level readiness (including its unknowns) to supply these records.
Source-provenance strings retain their versions, context and fixed-as-of references.
This API performs no propagation, readiness estimation, preparedness calculation or
ranking. #149 owns that mathematics; pending #176 owns valuation orchestration.
Its separate policy-version and publication/line inputs can consume this contract
upstream without an import of unmerged code or a competing scoring implementation.

Caller-qualified durable game obligations remain scoped annotations. A nonempty
durable source ID is required, but the pure API does not certify raw game evidence
or change existing gameplay admission exceptions. Snapshot callers also certify
review validity; invalidated reviews must be marked invalidated.

### Acceptance-criteria ownership

| #118 criterion | Existing coverage / this change / remaining owner |
| --- | --- |
| Learning or failed-practice parent permits progression; queued-only parent blocks | #134 production exposure and #151 complete-route exposure already satisfy this; policy wrapper regressions retain it. |
| One valid route; shared cards, multiple parents, transpositions, re-imports and invalidation | #151 already supplies these guards. This change fences their aggregation to explicitly selected policy routes. |
| Deep common decisions can outrank shallow rare decisions, without equal-depth rules | #149/#170 already supply mathematics/reach. This change passes supplied values unchanged; a real-foundation fixture reverses ordering through #149 when supplied reach reverses. |
| Weak/unknown prerequisite knowledge stays separate from maturity/exposure | #153 supplies readiness. This change exposes candidate/prerequisite decision inputs and preserves unknowns; sourced intervention assumptions and remediation valuation remain #112/#176 work. |
| Candidate, selected policy, blocked reason, generation, evidence and valuation provenance | #151 supplied exposure provenance. This change completes the independent policy-scoped input/output contract. |
| Deterministic, immutable and read-only | #151 already covered the original API; named regressions cover the new boundary, detached collections and forbidden storage/network/clock/scheduling access. |
| Global capacity, actual study pauses/attempts and production migration | #158/#159/#113/#114 retain ownership; this projection applies no quotas or admissions. |

The smallest future #114 cutover consumes a bounded coherent snapshot, validates
its current publication/evidence again at admission, and retains durable queue,
active-attempt and pause protections. #134 already changed immediate-parent
production unlocking; it must not be rebuilt. #113 must qualify the complete
allocation policy before #114 adopts it. This independent frontier contract does
not close #118's remaining dependency-owned requirements.

Development proof is the named cases and complete
`make python-file FILE=backend/tests/test_opening_frontier.py`, then the affected
reach/preparedness/readiness/progression files. CI owns current head/base/integration
qualification. No live reads, persistence, HTTP/UI, background handler, or deployment
is added. Diagnostics and exact timed commands are preserved outside the checkout
under `test-results/2026-10-11-issue-118-policy-frontier/`.

References: [#112](https://github.com/aaweaver-actuary/tempo/issues/112) owns
readiness/value calculations, [#113](https://github.com/aaweaver-actuary/tempo/issues/113)
owns shadow comparison and [#114](https://github.com/aaweaver-actuary/tempo/issues/114)
owns a guarded admissions cutover. #118 remains open because its broader score,
evaluation and integration acceptance criteria are not completed by this model.

## Validation evidence

The first new-feature run passed 60 cases (1.45 s pytest; 5.32 s command wall time)
on the dirty base plus the initial model, tests and diagnostic. No previously
existing production defect was changed; this is a new contract rather than a
claim that historical maturity-gated admissions were repaired here.
Final focused evidence on the settled model/test/diagnostic source (macOS arm64,
CPython 3.14.8, Node 26.10.0; pinned Python environment installed once):

| Command | Result | Observed wall time |
| --- | --- | --- |
| `make python-file FILE=backend/tests/test_opening_frontier.py` | 62 passed; 0.48 s pytest | 1.01 s |
| `make python-file FILE=backend/tests/test_opening_graph.py` | 24 passed; 23.34 s pytest | 24.92 s |
| `make python-file FILE=backend/tests/test_opening_progression.py` | 15 passed; 3.03 s pytest | 6.84 s |
| `backend/.venv/bin/python scripts/show_opening_frontier.py` | Valid deterministic JSON; four unique eligible card IDs, ten classified cards, one obligation annotation | Not separately timed |

The final frontier run includes assertions preserving trained color and learner
decision ranges in qualifying route output. The new descendant-prefix malformed
occurrence case first failed (eligible instead of structurally unreachable); the
model now requires each non-root occurrence to be a one-decision segment. The graph/progression source and its
existing fixtures did not change. Those runs retain existing library deprecation warnings. These
are 101 focused cases, not a full gate or performance measurement. The tests ran
on the dirty implementation before its commit; model/test/diagnostic SHA-256
provenance and raw logs are preserved outside the clone in
`test-results/2026-10-10-opening-frontier/`. Subsequent prose/registry updates do
not change those tested executable inputs. `make plan`/`make plan TIER=python`
were inspected. Diff/conflict checks and current-candidate CI are recorded in the
PR delivery evidence; required qualification is still pending at draft creation.

Candidate CI follow-up: run `38048686427` passed frontend/backend/build,
PostgreSQL durability, lifecycle and pinned visual/performance verification, but
the existing repertoire-limit browser test saw two cards after saving a limit of
one. The trace showed an unfinished initial queue publication and the same
`refreshing` generation throughout its observations. A separate test-only repair
marks those observations as background work and requires the real ready
publication alongside every existing count assertion. It changes no product
code, count assertions or deadlines. Local Docker inspection timed out; final required
CI owns real PostgreSQL/browser proof for the repaired candidate. Static repair
checks passed: `npm run lint` (28.64 s, zero errors and ten existing warnings),
`npm run typecheck` (17.09 s), `npm run check:conflicts`, and `git diff --check`.

A second full candidate run, `38051747505`, passed the repertoire-limit case
and 266 of 267 browser cases, but the existing Issue135 idle-worker proof stopped
periodic polling before fixture generation 44 had finished. A second test-only
repair establishes initial publication, includes scheduled ETA deliveries in the
existing idle check, and proves a newer committed generation. It retains the
foreground save/read, idempotent replay, stopped scheduler, and original
30-second post-commit deadline. Setup shares its original 20-second budget.
`npm run lint` passed (57.44 s; zero errors, ten existing warnings), and
`npm run typecheck` passed (41.13 s).

Once Docker was responsive, the unique focused case ran with
`make view 'VIEW=Issue135 foreground queue commit wakes an idle worker without periodic polling'`:
one passed (15.3 s browser execution; 93.02 s command wall time), using real
isolated PostgreSQL on macOS arm64, Node 26.10.0, Docker 29.8.1, Chromium 1243.
Its tested revision was `a9f7a2f` plus the exact test-only dirty patch; source
hashes and raw logs are retained in the dated evidence directory. This was a
focused fixture repair proof, not a full local gate. CI owns the final candidate.

Node dependencies were added only for the browser-test repair's static checks.
Disposable project `tempo-pg-regressions-92456-86d6ee1b` was owned by that focused
run. Runner teardown passed (8.55 s); exact project container/image/volume
inventories were empty afterward. Timings, resource names, ownership and teardown
provenance are preserved outside the clone. The initial inventory retained other
tasks' checkouts/resources because release/deletion eligibility was not
established. The owned checkout and Python/Node caches remain for PR review,
with cleanup after merge and preservation/activity checks. Live study state was
not changed.

Latest-main reconciliation: run `38054773275` executed all seven mandatory layers
successfully on head `3243a2e`, base `d046a21`, merge `638ac46`: 1,951 backend
cases (all 62 frontier), 1,428 frontend, 267 browser and 66 pinned cases, plus
Rust/build, durability and lifecycle. Aggregation refused qualification when main
advanced through PR #147 (browser CI) and #149 (pure preparedness). These are
historical runtime passes, not a current-base qualification claim.

Merge `c97951b` integrates main `cbc77ec`; only the overlapping queue-fixture
helper conflicted. The resolved file retains ready/pending-zero and count
assertions together at each transition, preserving the main branch's initial
publication precondition. Frontier/graph/progression executable inputs are
unchanged. On that clean merge revision, the frontier reran (62 passed, 0.48 s
pytest / 1.01 s wall), `node --test tests/runner/ci-browser-shards.test.mjs`
passed 18 cases (11.11 s wall), lint passed (32.20 s; ten existing warnings), and
typecheck passed (18.57 s). The post-merge `make plan` was inspected.

The combined unique-title `make view` filter for the queue-limit and Issue135
idle-worker cases passed 2/2 (41.2 s browser execution; 84.47 s command wall time)
against the updated disposable runner. Its exact command and source hashes are
in `latest-main-two-browser-source.json` in the dated evidence directory.
Project `tempo-pg-regressions-22026-e76dfa0a` teardown passed (7.35 s), and exact
container/image/volume inventories were empty. New current-head/current-base CI
must qualify this integration; no broad local gate or performance claim is made.
