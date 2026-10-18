# Tempo evidence mathematics

An independently installed pure library for selected-policy reach, explicit opponent
uncertainty, readiness evidence rules and independent hypothetical card effects.
It does not capture data, estimate FSRS state, schedule, admit, allocate or write reports.

Python >=3.12,<3.15. Public imports are from `tempo_evidence_math`; arithmetic imports
need only the standard library. Chess/Pydantic are loaded on explicit heavy API access.
See `docs/contracts.md` for versions, ownership, examples and limitations.

Development: install only `requirements-dev.lock` into a task-owned environment,
install a noneditable wheel, then run `python qualify.py suite --report /absolute/report.json`.
Artifact qualification: `python qualify.py artifacts --python /path/to/python3.12
--python /path/to/python3.14 --wheelhouse /absolute/wheelhouse --report /absolute/report.json
--artifacts /absolute/artifacts`. Network is denied during artifact qualification.
No backend source, PYTHONPATH, root fixtures or repository conftest is needed.
