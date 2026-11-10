# Study review and remediation libraries

The serving backend retains two small application libraries:

- [`application`](application/README.md) applies reviews through injected capabilities
  within the caller's transaction.
- [`remediation`](remediation/README.md) manages accepted remediation obligations and
  their receipts through the existing database adapter.

The independent Learning HTTP/CLI runtime, separate Learning database, transfer
prototype and publication stack are retired. Current queue, grading, scheduling
and opening-evidence helpers belong to
[`backend/app/study/policy`](../../backend/app/study/policy/README.md).
This directory no longer defines an independent service or deployment.
