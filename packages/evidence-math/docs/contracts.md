# Mathematical contracts and ownership

Source baseline: main d0485d9fdfb85b4fb6c8de2c403f02d87021817d. This is an
independently installable extraction, not production adoption. Legacy modules remain
unchanged. No database, API, scheduler, provider, model, publication or admission
capability is part of this library.

Public root exports are enumerated in `tempo_evidence_math.__all__`. Dataclass
annotations and Pydantic fields define the typed contract. Source models retain
opening probability wire v1 and shadow wire schema 2 (schema 1 is global-only).
Preparedness is `expected-correct-decisions-v1`; reach is `explicit-policy-reach-v1`.
New memory projection/graph/scorer contracts are version 1. Readiness is version 2;
valuation is `selected-policy-card-valuation-v2` because explicit memory projections
replace internal FSRS decoding and valuation now returns an immutable result, not a
comparison report. The calculation result exports a digest-bound ranking snapshot.

CanonicalObject stores only immutable canonical JSON text; nested mappings and
sequences expose frozen views. Serialized wire objects are unchanged, and ordered
opaque context arrays retain order. Metadata accessors return detached projections.
Contract construction rejects foreign identities, conflicting duplicates, unsupported
versions, illegal moves, impossible mass, booleans/nonfinite/oversized numeric input.
Do not treat a frozen Pydantic model's unchecked `model_copy(update=...)` as validation;
accept external data with model_validate. All calculations use aware explicit time.

The standard-library arithmetic module preserves existing bounds and event-union
semantics. Duplicate events and later revisits are absorbed; transposed disjoint
incoming events add once per unique decision. Finite graph routes never become an
infinite-horizon hitting probability. Policy identity differs from physical card,
decision, route, occurrence and publication identity. Decision hashes retain repertoire,
trained color, canonical legal-en-passant FEN, expected move and policy version.
Physical card IDs are supplied facts, never generated from presentation counts.

A selected learner action contributes no inferred probability. Italian Bc4 and Ruy
Bb5 remain distinct policies. Opponent deviations retain their conditional mass;
unauthored/unassigned replies are never renormalized away. Exact supplied estimates
are not calibration claims. Unknown/bounded/unavailable evidence retains reasons,
cohort/source/normalization/freshness lineage, even at zero reach. Staleness remains
explicit; caller selection owns suitability and no clock refreshes it. Fusion validates
a supplied strategy and lineage; this package provides no universal fusion algorithm.

MemoryProjection supplies target presentation, explicit as_of, value or unavailable
reason, memory reference, raw-state status/digest and estimator/parameter identity.
Learning/scheduling owns serialized-state validation and FSRS estimation. No private
scheduler API, hidden defaults, forgetting curve, Card construction or review replay
is copied here. Before production adoption the owner must publish a supported
projection/export API and prove FSRS 6.3.2 parity. See adapter-test-ownership.md.

Readiness preserves same-time failure conservatism, clean-study recovery, guided and
assisted exclusions, invalidation/revision fences, future-evidence exclusion and
coarse inherited evidence. Cumulative-prefix memory is correlated card context, not
independent per-move practice. Game success creates no FSRS credit; an observed miss
may support conservative zero. The binding retains original readiness and verified
policy-version decision identities without relabeling unrelated evidence.

Preparedness sums expected correctly recalled unique decisions and can exceed one.
Marginal card values are independent hypothetical interventions against one fixed
baseline. Example: reaches .21 and .0525, current recalls zero; effects (.6,.8)
yield .168 while an independent .9 effect at the earlier decision yields .189.
Neither number establishes observed learning gain or whole-opening survival.
Arithmetic ties use equal computed value then card ID; evaluator ties preserve
production rank then ID. No hidden rounding is added.

No existing global study-weight aggregation was found. This package intentionally
has no repertoire-choice probability, study-weight optimizer, budget allocation or
greedy top-K replanning. Positive scaling of evidence-normalization weights is a
separate property, not qualification of future M06 study allocation.

The isolated legacy coverage helpers remain with their current owner as historical
`legacy-coverage-selection-v1`: sample-weighted blending, positive-candidate
renormalization/thresholds, authored discovery and operational path-floor/fallbacks.
They are not the new explicit-uncertainty model. C3's classification/ranking remains
C3-owned; future exchange must use documented snapshots, not private imports.

Scorer export freezes reserved production-order@1 as the evaluator comparator from
captured ranks. Supplied valuation bundles never use that reserved identity. The
sensitivity baseline references the mathematical valuation bundle, independently of
production-order provenance. Replaying exported scores does not recompute readiness.
