# Position-owned Study — v2 expected-interface supplement

**Preparation / reference code, not production code.** This was added to the docs-only [draft PR #881](https://github.com/aaweaver-actuary/tempo/pull/881) for [coordinator #832](https://github.com/aaweaver-actuary/tempo/issues/832). It changes no product imports, database migrations, FSRS states, or serving paths.

- [Download complete v2 ZIP](./tempo-position-study-reference-v2.zip) — 40 reference code, fixtures, tests and documentation files.
- Open `recipes/API_CONTRACT_FREEZE_V2.md` inside the ZIP first for exact call signatures, ownership, proposed typed ports, borrowed-transaction handoffs, and negative acceptance cases.
- Open `recipes/CHEAP_WORKER_CONTRACTS_V2.md` for lower-cost agent input/output/test/consumer assignments.
- `reference/selection.py:select_and_freeze(SelectionRequest)` demonstrates pure Study due-witness + existing Repertoire certified route + saved-clean-prefix composition.
- `wire/issued_plan.v1.schema.json` and `wire/examples/italian_{full,suffix}.json` demonstrate a single strict browser wire contract. They deliberately do not embed FSRS or source authority.
- The new tests require a **same-day, same-owner-epoch, same-root** clean prefix before skipping and hold if the target was already cleanly tested that day.

## Executed local verification

```sh
cd tempo-position-study-reference-v2
python -m pytest -q tests             # 38 passed
node --test browser/*.test.mjs         # 9 passed
python -m compileall -q reference      # passed
```

ZIP SHA-256: `982c161a50aff977e43f2b4cf0e398ed946dad578769960177b5ece28eecf4cb` (59,974 bytes).

**Not executed:** native python-chess path validation, FSRS 6.3.2 decoding and unique-ID allocation, live or disposable PostgreSQL locks/transactions, Tempo runtime/browser integration, actual installed source/currentness and offline replay. Synthetic route inputs assume external Repertoire certification. The JSON display wire uses illustrative IDs and is not a server-signed authorization. A distinct independent reviewer still owns release qualification; the reference ZIP is not a migration.

Read the original [v1 README](./README.md), [A1 selected boundary](https://github.com/aaweaver-actuary/tempo/issues/847#issuecomment-6578135210), and [P-11 source/certification finding](https://github.com/aaweaver-actuary/tempo/issues/832#issuecomment-6580113746) for existing source owners and strict anti-coupling guardrails.
