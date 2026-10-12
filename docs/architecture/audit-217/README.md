# Audit #217 reproduction

Production baseline: `02b65f229aaccc91ae7c03fd11efc8f3aedd2883`.

- Read [REPORT.md](REPORT.md), [reverse imports](reverse-imports.md), and [writer inventory](state-writers.md).
- `python3 docs/architecture/audit-217/analyze.py /tmp/tempo-audit217-inventory` rebuilds the syntax inventories without importing Tempo. Decompress `inventory.json.gz` to inspect the preserved full JSON. The analyzer rejects changed production inputs relative to the pinned baseline.
- Python characterization uses the exact eight-file command in the report. Install selected dependencies from `backend` with `uv sync`, then `uv pip install --python .venv/bin/python -r requirements.txt`; return to repository root before execution.
- With Docker access, run `python3 docs/architecture/audit-217/native-checks.py /absolute/external/evidence-directory`. It creates a fresh project and random credentials, uses unchanged Compose services and native assertions, and follows regular runner background-consumer isolation. It rejects changed production inputs. Never provide live services or existing data.
- Native proof subprocesses do not contain source modifications. The harness adds only project setup, existing proof selection and teardown. This is characterization, not a replacement for CI/release qualification.
- All successful and failed setup evidence is in [evidence](evidence). Source claims link to production main; audit tooling/evidence are preserved on the separate audit ref. `evidence/handoff.json` records final preservation/cleanup state.

Uncompressed inventories and dated diagnostics are also retained under the root checkout's `test-results/issue-217-architecture-audit-2026-10-12` directory. Audit checkout removal does not remove that evidence.
