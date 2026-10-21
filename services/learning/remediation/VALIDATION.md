# Durable remediation validation — PR #281

The original qualification below is historical. The current independent-review
repair results are recorded in [the follow-up](#independent-review-repair-follow-up)
and `evidence/review-repairs.json`; old artifact hashes are not the repaired artifacts.

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
| `TEMPO_TEST_TIMING_DIR=test-results/remediation-browser-retry node database/tests/test-postgres-docker.mjs --mode browser --browser-file frozen-opening-order.spec.ts` | **1 native Chromium case passed (4.1s)**; issued board/attempt survives frozen-order refresh/replacement. Owned stack cleanup passed. |
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

## Independent-review repair follow-up

Status: **READY FOR INDEPENDENT RE-REVIEW** of the bounded component repairs.
Starting PR head: `1bcc084983cfb479d78fe5ddffa68ec1506cef9e`.
Tested implementation: `c3a956b310f195c57291a5ea46591b61acbd7123`.
The follow-up evidence commit changes this validation record and compact evidence
only, outside both packages' declared build inputs. Captured build-input hashes
were checked against the complete current inputs after qualification.

### Repairs and preserved guarantees

| Fix | Result |
| --- | --- |
| A — `PostgresRepository.settle` | Historical contributions match the immutable card revision without historical publication equality. Current attempt authority is still checked exactly. Native cases prove truthful committed receipts, replay, revision separation, and unchanged markers for qualifying reviews that settle no current work. |
| B — v2 `EvidenceAcknowledgement.completed_authority` | Rejects duplicate accepted IDs; the exported ordered-list schema has `uniqueItems: true`. Fixed fixtures prove Pydantic/schema parity, empty and ordered unique lists, serialization, strict validation and existing status constraints. |
| C — `order_candidates` | Eligible duplicates preserve the miss route in either input order; ineligible misses cannot replace eligible ordinary routes. Protected issued heads and ordinary frozen order remain intact. |
| D — `validate_delivery` | Only producer occurrence UTC-normalization overflow becomes `terminal_failure/occurrence_out_of_range`. Native failures write no acceptance or completed receipt; normal offsets and invalid trusted caller times retain their separate behavior. |

Current publication authorization, no cross-revision settlement, late/unknown-time
and captured-frontier fences, genuine-review-only settlement, invalidation,
delivery/review replay, rollback and sole-writer admission checks all remain
covered by the final passing native inventory. `_OUTSTANDING`, `has_current_miss`,
`_check_attempt`, transaction/lock ownership and migration SQL are unchanged.
V1 model/schema/fixture/producer inputs are byte-for-byte unchanged against both
the starting head and original base. No package version changed.

### Exact validation and outcomes

All final checks below were rerun against the repaired inputs. No historical
test result or artifact hash is reused as current evidence. Commands were run
from the isolated checkout root unless the table specifies otherwise. `EVIDENCE`
means `/Users/andy/tempo/test-results/remediation-281-repairs-20261015`.

| Command | Exit and actual outcome |
| --- | --- |
| From `services/learning/remediation`: `uv sync --locked --no-editable --python 3.12`, then `uv sync --locked --no-editable --reinstall-package tempo-learning-remediation --reinstall-package tempo-game-evidence-contracts` after source edits | 0; installed noneditable packages, component-local locked dependencies only. Contract was refreshed again after schema export. |
| `uv venv contracts/.venv --python 3.12`; `uv pip install --python contracts/.venv/bin/python --require-hashes -r contracts/requirements-test.lock`; `uv pip install --python contracts/.venv/bin/python --no-deps ./contracts/learning ./contracts/content-publication ./contracts/game-evidence ./contracts/intelligence ./contracts/analysis-jobs` | 0 each; isolated contract-only environment. Game Evidence was reinstalled after its model/schema edits. |
| `services/learning/remediation/.venv/bin/python -m pytest -c services/learning/remediation/pyproject.toml services/learning/remediation/tests/test_policy.py services/learning/remediation/tests/test_validation.py services/learning/remediation/tests/test_boundary.py -q --junitxml=$EVIDENCE/focused-pure.xml` | 0; **52 passed**, no failures/skips. |
| `contracts/.venv/bin/python -m pytest contracts/game-evidence/tests/test_v2.py -q --junitxml=$EVIDENCE/focused-v2.xml` | 0; **35 passed**, no failures/skips. |
| `TEMPO_COMPONENT_PYTHON="$PWD/services/learning/remediation/.venv/bin/python" TEMPO_REMEDIATION_EVIDENCE_DIR="$EVIDENCE/native-final" node tools/component-build/test-lane.mjs --component learning-remediation` | 0; **52 pure + 10 tooling + 47 actual PostgreSQL passed**; complete collection/execution/JUnit inventories, no skips/deselections/failures; owned fixture cleanup verified. |
| `TEMPO_COMPONENT_PYTHON="$PWD/contracts/.venv/bin/python" node tools/component-build/test-lane.mjs --component game-evidence-contracts` | 0; **99 passed**, no failures/skips. |
| `contracts/.venv/bin/python -m pytest contracts/tests --import-mode=importlib -q --junitxml=$EVIDENCE/shared-contracts.xml` | 0; **59 passed**, no failures/skips. |
| `contracts/.venv/bin/python contracts/export_schemas.py --package game-evidence`; the same with `--check` | 0 each; **6 exported / 6 verified**; only v2 acknowledgement schema bytes changed. |
| `contracts/.venv/bin/python contracts/export_schemas.py --check`; `contracts/.venv/bin/python contracts/check_dependencies.py` | 0 each; **31 schemas verified**, five contract dependency boundaries passed. |
| `TEMPO_COMPONENT_PYTHON="$PWD/contracts/.venv/bin/python" node tools/component-build/check-components.mjs --component learning-remediation --component game-evidence-contracts` | 0; selected registrations, exports and package inputs passed. |
| `services/learning/remediation/.venv/bin/python "$EVIDENCE/verify_installed.py"` | 0; both freshly built packages installed from wheel and sdist on **CPython 3.12.12 and 3.14.8**. Each of four consumers passed **52 remediation + 99 Game Evidence cases** with independent collection/execution/JUnit checks under service-import/connection guards. Installed imports, packaged migration and v2 schema verified; all source-free workspaces removed. Four deliberate zero-selection probes returned the required exit **5**. |
| `contracts/.venv/bin/python contracts/qualify_artifacts.py --python 3.12 --report "$EVIDENCE/contracts-312.json"`; same with `3.14` / `contracts-314.json` | 0 each; **505 installed contract cases per runtime**, all restricted build/install/producer/metadata-only patch compatibility stages passed; temporary resources removed. |
| `npm run check:conflicts`; `git diff --check`; preservation diffs against starting head and original base | 0 each; conflict/whitespace checks and v1 byte preservation passed. Migration and legacy backend/frontend have no repair diff. |

The source-free consumer proof adapts the retained prior `verify_installed.py`
workflow to this checkout/evidence path, adds the locked contract test dependencies
and complete Game Evidence suite, and explicitly checks installed v2 schema resources.
It builds only the two components' declared inputs and removes copied source
before consumption. Exact subcommands, hashes and complete identities are retained
in `installed-proof/installed-artifacts.json`; the script and report hashes are in
`evidence/review-repairs.json`.

The unchanged historical-frontier query regression visited **0** historical rows
against 1,000 review pairs (unchanged bound: 16). The additional same-frontier
diagnostic visited **1,000** rows against 1,000 nonsettling attempts, below its
linear bound of 2,016. Both EXPLAIN ANALYZE plans are retained with native evidence.
No query or schema redesign was needed.

### New artifact identities

| Artifact | SHA-256 |
| --- | --- |
| `tempo_learning_remediation-0.1.0-py3-none-any.whl` | `563826e0668c0cccd48bfb09c6e20e815e0257b230829364355a52f020bea894` |
| `tempo_learning_remediation-0.1.0.tar.gz` | `07aa54c0896955a8057ab404edf8aa5d2f074e6dd1469b263231b1bd0970752b` |
| `tempo_game_evidence_contracts-2.0.0-py3-none-any.whl` | `bdffa6ef5ef37e9b7a24fe3546b0b6e8b4b2968ed75af708f504125ace231777` |
| `tempo_game_evidence_contracts-2.0.0.tar.gz` | `13f2a2e04ae15f95c73a9edfbd5f1fe3e8cf99640d2094b1f286df4a3cba18e4` |

### Failure history, resources and remaining scope

Before runtime edits, the new pure cases reproduced **3 failures / 49 passes**,
the v2 cases **1 failure / 34 passes**, and actual PostgreSQL **4 failures /
43 passes**. These were the intended defect reproductions; their logs remain
separate from final passing evidence. The failing PostgreSQL fixture also verified
cleanup. The first registered lane could not import TypeScript and ran no tests;
installing only `tools/component-build`'s locked TypeScript 5.9.3 resolved it.
The subsequent complete registered lane passed without changing the runner.

Disk availability was checked before creating the native fixtures (about 32 GiB).
Both uniquely owned PostgreSQL containers verified removal; their databases were
disposable tmpfs, and no live database target was accepted. Source-free consumers
and contract qualification workspaces also verified removal. Raw logs, test
identities, JUnit, input hashes, current artifacts and reproduction scripts are
retained in the new evidence directory above; the original `remediation-281/`
evidence is preserved. The disposable repair checkout is eligible for removal
after ordinary fast-forward push verification, retaining the task branch and evidence.

Focused review confirmed no connection ownership, second admission writer,
encompassing-lock acquisition, synthetic history/FSRS effects or unrelated changes
were introduced. No full-system, browser, hosted Actions, production A/C/F adoption,
wire-v2 activation, registry release, merge, deployment or live migration is claimed.
There are no remaining selected-test failures. PR #283 remains separately owned
and must incorporate the repaired #281 head later; no downstream rebase was performed.
