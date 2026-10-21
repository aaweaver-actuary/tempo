# Content storage-only pure unit tests

These are existing Content-owned assertions physically placed beside their
concrete storage implementation. They use synthetic facts, fake transactions
and mocked readiness connection contexts. They open no database or service.
No production policy, SQL, schema, fixture result or assertion changed.

| Suite | Existing responsibility |
| --- | --- |
| [test_inventory_cursors.py](test_inventory_cursors.py) | Byte-identical moved reader-token integrity suite: canonical encoding, required fields/types, reader/workspace/source/revision/generation identity and route-pair validation. Its helper named `cursor` constructs a JSON token, not a database driver cursor. |
| [test_storage_guards.py](test_storage_guards.py) | Four moved test functions and their parametrization: binding target read/short circuit; atomic binding fence/insert/advance/hook; fenced failure write/error serialization; owner readiness connection context. The binding tests use only their original four immutable fixture facts; application tests retain their original shared fixture unchanged. |

The regular [Content runner](../../../../services/content/run_suite.py) selects
`services/content/tests` **and this exact unit directory**. It resolves the root
owner in a checkout and the local bundled database owner in a standalone sdist.
The existing [artifact qualifier](../../../../services/content/integration/qualify_artifacts.py)
copies only this unit subtree as test input to source-free consumers and selects
it explicitly alongside application tests. It does not copy native tests there.
The existing Hatch hook includes database tests in the sdist and excludes the
entire database test/tooling trees from the production wheel.

For a focused run from the repository root with Content installed:

```sh
services/content/.venv/bin/python -m pytest database/tempo_content_database/tests/unit -q --rootdir=.
```

Do not point a pure suite at `database/tempo_content_database/tests/`: its
[test_postgres.py](../test_postgres.py) requires the separate
[native runner](../../tooling/README.md). Native locking/rollback, live schema and
service behavior are not established by these pure tests. Application decisions,
semantic callback fakes and phase/computation boundary tests remain with
[Content application tests](../../../../services/content/REGRESSIONS.md).

This extra storage-test placement is outside the original 156 mixed-file
checklist. Source comparison preserves all moved decorators/assertions and all
retained application test/helper bodies. The 19 production Python files remain
unchanged. Historical [validation receipts](../../../../services/content/VALIDATION.md)
retain their original source locations and selections.
