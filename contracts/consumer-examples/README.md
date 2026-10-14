# Independent TypeScript consumers

These examples consume actual Python producer output through committed JSON Schema artifacts and separately maintained semantic checks/expected decisions. No Python model is imported or generated into this consumer.

From the checkout root:

```sh
npm ci --prefix contracts/consumer-examples --ignore-scripts
contracts/.venv/bin/python contracts/run_tests.py --typescript
```

The Python test runner creates a temporary producer exchange directory, runs the compiled TypeScript tests with its explicit location, then removes it. Direct `npm test` requires `TEMPO_CONTRACT_PRODUCER_OUTPUT` to name such an exchange directory; missing input is an error, never a silent zero-test pass. Structural schemas alone do not enforce cross-field coherence. The consumer's `semantics.ts` checks those invariants; shared versioned negative fixtures verify both consumers reject them.

No root npm workspace or lockfile changes are required. Artifact patch compatibility is separately proved by `qualify_artifacts.py`; it does not publish a synthetic patch or promise arbitrary N/N−1 compatibility.
