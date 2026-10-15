# Independent Learning runtime validation

Tested implementation: `c4e24327775e478f13cc46fec899775ecf4dacc6`, based on refreshed `main` `f43b5f7fb4d7ddafeb3124a95f4fa3d64556abc4`. The subsequent documentation commit records these results without changing executable inputs. No live checkout, database, service or development copy was reused.

## Commands and outcomes

Run from the repository root unless a different directory is stated. Evidence was recorded outside the disposable checkout; `$evidence` below names that directory, not a production resource.

| Exact check | Outcome |
| --- | --- |
| `PYTEST_ADDOPTS=--junitxml=$evidence/component-native.xml services/learning/runtime/.venv/bin/python services/learning/runtime/integration/run_postgres.py $evidence` | **44 passed**, 20.55 s. Exact collection and all execution phases matched JUnit, including the caller-specified receipt destination. |
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
- Tested minimal container: `sha256:b9aba3acc180f8280744e58ae7c61982fa8d8927395359172cafc5a32c441edb`.
- PostgreSQL 18.6 fixture: `sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336`.

`native.json` binds the revision, source hashes, exact commands, PostgreSQL/image identities and complete executed inventory. `installed-final/installed.json` binds all copied build inputs, six artifact pairs, dependency lists, interpreter identities, installed inventories and removal of temporary consumer workspaces. XML/logs accompany both. Native container/volume absence and task image-tag removal were verified after the passing run. Evidence is retained before checkout cleanup.

## Limits and remaining acceptance

This is focused component qualification, not a full-system or hosted merge-qualified pass. The full Study UI, D's recovery acceptance, E's shared browser runner matrix and B/F remediation adoption remain with their owners. The published seams are available for that integration; no second admission capability or independent transaction owner was introduced.

The file adapter currently accepts at most 128 definitions per publication, 100 memberships per definition and one active publication owner for each opening repertoire. Default local obligations use accepted delivery time because the current evidence contract lacks historical observation time. Producer extraction, richer chronology and any change to those contracts are outside this runtime PR.

Tablebase cache/offline behavior and broker-free operation are proven independently. Optional-provider protocol success/cache/failure used a real local HTTP provider. Public Lichess availability and provider truth are unverified. Frozen ranks are immutable accepted inputs; their production is unverified here.

No live migration, import/cutover, Content/Game Evidence extraction, authoring operation, installer redesign, deployment or merge was performed. #224/#194 should remain open until their complete demonstrated acceptance is assessed.
