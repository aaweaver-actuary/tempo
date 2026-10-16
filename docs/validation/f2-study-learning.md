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
and cleanup are recorded below after execution. Native browser/PostgreSQL evidence,
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
