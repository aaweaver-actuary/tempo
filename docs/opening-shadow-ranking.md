# Opening-card ranking evaluation and retired capture

The pure Python library in `backend/app/services/opening_ranking_evaluation.py`
retains immutable DTOs, ranking/metrics, selected-policy validation and JSON
snapshot serialization. `opening_card_valuation.py`, frontier, production
admission/capacity, frozen ordering, queue SQL and miss projection remain unchanged.
This library never admits, unlocks, reviews, publishes, refreshes or enqueues cards.
It does not complete #113's probability or held-out-game evaluation.

## Retired legacy diagnostic

The exclusive `scripts/evaluate_opening_rankings.py` capture/compare CLI,
`backend/app/services/opening_ranking_snapshot.py` and
`database/tempo_legacy_database/adapters/opening_ranking/` are retired after local
reference and import audits found no serving consumer. Both legacy CLI subcommands
are unsupported. Their implementation and canonical capture-flow drawing remain
recoverable at [last-good source 8c0b3b5a](https://github.com/aaweaver-actuary/tempo/tree/8c0b3b5a22fd357607497ac4e50ad9804884c12c).
This path was separate from PR #411's `tools/ranking-evaluation` removal.

No user data was accessed, exported or deleted. Saved snapshot examples remain.
Pure file/document replay through `load_snapshot`, `rank` and `compare` uses supplied
immutable evidence and opens no storage. Snapshot digests detect changed content;
they do not authenticate supplied evidence. Historical snapshots do not reconstruct
historical eligibility, quota consumption, deleted routes or missing probability
inputs; descriptive rank differences are not measured learning gains.

## Scorer and candidate contracts

`OpeningCardScorer` has stable string `name`/`version` and
`score(candidate, context) -> ScoreResult`. `rank(candidates, context, scorer)`
orders finite scores descending and breaks ties by production rank, then card ID.
`compare(production_order, alternatives, top_k)` validates identity/context and
projects metrics. Both live in `opening_ranking_evaluation.py`.

Each immutable `OpeningRankingCandidate` contains a physical `card_id`, a
one-based production rank and canonical immutable `metadata_json`. Its
`metadata()` method returns a detached dictionary. Metadata includes revision,
FEN/moves, eligibility fields, all captured repertoire memberships and published
route occurrences. Memberships retain priority scores/evidence, completed-line
IDs, frontier ply, source versions/timestamps and explicit unavailable-evidence
reasons. Routes retain repertoire, graph generation, line, decision and parent
identities. Shared/transposed cards occur once; their memberships remain separate.
Missing route metadata does not manufacture an authored route.

Production order must be a complete permutation with consecutive ranks. Snapshot
schema version 1 binds candidates, UTC as-of, server-local production study day,
source versions, scorer configuration, order basis and quota-constrained plan.
`OpeningRankingContext` stores nested configuration/evidence as immutable JSON
strings. No current clock or database handle is supplied to scoring.

`production-order@1` scores `-production_rank`; this is a passthrough encoding,
not a recalculated heuristic score. Original heuristic scores remain in metadata.
`shorter-card-test@1` scores negative move count, reporting missing/invalid moves
explicitly. It demonstrates the interface and claims no preparation benefit.

A future probability scorer implements this protocol and reads already supplied,
versioned evidence from candidate metadata or context. For example:

```python
import json
from app.services.opening_ranking_evaluation import ScoreResult

class CachedProbabilityScorer:
    name = "cached-probability"
    version = "1"

    def score(self, candidate, context):
        values = json.loads(context.scorer_configuration_json)["cached_scores"]
        if candidate.card_id not in values:
            return ScoreResult(None, "probability_evidence_unavailable")
        return ScoreResult(values[candidate.card_id])
```

Pass that object to `rank` and its result to `compare`; evaluator code does not
change. Fetching, model inference, freshness policy and score publication belong
outside this module. Scorers are trusted pure code, not sandboxed arbitrary plugins.
Exceptions are visible as `scorer_error:ExceptionType` without exposing provider
messages that could contain credentials.

## Metrics and checked example

Ranks start at one. `rank_delta = production_rank - alternative_rank`; positive
means promotion. Unscorable cards retain their identities and reasons with null
score/rank/delta and are excluded from ranked alternative prefixes.

- Coverage is scored candidates / all candidates; unscorable percentage uses the
  same denominator. Empty inputs are explicitly undefined.
- Spearman uses consecutive ranks within the commonly ranked subset. Fewer than
  two common candidates is undefined. Displacement uses original displayed ranks.
- Top-K overlap is intersection / `min(K, candidate_count)`. Incomplete score
  coverage cannot improve overlap by shrinking the denominator.
- Weighted overlap is weighted Jaccard: each card in each ranking prefix has
  weight `1/rank`; sum minimum weights / sum maximum weights over their union.
- Repertoire/frontier-ply/route distributions split each selected card's unit
  mass equally across distinct memberships for that dimension. Unknown evidence
  gets an explicit bucket. Concentration is `sum(share**2)`.

The retained fixtures under `docs/examples/` contain three illustrative cards:
production `a,b,c`; the shorter-card scorer produces `b,c,a`, scores `-1,-2,-3`.
Spearman is -0.5, mean absolute displacement 4/3, maximum 2, and K=2 overlap is
0.5 with weighted overlap 0.2. The baseline is identity. The snapshot and full
comparison remain historical examples; the former CLI output regression is
retired with that CLI. The regular pure tests retain hand-calculated metrics,
snapshot roundtrip/digest checks, policy fencing and legacy-schema compatibility.

## Validation scope

`backend/tests/test_opening_ranking_evaluation.py` owns retained pure ranking,
identity, metrics, missing evidence, policy and serialization checks. Its shared
`test_shadow_pool_timeout_is_opt_in_and_preserves_existing_connection_defaults`
continues to protect the PostgreSQL connection adapter. The exclusive capture,
CLI and native shadow read-only proofs are retired with their implementation.
Every other assertion in the existing PostgreSQL priority-recovery scenario
remains. Source/AST preservation and collection are separate from native execution.

## Explicit policy evaluation (Issue #156)

`OpeningRankingContext` has two scopes: `global_diagnostic` (the default, with
`policy_id=None`) and `selected_policy` (requiring a nonempty `policy_id`). Every
candidate in a selected-policy evaluation must carry that same keyword policy ID.
Foreign or untagged candidates fail before any scorer runs, including serialization;
comparison validates candidate policy and identical contexts. A physical card remains
one candidate within each evaluation and may occur independently in another policy.

Offline callers may use hypothetical policy IDs without accessing storage.
There is no supported live capture command.

Schema 2 snapshots and comparisons retain `evaluation_scope` and `policy_id`;
the snapshot digest covers them and each candidate's policy. Schema 1 snapshots
replay only as global diagnostics, without inferring policy from stored memberships.
Checked examples use schema 2. Global diagnostic rankings are not policy-conditioned
probabilities or a repertoire mixture.

Conditioned scorers must supply `P(reach decision | learner follows policy R)`
and pass the explicit selected ID to the preparedness kernel. Learner actions do
not receive inferred branch frequencies; opponent moves remain stochastic. The
framework does not compute reach or infer repertoire-selection/study weights.
The Italian/Ruy shadow regressions reuse the kernel's real shared-prefix fixtures,
retain opponent deviations, and compare full scoring inputs, preparedness, marginal
values and ranking after adding, expanding or removing the other policy.
