# Maia probability capability (#498)

`createProbabilityProvider({createExecution?, now?})` from
[`probability-provider.mjs`](probability-provider.mjs) starts no model on import.
`await provider.evidence(request, {signal?})` returns `{evidence, provenance,
diagnostic}`. `evidence` is the unchanged shared
[`ProbabilityEvidence` 0.2.0](../../backend/app/contracts/product_v02/analysis.py)
published by #482 at `2db98217`. No database, Study, ranking or queue writer exists
here. Outer composition adapts `result.evidence` to the same ProbabilityPort as
Explorer and retains provenance; activation belongs to #501 after T20.

| Required request field | Meaning |
| --- | --- |
| `position` | Shared PositionMoveUniverse: canonical four-field `fen_key`, every legal UCI move exactly once. |
| `fen`, `variant` | Exact six-field FEN matching that position; only `standard` supported. |
| `population` | Shared explicit `rating_min`, `rating_max`, nonempty distinct `time_controls`. D04 has no default. |
| `ratings` | Both tensor inputs: `{elo_self, elo_oppo}`, each 1100–1900 in steps of 100. `self` means side to move. |
| `rating_reference` | Caller-supplied rating system/reference; no platform conversion, rounding or calibration. |
| `limits` | Positive `initialization_ms` ≤120000, `inference_ms` ≤30000, `memory_mb` 1024–2048. |

The cohort selection is preserved, not asserted to be the model's empirical
training population. Actual inputs are occupancy plus two ordered ratings. There
is no history, variant or time-control tensor; castling/en passant affect legal
selection only. Provenance retains full FEN, every setting, requested moves,
model identity/implementation hashes, resource facts, date and limitations.
`counts` is always null: no invented sample count, including zero. No account or
live settings are selected. Upstream checkpoint/export revision remain unknown.

Ordinary calls request every legal move. Reused `createModel`, `preparePosition`
and `decodePredictions` own validation, orientation and softmax.
`diagnostic(request, subset, {signal?})` labels restricted results
`conditional_requested_moves` and returns **no shared distribution**:
`unavailable_reason=conditional_distribution_not_coverage`. Conditional predictions
exist only in `diagnostic`, so they cannot imply full repertoire coverage.

Malformed/unsupported requests throw `MaiaError` before execution. Valid requests
with unavailable assets/resources, load failure, worker death, timeout,
cancellation, busy capacity or closed provider return unavailable evidence with
the exact reason. Callers retain Explorer/cached rankings or their safe order;
Maia does not choose or mutate fallback state.

## Lifecycle and resources

Default execution forks one short-lived child via `probability-stdio.mjs` private
IPC. Only the child initializes the existing model: one WASM thread, one
inference, no queue. Process-wide admission permits at most one owned model child
across instances. Each result terminates it; no warm pool, retry, scheduler,
registry or old supervisor is restored.

Initialization and inference have separate finite deadlines. Cancellation,
`close()` and timeout fence output and wait for actual child exit before capacity
release. Stderr is bounded at 64 KiB. `node workers/maia/probability-stdio.mjs`
reads one ≤64 KiB JSON request through EOF within five seconds, emits one result
and closes. A Python consumer may use bounded subprocess/communicate.

Native execution requires an inherited Linux memory cgroup ≥1024 MiB and no
greater than the requested budget. No heap-limit/RSS substitute or bypass is
provided. The cgroup must be task/provider-owned; deployed host isolation remains
unverified. Missing/unlimited caps, including native macOS, return
`memory_limit_unavailable`. No system setting is modified. Existing `createModel`
asset options configure only local assets; no download or reconstruction write.

Injected execution for tests implements `initialize(limits) -> {identity,
resources?}`, `infer(job) -> [{move, probability}]`, `terminate()`. Termination
must fence output, stop native work and resolve only after exit. Injection is not
a production memory-cap override.

`model.mjs` accepts explicit ordered `ratings` alongside its existing single
`rating` API. Legacy callers still supply that rating to both tensors; ambiguous
simultaneous inputs fail. The descriptor's v1 wire limitation stays true.
Model/vocabulary bytes/version are unchanged; the existing implementation hash
changes with the model source.

## Focused evidence and adoption

Use an already-installed `TEMPO_PYTHON` with contract dependencies. No install:

```sh
node --test tests/runner/product-maia-probability.test.mjs
node --test tests/runner/product-maia-probability-smoke.test.mjs
```

The first suite injects fake execution and validates emitted available/conditional
values through #482's actual Python model, not a parallel DTO. The separately
invoked smoke verifies existing model parts/runtime, evaluates one legal
position and waits for exit. Missing assets/cap fail explicitly; never a passing
skip. Run under the existing nontruncating advisory validation lock, yielding to
first-milestone owners.

Regular regression registration and product activation belong to #501; no root
registration/composition is edited here. After T20 exists, reuse its consumer
fixture to select Maia, accept evidence into ranking, stop Maia and retain
Study/Explorer. Component readiness does not claim adoption, deployed resource
isolation or a passing real-inference check. No merge/deployment/live change.
