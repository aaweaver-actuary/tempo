# Queue wire compatibility fixtures

`producer.json` is generated from synthetic database rows by executing the current `_queue_payload`, `prepared_queue`, `queue_manifests`, and Learning `decision_manifest` functions. The generator extracts the route and manifest functions from their source AST to avoid importing the server composition root; its substituted reads, clock, and issuance are local fakes. This proves serialization compatibility, not database queries, issued-head persistence, or settlement. The unavailable case forces the optional evidence read to fail; the empty case returns a refreshing projection. Legacy absence and invalid fields are mutations of these generated payloads in the test.

Regenerate from the repository root using the backend Python dependencies:

```sh
PYTHONPATH=backend:database:services/learning/runtime/src:packages/scheduling/src python tests/fixtures/queue-wire/generate.py
```

`prepared_at` is removed before queue computation, as in `savePreparedTraining`; the snapshot itself stays in the card. Run `npx vitest run tests/unit/queue-producer-wire-regressions.test.ts` for pure computation, and `node tests/fixtures/queue-wire/worker-proof.mjs` for the bundled production entry point in a real Chromium Worker. The latter needs the installed Playwright Chromium browser.
