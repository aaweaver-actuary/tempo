# B01 controlled-browser pilot

Implementation test plan: production-local static hosting and strict API interception are the changed behavior. Plausible failures are live API fallback, method/query/body mismatch, swallowed unmatched traffic, producer drift, missing/duplicate/skipped identities, and leaked host processes/ports. Consumers are the selected native-browser cases and existing frontend response decoders. Smallest proof is the controlled host/matcher/inventory unit file, then provider parity and receipt/continuation consumers, followed by the nine native identities. Run lint, types, and local/controlled production builds. CI owns the existing candidate gate; B04 owns controlled-lane wiring. No local full gate or rendering baseline change is planned.

The pilot is additive: original tests and full-system configuration remain authoritative. B00's board-input and deletion/reload failures are outside this PR. Original cross-browser cases retain their existing blocked service-worker setting; phone cases retain real production shell registration. Offline/cache/update cases await a separate routing experiment.

## Running the pilot

Install locked Node dependencies once with `npm ci`. Install the lockfile-compatible
Chromium, Firefox and WebKit binaries with `npx playwright install` when absent.
Run `node scripts/test-controlled-browser.mjs` with loopback/browser capabilities.
The command always runs exactly nine identities; filtering cannot qualify the pilot.
No Docker, Python, PostgreSQL, Redis, FastAPI, Celery or backend credentials are
required by this runner. It starts a production build child, one loopback static
host in the runner process, and one Playwright child. Its environment allowlist
excludes backend configuration, proxy overrides and credentials.

The existing lane's dependency graph is selected Playwright identities → disposable
product host → API/PostgreSQL/Redis/workers (plus product image builds/startup).
The controlled graph is selected Playwright identities → explicit context routes
and production frontend assets → Node static host. Python is required only by the
separate provider-contract regression, never by the native controlled runner.

`test-results/controlled-browser/run-*/evidence.json` records revision/dirty-overlay
provenance, browser revisions, owned origin, child commands/PIDs, build/startup/
execution/teardown durations, API escapes and verified cleanup. Playwright reports,
existing timing spans, traces and per-case traffic are beside it. Wait/hook spans
are nested diagnostic unions, not extra suite time. The build is removed after
success or failure; diagnostics remain. A request reaching the host's API guard,
a caught unknown request, a skipped/missing/duplicated/retried identity, assertion
or native-action drift, or cleanup failure prevents success.

`startup.json` is a checked bounded transport snapshot produced by real backend
response functions against a temporary private SQLite fixture. `generate.py`
never runs the application lifespan and prohibits network access. Catalog records
are bounded to four real serialized packs and their themes; writer health is
omitted rather than claiming a running writer. Diagnostics explicitly describe
unavailable storage. `receipts.json` exercises the actual operation-receipt
serializer using controlled receipt rows. These contracts prove response
compatibility, not PostgreSQL durability or queue issuance.

The pilot ledger records original/new file/title/project identities, exact printed
assertions and native actions, their fingerprint, viewport parameters, and retained
native/full-system proofs. Originals remain unchanged. B04 must review this ledger
and wire the standalone runner before proposing any regular-lane movement.
