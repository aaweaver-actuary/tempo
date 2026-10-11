# Maia probability cache foundation (#110)

## Scope and development test plan

This independent change adds reusable local inference and Python-owned Maia source
acceptance/identity. It extracts the existing Docker worker's inference functions;
its coverage commands and browser inference remain compatible. Nothing calls this
adapter from training, queue reads, priorities, admissions, FSRS, Explorer or fusion.

Risks: incomplete legal support, black-side orientation, promotion identity,
invalid mass, unsupported rating inputs, mislabeled artifacts and incompatible
worker packaging. Smallest proofs are named deterministic Node and Python cases,
then their full files, existing probability contracts, worker-client tests and
real browser/container inference parity. JavaScript changes require lint and
typecheck; Docker packaging requires an isolated offline image proof. CI owns
final current-candidate qualification. No persistence behavior changes here.

## Audited capabilities and provenance

The bundled artifact is 45,683,686 bytes, reconstructed from six local parts. Its
SHA-256 matches the deployed worker artifact:
`405bf76c15727dad8728b352c06a8f3c1b80fb2760e8d666b32485c63d75b856`.
The manifest also pins both move-vocabulary checksums, ONNX Runtime Web 1.23.0,
chess.js 1.4.0 and inference schema `maia-legal-softmax-v1`. Loading rejects
changed bytes/runtime/schema instead of attaching old provenance to new outputs.
Updating these inputs requires an explicit new descriptor and cache identity.

Artifact inputs are float32 `tokens [batch,64,12]`, `elo_self [batch]` and
`elo_oppo [batch]`; outputs include 4,352 move logits and three value logits.
The two rating tensors are separate. The artifact internally casts ratings to
float16 and clips them to 0–5000. Maia's [upstream model implementation](https://github.com/CSSLab/maia3/blob/main/maia3/models.py)
confirms separate learned rating interpolation. This does not establish a
calibrated operating range for the exported artifact. The export's original
checkpoint, upstream revision and training population were not recorded;
`upstream_checkpoint` is explicitly null and evidence carries
`upstream_checkpoint_unrecorded`. No model size or upstream release is inferred.

Tempo initially exposes its nine existing levels, 1100–1900 in steps of 100, for
each ordered input. `side_to_move_rating` predicts the side on the board;
`other_player_rating` is the other player. On a requested opponent-turn inventory
position these mean opponent and learner, respectively. Unsupported values are
rejected without rounding. There is no time-control input, no speed conditioning
and no game-history input. Real local-artifact tests change each rating separately
and observe distinct complete distributions. This proves input sensitivity, not
calibration accuracy or empirical game frequency.

## Adapter and source acceptance

`scripts/maia-inference.mjs` reuses the bundled artifacts, lazy single session and
single WASM thread. Importing it starts no worker loop or inference. The existing
coverage worker imports its compatibility function and continues supplying the
same level to both rating tensors. Its polling, admission and retry behavior are
unchanged. The source entry point accepts a canonical four-field FEN, the complete
legal UCI universe, an explicit expected side and the ordered rating pair.

Before inference, chess.js derives every legal move and checks it against the
request. Black positions swap colors and reflect ranks for Maia; both directions
must preserve the full legal universe, including castling, en passant and four
promotion identities. Every legal model move must have an integer vocabulary
index whose reverse mapping is exact. Missing/mismapped entries fail rather than
losing unauthored replies. Output must be 4,352 finite float32 logits. Stable
float64 softmax uses all legal logits as its denominator, with no repertoire or
authored-route filtering. Mass is checked using #154's `1e-9` tolerance.

Successful responses are dense. Python's `MaiaInferenceResult` revalidates the
canonical universe, UCI identity, side, finite nonnegative probabilities and
unit mass. `build_maia_evidence` separately compares requested position, ratings,
model and side before producing #154's `OpeningMoveEvidence`. It records both
vocabulary hashes, artifact hash, schema, runtime/chess versions, explicit
calculation time, source generation, input fingerprint and normalization method.
No implicit clock or TTL is introduced; freshness has no expiry and its policy
state remains unknown.

Numeric zero is preserved, including float64 exponential underflow, with the
normalization policy recorded as `explicit_float64_softmax_underflow`. A successful
result has zero unknown mass. Missing/failed inference throws and supplies no
replacement evidence. A terminal Node request returns explicit `status: terminal`
and null evidence without calling inference. #154's nonterminal evidence contract
rejects terminal universes; integration must keep terminal outcomes separate.

Python owns `maia:v1:<sha256>` identity over canonical FEN, the full model
fingerprint (artifact, vocabularies, schema, runtime/chess versions and recorded
upstream provenance) and the actual ordered rating pair. Repertoire, profile,
route and move counters do not enter it. The source generation identifies the
accepted evidence occurrence separately from reusable computation identity.
Source validation rejects stale/mismatched results but supplies no database lease
or publication fencing by itself.

## Dependency boundary and integration assumptions

PR #117 was OPEN at implementation start (head `b0c82e8`) and remains OPEN
at pre-PR inspection (head `1ccbc3842f18fac1bbff5e4ef68c57345a2eaac0`,
rebased onto main `964b2a3a`). It owns canonical inventory,
publication and migration 046. This foundation adds no migration, database table,
background handler or cache API. It must remain a partial, dependency-marked draft
while that inventory is unmerged. It references #110 without closing it.

Inspection of #117 shows `inventory_progress`, paged `published_positions` and
`legal_replies` readers. The latter exposes `move_uci` and `resulting_fen_key`;
published rows carry legal-reply count/version and publication/source fences.
These are observations of an unmerged interface, not imports or a competing
schema. Verify their merged signatures and deletion semantics before integration.
Coordinate the next migration number with its owner after merge; no number is
reserved here.

The remaining implementation must:

- Read the requested repertoire's current published inventory in pages of at
  most 64, retaining inventory version, publication generation and source fences.
  Schedule shared canonical position/rating-pair work only on explicit requests;
  unchanged requests preserve work/cache hits and refresh is explicit.
- Add Maia-specific immutable accepted-evidence storage and use existing durable
  tasks for pending work. Logical task identity excludes model version; changes
  to desired model/schema advance generation and immutable desired cache key.
- Claim one bounded position, close its connection, run one inference, then use
  one short background write to atomically publish evidence and task completion.
  Match task generation, lease, desired key, legal universe and source provenance;
  reuse admission, recovery, retry and receipt conventions.
- Add separate cache commands to the existing Maia worker, alternate them with
  legacy coverage work, and keep one inference in flight with existing CPU/memory
  limits. Retain previously accepted evidence after failed refresh; old model
  evidence stays identifiable and cannot replace newer accepted evidence.
- Expose bounded request/lookup/progress/retry and worker claim/heartbeat/release/
  submit/failure interfaces. Report evidence availability separately from task
  state, completeness, retries, errors and provenance. Missing telemetry means
  worker availability is unknown; queued work never means complete.
- Add named foreground contention/restart/replay and training/queue-read proofs.
  Register real PostgreSQL rollback, restart, deletion-independent shared reuse,
  duplicate delivery, stale publication, failed refresh and model-replacement
  proofs in the regular durability runner, then run `make docker-durability`.

| Acceptance | Foundation evidence | Remaining dependency |
| --- | --- | --- |
| Shared position/rating/model identity | Python deterministic key regressions | PostgreSQL shared reuse and deletion independence |
| Separate ratings and complete legal support | Offline real model plus deterministic orientation fixtures | Published inventory reader integration |
| Zero/unknown, normalization, provenance | Node/Python/TypeScript source contracts | Atomic durable accepted-evidence publication |
| Restart, replay, stale results, refresh retention | Source mismatch rejection only | Durable task leases/generations/receipts and PostgreSQL proofs |
| Progress, retry and availability | No cache API added | Inventory-backed bounded request/worker interfaces |
| Foreground protection | Pure imports; no product caller wiring | Contention and training/queue workflow regressions |

## Reproducible validation and measurements

Named regressions are registered in `tests/REGRESSIONS.md`. Regular tests use
bundled local artifacts and deterministic fixtures, without model downloads or
live network inference. Run focused source files with:

```sh
make python-file FILE=backend/tests/test_maia_probability_contracts.py
npm run test:unit -- tests/unit/maia-probability-regressions.test.ts tests/unit/maia-container-parity-regressions.test.ts
npm run test:unit -- tests/unit/maia-coverage-client-regressions.test.ts tests/unit/maia-worker-regressions.test.ts tests/unit/opening-move-probability-contract-regressions.test.ts
make python-file FILE=backend/tests/test_opening_move_probability_contracts.py
npm run typecheck
npm run lint
```

`scripts/benchmark-maia-probability.mjs` performs serial offline measurement on
five fixed fixtures: both opening orientations, black castling, legal en passant
and promotions. It records cold initialization separately from first inference,
25 warm inferences, wall latency p50/p95/max, process CPU time, peak RSS and exact
inference count (26). It checks one WASM thread and peak RSS below 768 MiB. Run
only after validation/builds finish; preserve output with revision/environment
provenance. The isolated Docker proof also enforces one CPU, 768 MiB, no network
and read-only filesystem. These establish bounds on the measured fixtures;
they do not claim a speedup or a universal latency guarantee.

### Measured foundation candidate (2026-10-11)

Offline Linux ARM64 Docker proof, Node 22.23.3 / ORT Web 1.23.0, ordered ratings
1500/1600, one CPU and 768 MiB: cold initialization **18.063 s**, first inference
**2.250 s**, 25 warm inferences p50 **500 ms**, p95 **1,617 ms**, max **1,813 ms**.
Total process CPU was **34.659 s** (31.463 s user / 3.196 s system), peak process
RSS **624.96 MiB**, one WASM thread, exactly **26 inferences**. Container exited 0,
without OOM. End-to-end container invocation was 44.38 s. Other host activity was
present; these are measured fixture bounds under that condition, not a dedicated
host latency baseline. No latency improvement is claimed.

The image was built from the foundation source, with the benchmark and fixtures
mounted read-only. Docker's inspected limits were NanoCpus `1000000000`, memory
`805306368`, network `none`, and read-only root. No study database/API was mounted
or contacted. Diagnostics and exact resource identifiers are retained outside
the checkout under root `test-results/2026-10-11-issue110-maia/`; disposable
container/image removal is recorded there. The checkout is retained for the
unmerged PR.

Focused local execution on macOS ARM64 (Node 26.10.0 / Python 3.14.8), against
the foundation source subsequently committed with this document:

| Command | Result | Suite / observed command wall time |
| --- | --- | --- |
| `make python-file FILE=backend/tests/test_maia_probability_contracts.py` | 26 passed | 1.15 s / 3.49 s |
| `make python-file FILE=backend/tests/test_opening_move_probability_contracts.py` | 46 passed | 0.76 s / 1.87 s |
| `npm run test:unit -- tests/unit/maia-probability-regressions.test.ts tests/unit/maia-container-parity-regressions.test.ts` | 13 passed | 21.04 s / 22.95 s |
| `npm run test:unit -- tests/unit/maia-coverage-client-regressions.test.ts tests/unit/maia-worker-regressions.test.ts tests/unit/opening-move-probability-contract-regressions.test.ts` | 16 passed | 5.67 s / 8.03 s |
| `npm run typecheck` | Passed | 35.29 s |
| `npm run lint` | Passed, 10 existing warnings in unrelated files | 76.76 s |
| `docker build -f Dockerfile.maia -t tempo-maia:issue110-foundation .` | Passed, reused dependency layers | 12.87 s |

Conflict, diff and migration-inventory guards pass; current inventory remains 45.
`make plan` was inspected. No local full gate, browser workflow suite or PostgreSQL
cache durability run is claimed. CI owns the selected candidate gate; new cache
persistence/workflow proofs cannot run before that implementation exists.

An earlier combined local run timed out the existing 30-second legacy parity
case during concurrent host builds/tests. A diagnostic comparison with unchanged
main produced identical top-five probabilities (5.89 s original invocation /
4.08 s extracted invocation); this is compatibility evidence, not a speedup.
After diagnosis the final focused files passed without changing the timeout or
weakening any assertion. The initial new-feature Python collection failed before
the module existed; no claim is made that this was a pre-existing product defect.
