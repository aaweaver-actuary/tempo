# Sequence-aware opening frontier (read-only)

## Test plan recorded before implementation

Base: `4a222096f51f12d177687e97314f90a0d9063049`, October 10, 2026.
Checkout: `.dev-copies/opening-relaxed-frontier`; branch:
`codex/opening-relaxed-frontier`. This checkout belongs to the relaxed-frontier
task and remains preserved until its PR merges.

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
This module has no score/probability fields, ranking inputs or dependencies on
PR #116/#117. A future live adapter must load a bounded coherent snapshot, close
its connection before computation, and separately enforce admissions/quota/attempt
contracts at publication time; this PR adds no adapter or background handler.

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
| `make python-file FILE=backend/tests/test_opening_frontier.py` | 62 passed; 1.03 s pytest | 1.85 s |
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
