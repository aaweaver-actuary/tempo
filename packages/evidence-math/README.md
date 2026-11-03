# Tempo evidence mathematics

An independently installed pure library for selected-policy reach, explicit opponent
uncertainty, readiness evidence rules and independent hypothetical card effects.
It does not capture data, estimate FSRS state, schedule, admit, allocate or write reports.

Python >=3.12,<3.15. Public imports are from `tempo_evidence_math`; arithmetic imports
need only the standard library. Chess/Pydantic are loaded on explicit heavy API access.
See `docs/contracts.md` for versions, ownership, examples and limitations.

`tempo_evidence_math.position_learning` exports immutable position-owned Study
facts. These detached values do not attest source authority or perform scheduling;
Study owns issuance, grading and borrowed transaction ports.
Source `RouteOption` values allow up to 1000 plies / 500 learner decision
occurrences; Study's issued presentations retain their 40-move / 20-decision bound.

Development: install only `requirements-dev.lock` into a task-owned environment,
install a noneditable wheel, then run `python qualify.py suite --report /absolute/report.json`.
Artifact qualification: `python qualify.py artifacts --python /path/to/python3.12
--python /path/to/python3.14 --wheelhouse /absolute/wheelhouse --report /absolute/report.json
--artifacts /absolute/artifacts`. Network is denied during artifact qualification.
No backend source, PYTHONPATH, root fixtures or repository conftest is needed.
