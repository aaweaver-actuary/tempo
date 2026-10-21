# Content #108 validation and handoff

Assignment/base main: `0c7ef737bb2daacd8f671f252ce5a1d0787b1871`.
Tested executable: `b2feaa21c9b3e419e346585701efabe598a378c8` on
`codex/content-service-108`. The subsequent handoff commit changes documentation
only. No live deployment, data migration, Learning activation, or merge occurred.

## Reproduction and exact results

Run from the isolated repository root. All imports use installed distributions,
with no repository-root PYTHONPATH.

```sh
UV_CACHE_DIR=/private/tmp/tempo-content-108-uv-cache uv sync --project services/content --locked --no-editable --python 3.12 --reinstall-package tempo-content
services/content/.venv/bin/python services/content/run_suite.py pure
services/content/.venv/bin/python services/content/integration/run_postgres.py /private/tmp/tempo-content-108-evidence/native-final --benchmark
UV_CACHE_DIR=/private/tmp/tempo-content-108-uv-cache services/content/.venv/bin/python services/content/integration/qualify_artifacts.py /private/tmp/tempo-content-108-evidence/artifacts-final --consumers
TEMPO_COMPONENT_PYTHON="$PWD/services/content/.venv/bin/python" node tools/component-build/check-components.mjs --component content
TEMPO_COMPONENT_PYTHON="$PWD/services/content/.venv/bin/python" node tools/component-build/test-lane.mjs --component content --list
TEMPO_COMPONENT_PYTHON="$PWD/services/content/.venv/bin/python" UV_CACHE_DIR=/private/tmp/tempo-content-108-uv-cache node tools/component-build/test-lane.mjs --component content
npm run check:conflicts
git diff --check
```

Local runs reused the unchanged component tools from the primary checkout
read-only, with `TEMPO_COMPONENT_ROOT=/private/tmp/tempo-content-service-108`,
instead of installing Node/frontend dependencies. Preserved logs record the
exact absolute tool/interpreter paths.

| Proof | Result |
| --- | --- |
| Installed Content regular suite | 64 passed, zero failures/skips |
| Native PostgreSQL owning suite | 32 passed in 17.23 s |
| Registered component lane | 64 regular + 32 native + 105 chess-core + 99 Content-contract = 300 passed, zero failures/skips |
| Source-free wheel/sdist consumers | Identical 64 identities in Python 3.12 wheel/sdist and 3.14 wheel/sdist: 256 executions |
| Installed Learning/Game Evidence consumer | 1 named regression plus standalone assertions passed; Content absent from consumer environment |
| Boundary/discovery/packaging guards | Exact exports/dependencies, seven schemas, nonzero discovery, CLI/import isolation, packaged migrations, inventory/JUnit equality and zero-selection exit-5 probes passed |
| Conflict and whitespace checks | Passed |

Native cases cover both deletion lock schedules with observed PostgreSQL
blocking, durable bounded collection/re-registration, opposite-color shared
positions, crash/lease recovery, fairness, migration/grants, rollback, forged
payload/provenance rejection, immutable replay, policy coverage, special legal
moves, and installed CLI operations across fresh processes and two generations.
Earlier 63/18-case and diagnostic 28-case runs were development evidence only.

## Storage and native identity

Content owns one workspace-bound logical database, initial append-only migration
`0001_content`, and 18 tables. Migration SQL SHA-256:
`79655bc42742f7c22089f8ae4c80e166e35ae3980983e90bfb9e420214225b3a`.
The manifest/ledger bind its identity/checksum; startup applies no migration.

Tables own workspace metadata, staged input/manifests, sources/revisions,
tasks/leases/checkpoints, route artifacts/memberships/occurrences, canonical
positions/replies, exercise identities/versions/generation mappings, responses,
immutable publications/provenance and the collector. No Learning tables or
cross-service SQL are introduced.

Final native/benchmark owner: `tempo-content-108-983be067d5561bc8`; database
`content`; roles `content_owner` and `content_runtime`. Fresh guard databases
`learning`, `game_evidence`, and `intelligence` rejected the runtime credential.
The inspected `postgres:18.6-trixie` image was
`sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336`.
The container had one CPU, 512 MiB and an owned persistent volume. Both removals
were verified, including the registered lane's separate native resources.
No live Study resource was inspected, restarted, migrated or removed.

## Representative measurement

Fixture: 512 routes, up to 64 plies, 2,048 explanation exercises, 935,241 input
bytes; 512 artifacts, 33,127 occurrences, 31,859 canonical route positions,
15,793 opponent positions and 494,581 legal replies.

| Measurement | Observed |
| --- | --- |
| Cold installed import / readiness | 0.146 s / 0.0147 s |
| Source registration | 2.002 s |
| Reconciliation | 4,604 slices; 104.849 s summed processing |
| Slice median / maximum | 13.32 ms / 274.78 ms |
| Writer transaction median / maximum | 2.37 ms / 243.98 ms |
| Publication construction/persistence | 0.510 s |
| Restart/read/replay | 2.381 s |
| Peak observed driver RSS | 84,197,376 bytes |

Writer durations include pool acquisition/commit; RSS includes the benchmark
driver, not PostgreSQL. Full setup/tests/measurement/cleanup took 140.78 s.
These observations describe one host/fixture, not arbitrary scale or production
latency. Canonical legal universes and immutable history are retained; collection
owns obsolete memberships and orphan route caches.

## Compatibility, evidence and remaining owners

Content emits unchanged shared v1 Publication, supporting 1–10,000 definitions
and all five exercise kinds, with independently validated chess legality.
Learning's supported `study_exercise` adapter preserves Content IDs. A valid
129-definition Content publication is explicitly rejected by Learning's current
128-fact bound. Its legacy opening adapter separately requires historical
FEN/moves card hashes. No truncation, chunk activation or identity rewriting
conceals these limitations. Game Evidence consumes its existing service-local
snapshot with both colors, routes/bindings and matching publication; activation
is null. No new shared wire schema is declared.

#193 owns durable user approval, identity/history bridging, acceptance/activation,
atomic complete-generation serving, coordinated large-publication staging and
empty-source retirement. README records complete-bundle versus paged-staging
options and atomic-switch requirements. #265 owns full PGN/connected-Study
import, integrity interpretation and segmentation; this service accepts structured
snapshots with stable source keys. These are intentional handoffs.

Preserved evidence includes exact commands/inventories/JUnit, source/dependency
hashes, benchmark rows/timings, installed artifacts/hashes, consumer results and
resource cleanup. The final handoff identifies its durable absolute directory
and pushed revision. Final artifact hashes in `artifacts.json` are authoritative.

Read-only GitHub checks reported Actions disabled, no main branch protection
(404), and no repository rulesets. Settings were unchanged. Focused local
qualification is complete; hosted/full-system, deployment and Learning activation
qualification are not claimed.


## PR #288 repair qualification — October 16, 2026

### Revision and evidence binding

Reviewed starting head: `22db9b5198cc97f37ca8c4ed05449cfa1c7129aa`.
Main/base: `0c7ef737bb2daacd8f671f252ce5a1d0787b1871`. Main remained unchanged at final refresh.
Repair commit: `0f78f6672e0a11cee42dfbdd9381a8dfac728f1a`. Final tested executable:
`ccfcbe71e7db3f4b7f517545e8324e58f0708948`. The subsequent commit adds only this validation report;
its exact final pushed SHA, GitHub readback and cleanup state are recorded in
`HANDOFF.json` at the evidence location and in PR #288. This avoids treating a
self-referential documentation SHA as executable evidence.

Durable evidence directory:
`/Users/andy/.codex/visualizations/2026/10/16/01a1449b-29f3-7261-8c0b-f89c42289acf/content-288-repair-evidence`.
`qualification-commands.json`, `qualification-summary.json`, `self-review.json`,
`hosted-settings.json`, regular/native collection/execution JSON and JUnit,
artifact reports/hashes and logs bind commands and results to the tested source.
The source/artifact hash inventory describes the executable commit above; the
later validation-only documentation commit was not rebuilt into these artifacts.

### Findings, repair and regression proof

| Finding | Cause and substantive repair | Named regular owner |
| --- | --- | --- |
| R1 | Readers previously selected generations and queried rows through unrelated transactions. Read-only repeatable-read sections now share one connection across all contributing queries. Source-scoped pages have strict versioned workspace/source/kind/revision/generation cursors; coverage and pages expose matching metadata. Missing mandatory inventory fails explicitly. | `test_coverage_snapshot_survives_generation_replacement_and_collection`; `test_inventory_cursor_rejects_changed_source_generation` (both readers); cursor validation, corruption, transaction rollback and CLI cases in REGRESSIONS.md |
| R2 | Known-stale payloads staged before final rejection. One coherent read-only preflight now rejects before writes and returns identical replay without staging, while final source-locked CAS remains. | `test_stale_revision_is_rejected_before_any_input_staging`; identical replay, unknown-source, concurrent-editor and original interrupted-stage cases |
| R3 | Unscoped lookups returned the first matching source. A bounded current-revision query detects zero/one/two matches; ambiguity requires `--source-key`. | `test_unscoped_repertoire_lookup_rejects_ambiguous_source_keys`; `test_unique_and_missing_repertoire_lookups` |
| R4 | Readiness rejected excess powers but ignored absent required grants. Owner migration/readiness now share one 18-table privilege specification; every required privilege is checked independently. | `test_runtime_readiness_rejects_missing_required_grant_and_owner_repairs_it` (INSERT/UPDATE/SELECT); original forbidden-grant owner retained |

Baseline: all original 32 native cases passed; 12 new cases failed (34 total
passing, 12 failing). `baseline/instrumentation.patch` contains only the optional
coverage pause hook; its SHA-256 was verified against the runner's source hash.
The baseline G1 reader returned its previously covered authored reply as
uncovered after actual collection. Stale-edit row counts, unscoped ambiguity and
three missing grants each independently reproduced their defects. The additional
nested-JSON regression recorded `RecursionError` before the guard, then passed
with structured `invalid_cursor`; see `cursor-nesting-baseline.log`.

The R1 schedule publishes G1 with covered e7e5/Nf3 continuation, establishes the
reader snapshot and pauses after its opponent occurrence. A separate service
admits/completes/publishes G2 with materially different d4 inventory. Collection
removes G1 memberships, occurrences and its artifact, verified through fresh
committed queries. Releasing the reader returns complete G1 with the covered
reply and G1 revision/generation. Fresh G2 reads identify G2; its incomplete stage
returns unavailable with G2 metadata. Events have bounded waits and finally
release; no sleep establishes correctness. Existing lock-order first-holder
schedules remain selected and passing.

The editor regression barriers both distinct writers after staging and before
final admission. Exactly one succeeds; the other gets `stale_source_revision`.
Only one successor revision/generation exists and its complete publication
contains the winner's payload. Partial/crash staging recovery remains selected.

### Executed commands and exact results

Run from `/private/tmp/tempo-content-288-repair`, with
`UV_CACHE_DIR=/private/tmp/tempo-content-288-repair-uv-cache` and
`TEMPO_COMPONENT_PYTHON=$PWD/services/content/.venv/bin/python`.
The evidence journal records absolute argv, environment overrides, exit codes,
UTC timestamps, source state and durations for every command:

```sh
uv sync --project services/content --locked --no-editable --python 3.12 --reinstall-package tempo-content
services/content/.venv/bin/python services/content/run_suite.py pure --evidence "$EVIDENCE/regular-final"
services/content/.venv/bin/python services/content/integration/run_postgres.py "$EVIDENCE/native-final" --benchmark
services/content/.venv/bin/python services/content/integration/qualify_artifacts.py "$EVIDENCE/artifacts-final" --consumers
node tools/component-build/check-components.mjs --component content
node tools/component-build/test-lane.mjs --component content --list
node tools/component-build/test-lane.mjs --component content
npm run check:conflicts
git diff --check
```

| Proof | Newly executed result |
| --- | --- |
| Content regular | 103 passed; retained exact collection/execution/JUnit |
| Native PostgreSQL | 55 passed; 146.00 s including benchmark/setup/cleanup |
| Chess-core | 105 passed in registered lane |
| Content v1 publication contracts | 99 passed in registered lane |
| Complete registered lane | 362 passed = 103 + 55 + 105 + 99 |
| Python 3.12 wheel / sdist | 103 / 103 passed |
| Python 3.14 wheel / sdist | 103 / 103 passed |
| Installed artifacts combined | 412 case executions; matching identities, source-free installation |
| Installed Learning/Game Evidence | 1 named regression plus standalone adapter assertions passed |
| Component boundaries/discovery | Content, chess-core and seven-schema Content contract selected and validated |
| Conflict / whitespace | Passed |

No selected skip, deselection, missing/duplicate identity or failed phase was
accepted. Existing strict inventory checks and zero-selection probes remain.
Only Content dependencies and the component checker's pinned TypeScript package
were installed; no frontend or whole-repository installation was performed.

Installed Content artifact SHA-256:

| Artifact | SHA-256 |
| --- | --- |
| Wheel | `235e1a16b969eceaa7513622237e84af121a5decf2e2703b0c19e66eb9f36c70` |
| Sdist | `52902ba81cae5a8d2613a1147906eb3f927edc12b35103bb51c13f07410b0ce6` |

Other dependency and consumer artifact hashes and full input inventories remain
in `artifacts-final/artifacts.json` and `qualification-summary.json`.

### Representative benchmark and preservation

Same fixture: 512 routes, up to 64 plies, 2,048 exercises, 31,859 canonical route
positions and 494,581 legal replies. 4604 reconciliation slices;
111.144 s summed processing; 10.93 ms median and
1788.44 ms maximum slice. Recorded writer median/maximum:
2.07/1777.80 ms. Peak driver RSS:
82,460,672 bytes. Readiness:
0.0366 s; registration:
1.408 s; publication:
0.336 s; restart/replay:
0.110 s.

Historical comparison: 104.849 s summed processing, 274.78 ms maximum slice,
243.98 ms maximum writer and 84,197,376 bytes driver RSS. These are observations
from separate runs on one host, without a statistical performance threshold;
maximum variation alone does not establish a regression. Earlier repair-run
measurements are retained under `first-qualification/`, not substituted for this
final executable's measurements. Read sections are excluded from writer metrics.

Stable source/decision/card identities, canonical legal inventories, immutable
v1 publications, independent packaging, Content-only database ownership,
16-ply slices, 64-occurrence collection and collector lock ordering remain.
Migration SQL SHA-256 remains
`79655bc42742f7c22089f8ae4c80e166e35ae3980983e90bfb9e420214225b3a`.
No production Learning/Game Evidence dependency, activation or live migration
was introduced. Consumer proof preserves all five kinds and Content IDs, both
colors/provenance, null activation, explicit 129-versus-128 rejection and the
legacy opening identity incompatibility.

### Limits, resources and GitHub state

No known remaining R1–R4 defect was found in final self-review; no independent
review is claimed. Known-stale preflight does not reclaim all unadmitted input:
writers losing a later race and crashed writers can leave resumable stages.
[#292](https://github.com/aaweaver-actuary/tempo/issues/292) owns retention,
reference/fencing integrity, shared retries, bounded cleanup and authorized
immutable-table access. No DELETE privilege was granted for that future work.
#193 still owns acceptance/approval/history bridging/atomic activation,
large-publication staging and empty retirement; #265 owns full import execution.

Final benchmark/native owner: `tempo-content-108-67affda85cec2007`; runtime/owner credentials
were generated inside its disposable cluster. Container and volume removal were
verified. Baseline, development, earlier qualification and registered-lane native
resources have their own retained removal receipts. Artifact temporary build and
consumer environments were removed. The task checkout, its virtual environments
and component tooling are removed after remote preservation; final cleanup
verification is recorded in `HANDOFF.json`. Reusable caches and unrelated/live
Study resources are preserved.

GitHub read-only settings: Actions disabled, no repository rulesets, and main
protection returned the explicit “Branch not protected” 404. Settings were not
changed. PR #288 remains open, unmerged and undeployed; final remote head and
structural mergeability are verified after push in `HANDOFF.json` and the PR
summary. Local component qualification is complete; hosted/full-system tests,
deployment/cutover and Learning activation were not performed or claimed.

## Root database bridge extraction (2026-10-21, unmerged source)

Bridge SQL and driver encoding moved into the Content-only `tempo_content_database.adapters.bridge.BridgeStore`; existing application code moved to `tempo_content.bridge.application`, with the public `Bridge(service)` composition preserved. The 22 distinct original bridge SQL statements are unchanged. Duplicate identical queries use the same named adapter method, retaining their original application call sites and order. Source-scope lock ownership, original `_transaction()` sections, read helper boundaries, statement parameters, JSON payloads and all schema/migration files are unchanged. CLI readiness uses the database owner's `readiness_from_url`, with the same driver context and error class.

`PYTHONPATH=services/content/src:database /tmp/tempo-db-review-venv/bin/python -m pytest services/content/tests/test_bridge.py services/content/tests/test_execution_projection.py services/content/tests/test_inventory_cursors.py services/content/tests/test_bridge_storage_boundary.py -k 'not failure_transport_rejects_uncertain_http_outcomes' -q --rootdir=.` passed **121 cases**, selecting only non-network tests relevant to moved application/storage code. An initial pre-extraction inventory run selected the five existing `test_A_failure_transport_rejects_uncertain_http_outcomes` cases; each failed because the sandbox refused loopback `socket.bind` with `EPERM`. They were not retried with escalation, changed, or claimed passing. No live/native PostgreSQL, migration, external delivery, service, deployment, grant, credential or schema operation ran.

`python -m build --no-isolation --outdir /tmp/tempo-content-split-artifacts services/content` built the sdist and rebuilt its wheel from that sdist. Wheel inspection confirms that only the new public package/application/adapter locations are present. A separate task-owned environment installs that wheel and the unchanged required contract/library artifacts for package/import/help/resource checks. The first venv setup attempted the default unwritable UV cache and failed; setup then used the existing task cache at `/tmp/tempo-db-uv-cache`. No shared test environment was modified.

The freshly rebuilt/reinstalled wheel then passed **133 focused installed-package cases** across the same four non-network suites plus `tests/test_packaging.py` (`PYTHONPATH=`; separate `/tmp/tempo-content-split-venv`). The five loopback-only transport cases were explicitly outside this run and remain unverified here. Package tests confirm site-packages import, independent runtime requirements, CLI/help without socket activity, ordered packaged migrations and the existing qualification-inventory rules. Both original Content checklist files now have no concrete SQL/connection operations; public bridge imports resolve to the new composition package. Existing broader computation in the already-centralized `adapters/store.py` remains to be assessed separately; this batch does not claim that entire file has become policy-free.


## Source application helper separation

Five existing decision blocks (56 lines) moved from the root database store to `src/tempo_content/policy/source.py`; call sites preserve their original order. All 92 SQL literals (82 distinct), complete query parameters, fences and hook calls remain unchanged. The previous bridge/CLI nine Python code paths are byte-identical to their ready tested slice.

`PYTHONPATH=services/content/src:database /tmp/tempo-db-review-venv/bin/python -m pytest services/content/tests/test_source_policy.py services/content/tests/test_bridge_storage_boundary.py -q --rootdir=.` passed 21 cases. A fresh sdist→wheel installed into a private environment passed 24 selected policy/boundary/packaging cases (nine unrelated packaging cases deselected). Exact build/selection commands and artifact/source hashes are retained in `/tmp/tempo-content-policy-evidence/manifest.json`. Native PostgreSQL and the five previously denied HTTP loopback cases were not executed or retried. No schema/contract/behavior, deployment or CI change occurred; worker/route/publication orchestration remains extraction work. Private test environments were removed after preserving evidence.

## Reconciliation orchestration separation

`ContentService.work_once` now composes the existing semantic operations into `src/tempo_content/worker/application.py::reconcile_once`. Claim, idle collection, manifest lookup, phase dispatch and error classification preserve their original order and exception scope. The original fenced failure transaction remains in `_fail_task`; its only changed return is the boolean authority result consumed by the application. One-time AST comparison verified unchanged copied orchestration, all 41 other storage methods, all 92 SQL literals (82 distinct), the exact failed-task update/parameters and its fencing section. This disposable comparison is supporting move evidence, not a new SQL-freezing regression framework.

`PYTHONPATH=services/content/src:database /tmp/tempo-db-review-venv/bin/python -m pytest services/content/tests/test_worker_application.py services/content/tests/test_bridge_storage_boundary.py -q --rootdir=.` passed **18 cases**. `PYTHONPATH= /tmp/tempo-db-review-venv/bin/python -m build --no-isolation --outdir /tmp/tempo-content-worker-artifacts services/content` built a fresh sdist and wheel from that sdist. A private environment installed that wheel plus the unchanged required contract/library artifacts offline. With `PYTHONPATH=`, the same two suites and `tests/test_packaging.py::{test_installed_distribution_declares_independent_runtime_and_cli,test_packaged_migrations_have_complete_manifest_and_checksums,test_import_and_cli_help_do_not_initialize_services}` passed **21 cases**. These checks prove selected application/composition behavior and installed package/resource closure; they are not native PostgreSQL qualification. Artifact/source hashes, exact commands and logs are retained in `/tmp/tempo-content-worker-evidence/manifest.json`.

The initial application-only command omitted source `PYTHONPATH` and failed collection with `ModuleNotFoundError: tempo_content`; correcting that invocation selected 12 preparation cases, all passing. No source or selected assertion was changed to hide a failure. No live/native PostgreSQL, loopback transport, schema/migration execution, external delivery, security, deployment or CI operation ran. The five previously denied HTTP bind cases remain unverified and were not retried. Route traversal and publication build/orchestration remain explicit unfinished extraction work.

## Route, publication and inventory projection separation

The four-statement bounded route-window computation moved to `worker/routes.py::route_segment`. Publication exercise-count checking, construction, the existing serialization/decode pass and provenance moved to `publication/preparation.py::prepare_publication`; authoritative reconstruction moved to `publication_authority`. `_inventory_status`'s two-statement retirement/status/stale projection moved to `policy/source.py::inventory_report`. Each function receives the original Content facts at the original call point. One-time AST move evidence reconstructs all four original storage methods exactly after substituting the copied blocks, verifies the other 39 methods unchanged, and confirms all 92 SQL literals (82 distinct), query parameters, transactions, hooks and fences unchanged. This disposable comparison is evidence for the move, not a new SQL-freezing test framework.

Focused source selection passed **20 cases**: `tests/test_content_preparation.py` plus `tests/test_domain.py::{test_issue108_checkpoint_slices_equal_full_route_without_prefix_replay,test_issue108_illegal_authored_route_moves_do_not_produce_inventory,test_issue108_actual_shared_publication_round_trip_and_revision_validation,test_issue108_unordered_source_permutation_has_identical_canonical_payload_and_publication,test_issue108_publication_stable_cards_survive_rename_reorder_and_generation_change,test_issue108_empty_source_requires_retirement_protocol}`. The command uses `/tmp/tempo-db-review-venv/bin/python -m pytest`, `PYTHONPATH=services/content/src:database` and `-q --rootdir=.`. A fresh `python -m build --no-isolation --outdir /tmp/tempo-content-preparation-artifacts services/content` rebuilt the wheel from its sdist. A private environment installed that wheel and the unchanged required contract/library artifacts offline, then ran the same selection plus the three existing installed metadata/migration-resource/import-help cases with `PYTHONPATH=`: **23 passed**. Both affected Mermaid blocks parse with the existing parser; all four affected owner README link inventories resolve.

Exact commands, selected nodes, source/artifact hashes, baseline/current sources and logs are retained in `/tmp/tempo-content-preparation-evidence/manifest.json`. No live/native PostgreSQL, schema/migration execution, HTTP transport, security, deployment or CI operation ran; the five previously denied loopback bind cases were not retried and remain unverified. The private environment was removed after evidence preservation; the shared toolchain and reusable cache were retained.

Remaining mixed coordination is explicit: `load_source` sequences bounded staging/read-back/admission sections; `_route_slice`, `_exercise_slice` and `_binding_slice` sequence input reads, phase transitions and fenced bounded writes; `publication_build` sequences replay/precondition checks, build-time reservation and snapshot/revision reads; `store_publication` sequences replay and authoritative-input reads before its atomic insert guard. Cache/row serialization, manifest integrity, immutable identities, predecessor/revision checks, epoch/fence/lease guards, sorted locks and complete-generation/provenance checks remain persistence responsibilities. The pure preparation moves do not certify the entire store as application-free or make the remaining orchestration a permanent exception.

## Complete binding phase separation

The complete existing `_binding_slice` orchestration moved to `src/tempo_content/worker/bindings.py::reconcile_binding`. The root method composes exact owner operations: existing `_transition`, `_item` and `_hook`, plus `_binding_target`'s two full parameter-bound queries and `_commit_binding`'s original atomic section. Each target query retains its original `_one` connection boundary. Phase exhaustion, binding/target/exercise read order, missing-route conditional, policy invocation, hook timing, transaction/fence/write/advance order, exceptions and response shape are unchanged. One-time AST evidence reconstructs the original complete phase body by substituting the exact copied query and atomic blocks, confirms 42 other methods unchanged, and verifies all 92 SQL literals (82 distinct). This disposable comparison is supporting relocation evidence, not a permanent query-freezing framework.

`PYTHONPATH=services/content/src:database /tmp/tempo-db-review-venv/bin/python -m pytest services/content/tests/test_binding_application.py services/content/tests/test_source_policy.py::test_binding_policy_retains_route_completion_position_and_authored_response_guards services/content/tests/test_source_policy.py::test_binding_policy_preserves_accepted_alternatives_without_mutating_supplied_facts -q --rootdir=.` passed **18 cases**. A fresh `PYTHONPATH= /tmp/tempo-db-review-venv/bin/python -m build --no-isolation --outdir /tmp/tempo-content-binding-artifacts services/content` built the sdist and rebuilt its wheel. A private environment installed it with the unchanged required contract/library artifacts offline, then passed the same selection plus the three existing installed metadata/migration-resource/import-help cases with `PYTHONPATH=`: **21 passed**. Both worker Mermaid blocks parse; four affected README link inventories resolve. Exact commands, selected nodes, baseline/current sources, source/artifact hashes and logs are retained in `/tmp/tempo-content-binding-evidence/manifest.json`.

Synthetic cases cover exhaustion, missing/incomplete targets, missing exercise, failures before writes, exact target parameters/short circuit, and rollback on a stale fence or failed `after_writes` hook within the same opaque section. They do not establish actual PostgreSQL locks, leases, rollback or deployment isolation. No live/native PostgreSQL, schema/migration execution, HTTP transport, security, deployment or CI operation ran; the five previously denied loopback bind cases remain unverified and were not retried. The private environment was removed after evidence preservation; shared tools/cache were retained. Route/exercise phase coordination and larger source/publication multi-section orchestration remain explicit future extraction work, not permanent exceptions.

## Complete route phase separation

The full `_route_slice` application sequence moved to `src/tempo_content/worker/routes.py::reconcile_route`. Three root storage operations own the copied implementation: `_route_artifact` retains cache-key serialization/read; `_register_route` retains the original complete registration transaction; `_commit_route_segment` retains the original complete traversal transaction and returns saved-versus-artifact-advanced authority to the application. Existing `_transition`, `_item`, `_hook`, attachment/occurrence writers and the pure `route_segment` are reused. No generic SQL/dispatch interface, new schema, command protocol or traversal algorithm was introduced.

One-time AST evidence reconstructs the original complete phase by substituting those exact blocks, verifies 44 other methods and root helpers unchanged, and confirms all 92 SQL literals (82 distinct), query parameters, locks, fences, hooks and transaction order unchanged. The existing pure segment helper is identical. The existing preparation test functions and assertions are identical after following the moved `route_segment` monkeypatch from the root store into `worker.routes`. The traversal operation's boolean outcome maps the original artifact-advanced response; registration and successful result records are unchanged. This disposable comparison is evidence for a mechanical split, not a new qualification framework.

Focused source selection passed **11 cases**: `tests/test_route_application.py` (four missing boundary cases only), `tests/test_content_preparation.py::{test_route_preparation_stays_between_read_and_commit_hooks_without_a_pool_lease,test_route_preparation_keeps_sixteen_ply_windows_and_terminal_inventory,test_completed_route_reuse_does_not_revalidate_or_traverse_cached_facts}`, and `tests/test_domain.py::{test_issue108_checkpoint_slices_equal_full_route_without_prefix_replay,test_issue108_illegal_authored_route_moves_do_not_produce_inventory}`. The command uses `PYTHONPATH=services/content/src:database /tmp/tempo-db-review-venv/bin/python -m pytest` and `-q --rootdir=.`. A fresh `PYTHONPATH= /tmp/tempo-db-review-venv/bin/python -m build --no-isolation --outdir /tmp/tempo-content-route-artifacts services/content` rebuilt the wheel from its sdist. A private environment installed it offline with the unchanged required contract/library artifacts, then passed the same selection plus the three existing installed metadata/migration-resource/import-help cases with `PYTHONPATH=`: **14 passed**. Three worker Mermaid blocks parse and four affected README link inventories resolve.

Exact commands, selected nodes, baseline/current sources, comparison script/report, source/artifact hashes and logs are retained in `/tmp/tempo-content-route-evidence/manifest.json`. No full-suite, live/native PostgreSQL, schema/migration execution, HTTP transport, security, deployment or CI operation ran; the five previously denied loopback cases remain unverified and were not retried. The private environment was removed after evidence preservation; shared tools/cache were retained. Exercise phase and larger source/publication multi-section orchestration remain future extraction work, not permanent exceptions.

## Complete exercise phase separation and store audit

The full `_exercise_slice` application sequence moved to `src/tempo_content/worker/exercises.py::reconcile_exercise`. The existing root entry point composes `_transition`, `_item`, `_hook` and the exact `_commit_exercise` atomic operation. All six fixed SQL operations, fence, continuation checkpoint, immutable identity/predecessor/version checks, version/generation writes, cursor advance and after-write hooks remain in that root transaction. Its boolean completion fact maps the original pending-validation versus validated response in the application. Input parsing remains between the first payload read and dependent policy/repertoire reads; computation remains between the original `after_read` and `before_commit` hooks. One-time AST evidence reconstructs the entire original phase exactly, verifies 47 other methods/root helpers unchanged, and confirms all 92 SQL literals (82 distinct).

Focused source selection passed **7 cases**: four missing moved-boundary cases in `tests/test_exercise_application.py`, `tests/test_source_policy.py::test_exercise_policy_retains_execution_validation_and_learner_turn_rule`, and `tests/test_domain.py::{test_issue108_exercise_validation_yields_every_sixteen_plies_and_resumes_from_json,test_issue108_exercise_mode_and_legality_failures_are_actionable}`. The command uses `PYTHONPATH=services/content/src:database /tmp/tempo-db-review-venv/bin/python -m pytest` and `-q --rootdir=.`. A fresh `PYTHONPATH= /tmp/tempo-db-review-venv/bin/python -m build --no-isolation --outdir /tmp/tempo-content-exercise-artifacts services/content` rebuilt the wheel from its sdist. A private environment installed it offline with the unchanged required contract/library artifacts, then passed the same selection plus the three existing installed metadata/migration-resource/import-help cases with `PYTHONPATH=`: **10 passed**. Three worker Mermaid blocks parse; four affected owner README link inventories resolve.

The full remaining source audit accounts for all 49 methods and four top-level helpers in `adapters/store.py`. The [owner audit](../../database/tempo_content_database/adapters/README.md#store-responsibility-audit-after-exercise-phase-extraction) distinguishes database lifecycle/query transport (8), record/integrity reads (6), consistent read projections (14), native storage authority (13), existing composition (4), collector (1), remaining source workflow (1) and remaining publication workflows (2). Per-method source locations/reasons and bounded next-slice estimates are preserved in `tempo-content-store-audit.json` with the evidence. `load_source` still mixes preflight/staging/manifest/admission sequencing; `publication_build` and `store_publication` still mix replay/preconditions/reservation/input/build/admission sequencing. These are explicit future work, not permanent exceptions or an application-free claim for the whole store. Read-projection field/envelope formatting and database integrity guards are classified explicitly rather than silently treated as independent domain algorithms.

Exact commands, selected nodes, source/artifact hashes, baseline/current snapshots, comparison script/report, complete audit and logs are retained in `/tmp/tempo-content-exercise-evidence/manifest.json`. No full-suite, live/native PostgreSQL, schema/migration execution, HTTP transport, security, deployment or CI operation ran; the five previously denied loopback cases remain unverified and were not retried. The private environment was removed after evidence preservation; shared tools/cache were retained.

## Publication build/admission workflow separation

The complete `publication_build` and `store_publication` application sequences moved to `src/tempo_content/publication/application.py::{build_source_publication,admit_publication}`. Parsed models, serialization/decode/hash, replay/precondition decisions, authoritative reconstruction, provenance, preparation and callback/hook order remain at their original execution points. Existing `ContentService` entry points compose fixed owner operations. Root `_publication_task` and `_publication_payload` own complete queries; `_reserve_publication_build` and `_commit_publication` retain the exact original complete reservation and immutable/current-generation/provenance insert transactions. Both original payload lookup invocation sites call one identical SELECT implementation. Source SQL literal count therefore changes from 92 to 91 while all 82 distinct strings and runtime query invocation counts/order/parameters remain unchanged.

One-time AST evidence reconstructs both original complete workflow bodies exactly from the fixed operations, verifies 47 other methods/root helpers unchanged, and verifies the complete native sections. The existing preparation test functions/assertions are identical after following their two moved monkeypatch targets into `publication.application`. Focused source selection passed **13 cases**: five existing publication preparation/order cases, four missing replay/precondition/admission-boundary cases in `tests/test_publication_application.py`, and four existing domain publication round-trip/permutation/stable-identity/retirement cases. The command uses `PYTHONPATH=services/content/src:database /tmp/tempo-db-review-venv/bin/python -m pytest` and `-q --rootdir=.`. A fresh `PYTHONPATH= /tmp/tempo-db-review-venv/bin/python -m build --no-isolation --outdir /tmp/tempo-content-publication-artifacts services/content` rebuilt the wheel from its sdist. A private environment installed it offline with the unchanged required contract/library artifacts, then passed the same selection plus the three existing installed metadata/migration-resource/import-help cases with `PYTHONPATH=`: **16 passed**. The bounded publication Mermaid block parses; four affected owner README link inventories resolve and the prior audit anchor is preserved.

The updated full audit accounts for all 53 methods and four helpers: lifecycle/query transport (8), record/integrity reads (8), consistent read projections (14), native storage authority (15), composition (6), collector (1), and remaining source workflow (1). `load_source` is the last mixed application/storage workflow: its 57 source lines sequence preflight, staged pages, manifest sealing, hook and reserved admission across existing bounded sections. It remains explicit next work, not a permanent exception or completion claim. Publication/workers now have application sequencing outside the database tree and fixed native operations inside.

Exact commands/nodes, source/artifact hashes, baseline/current snapshots, comparison script/report, updated full audit and logs are retained in `/tmp/tempo-content-publication-evidence/manifest.json`. No full-suite, live/native PostgreSQL, schema/migration execution, HTTP transport, security, deployment or CI operation ran; the five previously denied loopback cases remain unverified and were not retried. The private environment was removed after evidence preservation; shared tools/cache were retained.

## Source staging/admission workflow separation

The complete `load_source` sequence moved to `src/tempo_content/source/application.py::load_source_snapshot`. Existing input preparation, preflight replay, `KINDS`/64-item stage iteration, header/count preparation, immutable manifest sealing, `after_staging`, reserved admission and public receipt fields keep their original execution points. The existing `ContentService.load_source` entry point composes four fixed storage operations. Root `_preflight_source`, `_stage_source_page`, `_seal_source_manifest` and `_admit_source` retain the exact original repeatable-read and transaction sections, including page slicing/enumeration inside the transaction, late bridge import/reservation authorization, source/task lock order, replay/stale checks and collector-before-revision/task writes. Native replay/admission facts map to the original receipt shapes. No SQL, field/JSON shape, schema or migration changed.

One-time AST evidence reconstructs the entire original workflow from these four operations and original receipts, verifies all 52 other methods and four helpers unchanged, and compares complete native sections. All 91 physical/82 distinct SQL literals and their parameters/invocation order are preserved. Focused source selection passed **9 cases**: four missing source boundary cases in `tests/test_source_application.py` and five existing input/canonicalization/hash/identity cases selected from `tests/test_source_policy.py`. The command uses `PYTHONPATH=services/content/src:database /tmp/tempo-db-review-venv/bin/python -m pytest` and `-q --rootdir=.`. A fresh `PYTHONPATH= /tmp/tempo-db-review-venv/bin/python -m build --no-isolation --outdir /tmp/tempo-content-source-artifacts services/content` rebuilt a wheel from its sdist. A private environment installed it offline with the unchanged required contract/library artifacts and passed the same cases plus three existing installed metadata/migration-resource/import-help cases with `PYTHONPATH=`: **12 passed**. The bounded source Mermaid block parses; all 68 relative links in six affected owner READMEs resolve, including the preserved prior audit anchor.

The final source audit accounts for all **57 methods/four helpers**: lifecycle/query transport (8), record/integrity reads (8), consistent read projections (14), native storage authority (19), composition (7) and collector (1). No additional independent application workflow was identified in that class. Retained non-SQL code is explicitly classified: complete-query consistency and row envelopes, immutable identity/digest/provenance/fence guards, parameter/driver encoding, cursor validation, atomic collector/transition result facts and composition. `BridgeStore` retains fixed SQL/borrowed-transaction projections and the source reservation guard; migration/readiness code retains database lifecycle authority. The **19 production Python files** under `src/tempo_content` have no direct database-driver imports or cursor/connection calls. Composition still imports the concrete root adapter and the public package eagerly exports `ContentService`; this is not a driver-free package-import claim. Existing SQL/native operations in `integration/benchmark.py`, `integration/run_postgres.py` and `integration/test_postgres.py` remain outside that runtime-source audit; they were not moved or executed in this slice.

Exact commands/nodes, source/artifact hashes, baseline/current snapshots, comparison script/report, final method/runtime audits and logs are retained in `/tmp/tempo-content-source-evidence/manifest.json`. Earlier moved policy, worker and publication runtime files remain byte-identical. No full-suite, live/native PostgreSQL, schema/migration execution, HTTP transport, security, deployment or CI operation ran; the five previously denied loopback cases remain unverified and were not retried. The private environment was removed after evidence preservation; shared tools/cache were retained.

## Native database tooling/test placement

The three remaining Content native harness files moved from `integration/` to the single root database owner: `test_postgres.py` to `database/tempo_content_database/tests/`, and `run_postgres.py`/`benchmark.py` to `database/tempo_content_database/tooling/`. Content remains their logical test owner. The test suite and benchmark are byte-identical: all 67 native test functions, 12 fixture/helpers, assertions, decorators, SQL and measurement logic remain in their original order. Public-API/CLI/restart cases in that suite still require its native service fixture. Pure application tests, consumer probes, artifact qualification and `run_suite.py` remain in this component.

Only runner paths/discovery and source-input hashing changed. The runner resolves the root database owner, existing Content `run_suite.py`, native suite and benchmark at their new paths; the independent sdist layout retains the component-root fallback for `run_suite.py`. Its source-input inventory now includes the root database owner. The existing artifact qualifier also hashes that owner, which it already copied into the build context. One-time AST comparison restores only these path/inventory changes and proves the complete original runner and qualifier bodies identical, including all native resource, migration, credential/grant, cleanup and SQL logic. These implementations were moved, not executed. Historical validation commands above retain their original paths/revisions; current command discovery lives in this component's README and the root database owner's tooling README.

The narrow existing Hatch hook includes the complete database owner in the sdist and omits its `tests/` and `tooling/` from the wheel. A fresh `PYTHONPATH= /tmp/tempo-db-review-venv/bin/python -m build --no-isolation --outdir /tmp/tempo-content-native-placement-artifacts services/content` built the wheel from its sdist. Archive inspection proves all three native files at their centralized sdist paths with exact current bytes, no obsolete integration copies, and no native test/tooling paths in the wheel. All **30 runtime/migration files** match the previous source-loading slice's wheel byte for byte. A private environment installed the new wheel offline with the unchanged contract/library artifacts and passed the three existing installed metadata/migration-resource/import-help cases with `PYTHONPATH=`: **3 passed**. The five changed/moved Python sources parse, paths resolve by static inspection, and all **50 relative links** across affected owner READMEs/regression inventory resolve. No new qualification framework or native execution was introduced.

Exact commands, source/artifact hashes, original and moved source snapshots, AST comparison script/report, archive inventories and logs are retained in `/tmp/tempo-content-native-placement-evidence/manifest.json`. The parent integration owner updates the component registry's native command/input paths; global discovery was not claimed before that update. Native runner/test/benchmark, lifecycle, security/provider, live PostgreSQL, loopback and full-system checks were not run; the five previously denied loopback cases remain unverified and were not retried. The private environment was removed after evidence preservation; shared tools/cache were retained.

## Storage-only pure test placement (additional scope)

The finite Content-only residual scan found no additional runtime SQL/driver implementation in its 19 production Python files. Beyond the original 156 mixed-file checklist, existing storage-only unit tests moved to `database/tempo_content_database/tests/unit`: the complete inventory cursor suite and only the binding target/atomic commit, failure-write fence and readiness-context test functions. Application phase/computation/composition tests remain component-owned. The moved cursor file is byte-identical; the other four moved function bodies/decorators/assertions are identical after normalizing two fixture-unpack lines to the same four immutable values. Every retained test/helper AST and every production Python file hash remain unchanged.

The existing pure runner now explicitly selects its application test directory and the root owner's unit subtree; it never selects the native parent test directory. The source-free artifact qualifier copies/selects that same pure subtree, retaining existing strict inventory/zero-selection behavior. The existing Hatch hook still excludes all database tests/tooling from wheels and includes them in sdists. Historical receipts above preserve their original paths and executions. Focused moved/retained boundary selection passed **70 cases** with fake drivers only. No native, loopback, service, full-system or previously denied transport operation ran for this placement; the five prior loopback limitations remain unverified.
