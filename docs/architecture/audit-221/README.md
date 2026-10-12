# Reproduce issue #221 characterization

Use an isolated checkout of the published audit branch. Its parent main revision
is recorded in REPORT.md. Install locked Node dependencies with `npm ci`. Docker
Desktop must be running and share the checkout; browser targets require elevated
Docker/loopback access under Codex. Python fixture code runs inside the existing
schema image; no host Python environment is required by this overlay.

```sh
make plan
make ui-file FILE=review-outbox-cross-tab.spec.ts
```

Smallest ordering reproduction:

```sh
make view VIEW='review_outbox_two_tabs_preserve_distinct_completed_intents: A-B'
```

Native proof files are written to `TEMPO_TEST_TIMING_DIR` (default
`test-results/performance`); traces/screenshots are retained in the runner's
`test-results/browser-postgres-<pid>` directory. Record the clean audit commit and
production baseline separately. Any subsequent source edits invalidate results.

The runner creates fresh credentials, ports, containers and volumes, then owns
its teardown. Never target the live stack, reuse mutable state, change study
configuration, or treat missing/pending/failed receipts as authoritative success.
The Chromium-only debugger barrier delegates all reads/writes to native storage.
The primitive harness bundles the actual outbox and its dependencies with esbuild;
it introduces no separate storage implementation or application test endpoint.

The new assertions are normative: failure preserves evidence of an unresolved
requirement. Do not add expected-failure markers or skip them for a green PR.
A published failing audit ref does not qualify a production correction for merge.
