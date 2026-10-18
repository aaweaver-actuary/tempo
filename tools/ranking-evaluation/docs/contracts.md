# Installed offline evaluation contracts

This tool depends only on tempo-evidence-math 0.1.0's public, versioned API. Its
producer is PR #300, commit 08ad6823f31ac2f36929f3a94f500e65aa0affc6. The dependency
lock binds the exact locally qualified producer wheel. No private service or package
implementation is consumed. Production has not adopted either artifact.

`tempo-evaluate-opening-rankings corpus [--corpus MANIFEST] [--scenario ID ...]
[--top-k K ...] [--output NEWFILE]` validates the complete local corpus before
ranking selected scenarios. The default is packaged data, never a repository path.
`compare --snapshot FILE [--scorer production-order|shorter-card-test|valuation ...]`
replays saved rank inputs. `score --snapshot FILE` decodes the producer's valuation-v2
mathematical input document and actually invokes reach/readiness/valuation first.
It then emits comparison plus a replay snapshot. `valuation_input_document` is the
supported producer serializer. Supplied-score replay does not validate valuation
correctness; report replay_basis identifies the operation.

Snapshot/report schema 2 and corpus/harness version 1 retain existing wire semantics.
Schema 1 snapshots remain global diagnostics only. Selected-policy candidate IDs
must match context; fixed production ranks must be complete and unique. Null scores
require reasons and get no alternative rank. Numeric scores sort descending, ties
by captured production rank then card ID. Arithmetic card-ID ties remain a separate
producer rule. Canonical objects sort keys; meaningful snapshot array order stays
content-digest-sensitive even when metric results are permutation-invariant.

ProductionOrderScorer only replays captured ranks. The six historical corpus files
retain donor hashes, production_planner_capacity_neutral_sorted_repertoire_then_card,
and reference_main 4dc17a96dd23da51047284d1bfb7434efee20d56. Neither alphabetical nor
shorter-card order replaces that baseline. precomputed-base@1 is the sensitivity
reference, not the production comparator. Producer exports now supply their real
valuation bundle and reserve production-order@1 for the built-in comparator.

The corpus has six new candidates per scenario. Supporting introduced/mature/failed
cards remain history. Synthetic white:FEN:move decision labels are checked for
geometry but are not relabeled as producer hashed IDs. Point probabilities and
historical low/high annotations remain replay assumptions; annotations around an
assigned value are not fed to the producer's unknown-allocation sensitivity API.
Legal moves, complete legal distributions, mass, true prefix parents and all thirteen
scenario dimensions validate before ranking, including unselected scenarios.
Probability evidence must be nonempty and bind to actual authored candidate replies
and declared cohorts. Supporting decisions must match legal presentation geometry
under the selected policy; observations must identify one of that card's validated
decisions and fall between introduction and the snapshot time. Tags and re-signed
digests alone cannot establish these coverage claims.

Limits: 4 MiB per input file; 16 MiB aggregate corpus; 1–32 scenarios; at most 16
bundles and 16 distinct positive exact-integer K values. Before ranking, aggregate
projection includes row/provenance overhead and transformed concentration labels:
card_mass and shares, production and alternative, every alternative/K, and the corpus
summary copies of top_k. The original coarse input-size guard remains a lower bound.
Projected and serialized output including newline cannot exceed 64 MiB. compare uses
the same label bound. score reserves input bytes*3*(8*K-count+16) with the stricter
pre-calculation multiplier (candidates+1)*(routes+readiness+1) to bound repeated
calculation provenance. Oversized inputs fail honestly rather than execute
with weakened limits. Resolved paths/symlinks must remain inside the corpus directory.

Success exits 0. Without --output, stdout contains one canonical JSON report plus
newline; file mode writes no stdout. Failure exits 2, emits bounded JSON stderr and
no success report. Validation and bounded serialization precede output creation.
Exclusive creation protects existing files, inputs and symlink aliases. A failed
write removes only the new file created by that invocation. Capture is not a command
in this installed tool; the legacy capture adapter remains separately owned.

The donor hand-check is preserved: production a,c,d,e,f,b versus supplied b,c,a,d,e,f.
Squared displacement sum is 32; absolute sum 10; Spearman is 3/35, mean absolute
5/3, maximum 5; K=2 overlap .5, reciprocal-rank min/max weighted overlap .2.
Coverage/null, Spearman common-set, displacement, top-K and concentration metrics,
cohort/bound/readiness/effect/rare-line sensitivities and provenance remain explicit.
They are diagnostics, not a global curriculum allocation calculation.

Machine-readable reports retain limitations: synthetic scenarios are not observed
games, calibrated recall or causal learning gains; saved ranks do not establish
historical counterfactual accuracy, queue delivery or global-allocation qualification.
Future adoption needs chronological held-out games, leakage limits, source/cohort
coverage and runtime/failure/backpressure comparisons. Those experiments are not
required to extract this offline tool and are not claimed by its fixture suite.

Qualify with one bounded process at a time. The local runner checks nonzero exact
collection/execution/JUnit identities and local regression registration. Source-removed
wheel and sdist-derived-wheel installs use only declared prebuilt dependency artifacts
on CPython 3.12 and 3.14. Dependencies install with --require-hashes from the complete
lock before the separately installed --no-deps candidate. The lock includes exact
prepared macOS pydantic-core wheels reconstructed from installed cache bytes verified
against RECORD; those hashes differ from upstream ZIP artifacts. Qualification retains
the exact wheel hashes and installer errors. Changed same-version producer or dependency
bytes fail before candidate installation or suites.

Hash-seed/timezone cases retain 1/UTC, 22/Pacific-Honolulu, random/Asia-Tokyo and 77/UTC.
Seeded children use -s -P -B with path-affecting Python environment variables removed.
They assert installed paths, disabled user site, ignore_environment=0, repeatable hashes
for each fixed seed, and different hashes for seeds 1 and 22 before checking report bytes.
Earlier -I runs did not honor these seeds; the correction strengthens the test proof
without claiming an observed runtime nondeterminism defect.
Network/service imports and initialization are denied.
macOS hard memory enforcement remains unverified; wall/CPU limits are enforced.
