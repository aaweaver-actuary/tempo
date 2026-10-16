# Maia worker regression ownership

Regular commands: `node test.mjs pure`, `node test.mjs protocol`, `node test.mjs model`. Artifact and container qualification use `qualify.mjs`. Empty file/case selections fail; Node case events retain executed identities. No backend, database or browser harness is required.

- `tests/artifact.test.mjs`: immutable external inputs, exact reconstructed model/vocabulary/schema bytes, corrupt-source rejection.
- Core/model-owned local tests: position legality, both orientations, castling/en-passant/promotions, vocabulary bijection, strict ratings, exact-set softmax, invalid outputs and installed provenance/load failures.
- Resource/protocol-owned local tests: finite cgroups, native/unlimited failure, request/cap compatibility, retry/conflict/generation fences, stale output, cancellation races, process exit/capacity, timeout/crash/response loss and restart.
- `tests/real-model.test.mjs`: real ONNX inference for both openings, castling/en-passant/promotion orientations, complete legal universes, subset conditionality, pinned/uncapturable en-passant, two ratings, determinism, terminal and unsupported handling.
- `qualify.mjs restricted`: installed public import without polling, isolated dependency closure and immutable hashes.
- `qualify.mjs container`: offline actual contract exchanges, CPU-observed in-flight termination, capacity release, complete restart/replay, failures and clean shutdown under real limits.
- `tests/contracts/`: independent installed Python schemas/reference-consumer qualification and adversarial accepted-effect fencing. `consume_observed.py` validates actual exchanges.

Legacy compatibility remains in root `tests/unit/maia-container-parity-regressions.test.ts`, `tests/unit/frozen-maia-inference-regressions.test.ts`, and `tests/unit/maia-coverage-client-regressions.test.ts`; existing root inventory registration remains authoritative until those consumers retire.
