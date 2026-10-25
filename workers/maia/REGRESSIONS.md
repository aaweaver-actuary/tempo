# Maia inference regression ownership

Regular commands: `node test.mjs pure` and `node test.mjs model`. Empty selections
fail; Node case events retain executed identities. No backend, database or browser
harness is required.

- `tests/artifact.test.mjs`: immutable external inputs, exact reconstructed model,
  vocabulary/schema bytes, corrupt-source rejection, transitive package boundaries
  and symlinked qualifier entrypoint.
- `tests/core.test.mjs` and `tests/model.test.mjs`: legality, both orientations,
  castling/en-passant/promotions, vocabulary bijection, strict ratings, exact-set
  softmax, invalid outputs and installed provenance/load failures.
- `tests/real-model.test.mjs`: real ONNX inference, full legal universes, subset
  conditionality, both orientations, two ratings, determinism and unsupported input.
- `qualify.mjs restricted`: installed public import without polling, dependency
  closure and immutable hashes.
- `tests/contracts/test_exchange.py` and its reference consumer retain shared
  schema/result-acceptance invariants; they do not prove an executable supervisor.

The unused standalone supervisor, CLI, resource admission, fake child, protocol
suite and observed-container exchange consumer are retired together. Their old
replay/cancellation/resource claims no longer apply to this computation package.
Core/model and descriptor checks are retained, not replaced by deletion assertions.

Legacy coverage compatibility remains in root
`tests/unit/frozen-maia-inference-regressions.test.ts` and
`tests/unit/maia-coverage-client-regressions.test.ts`. Root inventory registration
remains authoritative for those retained consumers.
