# Legacy database tooling

**Current source; live schema and deployment unverified.** [Database owner](../README.md).

- [Legacy bridge proof/resource helpers](legacy_bridge/README.md) retain one finite qualification/resource lifetime.
- [Learning review native runner](learning_review/README.md) retains its existing source-checkout fixture and guard policy.
- [Storage read benchmark](benchmark_storage_reads.py) is the existing in-process SQLite/PostgreSQL API read rehearsal; all function bodies,CLI arguments and credential-file handling are unchanged. Its source root/bootstrap follows its canonical location.
- [Writer benchmark entry point](../../tests/benchmarks/README.md) owns the existing disposable SQLite contention rehearsal and npm shortcut.

The relocation ran only source/AST/path checks,Python help and JavaScript syntax. No benchmark,native runner,restore,credential-file read,application startup or live database action was executed. These tools add no schema or new runtime connection authority.
