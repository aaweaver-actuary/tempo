# tempo-analysis-jobs-contracts

Artifact 1.0.0, explicit wire version 1. Public imports: `tempo_analysis_jobs_contracts.v1`;
analysis jobs also exposes `stockfish` and `maia` modules.

From the checkout root, install only the contract test environment:

```sh
uv venv --python 3.14 contracts/.venv
uv pip install --python contracts/.venv/bin/python -r contracts/requirements-test.txt
uv pip install --python contracts/.venv/bin/python ./contracts/analysis-jobs
contracts/.venv/bin/python -m pytest contracts/analysis-jobs/tests
contracts/.venv/bin/python contracts/analysis-jobs/examples/producer.py
```

The tests import the installed distribution; do not set PYTHONPATH to `src`.
See [the catalog](../README.md), [compatibility](../COMPATIBILITY.md) and
[ownership](../OWNERSHIP.md). Hand-authored fixtures live in `fixtures/v1`.
Schemas are included in wheels beneath the import namespace's `schemas/v1`.
Run `contracts/.venv/bin/python contracts/export_schemas.py --check` to verify
exports, or omit `--check` when deliberately updating authoritative schemas.
Examples illustrate contracts, not current production conformance or enforcement.
