# Content native database tests

[test_postgres.py](test_postgres.py) is the unchanged native suite moved from
`services/content/integration/test_postgres.py`. Its logical regression owner is
still [Content](../../../services/content/REGRESSIONS.md). All 67 test functions
require the runner-owned disposable native service; parametrized functions
expand into additional cases. The 12 fixture/helpers and all assertions remain
in the same file and order.

The suite owns actual persistence/restart, migration/readiness compatibility,
reservation/CAS, source staging and manifest integrity, fencing/leases,
lock ordering, immutable publication/replay, rollback, bounded collection and
consistent inventory reads. Some cases use only public Content APIs or installed
CLI subprocesses; their purpose remains the native persistence contract. They
are not copied production application logic or substitute pure-policy tests.

Use only the [owned native runner](../tooling/README.md) for this suite. Its
disposable-resource guards and test environment are unchanged. The suite is
included in the independent Content sdist and excluded from the production
wheel. Pure application and source-free consumer tests remain under
`services/content`; their existing [regression inventory](../../../services/content/REGRESSIONS.md)
links here for native proof.

This file relocation was checked by exact source comparison and offline
packaging checks. Native cases were not executed for this move; earlier native
receipts retain their original revision/path and do not establish current
native qualification.
