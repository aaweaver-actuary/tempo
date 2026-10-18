# tempo-game-evidence-contracts

Artifact 3.0.0 exports unchanged `tempo_game_evidence_contracts.v1` and explicitly
versioned `tempo_game_evidence_contracts.v2` and `.v3`. V1 is additive; v2 adds required
nullable occurrence time, exact encounter/miss/publication/card-revision linkage,
versioned obligations and revision-targeted explicit withdrawals. Omission in a
partial batch never withdraws. Each v2 list is capped at 128 records and expected
alternatives at 20. A withdrawal-only delivery may have no encounters.
See [v2 protocol semantics](PROTOCOL-V2.md) and hand-authored `fixtures/v2`.

V3 carries typed observed legacy graph authority, full physical decision binding,
separate normalized/raw game hashes and known occurrence/capture time. It does
not assert Content publication or activation. It permits only explicit real-game
miss obligations or revision-targeted withdrawals. The shared observation linkage
validator retains v2 behavior. See [the receiver checkpoint](../../docs/analysis-restoration/legacy-game-study-receiver-checkpoint.md)
for authority, coexistence and unqualified installation/native gates.

From the checkout root, install only the contract test environment:

```sh
uv venv --python 3.14 contracts/.venv
uv pip install --python contracts/.venv/bin/python -r contracts/requirements-test.txt
uv pip install --python contracts/.venv/bin/python ./contracts/game-evidence
contracts/.venv/bin/python -m pytest contracts/game-evidence/tests
contracts/.venv/bin/python contracts/game-evidence/examples/producer.py
```

The tests import the installed distribution; do not set PYTHONPATH to `src`.
See [the catalog](../README.md), [compatibility](../COMPATIBILITY.md) and
[ownership](../OWNERSHIP.md). Hand-authored fixtures live in `fixtures/v1`.
Schemas are included in wheels beneath the import namespace's `schemas/v1` and
`schemas/v2` and `schemas/v3`. V2 Python producer/consumer fixtures are tested in the owning suite;
the existing TypeScript exchange continues to exercise v1.
Run `contracts/.venv/bin/python contracts/export_schemas.py --check` to verify
exports, or omit `--check` when deliberately updating authoritative schemas.
Examples illustrate contracts, not current production conformance or enforcement.
