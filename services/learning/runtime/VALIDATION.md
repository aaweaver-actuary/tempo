# Independent Learning runtime validation

The original implementation and initial repair evidence are preserved below.
**Current-main compatibility refresh** records the final producer/tooling update
after main advanced during publication. Earlier 44-check, 74-check and pre-refresh
76-check results remain historical milestones with their exact source identities.

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
classification was introduced. The second independent source review and refreshed
affected qualification are recorded below.

## Final qualification after independent review

All refreshed affected checks below were personally executed on exact source
`6ff2583ba79f07185ff54e22f6140c7663e0a94c`. Any later commit updates validation
documentation only. The complete native inventory is now **76 cases**, including
all 44 original cases and 32 added cases; both newly reproduced identity races
pass with explicit conflict and verified loser rollback.

With `evidence=/private/tmp/tempo-pr284-evidence-20261015`, from the task checkout
root:

| Exact final check | Outcome |
| --- | --- |
| `PYTEST_ADDOPTS=--junitxml=$evidence/native-reviewed.xml services/learning/runtime/.venv/bin/python services/learning/runtime/integration/run_postgres.py $evidence/native-reviewed` | **76 passed in 29.42 s**. Real PostgreSQL 18.6, restricted container, both Chromium cases, all family/transaction/recovery cases, migration and both identity-race regressions. Collection, every phase and JUnit agree; zero required skips/deselections/failures. |
| `services/learning/runtime/.venv/bin/python services/learning/runtime/qualify.py $evidence/installed-reviewed` | **2 passed on Python 3.12.12 and 2 on Python 3.14.8**, six fresh wheel/sdist pairs, producer-source removal, packaged migrations, restricted consumers and both required exit-5 zero-selected probes. |
| `TEMPO_COMPONENT_PYTHON=$PWD/services/learning/runtime/.venv/bin/python node tools/component-build/check-components.mjs --component learning` | Passed Learning plus all five actual component dependencies. |
| `/Users/andy/.local/bin/ruff check services/learning/runtime/src services/learning/runtime/tests --select F821,F822,F823 --no-cache --output-format concise` | Passed focused undefined-name check. |
| `npm run check:conflicts` | Passed. |
| `git diff 38b6f7c --check` | Passed. |

The exact ten-file legacy command above was rerun from `backend/`, retaining
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1`, with the JUnit option
`--junitxml=/private/tmp/tempo-pr284-evidence-20261015/legacy-reviewed.xml`.
**110 passed in 3.57 s**. Its full argument list is also preserved in
`legacy-reviewed.json`.

The **73-pass** tooling result from `42402871` is reused: component-tooling source,
tests, fixture sources, root metadata and dependency/toolchain inputs did not
change. The latest receiver changes were separately covered by fresh boundary,
native, installed, legacy and static checks. No additional broad suite was
substituted for a failed or missing owning check.

Latest artifact identities:

- Runtime wheel: SHA-256 `ae059c7eb2c2beb36e72fd6652fb443759166551fe326852aaa3816edac163e1`.
- Runtime sdist: SHA-256 `96373034113a008e828f28440ce4dba53384ce1cf202256002b4e37d7109d1c3`.
- Tested runtime container: `sha256:96996d9100b308b5b266a4de46ee117148f67459be376378fdd105bb602a0efe`.
- PostgreSQL fixture: `sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336`.

`native-reviewed/native.json` and `installed-reviewed/installed.json` bind exact
source hashes, commands, artifact/runtime identities and full inventories to
`6ff2583b`; accompanying JSON/log/XML files retain outcomes. The final native
invocation `tempo-learning-78b6560307db579d` verifies its container, volume,
runtime image tag and scratch removal. Installed build/consumer workspaces were
removed. The real-PG red reproduction and the first independent review remain
preserved alongside final evidence. Disk was rechecked before this run (about
28 GiB available). Existing dependency deprecation warnings remain (2,263 native
warnings); no required case was suppressed. No live study resource was changed.

### Final independent assessment

The separately authorized reviewer inspected actual final source at `6ff2583b`,
the repair diff and affected transaction/admission/review paths, migration and
restricted-role behavior, both red PostgreSQL identity reproductions and final
qualification evidence. Result: **no remaining scoped P1/P2/P3 findings**. Both
first-round P2 findings are closed. The reviewer found no reverse acquisition
path for the delivery lock, and verified the conflict checks precede admission,
frontier advancement and acknowledgement while custom receivers retain authority.

The reviewer independently checked native collection/execution/JUnit agreement,
both installed consumer inventories, negative probes, artifact and log hashes,
and all 56 packaged runtime/fixture files against current bytes. The assessment
used saved executed evidence; it did not repeat infrastructure runs. Native
runtime coverage is Python 3.14; Python 3.12 has installed-consumer coverage.
This is an independently reviewed focused component merge recommendation, not
whole-product, hosted CI, live cutover or full Study UI qualification.

Both review rounds are retained as `independent-review-round1.json` and
`independent-review-round2.json` in the evidence directory. Final
`input-equivalence-reviewed.json` verifies the current runtime, tests, dependencies
and fixtures against the qualified inputs/wheel, with only this validation record
changing afterward. Refs, pushed PR identity and checkout cleanup are preserved
in `handoff.json` outside the checkout. All original publication-size, downstream
integration and provider-truth limits above remain explicit and nonblocking for
this component pass.

## Current-main compatibility refresh

Before publication, `main` advanced to
`52f26ee55b08a70d5529847cd5849b10cbd12351` by merging PR #281 (remediation and
Game Evidence contracts 2.0.0) and PR #282 (browser-lane tooling). Those changes
affect this PR's actual producer metadata and tooling inputs, so the task branch
integrated that main revision with a normal merge, preserving both histories.
The runtime pins the current contracts distribution 2.0.0 while continuing to
import its retained **v1** interface. No v2 receiver, remediation implementation,
new admission writer or migration registration was adopted. The component-local
and affected backend lockfiles are refreshed against the actual producer.

The changed inputs require fresh native, installed, compatibility, boundary and
component-tooling results below. The earlier 73-tooling result is not reused for
the newly merged browser-lane tooling. These checks remain component-focused;
passing them will not claim full browser-lane or remediation integration.

All refreshed checks were personally executed at
`563a04095d439d8c9e28a43585959b210a240104`, which includes current main
`52f26ee5`. With the same evidence root and working directories documented above:

| Exact current-main check | Actual result |
| --- | --- |
| `PYTEST_ADDOPTS=--junitxml=$evidence/native-current.xml services/learning/runtime/.venv/bin/python services/learning/runtime/integration/run_postgres.py $evidence/native-current` | **76 passed in 30.18 s**, zero required failures/skips/deselections, including both Chromium cases. PostgreSQL 18.6, native Python 3.14.8; collection/execution/JUnit agree. |
| `services/learning/runtime/.venv/bin/python services/learning/runtime/qualify.py $evidence/installed-current` | **2 passed on Python 3.12.12 and 2 on Python 3.14.8**, all six fresh wheel/sdist pairs built, source removed, installed closure/migrations checked, both zero-selected negatives exited 5. |
| `TEMPO_COMPONENT_PYTHON=$PWD/services/learning/runtime/.venv/bin/python node tools/component-build/check-components.mjs --component learning` | Passed Learning plus five dependencies; current Game Evidence producer exports and schemas match metadata. |
| `TEMPO_COMPONENT_PYTHON=$PWD/services/learning/runtime/.venv/bin/python node tools/component-build/test-node.mjs tools/component-build/tests` | **76 passed**, zero failures/skips/TODOs, including the three newly merged tooling cases. |
| `/Users/andy/.local/bin/ruff check services/learning/runtime/src services/learning/runtime/tests --select F821,F822,F823 --no-cache --output-format concise` | Passed focused undefined-name check. |
| `npm run check:conflicts` | Passed. |
| `git diff origin/main --check` | Passed against `origin/main` `52f26ee5`. |

The exact ten-file legacy command above ran again with
`--junitxml=/private/tmp/tempo-pr284-evidence-20261015/legacy-current.xml`:
**110 passed in 4.38 s**. `legacy-current.json` preserves the entire command and
environment; every other current check likewise has its own JSON/log receipt.
Clean/upgrade migration integrity and restricted-role regressions remain part
of the passing native suite; packaged migration order/resources and the actual
producer distribution version are checked by both installed consumers.

Latest qualified artifact hashes:

- Runtime wheel: SHA-256 `fb555e047a3e77932cd999c6683e859f415225a0c5206c550269191ca9b31acf`.
- Runtime sdist: SHA-256 `8438f2e3a032ece46bef49a0e8c3973ca739c739e866af75367919747697a5bb`.
- Game Evidence contracts 2.0.0 wheel: SHA-256 `bdffa6ef5ef37e9b7a24fe3546b0b6e8b4b2968ed75af708f504125ace231777`.
- Tested runtime container: `sha256:7a1fb07e598ff01890be960eacd8b96087c21cf1c81a09e42db34eed1d8e8ed5`.
- PostgreSQL fixture: `sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336`.

The final invocation `tempo-learning-44b2bee6537968a8` recorded container, volume,
image-tag and scratch removal. Installed consumer/build workspaces were removed.
`native-current/native.json`, `installed-current/installed.json`, JUnit and logs
retain all identities and hashes. `input-equivalence-current.json` verifies
current package bytes against the qualified wheel and allows only later edits
to this validation record. The runtime correction source remains identical to
the source already independently reviewed; the current-main metadata/producer
refresh received a separate final review readback before publication. No v2
receiver adoption, live migration, merge of this PR or deployment was performed.

The independent reviewer inspected the actual refreshed diff and producer/wheel
metadata at `563a0409` and verified the fresh 76 native, 76 tooling, 110 legacy
and both installed consumer results, negative probes, inputs, artifact hashes and
all 56 runtime/fixture wheel files. Result: **no new scoped P1/P2/P3 findings**;
the prior correctness review conclusions remain valid. This third bounded review
is retained as `independent-review-current.json`. Its same coverage limits apply:
saved execution evidence was reviewed, not rerun, and native Python 3.12 or
whole-product/hosted CI coverage is not claimed. Subsequent commits change only
this validation record; final current input equivalence is verified after commit.

## F1 adoption

The historical results above describe the original runtime extraction. F1 adds the separately installed remediation library, authenticated v2 producer delivery and issuance-bound settlement. Its current source-bound results and explicit remaining #194/#224 work are recorded in [F1 validation](../../../docs/validation/f1-game-evidence-learning.md) and [F2 handoff](../../../docs/study/f2-remediation-handoff.md). No live activation or conversion is implied.

## F2 production Study adoption

F2 adds the registered `learning-full-study` assembly using the production Study
assets, installed Learning, explicit accepted fixtures and restricted disposable
PostgreSQL. The new strict bootstrap/settings/teaching regression owner remains
in the regular native suite. The exact source-bound acceptance map, commands,
inventories, failed explorations, artifact identities and remaining transition
work are in [F2 validation](../../../docs/validation/f2-study-learning.md).
Runtime assembly qualification is separate from frontend artifact extraction,
source-removed Python packaging, controlled HTTP fixtures and hosted policy. No
live conversion, provider deployment, Actions change or merge is implied.


## Database mixed-file separation: deletion, miss facts and frozen Study policy

This unmerged owner batch separates `card_deletion.py`, `real_game_feedback.py` and `frozen_opening_order.py`. Their complete SQL/driver operations now live in `database/tempo_learning_database/adapters/{card_deletion,real_game_miss_projection,frozen_order}.py`; application guard/label composition remains in the original modules, and frozen preference decisions/public facts live in `src/tempo_learning_runtime/study/frozen_order/`. It adds no shared runtime, new writer/connection/commit authority, schema/contract change, framework or generic SQL port. Independent initialization order and original caller signatures remain. The existing 21 other Learning mixed-file candidates are still unfinished work; this is not a whole-runtime isolation claim.

A one-time AST/string comparison against the captured original source found all 25 SQL literals unchanged across the three splits. SQL query order, activation locks and bounded 16-card projection reads remain in the existing caller transaction. Fingerprint implementation/reader version and PostgreSQL migrations are unchanged. The canonical bounded ownership drawing is in the new Study policy directory README; existing source-schema diagrams remain authoritative for actual keys/fields/relationships.

Focused source checks passed 34 cases in `test_frozen_order_policy.py` and existing `test_queue_ordering.py`. A fresh separate environment installed the newly built artifact and its exact declared dependencies, then passed 36 selected cases: those two suites plus installed deferred-import/denied-authentication and receipt replay. The command used `python -m pytest services/learning/runtime/tests/test_frozen_order_policy.py services/learning/runtime/tests/test_queue_ordering.py services/learning/runtime/tests/test_installation.py -k 'not installed_artifact_has_migrations_and_no_optional_services' -q --rootdir=.` with no source `PYTHONPATH`. The first installation test remains outside this selection because its pre-existing metadata assertion expects Game Evidence contract 2.0.0 while current package metadata requires 3.0.0; no assertion was changed. One existing Starlette/AnyIO deprecation warning occurred. Tests opened only synthetic in-memory SQLite or rejected infrastructure entirely; native PostgreSQL and full-system execution were not run.

`python -m build --no-isolation --outdir /tmp/tempo-learning-db-split-artifacts services/learning/runtime` built the source distribution and rebuilt its wheel from that sdist. Artifact inspection confirmed the new owner fact/policy/storage files have source-identical bytes. The wheel SHA256 was `c5b59a50b0b21b44c6815c9905a40110e132d315c7dda087a2d3f55170c9b3f0` before this documentation-only validation append. Changed relative README links resolve. No live data/schema, services, credentials/grants/security settings, CI triggers or deployment were inspected or changed.


## Database mixed-file separation: Study recovery, bootstrap and teaching

The second Learning batch separates `queue_attempt_origins.py`, `study_bootstrap.py` and `teaching_commands.py`. Complete SQL operations now live in `database/tempo_learning_database/adapters/{queue_attempt_origins,study_bootstrap,teaching_states}.py`. Recovery conflict decisions, request/schema validation, settings field mapping/boolean conversion, essential queue-refresh decisions, accepted-line chess checks, timestamps and response translation remain in the original application modules. Origin reads still stop at the original failed identity/definition fences. Settings writes keep the same row lock and validated column iteration order. Teaching keeps card advisory lock → row lock → application/accepted-definition/learner-turn validation → idempotent write/read. The gateway/caller retains the connection, transaction and rollback authority; the adapters open/commit nothing.

A one-time AST/string comparison found all 16 SQL literals unchanged across the three application/adapter pairs (five origin, six settings/executable, five teaching literals). The f-string settings column comes from the original validated `FIELDS` mapping and the bound value retains its original application encoding. No schema, constraint, migration, public request or response changed. The canonical bounded drawing/source links are in `src/tempo_learning_runtime/study/README.md`; existing database schema READMEs still describe actual keys/fields and remain unchanged.

`test_study_command_storage_separation.py` passed ten source cases covering unprovable recovery before card reads, recovered-completion rollback on the caller transaction, exact failure-marker revision/content fences, settings alias/encoding/enqueue order, accepted-route membership verification and teaching learner-turn rejection before writes. A newly built sdist→wheel installed into a separate environment passed eleven selected cases: that suite plus `test_installation.py::test_import_all_deferred_paths_and_denied_auth_need_no_infrastructure`. The installed command was `PYTHONPATH= /tmp/tempo-learning-db-study-commands-env/bin/python -m pytest services/learning/runtime/tests/test_study_command_storage_separation.py services/learning/runtime/tests/test_installation.py::test_import_all_deferred_paths_and_denied_auth_need_no_infrastructure -q --rootdir=.` One existing Starlette/AnyIO deprecation warning occurred. The original native bootstrap/teaching/transaction suites were not selected or represented as passed; no native/live database or full-system resources ran.

`python -m build --no-isolation --outdir /tmp/tempo-learning-db-study-commands-artifacts services/learning/runtime` built the source distribution and rebuilt its wheel from that sdist. All three adapter files in the wheel match source bytes. Wheel SHA256 at the code/README build was `bd4a0823bdbcb51aaa5eb06710bb6d118396fe8e0f0c6b0b4bf3864ce3cf6262`; subsequent edits only append validation evidence. The new owner diagram parses with Mermaid 11.12 and relative source links resolve. There are now eighteen other Learning baseline candidates still requiring extraction; this batch does not claim full-runtime domain/storage isolation or live deployment qualification.

## Database mixed-file separation: essential work and evidence receipt facts

The third Learning batch separates `durable_tasks.py`, `maintenance.py` and `receivers.py`. Their twenty complete SQL operations now live in `database/tempo_learning_database/adapters/{durable_tasks,maintenance,evidence_receipts}.py` (nine/nine/two SQL literals respectively). Essential-kind/date/rescan/cursor/order decisions, retained-operation scheduling, lifecycle and retry handling, timestamps, workspace/digest validation, custom receiver authority and response translation stay in the application. Existing connection/transaction/rollback authority stays in composition. The owner adapter supplies the same driver exception identities to the original maintenance catches. No framework, SQL execution port, new writer or schema/contract change was introduced.

A one-time AST/string comparison found all twenty SQL literals unchanged; `claim` is an identical function-text move. Caller parameters, create-or-lock, event order, `FOR UPDATE SKIP LOCKED`, two-second lease, generation/token/expiry fences, late rescan row lock and task-before-projection publication order remain. The three selected application modules contain no direct execute/cursor/commit/rollback calls. The canonical bounded drawing, source links and links to complete source-schema ER charts/dictionaries are in `database/tempo_learning_database/adapters/essential-work/README.md`. These are existing source boundaries; real deployment/resource isolation was not checked.

Focused source execution passed eighteen cases: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=services/learning/runtime/src:database /tmp/tempo-db-review-venv/bin/python -m pytest services/learning/runtime/tests/test_essential_work_storage_separation.py -q --rootdir=.` A separate fresh installed environment passed nineteen cases with `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH= /tmp/tempo-learning-db-essential-work-env/bin/python -m pytest services/learning/runtime/tests/test_essential_work_storage_separation.py services/learning/runtime/tests/test_installation.py::test_import_all_deferred_paths_and_denied_auth_need_no_infrastructure -q --rootdir=.` Each run had one existing Starlette/AnyIO deprecation warning. The installed deferred-path check rejects optional imports/socket/SQLite connections. The pre-existing installation metadata assertion expecting Game Evidence 2.0.0 (declared/current dependency 3.0.0) was not selected or changed; this batch does not claim the full installation suite passed. Native enqueue/remediation/evidence/transaction suites and full-system tests were not run.

`PYTHONPATH= /tmp/tempo-db-review-venv/bin/python -m build --no-isolation --outdir /tmp/tempo-learning-db-essential-work-artifacts services/learning/runtime` built an sdist and rebuilt its wheel from that sdist. Fresh installed migration discovery resolves all four ordered entries without connecting, and all six application/adapter wheel files match source bytes. Wheel SHA256 at the code/README build was `38471f9de54e645f3d6a3e8a78d05b77c529e3236ee0f3c10bfeee8ff5d2cb4c`; later edits only correct/append Markdown evidence. The owner diagram parses with Mermaid 11.12, relative links resolve and selected files have no trailing whitespace. Artifacts, original-source captures, logs and the JSON report are retained under `/tmp/tempo-learning-db-essential-work-*`; the task-private installed environment is removed after verification. Fifteen Learning baseline mixed files remain unfinished extraction work. No live data/schema, services, credentials/grants/security, CI or deployment changes occurred.

## Database mixed-file separation: Study exercise and tactic attempts

The fourth Learning batch separates `study_attempts.py`, `study_attempt_commands.py` and `tactic_commands.py` (511 original lines, 43 database call sites). Complete SQL/locks/cursor operations live in same-named `database/tempo_learning_database/adapters/` modules; grading, request identity/replay, ownership/revision/retirement conflicts, requested versus guided rating, requeue/refresh and clean/light/normal scheduling decisions stay outside database. Every operation still borrows the caller's transaction; no connection/commit owner, generic SQL port, framework, domain/schema/contract redesign or shared runtime was added. The sibling cursor is consumed inside its adapter iterator, at the original iteration point.

All original SQL strings are retained. The 20 command query sites share two identical existing reads through named operations, leaving 18 physical literals there; completion/tactic storage retains 11/12 literals respectively (41 physical literals for 43 original sites). One-time captured-original/new comparisons passed seven representative paths and 55 total query executions, with identical returned values, exact SQL sequence and bound parameters: guided review completion including requeue/sibling effects, practice completion, automatic and pending Study submission, review self-assessment, failed and clean tactic attempts. This is a bounded fake-connection relocation comparison, not a native transaction or race proof. Selected application modules now contain no SQL or execute/cursor/commit/rollback calls. The canonical bounded ownership drawing, source links and links to actual ER charts/field dictionaries are in `database/tempo_learning_database/adapters/practice-attempts/README.md`; constraints/migrations are unchanged.

`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=services/learning/runtime/src:database /tmp/tempo-db-review-venv/bin/python -m pytest services/learning/runtime/tests/test_attempt_storage_separation.py -q --rootdir=.` passed 21 semantic cases. A fresh installed environment passed 22 selected cases with `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH= /tmp/tempo-learning-db-attempts-env/bin/python -m pytest services/learning/runtime/tests/test_attempt_storage_separation.py services/learning/runtime/tests/test_installation.py::test_import_all_deferred_paths_and_denied_auth_need_no_infrastructure -q --rootdir=.` Both runs had one existing Starlette/AnyIO deprecation warning. The existing first installation metadata assertion expecting Game Evidence 2.0.0 versus current declared 3.0.0 remains unselected/unchanged. Native PostgreSQL family/eligibility/transaction suites and full-system tests were not run or claimed.

`PYTHONPATH= /tmp/tempo-db-review-venv/bin/python -m build --no-isolation --outdir /tmp/tempo-learning-db-attempts-artifacts services/learning/runtime` built the sdist and rebuilt a wheel from it. All six application/adapter wheel files match source; the source-free installed package resolves all four ordered migration resources offline. Wheel SHA256 at the code/README build was `a4dc109f8860c5eb39c449391c08642e5e47f1450faa7f7ce53db5f2c4acab63`; subsequent edits only correct/append Markdown evidence. The new Mermaid 11.12 diagram parses and README relative links resolve. Original-source captures, differential comparison, artifacts, logs and JSON report remain under `/tmp/tempo-learning-db-attempts-*`; the private installed environment is removed after verification. Twelve Learning baseline mixed files and the initially moved adapter policy audit remain unfinished work. No native/live database, data/schema, service, credential/security, CI or deployment work occurred.
