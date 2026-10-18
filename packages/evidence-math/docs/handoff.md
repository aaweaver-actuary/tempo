# Serialized integration requests

This package is independent and is not adopted by production. Do not change legacy
services to make a component check pass. Existing modules remain intact.

C3 owns components.json: request evidence-math status implemented, root
packages/evidence-math, artifact tempo-evidence-math 0.1.0 (wheel+sdist), Python
>=3.12,<3.15, qualification 3.12/3.14, dependencies chess/pydantic/pydantic_core;
publicExports exactly src/tempo_evidence_math/__init__.py __all__. Build command:
python -m build --no-isolation. Build inputs: src, tests, pyproject.toml,
requirements-dev.in, requirements-dev.lock, README.md, REGRESSIONS.md, docs,
qualify.py, qualification. Local test inventory REGRESSIONS.md; test owner
evidence-math, kind python, cwd packages/evidence-math, command
python qualify.py suite --report /absolute/task-owned/evidence-math.json
(the owner must assign an actual task-owned report location, not a shared one).

Study-priority owns resource controls/test discovery: register the local suite
through supported interfaces; root REGRESSIONS may link this inventory. Do not
copy every case into shared files or edit shared runners. Main d0485d9f has no
released devbudget interface; refresh before each later invocation. A denied budget
blocks execution. Qualifier is serial, wall/CPU bounded; macOS hard memory limits
are explicitly unverified. Source-removed proofs install from local dependency
artifacts; local cache-derived dependency wheels are RECORD-verified and their
hashes/provenance are recorded separately from upstream lock hashes.

Learning/scheduling must publish a supported fixed-time projection/export contract
before any legacy readiness adoption. Existing readiness imports an absent backend
_scheduler; reaching into tempo_scheduling._scheduler is not a remedy. Estimator
recurrence/state decoder assertions stay in the legacy regular suite; local math
tests use explicit immutable projections. See adapter-test-ownership.md.

Later compatibility edits, only after owner release: backend opening_preparedness,
opening_policy_reach, opening_move_probability_contracts forward public names;
decision_readiness translates owned projections; opening_card_valuation composes
new math with the installed evaluator; opening_ranking_evaluation forwards DTOs and
ranking helpers. scripts/evaluate_opening_rankings dispatches offline commands to
the installed tool and retains capture externally. Backend metadata/locks/image
inputs and affected adapter tests are serialized owner changes. No such edits are
included here. Segmentation recommendations, legacy coverage policy, eligibility,
FSRS mutation, queue/admission, persistence and frontend stay outside this lane.

No global study-weight aggregation exists in the inspected source. Request explicit
issue-owner acknowledgment that #156 extraction acceptance covers present kernels;
future allocation and weight-scaling acceptance belongs to M06, not invented math.
