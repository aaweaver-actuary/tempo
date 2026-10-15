# Learning runtime regression ownership

The complete native inventory is discovered by `integration/run_postgres.py` from `tests/`; collection, every setup/call/teardown phase and JUnit must agree. No selection flags or skips are accepted. `components.json` registers the owning native suite and its real client/runner inputs. Installation checks also run in source-removed Python consumers through `qualify.py`.

| Regular file | Proof |
| --- | --- |
| `test_runtime.py` | Load/issue/review, immutable logical and transport replay, scope/origin/role denial, authoritative concurrent first enqueue |
| `test_transactions.py` | Opening evidence atomicity; handler and handled-reconciliation rollback; competing payloads; bounded current-card contention; nonblocking optional successor; stale/deleted/retired definitions; retained quota; finite-contention maintenance; local-day boundary |
| `test_families.py` | All five typed specifications, practice/review/self-assessment/feedback; tactical discovery and binding; defense recognition, covered grading and uncovered durable intent |
| `test_eligibility.py` | Parent exposure, integrity fences, competing issuance, middlegame review, guided typed attempts/sibling burial, local evidence promotion and required settlement |
| `test_endgames.py` | Offline cache-first probe, deterministic attempt and immutable retry; configured HTTP-provider protocol success/cache/failure separately; full-FEN cache isolation in both halfmove-clock orders and historical-key exclusion |
| `test_publications.py` | Immutable staging, completeness and latest-generation recheck; accepted frozen ranks; migration/workspace/privileged-role readiness; unique route positions and memberships; every-line learner-turn validation for both colors; exact multi-membership graph completeness and rollback; competing owners, retirement/replacement locking (including removed memberships), independent repertoires and retryable owner contention |
| `test_evidence_revisions.py` | Monotonic revisions, original transport replay, semantic retries without re-promoting settled misses, conflicting content, empty corrections, transaction-wide rollback, both concurrent revision orders, installed HTTP process restart, historical receipt migration and restricted-role access, custom receiver authority; actual PostgreSQL cross-evidence obligation/delivery identity races and loser rollback |
| `test_enqueue.py` | Barrier-controlled creator rollback with waiting insertion; existing leased task replacement; authoritative generations/event identities |
| `test_restart.py` | Installed HTTP process restart, response loss, durable intent resumption and task-owned database restart |
| `test_browser.py` | Real Chromium with existing strict schemas/outbox; progression and lost-response reload recovery against the real Learning API |
| `test_container.py` | Final restricted-user image, actual PostgreSQL load/issue/review/replay and absence of backend/broker/model source |
| `test_installation.py` | Installed closure, packaged append-only migration dependency/resources, forbidden optional imports, transaction owner and denied-auth startup without infrastructure |

Legacy compatibility adapters retain the regular scheduling application/package/boundary and defensive grading/validation/detection suites. Scheduling ownership metadata records the packaged owner. The retained enqueue primitive changes its returned row/event identity to PostgreSQL's `RETURNING` result; `backend/tests/test_durable_task_enqueue_identity.py` exercises the retained consumer's event/result binding, while native concurrency evidence lives in Learning's regular suite rather than a parallel harness. Shared runner normalization has a named regression in the existing component-build boundary suite.
