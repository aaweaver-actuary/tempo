# Durable remediation validation — PR #281

Base: `f43b5f7fb4d7ddafeb3124a95f4fa3d64556abc4` (merged #278).
Tested implementation: `2e2d8cab54193033c02b0a6b3bdbc76f7ab5bd19`.
Branch: `codex/learning-remediation`.

The handoff commit adds this record and its evidence summary only. Package,
contract, migration, dependency, runner and test inputs match the tested revision.
The registered artifact is `tempo-learning-remediation` 0.1.0; Game Evidence
2.0.0 exports unchanged v1 and explicit v2. No registry upload is claimed.

## Executed checks

| Command / proof | Actual result |
| --- | --- |
| `TEMPO_COMPONENT_PYTHON="$PWD/services/learning/remediation/.venv/bin/python" TEMPO_REMEDIATION_EVIDENCE_DIR=/Users/andy/tempo/test-results/remediation-281/native-release node tools/component-build/test-lane.mjs --component learning-remediation` | **36 pure + 10 tooling + 41 real PostgreSQL passed**, complete independent collection/execution/JUnit inventories; no skips/deselections/failures. |
| Standard wheel/sdist builds from the two components' declared inputs; fresh installs and guarded package suites on CPython 3.12.12 and 3.14.8 | **36 passed in each of four consumers** (wheel and sdist on each interpreter); build sources removed, service imports/connections denied. Intentional no-match probes returned pytest exit 5. |
| `services/learning/remediation/.venv/bin/python contracts/qualify_artifacts.py --python 3.12 --report <evidence>/contracts-312.json` and the same command with `3.14` / `contracts-314.json` | **495 contract cases on each runtime**, all build/install/producer/metadata-only next-patch stages passed; temporary resources removed. Includes the Game Evidence 2.0.0 → synthetic 2.0.1 v1 compatibility check. |
| Contract-only environment: `python -m pytest contracts/tests --import-mode=importlib -q` | **59 passed**: catalog, consumer exchange, cross-contract and dependency/coherence checks. |
| `TEMPO_COMPONENT_PYTHON=<contract-consumer>/bin/python node tools/component-build/test-lane.mjs --component game-evidence-contracts` | **89 passed**, zero failures/skips. |
| Contract-only environment: `python contracts/export_schemas.py --check`; `python contracts/check_dependencies.py` | **31 schemas verified**; five packages retain their declared import boundaries. |
| `node tools/component-build/check-components.mjs --component learning-remediation --component game-evidence-contracts` | **Passed**: actual metadata, public exports, migrations/build inputs and registered suites. |
| `TEMPO_TEST_TIMING_DIR=test-results/remediation-browser-retry node scripts/test-postgres-docker.mjs --mode browser --browser-file frozen-opening-order.spec.ts` | **1 native Chromium case passed (4.1s)**; issued board/attempt survives frozen-order refresh/replacement. Owned stack cleanup passed. |
| `npm run check:conflicts`; `git diff --check`; byte-preservation diff against base for v1 models/schemas/fixtures/producer | **Passed**, v1 wire inputs unchanged. |

The final native image identity was
`sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336`
(`postgres:18.6-trixie`). Runtime tools: Node 26.10.0, npm 11.19.1,
pytest 9.1.1, Pydantic 2.12.5 and psycopg 3.3.6. The browser host installed only
`@playwright/test`, `playwright`, `playwright-core` 1.63.0 and `chess.js` 1.4.0;
their versions/integrities matched the root lockfile. No whole-repository host
dependency installation was needed.

Contract artifact proof was executed at `110717f3`; all its package/tooling inputs
are unchanged in the tested implementation. Browser proof was executed at
`a6a1ca27`; the later change affects only the unadopted remediation module and its
regression, so its actual legacy browser/reader/build inputs are unchanged.
These are explicit evidence reuses, not newly executed claims for another scope.

## Retained artifact identities

| Artifact | SHA-256 |
| --- | --- |
| `tempo_learning_remediation-0.1.0-py3-none-any.whl` | `91125c6cfbf8f3dcbc999c7e1c6b0ebb949a36b5e83ed4dd8892c984edad068b` |
| `tempo_learning_remediation-0.1.0.tar.gz` | `dfdb1f0cf6166525a5acbd747e65b6aec9d74cdfc119ea5a217903dbe3c1f8b5` |
| `tempo_game_evidence_contracts-2.0.0-py3-none-any.whl` | `db89ba968cbf6ef67cff65dee83b888eea9ca742903bf093c7a938f2d9efce37` |
| `tempo_game_evidence_contracts-2.0.0.tar.gz` | `dcc75f8ae87aad1387dd670e865ef5fde16e22e4451a01ed05c7ff8ffe26b008` |

Artifacts, exact commands, source-input hashes, JUnit/collection/execution
receipts, native resource reports, browser logs/timings and the minimal browser
dependency lock are preserved outside the task checkout at
`/Users/andy/tempo/test-results/remediation-281/`. The latest artifact proof is in
`release-proof/`; the latest native proof is `native-release/`.

## Failure history and cleanup

The first native invocation was interrupted by the host/session disruption and
left an incomplete report; it is not passing evidence. The missing temporary
checkout was recovered from the pushed commit into a managed checkout. The exact
interrupted container was found exited, its owner/disposable labels verified,
and only that container was removed; absence was verified. Recovered native
runs subsequently passed and verified teardown.

A new bounded-read regression initially failed with 1,001,000 historical row
visits. The corrected parameterized frontier-first lookup passed the unchanged
row bound; its final EXPLAIN visited zero historical attempt/settlement rows
against 1,000 fixture history pairs. The failed run and final plan are retained;
cleanup passed in both runs.

The first browser attempt failed before selecting tests because the host lacked
`@playwright/test`. Its stack cleanup passed. After installing the exact minimal
locked dependencies, the selected case passed; both logs are retained.

Native fixtures and browser projects removed only their verified owned
containers/volumes/networks/images. Source-free build/consumer workspaces were
removed after preserving their reports and artifacts. Final checkout cleanup
retains committed/pushed refs and evidence; it does not delete branches or other
tasks' resources. Shared caches, base images, live study data, frozen live ranks,
settings and backups remain untouched.

## Scope and remaining owners

This proves the independently installed module, semantic contract consumer,
bounded pure behavior and actual Learning PostgreSQL repository. Admission
callback fixtures prove the borrowed-transaction seam and unchanged schedule/
history sentinels; they are not production A/F writer integration. The browser
case proves the existing production frozen-order reader, not new wire-v2 intake.

A must provide authenticated/current source and accepted-definition facts,
acquire encompassing locks before the module lock, bind the frontier at initial
authoritative issuance, and call settlement alongside its genuine review effects.
A must commit before publishing a completed acknowledgement. C owns observed-game
computation and consumes the published fixtures; F adopts these artifacts through
A's published runtime interfaces and ordered migration registration. No dormant
integration patch or second queue writer is included.

The module implements the selected clean-only settlement policy, fresh reviews
for late imports, post-acceptance issuance for unknown times, current source/
content fences, explicit withdrawals and stable satisfaction across equivalent
reanalysis. Full #230/#231/#232 policy extraction, full #41, production wire-v2
activation, domain cutover, deployment and full-system/hosted qualification remain
outside this bounded PR. Actions is unchanged; no merge qualification is claimed.
