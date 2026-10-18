# Selective donor reuse and serialized handoff

Donor #177 head c3bccdf9fd8bdb090e921af7fd54247b0a615c68, historical base
964b2a3a2ede4d9ac92288bb98a9ccba831d6b62, closed unmerged. Historical pass counts are
not current evidence. No cherry-pick, merge or legacy source change is included.

| Donor artifact | Treatment | Owning replacement proof |
| --- | --- | --- |
| backend/app/services/opening_ranking_harness.py | Adapt to harness.py with public producer DTOs, frozen scorer lookup, full pre-ranking validation | Local test_harness and test_cli |
| backend/tests/test_opening_ranking_harness.py | Preserve pure assertions and named fixes; adapt installed paths and valid pure dependencies | Installed full suite, negative cases, seed/timezone replay |
| scripts/evaluate_opening_rankings.py corpus additions | Reimplement installed console command; exclude sys.path/backend injection and capture | Installed CLI tests |
| manifest and six scenario JSONs | Preserve bytes and original digests as packaged corpus | Full legal/mass/identity/coverage content tests |
| build_corpus.py | Exclude entirely from package/runtime/default tests | External owner-only rebuild using actual historical planner |
| test_harness_checked_snapshots_rebuild_with_real_production_planner | Remains external adapter qualification, not local selected evidence | Explicit actual-planner proof with named source revision when authorized |
| docs/opening-shadow-ranking.md additions | Adapt contracts/limits/limitations locally | CLI/example and hand-calculation tests |
| tests/REGRESSIONS.md additions | Map names into local REGRESSIONS.md | Local discovery/registration validation |

All named fixture-parent, unassigned-authored-reply, oversized-score, scenario-ID and
pre-ranking amplification regressions remain selected. Missing/foreign/renamed IDs,
excess bundles/K and single/aggregate metadata expansion are covered without weakening
the donor limits. The old production baseline is frozen evidence, not a current
planner qualification claim. Rebuilds belong to external tooling and create explicitly
versioned new fixtures rather than replacing historical provenance.

Current d0485d9f ranking tests are split by behavior: pure ranking/snapshot assertions
are local; capture, pool/Redis/activity, deployment-calendar and recovery checks remain
with their legacy adapters. The producer owns calculation/evidence rules; this tool
owns rank/compare/replay/corpus. No backend test import order or sibling test module
is used. Test support is local and math input fixture serialization uses the frozen
public producer API. Python/TypeScript source contracts are not changed in legacy
consumers, so no unrelated frontend or live-service suite is claimed.

C3 owns components.json. Request a ranking-evaluation tool row rooted at
tools/ranking-evaluation, artifact tempo-ranking-evaluation 0.1.0 wheel+sdist,
dependency tempo_evidence_math; public exports exactly __all__; Python >=3.12,<3.15,
qualification 3.12/3.14. Inputs are src, tests, pyproject.toml, requirements-dev.in,
requirements-dev.lock, README.md, REGRESSIONS.md, docs, qualify.py, qualification.
Build: python -m build --no-isolation. Test owner ranking-evaluation, kind python,
cwd tools/ranking-evaluation, suite via local qualify.py with task-owned report output.
Study-priority owns test inventory/resource integration and any shared tool-builder
support. Current restricted-build rejects tool components; do not misdeclare this
as a library or change that runner here. Local artifact proof is independent.

Optional later compatibility files: scripts/evaluate_opening_rankings.py forwards
offline commands to installed cli while capture stays external;
backend/app/services/opening_ranking_evaluation.py forwards DTO/ranking APIs;
opening_card_valuation.py composes v2 calculation with reporting. Shared manifest,
backend dependency/lock/image files and root registration changes are small serialized
owner handoffs after local proofs. No production adoption, persistence, FSRS, queue,
admission, frontend or publication integration is included.
