# PR #278 integration qualification

## Historical evidence

Original tested implementation: `52f1ac8fc1e7168e1b1aa8580468be60ec45ef9c`;
original evidence-only head: `56764278a1d1cd57ecbc789d5b47cba2bf8b6143`.
The original [machine-readable record](evidence/qualification.json) is preserved
unchanged. Its 43 installed cases per runtime, 24 native cases, 55 additional
legacy cases and 177-pass/1-failure affected selection are historical results,
not qualification of the integrated candidate. Original raw evidence remains at
`/Users/andy/tempo/test-results/learning-review-224`.

## Integration

Current main `80db7972b2e2ec525084120f8d541509379673a9` includes merged #275,
#277 and #280. Ordinary integration commit:
`ad806afedaf02d15b7949b1c02ee6725e9a6e775`. The merge had no conflicts.
Scheduling 0.2.0, chess-core 0.1.0, all prior registrations, selectors and image
inputs are preserved. Learning 0.1.0 is now included in the actual dependency,
lockfile, test-install, image and component declarations. The original pending
patch is archived outside tracked source and removed from the application.

The production backend Dockerfile is the affected image path. The maintenance
image copies only migration-specific modules and does not import review commands;
it requires no Learning installation. There is no separate affected worker image.

## Completeness repair

The original nonempty-JUnit acceptance predicate accepts a reproduced partial
selection: **1 passed, 42 deselected**. Both runners now reject inherited selectors
before resource allocation. The native runner narrowly permits one external JUnit
destination in either supported spelling/form, preserving the component lane's
structured reporting contract. External pytest plugin injection is disabled.

Independent controlled collection must match unique executed node IDs. Every
setup/call/teardown phase must pass, with zero deselections, missing/duplicate
identities, skips, failures or errors. JUnit must also contain the complete passing
inventory. Test totals are derived from collection, not hardcoded.

## Integrated candidate evidence

Final qualification is recorded after the executable repair commit. Raw command
logs, receipts, installed package origins, artifact hashes, resource records and
patch archive are kept outside the tracked checkout at:
`/Users/andy/tempo/test-results/pr278-integration-20261015/`.

No merge, deployment, live-data/schema change, full historical CI, browser, model,
Rust or Maia workload is part of this qualification. No standalone Learning
service, domain database cutover or production wire-v1 adoption is claimed.


## Original integration results (before qualification review)

Historical integration verdict: **READY FOR MERGE — SUBJECT TO FINAL INDEPENDENT REVIEW.** Tested executable
revision: `16402a9d83d3376cea5af999f81b8477cbf9c5e8`. The final evidence follow-up
changes only this validation document and the new
[integration qualification record](evidence/integration-qualification.json).
The external final-state receipt verifies that all previously tracked inputs,
including executable source, fixtures, manifests, locks, runner scripts and the
README/regression build inputs, remain byte-identical to the tested revision.

| Check | Actual integrated result |
| --- | --- |
| Source-free wheel, CPython 3.12.12 | 43 passed; exact collected/executed identities, guarded imports/connections |
| Source-free wheel, CPython 3.14.8 | 43 passed; same completeness and guard requirements |
| Native PostgreSQL 18.6 through normal component lane | 24 passed; real transactions/concurrency/recovery/frozen-admission cases |
| Local-day compatibility regression | Named case passes; #275 resolves the historical missing `scheduler.date` seam |
| Ten-file affected legacy selection | 199 passed; independently collected identity set matches JUnit |
| Evidence/deletion/lock-order selection | 55 passed; independently collected identity set matches JUnit |
| New runner and installed-backend regression suite | 53 passed in the normal component lane (52 runner cases + 1 installation case) |
| Component checker and declared suite listing | Pass; only Learning application owner selected, three suites |
| Normal owning component lane | 43 application + 53 tooling/installation + 24 native passes; external JUnit receipt preserved |
| Existing component-tooling suite | 72 passed, zero failures/skips/TODOs after declared fixture dependency setup |
| Consumer public-import boundary | Pass using existing Python boundary checker |
| Fresh noneditable backend install | Learning 0.1.0, scheduling 0.2.0, chess-core 0.1.0 in environment site-packages; direct-url editable flag absent/false |
| Production backend Dockerfile | Build and network-disabled/read-only import smoke pass; same versions and noneditable installed origins |
| Lock, registration preservation, conflict artifacts and diff whitespace | Pass; all unrelated lock entries and prior component/shared-suite declarations unchanged |

No qualifying selection has skipped, deselected, missing, duplicate, failed or
errored required cases. Counts reflect current collection, not fixed historical
totals. Setup/call/teardown receipts are retained for installed/native suites.
Installed-wheel SHA-256 remains
`f7063555a77da85e991cc91ac79980784a23208f7433a207a56d68feabf228ce`.
Production image identity:
`sha256:4e0870ba2dbdac6d74a9deb5d46c293768460c0388425cdd1420b207c74e0e52`.
Task-owned native container `tempo224-3e23744a30c04a26` was removed and absence
verified; its PostgreSQL image is recorded in the machine-readable report.
Temporary production image and smoke container were also removed. Shared images,
volumes, caches, checkouts and running services were not cleaned or restarted.

### Commands and identity evidence

The new JSON record contains exact qualification/setup commands, runtimes,
installed dependencies, collected/executed case identities, package input/artifact
hashes, image commands and resource evidence. Raw logs remain at the external
path above. Its `installed/installed.json` contains every build/consumer command;
`native/native.json` contains controlled collection/execution commands and external
JUnit routing. `installation-final.log` preserves all three distribution origins
and direct-url metadata. `legacy-inventories.json` contains exact independent
collection commands and identity comparisons. The additional tooling commands were:

```sh
node tools/component-build/test-node.mjs tools/component-build/tests
TEMPO_COMPONENT_PYTHON="$PWD/backend/.venv/bin/python" node tools/component-build/test-node.mjs tools/component-build/tests
```

The consumer proof invokes `tools/component-build/check-python.py consumers
backend/app tempo_learning_review EXPORTS_JSON` with the actual public `__all__`.
The normal owning lane uses `TEMPO_COMPONENT_PYTHON` for the installed backend
interpreter and `TEMPO_LEARNING_EVIDENCE_DIR` for external native evidence.
All legacy selections clear ambient database and Redis targets and use a task-owned
`TEMPO_DB_PATH`; package source directories are never added to `PYTHONPATH`.

### Failed setup attempts, corrected without code changes

The first legacy runs supplied an erroneous unavailable Redis URL in the task
harness: affected selection **155 passed / 44 failed**, additional selection
**54 passed / 1 failed**. HTTP middleware correctly returned admission-unavailable
503 responses. Removing that task-supplied Redis override restored the normal
SQLite test environment; the unchanged executable revision then passed all
199 and 55 cases. Both original failure logs and corrected reports are retained.

An additional component-tooling run initially produced **67 passed / 5 failed**
because its TypeScript boundary fixtures lacked their declared `chess.js`
dependency. Installing only `packages/chess-line` dependencies restored all 72
passes. No assertions, product code, fixture behavior, or runner code changed.
Legacy HTTP tests retain an existing Starlette/AnyIO deprecation warning.

### Selection and hosted limits

The existing component tooling accepts explicit component/suite IDs. Its actual
Learning selection executes the three registered suites. Declared build/test
inputs cover Learning source and backend adapter consumers; an unrelated
`docs/frozen-maia-order.md` control has no Learning owner. These declaration checks
are preserved in `selection-proof.json`. There is no automatic changed-file
component dispatcher to certify. The separate historical CI planner conservatively
falls back to FULL for unknown source paths; it is unchanged and was not executed.
This existing planner behavior is not a prerequisite for the bounded component proof.

GitHub Actions remains disabled, the PR has no hosted check results, main branch
protection returned 404, and repository rulesets were empty when inspected. No
GitHub settings changed. This is completed engineering qualification for the
specified integration, subject to final independent review; it is not a full-product
or deployment qualification. That result predates the qualification review findings; the follow-up below establishes their repair status.


## Qualification review follow-up

The subsequent review reproduced three tooling gaps: direct registered pytest
commands accepted partial Python selections; failed Docker startup could leave a
created container; and a failed native retry could retain an earlier success
summary. The follow-up replaces both Python registrations with a Learning-owned
complete-suite wrapper, verifies cleanup after every creation attempt, and makes
current atomic native evidence conditional on both inventory and cleanup success.
Shared component tooling, production application behavior and dependency versions
remain unchanged. Fresh final qualification is recorded after the executable
repair commit; earlier results remain historical unless explicitly verified reusable.

Follow-up raw evidence is stored outside tracked source at
`/Users/andy/tempo/test-results/pr278-qualification-repair-20261015/`.


### Follow-up final results

**All three review findings resolved; ready for final independent review.**
Tested executable revision: `9e14bd103c020a95b4cd436e91d444af854fb4c4`.
The later evidence commit changes only this validation document and
[the qualification repair record](evidence/qualification-repair.json); the external
final-state receipt verifies all other tracked inputs remain byte-identical.

| Fresh check | Actual result |
| --- | --- |
| Source-free installed wheel, CPython 3.12.12 | 43 passed; exact inventory and existing service guards |
| Source-free installed wheel, CPython 3.14.8 | 43 passed; exact inventory and existing service guards |
| Actual normal component lane | 43 application + 101 tooling/installation + 24 native passed |
| Owning tooling regressions | 100 runner/lifecycle cases + 1 backend installed-origin case, all passed through the controlled wrapper |
| Actual component lane with reproduced inherited selector | Rejected with exit 1 before native provisioning; no component/native evidence allocation |
| Actual component lane with no-match inherited selector | Rejected with exit 1 before native provisioning; no component/native evidence allocation |
| Native PostgreSQL 18.6 | All 24 required identities passed; pytest and qualification exits both 0; ownership-checked removal and absence verified |
| External JUnit contract | Normal component tooling consumed all three suite reports; native external output also retained in its own invocation/root evidence copies |
| Component checker/listing, lock verification, conflict and whitespace checks | Passed |
| Fresh real backend Dockerfile build/import smoke | Passed; all three local packages noneditable with expected versions/origins |

The selector probes use:

```sh
PYTEST_ADDOPTS="-k 'ordinary_review_sequence or full_qualification_requires_exact_complete_normal_inventory'" TEMPO_COMPONENT_PYTHON="$PWD/backend/.venv/bin/python" node tools/component-build/test-lane.mjs --component learning-review-application
PYTEST_ADDOPTS="-k no_such_required_identity" TEMPO_COMPONENT_PYTHON="$PWD/backend/.venv/bin/python" node tools/component-build/test-lane.mjs --component learning-review-application
```

Both are intentional negative checks, not failed qualification attempts.
The full positive lane clears inherited selectors and uses
`TEMPO_LEARNING_EVIDENCE_DIR` for external raw evidence. Its new wrappers independently
collect and execute the exact identity sets, validate successful setup/call/teardown
phases and preserve the shared JUnit destination. Suite counts come from collection,
not fixed acceptance totals.

Native container `tempo224-e16d11554644c7f9` was removed and absence verified before
publishing `complete: true`. Every invocation has its own receipt/log directory;
root summaries now separate `pytest_exit_code` from overall qualification status.
Unit fault fixtures prove failed starts after creation are cleaned, mismatched
resources are retained, inspection errors never become absence, and failed cleanup
cannot publish success. Success-then-failure fixtures preserve earlier evidence
while invalidating the current success marker. These simulations created no real
containers and do not substitute for the fresh 24-case native execution.

The backend image was rebuilt because Docker COPY inputs include the changed tooling.
Image identity: `sha256:3c146064335ee556c96d0774ad0d7b3211fda1fad2846f744167491933ddc059`.
Its guarded, network-disabled/read-only import smoke passed; its temporary tag and
smoke container were removed. The wheel hash remains
`f7063555a77da85e991cc91ac79980784a23208f7433a207a56d68feabf228ce`.
The new record preserves exact build/test commands, Python versions, installed
artifacts, node IDs, per-phase receipts, image identity and cleanup evidence.

### Explicitly reused evidence

The earlier **199 affected legacy** and **55 additional legacy** passing results
were not rerun. All **599** tracked inputs under backend, scheduling, chess-core,
scripts and shared component tooling match their tested hashes. The CPython 3.12.12
backend environment's pinned distribution versions also match the prior record.
No review/scheduling application source, backend fixture, package dependency,
production Dockerfile or shared component runner changed in this follow-up.
`reuse-proof.json` and the full source snapshots in the external evidence directory
preserve this comparison. The prior production-image result was not reused.

No final positive check failed or skipped required cases. The qualification fixes
remain Learning-owned; no shared CI behavior, application/API semantics, GitHub
settings, live database/schema, frozen ordering, merge or deployment changed.
