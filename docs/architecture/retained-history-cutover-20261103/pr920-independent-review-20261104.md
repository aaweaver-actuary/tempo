# S04 independent adversarial review

Reviewed baseline `967289c3e9463b5bf6bdd631bb30d18c46df19c4` through final executable candidate `acfb14be880c8fe5f5ae46a53e557cf57088eda0`. Reviewer: S04, independent of implementation. Read-only source review; no live operations, code edits, deployment or activation.

**Bounded source review: PASS; no unresolved P0/P1. Programme cutover: NO-GO. Full installed qualifier: BLOCK.**

Reviewed complete changed implementation/test diff, issue #908 DF-01–05/F01–14, repository guidance, relevant SPEC and readiness handoff. Challenged donor-nullability/provenance, immutable warm/final collision, ingress exposure, transaction lock ordering, original receipt replay, checkpoint atomicity, local-day credit, unsupported Games effects and disposable fence limits. Original 0004/0005 migrations remain byte-unchanged; 0006/0007 are additive and registered in dependency order. Fresh-target strategy does not falsely claim generation labels isolate existing staging tables. Guard remains unselected and cannot authenticate claimed evidence.

## Findings and resolution

1. **P1 — original response could change during accepted-effect transaction (resolved).** `database/tempo_learning_database/adapters/position_effect_store.py:100` previously read response without a lock before waiting on authority/state; the FK only bound identity. Deterministic counterexample: B locks authority; A reads response then blocks on B; C changes response accepted_at to tomorrow and commits; B releases; A commits Good at today's acceptance against invalid current evidence. Payload/digest rewrites similarly invalidate original replay. Acceptance requirement: effect, receipt and checkpoint consume frozen original response bytes/clock, retained after commit. Candidate locks response FOR SHARE before source/state waits and 0007 lines85–92 reject response UPDATE/DELETE. Named regression `test_response_is_locked_during_source_wait_and_append_only_after_commit` observes actual PostgreSQL blocking edges, original clock, immutable postcommit evidence and exact replay. Baseline new regression failed; repaired native test passed. No safety assertion was weakened; corrupt evidence is now inserted malformed rather than rewritten through an immutable boundary.

2. **P1 — fence prototype failed before admission denial (resolved; independently found by T07).** Original `pg_shdescription` call was not a PostgreSQL function. `database/tempo_learning_database/tooling/disposable_writer_fence.py:50,62` now uses `shobj_description`. T07 native regression exercises actual old-session termination/rollback, reconnect refusal, retained committed data, allocation refusal and administrative reopen. This proves only owned disposable admission/session behavior.

## Evidence inspected

- Final combined receipt `/private/tmp/tempo-pr920-integrated-native-final-20261104/native.json`: exact candidate SHA above, **113 passed**, complete=true, container_absent=true, volume_absent=true, temporary_test_data_removed=true. Execution `run-db884ebe667f1598`; identity `tempo-learning-a53260446356cdd9`; PostgreSQL `sha256:3a4264520e121176619f54ff867d12774bc81e95e7b84db58d63bccdb6144b81`. runtime_image_required=false. Selected freshly installed noneditable migration/import checks are included; this is not a runtime container build.
- S02 before/fixed receipts inspected: original race failure then repaired14-pass worker run; source-input evidence predates worker commit and is superseded for whole-candidate execution by final combined receipt.
- Coordinator reports Node guard9 passed and qualification-input2 passed. Reviewer inspected their changed source; did not independently rerun them.
- `git diff --check` passed. Final comparator-only delta explicitly adds backend source within its existing pure legacy comparison test; assertions remain intact and native invocation fingerprints that comparison source.

## Remaining limits and next owner

Full installed qualifier `/private/tmp/tempo-pr920-integrated-installed-20261104` remains BLOCK: **64 diagnostics**, comprising baseline63 plus candidate `services/learning/runtime/src/tempo_learning_runtime/position_effect_owner.py:6` importing the same bundled database namespace. Do not describe candidate as unchanged63 or claim full qualification. Build/qualification owner must resolve declaration/checker ownership separately; this review does not waive it. Full wheel/sdist artifact matrix and runtime container build were not run.

Real Content/currentness and authoritative completion provenance, retained-stage adoption, serving review/issuance, accepted Games debt, complete Tactics/browser replay, saved-review→playable-next, final fenced capture/watermark, backup/restore and complete F01–F14 remain BLOCK/NOT RUN. Prototype completion and authority rows are synthetic. T07 reconnect labels use psycopg probes, not actual legacy worker/CLI executables; privileged-login denial, prepared transactions, bounded-drain failure, host/SQLite controls and full operational F13 are unproved. Administrative reopen is an explicit residual bypass, not solved by allocation comments.

Stop condition met for independent bounded source/native review. Delivery coordinator owns truthful handoff and merge qualification; downstream semantic/release owners retain serving and cutover gates. Implemented/component-tested does not imply assembled-demonstrated, merge-qualified, installed or activated. No installed authority change is approved by this review.
