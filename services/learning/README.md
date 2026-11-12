# Study review and remediation libraries

The serving backend retains a small application library:

- [`application`](application/README.md) applies reviews through injected capabilities
  within the caller's transaction.

The independent Learning HTTP/CLI runtime, separate Learning database, transfer
prototype and publication stack are retired. Current queue, grading, scheduling
and opening-evidence helpers belong to
[`backend/app/study/policy`](../../backend/app/study/policy/README.md).
This directory no longer defines an independent service or deployment.

The independent remediation library is also retired. Historical migration SQL
is retained under [storage DDL](../../database/tempo_storage/ddl/README.md);
retained receipts/history do not require reviving its package.
