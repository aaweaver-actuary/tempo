# F2 production Study / independent Learning acceptance

The implementation connects the existing production Study UI, board, intent store,
session coordinator and exercise adapters to installed Learning and disposable
PostgreSQL. The source/interface owner is documented in the existing
[F2 handoff](../study/f2-remediation-handoff.md). No migration is added. Live data,
profiles, deployment, Actions configuration and merge are outside this change.

## Source-bound acceptance map

| Scope | Implementation / regular proof | Boundary |
| --- | --- | --- |
| #194 / #224 Study runtime assembly | `static/src/main.tsx`, connection boundary/client; `integration/run_study.py`, `integration/study_tests/test_study_ui.py` | Real production UI, installed Learning, restricted PostgreSQL. This is runtime integration; independent frontend source-removed packaging is not established. |
| #219 / #221 session and intent ownership | `home_view.tsx`, Navbar, dedicated Learning delivery; request inventories in native proof logs | No essential legacy/optional service. Workbench launch is explicit. Catalog assets remain shipped frontend assets. |
| #13 / #198 exercise adapters and Study entry | Learning bootstrap/settings/teaching handlers; ordinary queue/coordinator, named bootstrap/teaching tests and full UI scenarios | Six Learning-owned settings only, timezone read-only; no account/provider configuration. |
| #249 / #266 recovery and offline | Existing browser-intents/study-session, authority-scoped offline facade; regular intent/queue suites, native faults/storage/two-tab/cached-shell proofs | Original commands retained; foreign unresolved or ambiguous legacy work blocks conversion. Offline answers require an issued attempt and Learning confirmation. Release-scale device/browser qualification remains separate. |
| F1 remediation | Existing regular `test_remediation.py`, producer suites; full UI real-producer early/late scenarios | Authenticated actual producer delivery, stopped producer, issuance-bound settlement; no new local settlement authority. |
| Families | Regular `test_families.py` / `test_endgames.py`; production native five typed forms, tactics, middlegame, defense and endgames | Uncovered defense and uncached unavailable tablebase results create no invented grade. |

The assembly uses task-owned static and forwarding servers, installed Learning and
an explicitly provisioned disposable PostgreSQL database. Only the two remediation
cases start the separate installed producer CLI, which exits before settlement.
The legacy backend, Redis, brokers, Content/Intelligence/model/engine services are
absent from this assembly. Unrelated live services on the host were preserved;
browser traffic to any origin outside Study and its Learning forwarding endpoint
fails acceptance. Forwarding faults drop real requests/responses without inventing
essential API responses. Fixture activation is explicit and separate from Study.

## Evidence categories and limits

Final exact commands, nonzero inventories, tested revision, source hashes, artifacts
and cleanup are recorded below and in [the machine-readable acceptance map](f2-study-learning.json). Native browser/PostgreSQL evidence,
installed source-removed package consumers, controlled/mocked UI and reused evidence
are identified separately. Failed exploratory runs are retained as failed evidence.
No local result implies hosted qualification or closes a broad parent issue.

Remaining work includes live historical obligation/content/history/profile import
and provenance conversion, publication scale beyond the retained 128-definition
bound, independently extracted Study/Workbench frontend artifacts, offline release
and deployment/production cutover. There is no live conversion, deployment, merge,
Actions policy change or broad issue closure in F2.

The full frontend owner lane exposed a timer-policy drift: the root suite keeps
IndexedDB task turns real, while the lane configuration froze them. The lane now
reuses the root timer configuration. Existing named storage/timer regressions
remain selected; no timeout, assertion or inventory exclusion was increased to
mask the failure. Protocol fixtures now return their actual submitted attempt or
operation identity, await durable setup and observable deferred-panel readiness,
and distinguish a completed review receipt from unrelated teaching receipts.

## Qualified revision and commands

Base and rechecked remote main: `0c7ef737bb2daacd8f671f252ce5a1d0787b1871`.
Tested implementation: `cceafce1210282d7a2510e0dd54ff436f035068b` on
`codex/f2-study-learning`. Later publication commits change only documentation and acceptance
records; there are no subsequent executable differences. The final published SHA,
PR and cleanup readback are preserved in `handoff.json` in the evidence directory.

Original working directory: `/Users/andy/.codex/worktrees/f2-study-learning/tempo`.
Evidence: `/Users/andy/tempo/test-results/f2-study-learning-20261016` (outside the
eligible disposable checkout). In the commands below, `taskEvidence` means that
absolute path. Complete parent/child command arguments, source hashes, collection,
execution, JUnit, dependency versions and cleanup receipts remain in the raw reports.

| Exact check (repository root) | Outcome / inventory |
| --- | --- |
| `services/learning/runtime/.venv/bin/python services/learning/runtime/integration/run_study.py "$taskEvidence/study-release"` | **37/37 passed**, 184.83 s; production UI, real Chromium, installed Learning and restricted PostgreSQL. Exact collected/executed/JUnit identities agree. |
| `TEMPO_COMPONENT_PYTHON="$PWD/services/learning/runtime/.venv/bin/python" node tools/component-build/test-lane.mjs --suite learning-full-study` | **35/35 passed**, 157.38 s native execution; proves actual owning-suite dispatch. This earlier source-bound run is retained separately; the final canonical command above covers subsequent delivery/runner compatibility repairs. |
| `services/learning/runtime/.venv/bin/python services/learning/runtime/integration/run_postgres.py "$taskEvidence/native-release"` | **160/160 passed**; complete regular Learning native suite, including F1, actual producer, installed-container, transaction, migration, teaching and durable-session proofs. |
| `services/learning/runtime/.venv/bin/python services/learning/runtime/qualify.py "$taskEvidence/installed-release"` | **3/3** in each Python **3.12.12 wheel/sdist** and **3.14.8 wheel/sdist** consumer; 14 artifacts from seven packages; sources removed; four required empty-selection probes each exited 5. |
| `TEMPO_TEST_LANE=frontend node_modules/.bin/vitest run --config vitest.lanes.config.ts --reporter=default --reporter=json --outputFile="$taskEvidence/frontend-release.json"` | **1,666/1,666 passed**, 154 files. A separate `vitest list --config vitest.lanes.config.ts --json --staticParse=false` collection exactly matches the executed multiset. Existing repeated parameter titles are retained with occurrence identities. |
| `TEMPO_PYTHON=/private/tmp/tempo-f2-schema-support-20261016/python node_modules/.bin/vitest run tests/unit/api-schema-parity-regressions.test.ts --reporter=default --reporter=json --outputFile="$taskEvidence/schema-release.json"` | **6/6 passed**. The task-owned wrapper uses installed Learning's interpreter plus only the missing pinned `redis==6.4.0` client in a separate support path; no Redis/backend service starts. The wrapper/package setup is retained in `schema-support-setup.json`. |
| `npm run test:ui-isolated:list` then `npm run test:ui-isolated` | **28/28 passed**, 42.4 s; Chromium plus the selected Firefox/WebKit interaction cases, strict controlled legacy HTTP fixtures, zero API escapes, cleanup verified. |
| `node tools/component-build/test-lane.mjs controlled-browser` | **54/54 passed**, reused unchanged, including non-mutating pre-start storage inspection; separate collected/executed identity equality. |
| `node tools/component-build/test-lane.mjs --component browser-intents --component study-session` | **15/15** intent and **10/10** session cases, reused unchanged; original package regressions retained. |
| `TEMPO_COMPONENT_PYTHON="$PWD/services/learning/runtime/.venv/bin/python" node tools/component-build/check-components.mjs --component learning --component browser-intents --component study-session --consumers` | **9 components** and retained consumer boundaries passed. |
| `node tools/component-build/test-node.mjs tools/component-build/tests` | **77/77 passed**, reused unchanged owning tooling inventory. |
| `npm run typecheck` | Passed. |
| `git diff --name-only origin/main -- '*.ts' '*.tsx' \| xargs node_modules/.bin/eslint --format json --output-file "$taskEvidence/lint-release.json"` and `node_modules/.bin/eslint scripts/prove-study-learning.mjs --format json --output-file "$taskEvidence/harness-lint-release.json"` | **44 TypeScript files plus the native harness**, zero errors/warnings. |
| `/Users/andy/.local/bin/ruff check services/learning/runtime/src services/learning/runtime/tests services/learning/runtime/integration --select F821,F822,F823 --no-cache --output-format concise` | Passed focused undefined-name checks. |
| `npm run check:conflicts`; `git diff origin/main --check` | Passed. |
| `npm run build` | Existing default legacy production build passed. Compilation does not establish SSR runtime acceptance. |
| `TEMPO_TARGET=demo TEMPO_BROWSER_BUILD_DIR=<task-owned-temp> node_modules/.bin/vite build --config vite.static.config.ts` | Existing demo production configuration passed using genuine pinned prebuilt chess assets; task output removed. No Rust rebuild or new demo runtime claim. |

## Artifact and isolation evidence

The runtime wheel is SHA-256
`b965434e99486c12694595d7b4741941316faa8cafa8ad7fb87bd6d4c0bb9647`;
sdist `13040e6c691b7ab88107832958663f392f38463d123b573a5ff3391e4d3c0a2c`.
All 14 artifact hashes and consumer inventories are in `installed-release/installed.json`.
The final Study container is
`sha256:06bd1bb88f782ed460aef82f4902fc26cec9529f5a74f22c335aeb0b64952b32`;
PostgreSQL fixture is
`sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336`.
`study-release/study-assets.json` records all **849** production assets;
its SHA-256 is `b8a252113e2a97ab108a482f02aeb845729a786e19cacb3a1007a88f415c1332`.
No credential is embedded in those assets or retained command envelopes.

`input-equivalence-release.json` verifies **94 runtime sources + 308 dependency
inputs**, **94 full-Study runtime sources + 1,208 assembly dependencies**, and
**236 installed-package build inputs** against the qualified bytes, with no
differences. Native proof logs retain redacted request inventories, original
command keys/bodies, actual browser version and fixture/fault events. The cached
shell case retains the offline answer across reload with no committed review,
then verifies its exact completed authenticated Learning receipt and one actual
PostgreSQL review. Offline rendering alone is not treated as confirmation.

The native UI includes actual delayed-response/replacement cancellation and a real
credential rotation followed by a second Learning origin: the original receipt
remains unchanged, and the new command binds the new authority. Allowed-origin
401/403 responses are visible through CORS; Host validation still covers preflight.
A regular regression rejects an older writer arriving while bootstrap is in flight.
Successful connection reloads the shell so stores, caches and coordinators rebind.
Native PostgreSQL uses Python 3.13.5. The 160-case owner includes both ASGI route
calls with real PostgreSQL and actual installed TCP/container/browser cases; the
37-case production UI suite uses actual HTTP throughout.

Native/postgreSQL evidence is separate from the 28 controlled browser cases,
which use mocked legacy HTTP and block service workers, and from pure unit/static
checks. Source-removed Python artifact independence is established by the four
installed consumers. Separately extracted Study/Workbench frontend package
independence is **not** established; those manifest targets remain planned.
No historical F1 report substitutes for the fresh 160-case run. Exploratory
source-equivalent passes are not added to final counts.

The pre-routing baseline `controlled-browser-runs/run-Ps60og` has head `0c7ef737`,
a recorded uncommitted overlay (`9499ec374d7def52c301144c4da138ad42864a32259242757f59b98d3b852fdb`),
and 28 passing browser cases, but the invocation **failed** for an escaped
`GET /api/repertoires`. Its baseline request inventory is preserved as failure
evidence. Earlier missing-route, adapter, fixture and timer-policy failures remain
in their original directories/logs. The final passing owner runs supersede them;
none was skipped or relabelled as a pass. Existing Starlette deprecation and
browser-environment diagnostics remain in raw output.

GitHub Actions policy was read with
`gh api repos/aaweaver-actuary/tempo/actions/permissions` and returned
`{"enabled":false,"sha_pinning_required":false}`. Remote main was rechecked at
`0c7ef737`. No Actions setting, workflow, branch protection or issue state changed.
These are local focused results, not hosted or full-system merge qualification.

All native invocations recorded removal of their exact owned PostgreSQL container,
volume, runtime image tag and scratch. The canonical build removed its temporary
asset directory; installed consumer workspaces and demo output were removed.
Browser evidence and original refs are preserved outside the checkout. Final
checkout/process/resource verification and the published ref readback are in the
external handoff; unrelated development copies, live databases/profiles and shared
caches are preserved.

The 15/10 package, 54 harness-unit and 77 tooling results are explicitly reused: their
tracked sources, fixtures, metadata and toolchain inputs are unchanged since
`0175a978`. `input-equivalence-release.json` records the zero-difference path audit;
current runtime/component boundaries were checked freshly. No old native, package
consumer, production UI or full frontend result substitutes for the final runs.


## PR #291 independent review repairs — October 16, 2026

Starting PR head: `5cc32bb99d12ad3ef91517a26203000b57dab601`; its original base
was `0c7ef737bb2daacd8f671f252ce5a1d0787b1871`. Current remote main
`fbf02120b311a9c0ad9ece9deb271665f79b4544` was merged normally, preserving both
regression-index additions. Qualified executable revision: `67684c2a8532df9d28d67cc02a85a1553181a105`.
The final published documentation commit and exact remote readback are recorded in
`/Users/andy/tempo/test-results/pr291-review-repair-20261016-a1/handoff.json`; the publication diff must contain documentation only.
The original acceptance record above and its evidence directory remain intact.

R1 now classifies connection failures explicitly. Only a transport failure while
resuming the exact saved, registered authority can expose cached practice, after
schema/workspace/timezone and read-only retained-storage validation. The browser
network flag confers no authority. HTTP rejection, invalid bootstrap, unsafe
bindings and storage failure remain blocked; no cache authorizes a first connection.

R2 distinguishes original resumption, explicit registered recovery without a tab
session, and conservative cutover. Registered foreign work remains isolated during
recovery; it still fences unrelated cutover. Both inventory checks remain, and a
connection generation fence rejects stale bootstrap results and disconnect races.
Existing storage keys, immutable envelopes, claims and Learning contracts remain.

R3 removes activation, admission and capture affordances in independent Study while
keeping packaged tactics and accepted/local endgame practice. Capture rejects before
pending-state reads, mutation or transport. The nearby audit also found automatic
legacy card-deletion recovery: Study now gates it and the shared command rejects
before side effects. Exact pending capture/deletion keys are inspectable and block
ambiguous cutover. Workbench/legacy authoring retains its regular regressions.

Evidence is retained at `/Users/andy/tempo/test-results/pr291-review-repair-20261016-a1`. Exact argument vectors, revisions, exit codes and
logs are in `command-ledger.json`; `inventory-validation.json` checks actual case
identities; `input-equivalence.json` verifies unchanged consumed inputs across the
qualified commits. All following runs were executed in this repair task. Earlier
qualified commits are identified explicitly rather than relabelled as newer runs.

| Suite | Command receipt | Passed / selected |
| --- | --- | ---: |
| Focused frontend | `focused-qualified.json` | 261 / 261 |
| Full frontend | `frontend-complete.json` | 1,701 / 1,701 |
| Native Study | `native-complete.json` | 41 / 41 |
| Registered native dispatch | `registered-qualified.json` | 41 / 41; same inventory |
| Learning/PostgreSQL | `learning-qualified.json` | 160 / 160 |
| Controlled browser | `ui-qualified.json` | 28 / 28 |
| Persistence/session packages | `intent-session-packages.json` | 15 / 15 and 10 / 10 |
| Controlled harness unit | `controlled-unit.json` | 54 / 54 |
| Installed consumers | `installed-final.json` | 3 / 3 in each of four consumers |

Typecheck, changed TypeScript/TSX and native-driver lint, nine component boundaries,
conflict/whitespace checks, and Study/legacy/demo builds pass. Frontend collection
and execution agree on all 1,701 cases in 156 files, with no failed, pending or TODO
cases. Native collection/execution/JUnit agree. Native dispatch repeats the same
41 cases and is not counted as independent extra coverage. Python 3.12 and 3.14
wheel/sdist consumers retain their source-removed and expected-empty-selection proofs;
all 236 installed-package build inputs remain identical to their fresh task run.

The two new browser authority origins forward to one installed Learning database.
Their same-context tabs share localStorage/IndexedDB and use separate session
bindings. Actual settings commands lose replies, then reconcile through their
original origins with unchanged identities, payloads and foreign records. This is
not a separate-database proof. The browser-online outage retains an actual issued
head and answer until a real receipt and one PostgreSQL review confirm it.

Failed exploratory runs remain failures in the ledger: 14 baseline reproductions;
an early Endgames read gate and tab-role test error; the tab's own recovery racing
with a foreign-byte snapshot; and an interruption fixture reloading before its
failed delivery released its claim. Two existing frontend fixtures now await real
control readiness and durable writes. A concurrent Learning run hit two transaction
timeouts; the separate complete 160-case run passed without changing budgets.
No assertion, timeout, selected case or test registration was weakened.

The actual 850-asset production build matched its manifest and contained no
credential literals under the recorded scan. Task owner labels and cleanup receipts
verify removal of eight PostgreSQL fixture containers, two runtime test containers,
eight volumes and eight image tags. Native temporary data/builds and the demo output
were removed; shared caches, other checkouts and live Study were preserved. Final
checkout removal and process/readback verification are in the external handoff.

Historical migration, production cutover, publication scale, separately extracted
frontend artifacts and cross-device/browser offline release qualification remain
outside F2. Local results do not establish hosted or full-system merge qualification.

## Post-merge P3 — bootstrap body transport recovery, October 16, 2026

Base: merged F2/current main `0edf566ad48f51846a43d3341809379ff0469ada`.
Branch: `codex/study-bootstrap-body-read-p3`. Qualified executable source:
`dda0ab11c9c21252629e5b0fd488572cb70cd09d`. Publication identity and final
documentation-only source comparison are retained in the external handoff.
This is a separate follow-up to merged PR #291; its historical records above
remain unchanged.

`response.json()` combined network body consumption and JSON parsing under a
bootstrap-error catch. A stream interruption after HTTP 200 therefore denied
otherwise authorized cached practice. This was a nonblocking F2 availability
defect: it failed closed and did not grant authority or corrupt retained data.
The client now reads the body once with `response.text()`, classifies recognized
stream/abort/timeout failures through the existing transport eligibility helper,
then parses and validates the completed text separately. One original five-second
signal governs both acquisition and consumption. Used/locked bodies, unknown body
exceptions, completed malformed/empty JSON, schema/workspace/timezone failures,
explicit HTTP rejection and unsafe retained storage remain blocked.

The only changed production file is `app/lib/study-connection.ts`; the existing
connection boundary is unchanged. The other changes are its two existing frontend
test owners, `scripts/prove-study-learning.mjs`, the existing native Python
parameter inventory, the two owning regression documents and this appendix.
Original resume/recover/cutover meaning, dual authority inventory, generation
fences, immutable commands, issued-head restrictions and Learning-only settlement
remain intact. There is no migration or deployment.

The named red regression is `P3-U1 post-header body transport failure retains
original-authority cached practice and immutable commands` in the existing
connection suite. At test-only commit `c3a9873289e942740598cf21986ed72e636de572`,
an actual HTTP-200 `Response` delivered a chunk and then errored while being read:
the expected `transport`/cached-available result was instead
`bootstrap`/cached-unavailable. The same regression passes after repair.
P3-U1–U15 and additional unknown-exception/cache/recovery cases retain their
frontend owner; P3-C1–C4 retain the startup owner. No selected assertion, timeout
or existing case was removed or weakened.

Evidence directory: `/Users/andy/tempo/test-results/study-bootstrap-body-p3-20261016-a1`.
`command-ledger.json` retains exact ordered argument vectors, revisions, overlays,
exit codes and logs. `qualification-summary.json`, `inventory-validation.json`,
`input-equivalence.json`, `adversarial-review.json` and `handoff.json` retain the
compact qualification, source binding, independent self-review and cleanup.
In the commands below, `$taskEvidence` is that absolute directory and
`$taskPython` is the isolated checkout's `services/learning/runtime/.venv/bin/python`.

| Check / command | Result and source |
| --- | --- |
| Focused connection/startup, intent, pending-attempt, offline/recovery and authoring owners; exact ten-file `vitest run` arguments in `focused-final.json` | **247/247**, at `ba371631`; its consumed inputs are unchanged at the qualified executable SHA. |
| `TEMPO_TEST_LANE=frontend node_modules/.bin/vitest run --config vitest.lanes.config.ts --reporter=default --reporter=json --outputFile="$taskEvidence/frontend-qualified-vitest.json"` | **1,739/1,739**, 156 files, at `ba371631`; the later native-harness change is outside this lane's inputs. |
| `TEMPO_TEST_LANE=frontend node_modules/.bin/vitest list --config vitest.lanes.config.ts --json --staticParse=false` | **1,739** collected; the complete execution multiset agrees, with no failed, skipped or TODO cases. |
| `$taskPython services/learning/runtime/integration/run_study.py "$taskEvidence/native-study-final"` | **42/42**, production Study/real Learning/restricted PostgreSQL, at the qualified executable SHA. |
| `TEMPO_COMPONENT_PYTHON="$taskPython" node tools/component-build/test-lane.mjs --suite learning-full-study` | **42/42**, same collected/executed/JUnit inventory; registered dispatch repeats coverage and is not counted again. |
| `$taskPython services/learning/runtime/integration/run_postgres.py "$taskEvidence/learning-final"` | **160/160**, fresh complete Learning-native qualification at the executable SHA; matching collection/execution/JUnit. |
| `$taskPython services/learning/runtime/qualify.py "$taskEvidence/installed-qualified"` | **3/3** in each of Python 3.12/3.14 wheel/sdist consumers; **14 artifacts**, source removed, four required empty-selection probes returned 5. All 236 package inputs remain identical to the fresh `5571e2a2` run. |
| `npm run test:ui-isolated:list`; `npm run test:ui-isolated` | **28/28**, fresh at `5571e2a2`; consumed production/browser inputs remain identical. |
| `node tools/component-build/test-lane.mjs --component browser-intents --component study-session` | **15/15** and **10/10**, fresh at the qualified executable SHA. |
| `npm run typecheck`; changed-file ESLint; component/consumer boundary checks | Passed. Exact commands and source equivalence are retained in their receipts. |
| `npm run build:study` through the native runner; `npm run build`; demo Vite build with task-owned `TEMPO_BROWSER_BUILD_DIR` | Passed production compilation; unchanged default/demo consumed inputs are checked against their fresh `5571e2a2` runs. This is not SSR runtime acceptance. |
| `npm run check:conflicts`; `git diff origin/main --check` | Passed; final publication checks are retained separately. |

The new `bootstrap-body-interruption-browser-online` scenario uses real Learning
HTTP, restricted disposable PostgreSQL, production Study, native board interaction,
the service worker and an actual issued head. For both interrupted reloads,
Chromium observed HTTP 200 and began body consumption before the proxy cut the
response after 144 of 288 actual upstream bytes. Browser online status remained
true. The answer stayed local without a committed review or invented successor.
Reconnect preserved its command identity/payload, received a real Learning receipt,
and observed its acknowledgement transaction commit before normal queue replacement.
The owning Python case independently requires exactly one PostgreSQL review.
The retained request inventory rejects unrelated origins and the evidence audit
also checks that no legacy frontend API or optional Learning path was used.

Failed attempts remain explicit: two setup runs selected no tests because the
copied dependencies lacked local links and pinned `fake-indexeddb`; the first
native run exposed an acknowledgement-observation race in the new test; the next
run passed the P3 case but an existing remediation fixture's Learning process did
not become ready, leaving an empty server log. Its cause is unestablished. The
complete suite was rerun without changing budgets, selection or assertions.
An initial Learning-only run passed 157 cases but its three browser proofs lacked
their component-local TypeScript compiler. Only that browser package's locked
dependencies were installed before rerunning the complete 160-case owner.

Cleanup receipts verify removal of six PostgreSQL fixture containers, two runtime
API test containers, six volumes and six temporary image tags. All 362 observed
ports were closed and no task-owned process remained. Four native Study build
directories, the demo output, controlled-browser output and installed-consumer
scratch were removed; raw evidence is retained outside the checkout. Final
checkout/build cleanup follows pushed-ref verification and is recorded in the
external handoff. Primary main remains clean at the original base; live Study,
other worktrees, historical evidence and shared caches were preserved.
Native offline evidence is Chromium/macOS only. There is no cross-device offline
release qualification, live data conversion, production deployment, hosted CI pass,
whole-product gate or merge claim.

## #382 native essential acceptance preparation — 2026-10-25

**Harness fix and assembled acceptance are separate:** the UUID import repair
has a passing named regression and independent review. The ten native acceptance
scenarios remain **10 collected, 0 executed**; #382 is not accepted.
Final assembled execution awaits the parent-provided #380 composition receipt.
The reported #380 Learning-outage request timed out at 10 seconds with nginx 499;
explicit failure/no-fallback and same-artifact recovery remain unobserved. That
owner continues its diagnosis. This native section adds no Compose/browser proof,
role/outage/teardown repetition, or #382 completion claim.

Pinned base is merged PR410/main `311a712581deb95bf9035b2a3b934c03335a1cd5`.
Prepared executable commit is `594c561d797b1c41a77ab86d5fc87f02854062b9`, tree
`12263734cda741dfc94fc67fa09abac5aa32ec66`, on
`codex/382-native-acceptance` in `/tmp/tempo-382-native-20261025`.
Current main `782bf5374b87f223a023568305056415d5b63cea` was integrated in clean
merge `ea4d5193b26ec00e4027bae911315fb6eec027fa` while #380 remains pending.
`integrated-source-correspondence.json` verifies that the harness, named regression,
selector, runtime/database, imported fixtures and dependency/lock inputs are
byte-identical to their checked inputs at `594c561d`; the existing 1 regression
pass, 4 selector passes and ten-case collection remain applicable. No unit or
native execution was repeated for this source correspondence/documentation work.
Independent current-main review found no actionable issue and considers the tiny
UUID repair and named regression ready for scoped merge independently of #382
execution. No merge has been performed; assembled acceptance remains pending.

The current default `scripts/prove-study-learning.mjs` is explicitly pinned to
Git blob `d12f405b81289b09aa89f1ad5fdd63b792b73e17`, SHA256
`1bb15cd682cded8009f7026e0000755138c26bcd5eca211e9f1378681ef5200b`.
It is unchanged across prepared `594c561d`, current main and the integration.
The browser owner's future opt-in composed-ingress change will be assessed when
final; it does not block integration or scoped review of this harness fix. The
native owner has not modified that shared script.

#419 retired ten unused Games client/adapter files and modified four recorded
preparation inputs (two documentation files, Docker ignore rules and the Study
composition manifest). The original 744 assets and their manifest remain intact
for later exact comparison. They are not relabeled as a build of current main;
final assembled acceptance still needs corresponding production-build evidence.
The only executable repair imports `uuid` in the owning native UI harness.
The new regular owning-suite regression
`tests/test_study_ui_harness.py::test_native_study_harness_initializes_fixture_key_before_starting_servers`
executes its actual setup twice through distinct UUIDv4 fixture-key creation,
before servers or database work. Original source produced **1 failed** with
`NameError: name 'uuid' is not defined`; the repaired source produced **1 passed,
0 skipped**, 0.04 seconds, on Python 3.12.12 / pytest 9.1.1. Independent read-only
review found no actionable issue. No assertion, skip, timeout or browser protocol
was changed.

Evidence is retained outside the checkout at
`/tmp/tempo-382-native-evidence-20261025`. Exact preparation commands and receipts:

- `uv sync --project services/learning/runtime --locked --no-editable --python /Users/andy/.local/share/uv/python/cpython-3.12.12-macos-aarch64-none/bin/python3 --no-python-downloads --offline` installed cached dependencies into the task venv. Isolated installed-package discovery verifies **54 runtime / 36 database Python files** byte-match the checkout; `prepared-native.json` records every matching file. No fresh dependency/Python download or host install occurred.
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 services/learning/runtime/.venv/bin/python -m pytest -c services/learning/runtime/pyproject.toml -o addopts= -p no:cacheprovider services/learning/runtime/tests/test_study_ui_harness.py -v --tb=short`: **1 passed**. `python3 -m unittest discover -s database/tests -p test_study_essential_selection.py -v`: **4 passed**.
- `docker build --pull=false --target build -f Dockerfile.study-web --label tempo.owner=tempo-382-native-20261025 --label tempo.disposable=true -t tempo-382-native-build:20261025 .`: passed using cached production build layers. Official `node:22-slim` identity is `sha256:de170b39439eb0e5a3444ad7d60b015614a381e10fecae2e30ec9e735122f38d`, Node **22.23.4**. Resulting build-stage image is `sha256:607058f84741b5f22814a55ca591aa7668b19323bf4973a8fdf22bdc82285da2`; this is a build image, not an executed Study web service.
- `services/learning/runtime/.venv/bin/python /tmp/tempo-382-native-evidence-20261025/prepare-native.py`: **10 collected, 0 executed**, with no collection errors/deselections. It imports the merged `study_test_paths('essential')` selector and records the complete qualification child command in `prepared-native.json`. Node IDs are `integration/study_tests/test_study_ui.py::test_production_study_learning_native[SCENARIO]`, where `SCENARIO` is exactly `normal`, `teaching`, `lost-response`, `before-commit`, `next-load`, `two-tabs`, `offline-shell`, `wrong-credential`, `origin-transition`, `quota`.

The production assets were extracted from that image: **744 files**;
`study-assets.json` SHA256 is
`b54282ff61ff360cca3f93e32226343db0a5666d36f7e15eba33b3a668ae0999`.
`prepared-source-inputs.json` records 1,425 inputs, SHA256
`1710c87b596d8cce3a0e84304b19ac03ce27ead048d11ffb240715a1c4aad143`.
The cached PostgreSQL 18.6 image is
`sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336`;
no PostgreSQL container/volume has been created in this preparation.
Existing host Node **26.12.0**, locked Playwright **1.63.0**, chess.js **1.4.0**
and cached macOS Chromium 1243 (**153.0.8010.12**) are identified for the pending
native proof; `browser-toolchain.json` records the executable SHA256.
Only four JavaScript proof dependencies were extracted into the temporary
checkout; no host browser or software installation was performed.

`365-source-correspondence.json` verifies no changes from the #365 tested
`e6704daf08e39c19a3731fa6c3510f15f874374b` to the runtime/database executable,
dependency, lock and fixture inputs of its ordinary-introduction and frozen-rank
cases. Their prior results remain original-owner-reported historical evidence
in [Learning VALIDATION.md](../../services/learning/runtime/VALIDATION.md#preserved-pr-372-readiness-qualification--2026-10-22),
not fresh execution or current-image qualification. The original raw receipt is
no longer at its documented temporary path. No broader Learning suite was rerun.

After final source/build correspondence is accepted, `run-native.py` will use
the same merged selector and owned
PostgreSQL runner with `fixture_profile='essential'`, its exact extra-source
closure, and these already-built production assets. It requires the parent's
accepted #380 receipt, checks the pinned source/assets, then re-collects, executes
and reconciles all ten identities, all 30 setup/call/teardown phases and JUnit.
The parent must assess the receipt's contents and source correspondence; the
prepared path-existence guard does not itself establish #380 acceptance. Its
original source-manifest guard now intentionally rejects the integrated tree
until final build/input correspondence is resolved; do not bypass that guard.
Pending invocation, from the disposable checkout, is:

```sh
TEMPO_382_COMPOSITION_RECEIPT=/absolute/accepted/380-receipt.json \
  python3 /tmp/tempo-382-native-evidence-20261025/locked-run.py \
  services/learning/runtime/.venv/bin/python \
  /tmp/tempo-382-native-evidence-20261025/run-native.py
```

Install/build work opened the existing advisory lock in append mode, held it
exclusively through child completion, and preserved it. The asset-export/version
containers and task-owned build image tag were removed; no task container remains
(`preparation-cleanup.json`). Shared images/build cache were preserved.
Assets, venv, minimal proof dependencies and isolated
checkout remain task-owned pending the never-executed native acceptance; no
Learning/Study server is running. First failing reproduction, passing regression,
collection, source/install/asset manifests and independent review are retained.
`npm run check:conflicts` and `git diff --check` pass. Native execution/JUnit,
its PostgreSQL cleanup receipt, assembled browser acceptance and physical-phone/
live-data qualification remain **UNVERIFIED**. No live resource, history, host
setting, secret, deployment or CI was touched.
