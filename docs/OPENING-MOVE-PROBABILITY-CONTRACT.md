# Opening move probability evidence contract v1

This is the source-independent seam for
`P(opponent move | position, player/opponent context)`. It is an opt-in domain
contract, not a provider client, persisted publication, or admission policy.
Python owns authoritative JSON serialization. The matching TypeScript/Zod
boundary and shared fixtures protect consumers before providers exist.

## Identity and legal support

`PositionMoveUniverse` contains Tempo's existing `fen_key` and **all** legal UCI
moves. `move_universe` / `moveUniverse` accepts a FEN, derives the universe, and
rejects positions with no legal opponent moves. Supplying an inventory requires
exactly the same legal set, with no duplicates. Four-field identity retains
placement, side to move, castling rights and **legal** en passant; counters are
excluded. Python reuses `canonical_prefix.position_key`, which agrees with
`repertoire_comparison.canonical_fen`. TypeScript validates a board and passes
`board.fen()` to `canonicalFenKey`; slicing an arbitrary raw FEN would not suffice.
Rust's existing `canonical_fen_key_native` uses `EnPassantMode::Legal` and the same
four fields. No competing key/hash or chess-variant normalization is introduced.

Move identity is lowercase UCI, including the promotion suffix. SAN, null moves,
malformed moves and legal-looking moves illegal at this position are rejected.
Standard chess is the supported universe. The distribution describes choices by
the side to move. Both boundaries require exactly one king of each color and no
pawns on the first/eighth ranks, matching the existing TypeScript board validator;
this does not infer historical reachability. Target context must select an
opponent-turn position. Terminal positions have no probability distribution and
require an upstream terminal state.

## Wire models

Every serialized top-level model carries `contract_version: 1`. Constructors
default to v1; other versions and extra envelope fields are rejected. Extension
objects contain JSON values, not arbitrary runtime objects. Numeric values must
be finite, and integer-valued numbers must be exactly representable in JavaScript
(absolute value at most `2^53 - 1`). Counts are nonnegative integers, never booleans
or numeric strings. Timestamps use calendar years 1–9999, include seconds, a valid
timezone offset (or `Z`), and at most six fractional digits. Optional values are
explicit `null`.

| Model | Responsibility |
| --- | --- |
| `OpeningMoveEvidence` | Independently identified source snapshot: source ID/generation/version, actual cohort, tagged raw evidence, distribution, optional normalization descriptor, timestamps, freshness, quality, provenance, and `derived_from` IDs. |
| `MoveProbabilityDistribution` | Position plus every legal move's nullable probability and numeric `unknown_mass`. |
| `OpeningMoveEvidenceBundle` | One position, requested `target_context`, and one or more independent source snapshots. Duplicate evidence IDs and different positions are rejected. |
| `FusionRequest` | Evidence bundle and policy descriptor (`id`, `version`, JSON `parameters`). |
| `FusedMoveDistribution` | Separate available/unavailable result, target context, policy descriptor, distribution or reason, source contributions, freshness, quality and lineage. |

`raw_evidence.kind = "counts"` supplies a sample `total_count` and sparse
`{move_uci, count}` rows. Reported counts cannot exceed the denominator. A missing
row is unreported, not zero. `kind = "scores"` supplies sparse `{move_uci, value}`
rows and `basis = "probability" | "weight"`. Raw probability mass cannot exceed
one within tolerance; unnormalized weights are distinguishable from probabilities.
The source's cohort is its **actual** conditioning, not the user's desired cohort.
Unsupported conditioning belongs in provenance limitations/quality flags.

Quality includes optional effective sample size, unordered flags and optional
source-supplied uncertainty JSON. No confidence interval or calibration is inferred.
Each provenance record retains a record ID, input fingerprint, references and
limitations. It does not store a provider response or credentials. Freshness uses
caller-supplied `as_of` and optional `valid_until`: expiry is stale at the exact
deadline; absent expiry is unknown. No wall clock or default TTL is consulted.
Staleness does not erase evidence or assign zero probabilities.

## Unknown, observed zero, and normalization

- A probability `null` means unassigned. Numeric `0` is an explicit estimate.
- A count of `0` is an observed zero count. It does **not** automatically assert
  zero predictive probability. An omitted count has no empirical frequency.
- `empirical_frequencies` / `empiricalFrequencies` returns count/sample ratios
  only for reported rows; a zero denominator returns `null` for those rows.
- Raw-only snapshots have no normalization descriptor, all probabilities `null`,
  and unknown mass `1`. Complete finite samples do not silently calibrate themselves.
- Known mass is at most `1 + 1e-9`; known plus unknown mass equals one within
  `1e-9`. Probabilities and residuals are finite and nonnegative. Any unassigned
  move requires a positive residual. Zero residual requires every legal move to
  have an explicit probability, including any explicit zeros.

`normalize_evidence_weights` / `normalizeEvidenceWeights` allocates supplied
weights in proportion to their sum, scaled to `1 - unknown_mass`. Both the
residual and method descriptor are required. Empty/all-zero, duplicate, illegal,
nonfinite, or negative weights fail; no uniform fallback or implicit smoothing
is provided. The caller must supply a distinct derived evidence ID. The returned
snapshot retains the input ID in `derived_from`, raw observations, cohort,
timestamps and provenance. Different normalization versions can coexist with the
original raw snapshot. This helper does not choose weights from counts or a model.

`coverage_mass_summary` / `coverageMassSummary` accepts a caller-supplied legal
covered-move set and returns `known_covered_mass`, `known_uncovered_mass`, and
`unknown_mass`. Unknown mass cannot be allocated to covered/uncovered replies
without more evidence. Known probabilities for unauthored replies are retained;
the helper never renormalizes around the repertoire.

Python `serialize_contract` orders legal/raw/probability move rows by UCI, source
snapshots and contributions by evidence ID, unordered flags/references, provenance,
and object keys. Ordered context arrays retain their order. The TypeScript boundary
accepts the same semantic values; it does not independently produce a competing
cross-language byte hash. Shared fixture inputs round-trip through both validators.

## Fusion interface

`OpeningMoveFusionPolicy` is a Python callable protocol / TypeScript function type.
`fuse_move_evidence` / `fuseMoveEvidence` invokes the supplied strategy on a copy,
validates the result against position/context/policy and checks contribution IDs
against the request. It carries contributing source provenance into the result.
Raw source snapshots remain in the bundle and never become fused raw evidence.

An available result requires assigned positive mass and contributing sources.
An unavailable result has no distribution and an explicit reason, not equal
weights disguised as measured evidence. A caller with no snapshots can construct
that result directly; the strategy request itself requires one or more snapshots.
Contribution weights/effective sample sizes are optional descriptive values;
the policy descriptor must explain their units and interpretation. The contract
does not impose a convex blend, source preference, effective-sample formula, or
staleness cutoff. Selection, weighted blending, fallback, and Maia-informed
shrinkage can be future strategies using the same raw and normalized structures.

## Required integration with #116 and #117

The fixtures' `target_context` shows illustrative mappings, not imports of either
unmerged PR and not new schemas owned by those PRs.

**PR #116 / issue #107:** The future profile adapter must preserve `version`,
`method_version`, `evidence_digest`, `source_account`, requested/effective speed
mixture, each speed's opponent-rating distribution, effective sample size,
unsupported-speed mass, freshness and quality flags. Profile identity belongs
in requested target context and fused provenance, not in independently reusable
raw source cache identity. Source mapping/interpolation must expose the actual
supported cohort and unsupported conditions; a profile is not move evidence.
Mapping policy and profile changes may recompose the same raw snapshots.

**PR #117 / issue #108:** The future inventory adapter must provide the canonical
`fen_key`, complete legal `move_uci` list, inventory version/publication generation,
graph/source revisions for ownership/fencing, and optional `resulting_fen_key`
references. Validate the complete legal universe before normalization/fusion.
Keep authored/response/transposition coverage as a separate classification input;
it must not restrict probability support. Shared source identity is independent
of repertoire membership or deletion. `inventory_positions` and
`inventory_legal_replies` supply legality; publication/membership projections
supply coverage and provenance. This PR neither reads those tables nor owns storage.

Related future work: #109 (Explorer), #110 (Maia), #111 (personalized fusion), and
#112 (coverage/readiness). Existing `blend_probabilities`, coverage tables and
priority consumers retain their behavior. None of these broader issues is closed.

## Examples and validation

`tests/fixtures/opening-move-probability/examples.json` is synthetic and contains
partial Explorer-like counts with observed zero, dense Maia-like probabilities
with explicit zero, sparse predictions with residual, multiple cohorts from one
source, profile/inventory context, and available/unavailable fusion examples.
The small kings-only position makes the full five-move denominator inspectable;
additional identities cover promotions, castling and legal/pinned/uncapturable
en passant. No example came from a provider or model call.

Focused checks:

```sh
make python-file FILE=backend/tests/test_opening_move_probability_contracts.py
make unit-file FILE=tests/unit/opening-move-probability-contract-regressions.test.ts
```

The frontend file runs the shared Python boundary probe, so it requires the
backend environment. Named regular regressions are registered in
`tests/REGRESSIONS.md`; CI owns final candidate qualification.
