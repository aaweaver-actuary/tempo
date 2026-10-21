# Legacy bridge native database proof

**Current source; native execution remains separately controlled and unverified for this move.** [test_native.py](test_native.py) and [conftest.py](conftest.py) are the same six existing legacy backend boundary cases and their owned fixture, moved from `backend/integration/legacy_bridge`. Their SQL, assertions, backend processes, transaction/lock checks, fixture scope and cleanup body are unchanged. The sole fixture predicate adjustment recognizes the exact existing read-only `/source/database` import path used by the controlled qualifier.

The canonical launcher and finite resource/receipt rules live in [legacy bridge qualification tooling](../../tooling/legacy_bridge/README.md). The qualifier now collects these repository-root-relative node paths using an explicit repository root; registered test names and the `[prior]` parameter identity are unchanged. Do not execute this directory through regular unit-test discovery.

The cases cover accepted evidence/replay/restart and normal Study, a prior issued attempt, receipt/effect rollback, typed feedback projections borrowing the caller transaction, card/day lock lifetime and accepted-scope exclusion. Existing shared-storage integration is being proved; these tests do not establish independent deployment/resource ownership. The fixture still rejects ambient targets, requires the exact owned backend manifest and preserves bounded subprocess/connection cleanup. No fixture, service, native SQL or live schema ran during relocation.

Stored data remain described by the [legacy source schema dictionary](../../README.md) and [remediation database README](../../../tempo_remediation_database/README.md). Native SQL relationships and data live in those existing owners; this move creates no schema, canonical lines/positions/history model, grant or new cross-owner access.

Focused source evidence: the existing pure plan/guard suites pass; guarded collection discovers these exact six identities without fixture execution. Installed-artifact/native runtime claims retain their own separately authorized qualification; historical receipts keep their historical node paths.
