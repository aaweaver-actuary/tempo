# Content native database tooling

These are the existing Content-owned disposable PostgreSQL tools, physically
moved from `services/content/integration`. Their native implementation and
resource/transaction authority are unchanged. They are source-checkout tools
included in Content's independent sdist, excluded from its production wheel.

| File | Existing responsibility |
| --- | --- |
| [run_postgres.py](run_postgres.py) | Own the fresh disposable cluster, migration setup, exact native collection/execution inventory, optional benchmark and resource cleanup. Caller database URLs are never accepted. |
| [benchmark.py](benchmark.py) | Generate the existing representative fixture and measure native registration, reconciliation, publication, restart/replay, transaction durations and row counts. |

Run from the repository root with Content's prepared installed environment:

```sh
services/content/.venv/bin/python database/tempo_content_database/tooling/run_postgres.py EVIDENCE
```

The optional `--benchmark` behavior is unchanged. The runner resolves the Content
component's [run_suite.py](../../../services/content/run_suite.py), executes the
[native suite](../tests/README.md) and invokes the benchmark by their new exact
paths. Its source inventory now includes this root database owner as well as
the existing Content/dependency inputs. The independent sdist retains the tools
and resolves `run_suite.py` at its component root; it does not bundle the sibling
dependency source checkouts required by the full repository proof workflow.

Pure application tests and source-free consumer qualification remain under
[services/content](../../../services/content/README.md). This move does not
change grants, credentials, schema, SQL, resource limits, ownership labels,
cleanup safeguards, test assertions or benchmark measurements. Existing native
results remain historical evidence at their recorded revisions and old paths;
the relocation itself was checked offline without running these tools.
