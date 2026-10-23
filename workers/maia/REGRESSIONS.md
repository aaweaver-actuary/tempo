# Maia worker regression ownership

Regular commands: `node test.mjs pure`, `node test.mjs protocol`, `node test.mjs model`. Artifact and container qualification use `qualify.mjs`. Empty file/case selections fail; Node case events retain executed identities. No backend, database or browser harness is required.

- `tests/artifact.test.mjs`: immutable external inputs, exact reconstructed model/vocabulary/schema bytes, corrupt-source rejection.
- Core/model-owned local tests: position legality, both orientations, castling/en-passant/promotions, vocabulary bijection, strict ratings, exact-set softmax, invalid outputs and installed provenance/load failures.
- Resource/protocol-owned local tests: finite cgroups, native/unlimited failure, request/cap compatibility, retry/conflict/generation fences, stale output, cancellation races, process exit/capacity, timeout/crash/response loss and restart.
- `tests/real-model.test.mjs`: real ONNX inference for both openings, castling/en-passant/promotion orientations, complete legal universes, subset conditionality, pinned/uncapturable en-passant, two ratings, determinism, terminal and unsupported handling.
- `qualify.mjs restricted`: installed public import without polling, isolated dependency closure and immutable hashes.
- `qualify.mjs container`: offline actual contract exchanges, CPU-observed in-flight termination, capacity release, complete restart/replay, failures and clean shutdown under real limits.
- `tests/contracts/`: independent installed Python schemas/reference-consumer qualification and adversarial accepted-effect fencing. `consume_observed.py` validates actual exchanges.

Legacy frozen-rank and service compatibility remain in root `tests/unit/frozen-maia-inference-regressions.test.ts` and `tests/unit/maia-coverage-client-regressions.test.ts`; the app-side browser/container parity consumer is retired. Existing root inventory registration remains authoritative for the retained consumers.

## PR #290 lifecycle repair

All cases are discovered by `node test.mjs protocol` in `tests/protocol.test.mjs`; the existing fake child adds only an explicit startup gate. New tests use bounded timeouts, controlled termination and readiness signals.

- `repair A1` / `repair A2`: a pre-cancelled newest job invalidates older replacements before predecessor exit and during model warmup; stale terminal receipt, cleared timer, no inference/result and unrelated-job recovery. Both fail on reviewed supervisor `9236de7e` by observing an obsolete result.
- `repair A3`: future cancellation alone preserves the eligible replacement.
- `repair A4`: the actual result handler independently fences controlled stale active state and preserves the terminal receipt.
- `repair B1` (including B2 replay): every retained older cancellation ID automatically receives final staleness, with strict schema validation, late-job rejection and final acknowledgement replay. Fails on `9236de7e` with neither final ID present.
- `repair B3` / `repair B4`: workspace/job/revision isolation, future tombstone isolation, exactly 64 retained final IDs and no invented overflow acknowledgement.
- `repair C1` / `repair C2`: ordinary replacement during gated warmup; real child exit precedes termination acknowledgement even during supersession.
- Existing `response loss exact replay`, `completed generation replay`, and `worker crash` cases own C3 result/provenance and final-failure replay.
- `repair C4`: conflicts in digest, ordered moves, constraints and model, invalid records and receipt-capacity rejection preserve valid active authority.
- `repair C5`: drain/interrupt close during predecessor termination, queued replacement, warmup and supersession; drain-to-interrupt wakeup; immediate dispatch/result shutdown fences and synchronous readiness interruption.
- `repair C6`: reentrant advancement sees completed tombstone settlement; pending dispatch, admission cancellation and shutdown remain coherent. A synchronous result injected by a `requested` acknowledgement listener is fenced; its named regression also failed before the cancellation fence was moved ahead of notification.
- `repair C7` plus existing resource/protocol suites: admitted early model failure still invalidates queued work; limits, memory, deadlines including warmup, busy behavior, crash recovery and unsupported context retain their regular coverage.
