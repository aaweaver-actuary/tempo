# Independent Learning runtime validation

The original implementation and initial repair evidence are preserved below.
The independent-review follow-up records additional identity-race repairs;
earlier 44-check and 74-check results are historical milestones, not substitutes
for qualification of that follow-up.

Installed artifact qualification: `c4e24327775e478f13cc46fec899775ecf4dacc6`. Final native resource/cleanup qualification: `7fa179b06bae859aeb6e680498e8ae430d4cb3ae`, based on refreshed `main` `f43b5f7fb4d7ddafeb3124a95f4fa3d64556abc4`. The intervening runner change confines scratch data to the invocation; runtime package source is unchanged. Subsequent documentation records these results. No live checkout, database, service or development copy was reused.

## Commands and outcomes

Run from the repository root unless a different directory is stated. Evidence was recorded outside the disposable checkout; `$evidence` below names that directory, not a production resource.

| Exact check | Outcome |
| --- | --- |
| `PYTEST_ADDOPTS=--junitxml=$evidence/component-native.xml services/learning/runtime/.venv/bin/python services/learning/runtime/integration/run_postgres.py $evidence` | **44 passed**, 23.13 s. Exact collection and all execution phases matched JUnit, including the caller-specified receipt destination. |
| `services/learning/runtime/.venv/bin/python services/learning/runtime/qualify.py $evidence/installed-final` | **2 passed on Python 3.12.12 and 2 on Python 3.14.8** after producer source removal; all six wheels/sdists built; both zero-selected negative probes exited 5 as required. |
| `TEMPO_COMPONENT_PYTHON=services/learning/runtime/.venv/bin/python node tools/component-build/check-components.mjs --component learning` | Runtime and all five actual component dependencies passed the existing source/export/artifact/contract checks. |
| `TEMPO_COMPONENT_PYTHON=services/learning/runtime/.venv/bin/python node tools/component-build/test-node.mjs tools/component-build/tests` | **73 passed**, no failures/skips; includes normalized Python distribution dependency selection. |
| `/Users/andy/.local/bin/ruff check services/learning/runtime/src --select F821,F822,F823 --no-cache --output-format concise` | Passed. This focused undefined-name check is not a full style/lint gate. |
| `git diff --check` | Passed. |

From `backend/`, with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1`:

```sh
.venv/bin/python -m pytest \
  tests/test_scheduling_review_application.py \
  tests/test_scheduling_package_adapter.py \
  tests/test_scheduling_boundary.py \
  tests/test_defensive_threat_grading.py \
  tests/test_defensive_threat_validation.py \
  tests/test_defensive_threat_detection.py \
  tests/test_scheduling_review_store.py \
  tests/test_opening_decision_evidence.py \
  tests/test_learning_package_installation.py \
  tests/test_durable_task_enqueue_identity.py \
  -q -p no:cacheprovider --tb=short \
  --junitxml=$evidence/legacy-affected.xml
```

**110 passed in 2.91 s.** The legacy-only import seam needed `redis==6.4.0` from the existing backend requirements; it was installed only in the disposable backend environment. Redis/Celery/model distributions remain absent from the standalone installed consumers and final container. Earlier incomplete runs exposed missing legacy dependencies, fixture restart ports and protocol differences; those runs did not qualify the change. Their logs are preserved separately from the final passing receipts.

The native run builds the minimal container itself, then proves actual HTTP load/issue/review/retry against its owned PostgreSQL. The regular inventory also covers all five typed specifications, tactical discovery, middlegame/endgame review, defense recognition/grades/uncovered intent, required opening evidence, savepoint rollback, conflicting retries, concurrent reviews/issuance/enqueue, a genuinely blocked creator rollback, retained quota, frozen order, parent/integrity fences, guidance/sibling burial, role denial, bounded contention, HTTP/database restart and a saved queue cursor.

The two Chromium cases bundle the existing strict queue schemas and review outbox and call the real installed HTTP process. The response-loss case aborts delivery only after the real server committed, reloads the page and recovers through the original client. Node was **26.10.0**, Playwright **1.63.0**, uv **0.12.22**; the native host used Python **3.14.8**. FastAPI emitted 1,277 Python 3.14 deprecation warnings about `asyncio.iscoroutinefunction`; no required case was skipped or suppressed.

## Tested artifact identities

SHA-256:

- Runtime wheel `tempo_learning_runtime-0.1.0-py3-none-any.whl`: `0d70b7419239a0947f529126268c646cafc32269197da8428cfd10627df23910`.
- Runtime sdist `tempo_learning_runtime-0.1.0.tar.gz`: `69fbf372de0677ac5097c06eba787ce7695a4434dbc0373744d091bcf6faa953`.
- Tested minimal container: `sha256:e4c249931c12210b5b712d220609ef81646c1fd2fe4d96a009d4450d8dc9cb4a`.
- PostgreSQL 18.6 fixture: `sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336`.

`native.json` binds the revision, source hashes, exact commands, PostgreSQL/image identities and complete executed inventory. `installed-final/installed.json` binds all copied build inputs, six artifact pairs, dependency lists, interpreter identities, installed inventories and removal of temporary consumer workspaces. XML/logs accompany both. Native container/volume absence, task image-tag removal and invocation-owned scratch removal were verified after the passing run; service logs were retained. Evidence is retained before checkout cleanup.

## Limits and remaining acceptance

This is focused component qualification, not a full-system or hosted merge-qualified pass. The full Study UI, D's recovery acceptance, E's shared browser runner matrix and B/F remediation adoption remain with their owners. The published seams are available for that integration; no second admission capability or independent transaction owner was introduced.

The file adapter currently accepts at most 128 definitions per publication, 100 memberships per definition and one active publication owner for each opening repertoire. Default local obligations use accepted delivery time because the current evidence contract lacks historical observation time. Producer extraction, richer chronology and any change to those contracts are outside this runtime PR.

Tablebase cache/offline behavior and broker-free operation are proven independently. Optional-provider protocol success/cache/failure used a real local HTTP provider. Public Lichess availability and provider truth are unverified. Frozen ranks are immutable accepted inputs; their production is unverified here.

No live migration, import/cutover, Content/Game Evidence extraction, authoring operation, installer redesign, deployment or merge was performed. #224/#194 should remain open until their complete demonstrated acceptance is assessed.

## Initial correctness repair qualification — October 15, 2026

Reviewed baseline: `38b6f7cbaf805fbd3b6c76ef2fa94a26bba7fbe3`; base:
`f43b5f7fb4d7ddafeb3124a95f4fa3d64556abc4`. All six reported defects remained
applicable. Repairs and all regression additions were committed at
`42402871c2769828fec2058758e0ac4bf1c46a7e`. The final native run tested
`e31f87774afec643bb369f3e98f245570ad11174`, whose sole additional change reads
canonical moves from durable card state in two new valid-line assertions. Runtime
source, dependencies, fixtures and installed-consumer checks are identical across
these commits. Subsequent validation documentation does not change runtime/tests.

These runs were personally executed for this repair pass, separately from the
author-reported original evidence above. Evidence is retained outside the
disposable checkout at `/private/tmp/tempo-pr284-evidence-20261015`.
Each command has its revision, exact arguments, environment, exit status and log
hash in a corresponding JSON receipt. Native/installed reports additionally
record source-input hashes and complete collected/executed identities.

### Exact commands and results

Commands below ran from the task checkout root, with:

```sh
evidence=/private/tmp/tempo-pr284-evidence-20261015
```

| Command | Tested revision | Actual result |
| --- | --- | --- |
| `PYTEST_ADDOPTS=--junitxml=$evidence/native-qualified.xml services/learning/runtime/.venv/bin/python services/learning/runtime/integration/run_postgres.py $evidence/native-qualified` | `e31f8777` | **74 passed**, 49.48 s; no skipped, deselected, failed or missing required cases. Collection, execution phases and JUnit agree. |
| `services/learning/runtime/.venv/bin/python services/learning/runtime/qualify.py $evidence/installed-final` | `42402871` | **2 passed on Python 3.12.12 and 2 on Python 3.14.8** after producer-source removal; six wheel/sdist pairs built; both negative zero-selected probes returned required exit 5. |
| `TEMPO_COMPONENT_PYTHON=$PWD/services/learning/runtime/.venv/bin/python node tools/component-build/check-components.mjs --component learning` | `42402871` | Passed Learning and its five actual component dependencies, public exports, dependency declarations, artifacts and contract schemas. |
| `TEMPO_COMPONENT_PYTHON=$PWD/services/learning/runtime/.venv/bin/python node tools/component-build/test-node.mjs tools/component-build/tests` | `42402871` | **73 passed**, zero failures/skips/TODOs. |
| `/Users/andy/.local/bin/ruff check services/learning/runtime/src services/learning/runtime/tests --select F821,F822,F823 --no-cache --output-format concise` | `42402871` | Passed focused undefined-name checks; this is not full lint/type qualification. |
| `npm run check:conflicts` | `42402871` | Passed tracked-text conflict-marker scan. |
| `git diff 38b6f7c --check` | `42402871` | Passed repair-diff whitespace check. |

The applicable runtime package metadata/boundary checks are part of the component
checker. There is no separately configured runtime type-check target. The final
test-assertion correction does not alter the inputs of the installed consumers,
legacy suites or component tooling. Input-equivalence evidence is retained with
the qualification reports; these are not claims that the whole system passed.

From `backend/`, the exact selected compatibility command was:

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 \
.venv/bin/python -m pytest \
  tests/test_scheduling_review_application.py \
  tests/test_scheduling_package_adapter.py \
  tests/test_scheduling_boundary.py \
  tests/test_defensive_threat_grading.py \
  tests/test_defensive_threat_validation.py \
  tests/test_defensive_threat_detection.py \
  tests/test_scheduling_review_store.py \
  tests/test_opening_decision_evidence.py \
  tests/test_learning_package_installation.py \
  tests/test_durable_task_enqueue_identity.py \
  -q -p no:cacheprovider --tb=short \
  --junitxml=/private/tmp/tempo-pr284-evidence-20261015/legacy-final.xml
```

**110 passed in 10.07 s** on `42402871`. Only the declared backend test setup and
its existing `redis==6.4.0` import seam were installed in the separate backend
environment. Redis and Celery remain absent from the standalone artifact and
restricted container. Node was **26.10.0**, uv **0.12.22**, native Python
**3.14.8**, Playwright **1.63.0**, PostgreSQL fixture **18.6**.

The native total includes the two existing Chromium identities
`test_native_existing_review_client_progression_and_recovery[progression]` and
`test_native_existing_review_client_progression_and_recovery[lost-response-reload]`.
They use the actual HTTP process, strict client schemas and existing review
outbox. The latter still proves recovery after the server commits and delivery
is lost. These are focused browser/API proofs, not full Study UI acceptance.

### Repair proofs and migration integrity

All **30 added parameterized native cases** passed, alongside all 44 original
native cases. The full 74 identities and JUnit are preserved in
`native-qualified/native.json` and `native-qualified.xml`.

| Repair | Named regular regression identities and proof |
| --- | --- |
| Evidence revisions | `test_evidence_revisions_are_monotonic_and_original_transport_replays`; `test_semantic_retry_does_not_readmit_a_settled_miss`; `test_empty_correction_advances_frontier_and_cannot_reintroduce_obligations`; `test_evidence_frontier_obligations_queue_and_receipt_rollback_together`; `test_concurrent_evidence_revisions_serialize_on_durable_frontier[1/2]`; `test_evidence_frontier_survives_service_restart`. Real independent PostgreSQL transactions, actual obligation/queue/card/frontier comparisons, original transport results and installed process restart. |
| Repertoire ownership | `test_concurrent_publications_cannot_share_an_opening_repertoire`; `test_concurrent_retirement_and_successor_activation_use_the_same_lock_order`; `test_unrelated_repertoire_activation_is_independent`; `test_publication_owner_contention_keeps_original_intent_retryable`; `test_replacement_locks_removed_repertoires_until_new_ownership_commits`. Test-owned gates and observed PostgreSQL blocking establish real contention; loser state rolls back, conflict receipts replay, and succession succeeds. |
| Opening graph completeness | `test_duplicate_opening_route_positions_are_rejected_before_staging[False/True]`; `test_every_valid_opening_membership_has_an_executable_route[False/True]`; `test_graph_completeness_failure_rolls_back_activation`. Checks explicit/default collisions, repertoire-local scope, exact multi-membership identities, ordinary introductions and rollback of an injected lost route. |
| Every-line final turn | `test_every_opening_line_finishes_on_the_trained_learner_move` (seven cases). Invalid first/later lines and illegal later moves fail with the relevant validation reason; valid White/Black lines retain canonical card identity and native opening manifests. |
| Duplicate memberships | `test_duplicate_memberships_are_rejected_during_staging`; `test_old_staged_duplicate_memberships_record_replayable_terminal_failure`. Historical staged JSON revalidates, records terminal failure and replays it without partial serving/card/graph/queue effects. The separate owner-contention regression confirms transient failure retains a queued intent and recovers. |
| Tablebase cache | `test_tablebase_cache_keeps_halfmove_clocks_separate_and_ignores_old_keys[clocks0/clocks1]`. Real local HTTP requests distinguish clock 0/99 in both orders; repeated/offline requests use separate full-FEN cache entries, historical four-field entries are ignored and an uncached offline request returns 503. |

`test_revision_migration_backfills_legacy_receipts_and_preserves_replay` exercises
the v1 receipt upgrade, maximum-revision backfill, conservative null-digest policy,
exact historical replay, newer acceptance, migration rerun, unchanged v1 checksum,
readiness and restricted writer/reader access. Every native fixture also proves
clean installation of both registered migrations. Both source-removed installed
consumers verify the packaged migration order, dependency and SQL resources.
`test_registered_receiver_retains_sole_revision_authority` confirms the default
frontier does not wrap a custom receiver.

The original and repaired validators were also run against identical malformed
payloads in `validation-baseline.py`/`.json` outside the checkout: the original
revision accepted duplicate routes, duplicate memberships and an invalid first
line followed by a valid line; the repaired validator rejects all three.

Incomplete runs remain preserved separately. The first development run was
70 passed/2 failed because the new valid-line fixtures used an unsupported mode;
the next was 72 passed/2 failed because the new assertions read an internal field
absent from queue JSON. Correcting the fixtures and querying durable card state
produced the final 74-pass result. An initial tooling run was 68 passed/5 failed
with missing chess-line fixture dependencies; the required component-local locked
dependencies were installed and all 73 then passed. No failed run is counted as
qualification, and no selected case was skipped, deleted or weakened.

### Tested artifacts and resources

- Runtime wheel at `42402871`: SHA-256 `56e05e193cb8e670b992bd8784aa1bb92f42450c524f77536d66949d8ccebbc1`.
- Runtime sdist at `42402871`: SHA-256 `987c465ad83658e30d0607b34f128fade5bc63bc983a4c945a00765624df9613`.
- Final native runtime container: `sha256:ef759f127e036d1a88ad4bec94438157cff8f9b87bdd01d0053667824d7007ca`.
- PostgreSQL 18.6 fixture: `sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336`.

The final native invocation was `tempo-learning-f9bccabacf4d27ad`. Its report
verifies removal of its container, volume, runtime image tag and temporary test
data; installed qualification verifies removal of its consumer/build workspace.
Earlier development invocations also recorded their own cleanup. Evidence and
service logs remain outside the disposable checkout. No shared prune, Docker
restart, existing-volume deletion or live database migration was performed.

Disk capacity was checked before native qualification (about 30 GiB available).
Only component-local dependency environments were created. Final checkout cleanup
and independent source review are recorded in the task handoff after preservation
and verification of the pushed refs.

The six repairs retain one transaction authority and one admission writer,
required review/evidence/settlement atomicity, immutable completed attempts,
bounded retryable contention and nonblocking optional successors. Frozen ranks
are not recomputed. Existing study data and services were not changed.

Larger publication import (128 definitions/100 memberships and one owner), richer
B/F evidence correction/chronology, PR #281/#283/#285 adoption, full Study UI,
shared browser integration and live cutover remain separate work. Publications
are complete generations; multiple independently active chunks are not a
cumulative repertoire protocol. Public Lichess availability/truth is unverified.
Existing FastAPI/AnyIO deprecation warnings remain (2,205 native warnings), with
no required failures or skips. Hosted Actions was not enabled; the GitHub branch
protection endpoint reported no protection and the active main rules endpoint
returned an empty rule list during this pass. No merge or deployment occurred.

## Independent-review follow-up

The separate reviewer inspected actual source and evidence at `e31f8777` and
identified two additional P2 receiver identity races. Different evidence IDs
could concurrently reuse one global obligation ID and both be acknowledged,
because the payload precheck preceded an `ON CONFLICT DO NOTHING` insert. They
could also reuse one global delivery ID and cause the loser to raise an
unclassified PostgreSQL uniqueness error. Neither is covered by an evidence-ID
lock alone.

Both findings were reproduced against real PostgreSQL at
`b3551efc3a80545ee891c8f62577c9796a8e5f73` using
`test_cross_evidence_identity_reuse_conflicts_atomically[obligation/delivery]`.
The runner command was the same native command above with evidence/JUnit
destinations `identity-red`/`identity-red.xml`. Result: **74 passed, 2 failed** in
31.55 s. The obligation contender incorrectly returned success; the delivery
contender raised `UniqueViolation`. The tests gate the creator after its actual
insert, observe the independent contender blocked inside PostgreSQL, then release
the creator. These are preserved failing proofs, not qualifying passes.

The receiver now takes a global delivery identity transaction lock before its
evidence identity lock. It validates the authoritative stored obligation payload
with a fresh statement after the insert/conflict wait and before admission,
frontier advancement or receipt creation. Conflicting lineage/transport is an
explicit HTTP 409; all losing domain/frontier/receipt effects roll back. No
additional obligation-lock tier, transaction owner or broad database-error
classification was introduced. A second independent source review and refreshed
affected qualification are recorded below when complete.
