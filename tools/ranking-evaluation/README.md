# Tempo offline ranking evaluation

Install tempo-ranking-evaluation noneditably with its declared evidence-math artifact.
Run `tempo-evaluate-opening-rankings corpus` for the packaged six-family historical
synthetic corpus; `compare --snapshot FILE` replays scores, while `score --snapshot FILE`
calculates a versioned mathematical input snapshot before comparison. None captures
live state. `--output FILE` exclusively creates a bounded canonical report.

Python >=3.12,<3.15. No backend, PYTHONPATH, source-tree injection, database, network,
model, scheduler mutation or admission dependency is present. See docs/contracts.md
and docs/donor-map.md for schemas, limits, provenance and excluded adapter work.

Run the component-local `python qualify.py suite --report /absolute/report.json` in
an environment containing the installed wheel and locked development dependencies.
The `artifacts` mode has the same interface as evidence-math and proves wheel and
sdist-derived-wheel installations on Python 3.12/3.14 outside the source tree.
