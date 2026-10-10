# Opening-card shadow ranking

This opt-in Python library and JSON diagnostic compare rankings over identical
eligible opening cards. They never admit, unlock, introduce, review, reprioritize,
publish, refresh or enqueue anything. There is no product endpoint, startup hook,
worker, feature flag, model call or UI. This is partial infrastructure for #113,
not completion of its probability, relaxed-frontier or held-out-game evaluation.
It is independent of PRs #116 and #117.

## Run the diagnostic

Use the repository's Python environment. Capture requires the normal primary
PostgreSQL configuration (`TEMPO_DATABASE_WRITE_URL`) and the product's Redis
admission configuration (`TEMPO_REDIS_URL`). The primary connection uses a
PostgreSQL-enforced read-only transaction even when the configured role can write.
Missing Redis admission configuration is rejected before opening PostgreSQL;
the standalone CLI cannot infer other processes' study activity from a local gate.
Do not change database roles or point disposable tests at the live study stack.
Match the deployed Tempo timezone with `TZ` or `--production-timezone`; the
default is `America/New_York`, matching Compose. Capture derives the study day
from the database transaction timestamp in that timezone, independent of the
CLI computer's local date, and records the timezone/basis in source provenance.

```sh
backend/.venv/bin/python scripts/evaluate_opening_rankings.py capture --output snapshot.json
backend/.venv/bin/python scripts/evaluate_opening_rankings.py capture --repertoire-id REPERTOIRE_ID --output repertoire-snapshot.json
backend/.venv/bin/python scripts/evaluate_opening_rankings.py compare --snapshot snapshot.json --scorer production-order --scorer shorter-card-test --top-k 1 --top-k 5 --top-k 10
```

Repeat `--repertoire-id`, `--scorer` or `--top-k` to select multiple values.
Default comparison runs both built-in scorers at K=1/5/10. An optional
`--output comparison.json` creates a new file; existing files are protected.
Errors return exit 2 with an actionable JSON diagnostic on stderr. Missing
individual scores instead produce an inspectable `partial` comparison.

File comparison needs only Python's standard library and never connects to
PostgreSQL or Redis. Output is deterministic for identical snapshot/scorer inputs,
including across process hash seeds. A content digest detects accidental changes;
it is not a signature or proof that supplied evidence is authentic.

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

## Production basis, safety and replay limits

Capture reuses the production eligible-candidate SQL and pure admission planner.
It sorts candidate rows by repertoire/card ID for reproducibility and removes
capacity limits to obtain a complete diagnostic priority order. The basis is
`production_planner_capacity_neutral_sorted_repertoire_then_card`.
Production normally has quotas, gameplay obligations, breadth logic and global
shared-card deduplication; it does not store a total rank for every candidate.

`actual_admission_plan` preserves the same planner's quota-constrained result
using captured limits and introduction counts. Its separate basis explicitly
states that it is computed on sorted captured inputs: it is not a record of cards
actually served, committed admissions or the separately shuffled daily display
order. Alternative top-K sets are ranking prefixes, not alternative admissions.
Repertoire-filtered capture evaluates that selected scope only.

All source reads occur at one authoritative repeatable-read boundary, inside a
foreground-admitted read-only transaction. Capture retains existing transaction
and lock budgets; it never raises them. Pool acquisition has a 100 ms timeout;
the storage seam adds an optional timeout without changing existing callers.
SQL bounds transferred data before Python
decoding. Limits are 10,000 physical candidates, 40,000 membership/route rows per
projection and 4 MiB total raw/projected metadata. The capture deadline is ten
seconds. Contention, database failure or exceeded limits return an error, with no
successful partial export; narrow the repertoire selection or retry when idle.
Connections close before JSON interpretation, planning, hashing and file output.

Saved snapshots support exact replay of their captured ranking inputs. Capture
accepts only the current server-local production day; `--study-day` cannot backdate
live state. Current rows and retained priority generations do not reconstruct
historical eligibility, card/introduction state, quota consumption, deleted routes,
or historical profile/probability evidence. Output marks historical counterfactual
support false and lists these limits. Descriptive ranking differences are not
observed learning gains or measured probability of preparation.

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

The checked fixtures under `docs/examples/` contain three illustrative cards:
production `a,b,c`; the shorter-card scorer produces `b,c,a`, scores `-1,-2,-3`.
Spearman is -0.5, mean absolute displacement 4/3, maximum 2, and K=2 overlap is
0.5 with weighted overlap 0.2. The baseline is identity. Regenerate/inspect with:

```sh
backend/.venv/bin/python scripts/evaluate_opening_rankings.py compare --snapshot docs/examples/opening-ranking-snapshot.json --top-k 2
```

The full JSON comparison is `docs/examples/opening-ranking-comparison.json`.
The regular regression checks it against actual CLI output.

## Validation scope

Changed behavior: opt-in snapshot reads, pure ranking and JSON replay only.
Risks: unintended writes, inconsistent snapshots, missing evidence, identity
duplication, tie instability, incorrect metrics and overstated historical claims.
Smallest proof: named evaluator/capture/CLI regressions, existing candidate-page
tests and the gameplay/breadth/shared-card planner regression. A real PostgreSQL
proof in the existing priority-recovery durability scenario verifies read-only
enforcement, unchanged public tables, foreground denial and connection-free replay.
Run focused files first and elevated `make docker-durability` once settled.
CI owns complete current-candidate qualification; no frontend, admission or FSRS
behavior changes are claimed.
