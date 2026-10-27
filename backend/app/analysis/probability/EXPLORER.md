# Explorer acquisition contract (T12 / #493)

Implemented source, proposed for integration; no runtime activation or deployment
is implied. Uses T01 [ProbabilityEvidence](../../contracts/product_v02/analysis.py)
from #513 `2db98217`, unchanged. [Boundary drawings](README.md) are canonical.

## Public operation

`ExplorerClient(transport_factory=..., cache=None, clock=..., base_url=...).fetch(
request: ExplorerRequest, *, credential: str | None,
cancellation: Cancellation | None = None) -> ExplorerResult` is asynchronous.
`cache_key(request) -> str` is pure and credential-free.

`ExplorerRequest` in [ports.py](ports.py) requires a complete legal position
inventory, full root FEN, exact UCI history reaching that position, variant,
explicit population or `None`, inclusive `since`/`until` month filters, and move
limit. One fetch makes at most one HTTP request, with no redirects or automatic
retry. Defaults are engineering resource ceilings: total operation 20 seconds,
connect 5 seconds, decoded response 1 MiB, history 512 plies, result 256 moves;
each request can lower these bounds. Cache reuse is disabled by default and may
be enabled explicitly for at most 86,400 seconds. No population is defaulted.

The operation returns T01 `ProbabilityEvidence` plus immutable request/source/
adapter provenance, diagnostic and `from_cache`. Its `reported_counts` reuses
evidence-math `MoveCount`: known reported move counts survive even if the root
sample denominator is missing. In that case T01 `counts` is `None`, because
`CountEvidence` requires a known total. Counts not supplied by the source stay
absent. Returned probabilities are empirical sample frequencies, not calibrated
predictions. `observed_at` is capture time; provider data-generation time is
unknown. The evidence ID binds request, observation time and retained counts.

For a pure T01 `ProbabilityPort` consumer, composition supplies already-acquired
`result.evidence`; it must never initiate async acquisition inside a ranking
policy or Study read. This adapter makes no ranking, settings, scheduling or
fallback-order decision. #501 wires acquisition/cache; #494 consumes evidence.

## Explicit population and source limits

Only `standard` is supported: T01's legal inventory is a standard-chess
contract. Other variants receive `unsupported_variant`, before HTTP. The
source is Lichess `/lichess`; masters and personal-player datasets are not
silently substituted.

Population bounds describe inclusive **average-player-rating bucket ranges**,
not exact next-opponent ratings. Accepted lower bounds are
`0,1000,1200,1400,1600,1800,2000,2200,2500`; the inclusive upper bound must be
one below a later boundary in that list or 2799. For example the explicitly
synthetic band 1600–1999 sends rating groups `1600,1800`. Arbitrary bands such
as 1500–1700, and unbounded upper-tail groups, return `unsupported_population`;
there is no silent rounding, cohort inference or user settings write. Speeds
must be explicit Lichess speed names. Missing population yields permanent
`configuration_required`, with no evidence object and no cache/HTTP call.
D04 remains open for the user and integration owner.

Primary sources checked for parameter semantics:
[Lichess Explorer API](https://lichess.org/api#tag/Opening-Explorer) and
[provider bucket selection](https://github.com/lichess-org/lila-openingexplorer/blob/master/src/model/lichess.rs).
These document provider capabilities, not selected user settings. No Explorer
request is needed for the component checks.

## Examples and failure behavior

All examples below are synthetic and omit the full legal inventory for brevity;
actual `ProbabilityEvidence` always includes it. The shared
[T01 fixtures](../../../../contracts/product-v02/fixtures) retain complete
JSON examples; `test_complete_distribution_round_trip` exercises this producer.

| Sample | Reported move counts | Evidence probabilities | Unknown mass |
| --- | --- | --- | --- |
| Complete, total 80 | e2e4=60, d2d4=20 | .75, .25; remaining legal moves .0 | .0 |
| Partial, total 100 | e2e4=60, d2d4=20 | .6, .2; unreported moves `None` | .2 |
| Zero, total 0 | empty or explicit zero counts | all legal moves `None` | 1.0 |
| Missing total | retained `reported_counts`, `counts=None` | all legal moves `None` | 1.0 |
| HTTP 429 | no sample result | `distribution=None`, `unavailable_reason=rate_limited` | no fabricated distribution |

`ExplorerDiagnostic` is typed and contains only a fixed code, retryability,
optional UTC `retry_at` and auth-failure flag. 401/403 and missing credentials
require correction; 429 honors a valid Retry-After (otherwise 60s), 5xx/network
and timeout are retryable. Other HTTP statuses, malformed/duplicate/nonfinite
JSON, illegal/duplicate moves, negative/boolean/overflowing counts, mismatched
source/position and exceeded response/result bounds fail permanently for that
request. Unexpected compression is rejected after requesting identity encoding.
No raw body, exception message, authorization header or URL query enters output.

An injected cancellation event or direct task cancellation returns retryable
`cancelled` after cancelling/awaiting child work and closing the request-owned
HTTP resources. Pre-cancelled work touches neither cache nor HTTP. The total
time ceiling includes the cache capability; capabilities must cooperate with
cancellation. The adapter does not retry or launch another job.

## Resource and cache ownership

The transport factory creates a **fresh, request-owned** async transport. The
adapter closes its HTTP client and transport on every exit. For fake HTTP use
`transport_factory=lambda: httpx.MockTransport(handler)`; production composition
may supply `lambda: httpx.AsyncHTTPTransport(...)`. There is deliberately no
default network transport or environment credential lookup.

`ExplorerCache.get/put` are semantic async operations that must release their
own connection/transaction before returning. The adapter awaits `get` fully
before creating HTTP resources and closes HTTP before `put`. It never borrows
a DB connection. Composition owns real storage capacity, TTL eviction and retry
cadence. Cache errors produce safe `cache_unavailable`; failures are never
cached. Wrong-context/malformed cached results fail as `invalid_cache_entry`;
expired/future-dated samples are refreshed. Cache reuse retains the original
observation date. A key hashes exact FEN/history/variant, population, month
filters, result/resource bounds, provider origin and adapter version. Credentials
are a call argument only and never part of data, cache or diagnostic text.

## Reuse and focused proof

[explorer.py](explorer.py) adapts the small donor
[ExplorerClient/validate_evidence](../../../../services/intelligence/src/tempo_intelligence/provider.py)
acquisition logic: bounded streaming, no redirects/trust-env, strict native
outcomes, outcome-wise subtotal checks, duplicate-field detection and safe
HTTP/Retry-After errors. Donor callers are unchanged. Its descriptor/runtime
orchestration is not imported or copied. Empirical frequencies, counts, legal
inventory and unknown distributions use public `tempo_evidence_math` exports.

`make python-file FILE=backend/tests/test_product_explorer.py` is the regular
owning proof, using only fake transport/cache/clock and synthetic populations.
It extends T01's scoped import/cycle guard over this boundary without editing
the shared guard. Central regression/dependency registration belongs to #501;
no test inventory or dependency lock is changed by this component. Actual
provider connectivity, persistence and product acceptance are unverified and
belong to integration; no donor regression is needed because donor source is
unchanged.
