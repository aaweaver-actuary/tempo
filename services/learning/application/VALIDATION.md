# Agent B / #224 validation and remaining scope

Tested implementation: `52f1ac8fc1e7168e1b1aa8580468be60ec45ef9c`, based on refreshed
main `6b99085f8978346a959b5d4a6ea44b6e56f05dd6`. The later evidence-only commit
changes no runtime, dependency, fixture or build input. [The machine-readable
record](evidence/qualification.json) preserves executed identities/counts, source
hashes, artifact hashes, runtimes, commands, D compatibility and the known failure.
The broad affected legacy selection ran at `744c47ce`; its complete relevant
application, test, scheduler and import-policy inputs remain byte-identical.

## Results and commands

| Selection | Result |
|---|---|
| Pre-extraction scheduling/store/recovery/continuation characterization | 98 passed, zero skipped |
| Installed application under import/connection guards, CPython 3.12.12 | 43 passed, zero skipped |
| Same installed suite, CPython 3.14.8 | 43 passed, zero skipped |
| Real installed application + legacy adapter, PostgreSQL 18.6 | 24 passed, zero skipped |
| Retargeted evidence, deletion and lock-order seam selection | 55 passed, zero skipped |
| D published checker / owning test-lane compatibility | Checker passes; 43 pure + 24 native, structured identities preserved |
| Affected legacy seam selection below | 177 passed, 1 failed, zero skipped |
| Original unchanged local-day failure reproduced separately | 1 failed, same absent compatibility symbol |
| Existing shared Python package boundary checker | Passed |
| `npm run check:conflicts`; `git diff --check`; patch application check | Passed |
| Pending patch lock check in disposable context | Passed; only this dependency and its metadata added |

```sh
.venv/bin/python services/learning/application/qualify.py EVIDENCE/revised-installed
.venv/bin/python services/learning/application/integration/run_postgres.py EVIDENCE/revised-native
PYTHONPATH=backend:backend/tests .venv/bin/python -m pytest backend/tests/test_scheduling_review_application.py backend/tests/test_scheduling_review_store.py backend/tests/test_scheduling_package_adapter.py backend/tests/test_scheduling_boundary.py backend/tests/test_prepared_review_continuation.py backend/tests/test_queue_attempt_recovery.py backend/tests/test_queue_attempt_issuance.py backend/tests/test_phone_offline_training.py backend/tests/test_study_durability.py backend/tests/test_frozen_opening_order.py -q -o cache_dir=CHECKOUT/.pytest_cache --junitxml=EVIDENCE/final-affected.xml
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=UNCHANGED_CHECKOUT/backend .venv/bin/python -m pytest UNCHANGED_CHECKOUT/backend/tests/test_study_durability.py::test_default_scheduler_uses_local_day_not_utc_day -q -p no:cacheprovider --junitxml=EVIDENCE/baseline-scheduler-failure.xml
git -C DISPOSABLE_D_CONTEXT apply --check CHECKOUT/services/learning/application/registration.patch
uv lock --check --offline --directory DISPOSABLE_D_CONTEXT/backend
```

`CHECKOUT`, `EVIDENCE`, `UNCHANGED_CHECKOUT` and `DISPOSABLE_D_CONTEXT` are
absolute local directory substitutions, not new runner flags. The baseline four
files were the scheduling application/store, prepared continuation and queue
attempt recovery files from the affected selection. The installed qualifier logs
its exact subprocess commands, exports pinned development requirements, checks
zero-selection failure, removes unrelated/build source, and preserves artifacts.
Full logs/JUnit and wheels are retained outside the disposable checkout under
`/Users/andy/tempo/test-results/learning-review-224`.

Wheel SHA-256: `f7063555a77da85e991cc91ac79980784a23208f7433a207a56d68feabf228ce`.
Native image: `sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336`.
Native cluster `tempo224-3641887fe3a2648a` was uniquely owned, used ephemeral
storage and separate legacy reader/writer credentials, and was removed with its
absence checked. No external database target was accepted. Broker wakes were
pointed at unavailable loopback port 1; durable queue intent remained asserted.

## Acceptance mapping

- Real orchestration moved out of HTTP main: application validation, original
  request binding, replay/recovery, guided scheduling decisions, receipt/result
  sequencing, evidence completion and prepared-continuation decisions execute in
  the installed library. Main is a compatibility delegate; legacy command assembly
  has no reverse import of main or the global task registry.
- Source-free package execution: all 43 application/boundary cases execute under
  service guards on both qualified Python versions. No lazy legacy/provider/model
  import is allowed. Explicit command registration has no import-time side effect.
- Atomic persistence: native failures after evidence/history/logical receipt and
  transport completion undo all logical effects; handled reconciliation conflict
  rolls back before its normal response/transport receipt. Native barriers observe
  two competing connections blocked on real locks, preserving one logical attempt.
- Replay and recovery: lost response, original/changed payloads, independently
  completed attempts, deletion/archive/revision distinctions, terminated connection
  rollback and fresh-process replay retain their existing distinct meanings.
- Prepared continuation: date/cycle/revision/kind/unavailability/contention checks,
  current admission and the original edit/deletion lock regression pass through
  the relocated seam. Active/inactive frozen reader fixtures preserve admission
  and positions without invoking private plan creation/apply or reranking.
- Required wrapper effects: existing scheduling/store characterization retains
  history-before-maturity, clocks/seeds, remediation and legacy unlock sequencing;
  native ordinary review also clears satisfied gameplay priority and proves durable
  evidence, snapshots, queue refresh and validated repeat scope.

## Concrete remaining work

D's initial schema/registration baseline #277 is published but has not merged.
Shared manifests, backend metadata/lock and central tooling remain untouched on
B's branch. `registration.patch` now targets D's exact published baseline
`cbc12f77b114c60e91a91d1f9d4b8cb929f4153f`, including explicit test cwd and local
inventory. It applies in a disposable combined context: D's unmodified checker
passes, and its owning test lane records 43 pure + 24 native passing identities.
B's native runner preserves D's externally supplied JUnit path and also retains
its own report; both standalone and D report modes pass. The patch also includes
only B's backend dependency/image install additions. Apply/reconcile those small
additions after D merges; the parent Learning service stays planned.

The 55-case additional selection retargets the existing evidence and card-lock
mocks to their actual new owners, preserving connection identity, evidence order,
rollback and result assertions. No tests were skipped or assertions removed.

The broader affected selection is **not green**: unchanged
`test_default_scheduler_uses_local_day_not_utc_day` patches missing
`app.services.scheduler.date`. It also fails on unchanged source. C owns this
compatibility behavior; C reports its pending PR #275 restores the existing clock
seam with the original test unchanged. B has not imported C's implementation or
claimed a combined installation pass. Rerun that boundary after C integrates.

No full-system pass, independent review, browser progression run, model/provider
workload, standalone Learning deployment, broker removal, domain database cutover,
production wire-v1 conformance, merge or deployment is claimed. No browser/client
behavior or public wire shape changed. #224 stays open for thin HTTP composition,
essential-worker assembly and independent runtime acceptance; #194 owns bounded
broker-independent foreground delivery. Local passes do not certify those items.

GitHub Actions was read as disabled. Classic main branch protection returned 404
("Branch not protected"); the repository ruleset listing was also empty. No GitHub settings or protections were changed.

The D compatibility commands (with only B's patch applied in the disposable
context) are recorded verbatim in the JSON evidence. D tools were hash-compared
to its published commit and remained unchanged. No other owner's worktree or
shared source was edited for this integration proof.
