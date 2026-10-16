# C — Source-backed findings / analysis-v1

## Ownership and checkpoint

Branch `codex/analysis-c-findings`; base `0edf566ad48f51846a43d3341809379ff0469ada`.
Task checkout: `/Users/andy/.codex/worktrees/analysis-c-findings/tempo`.
C owns `services/intelligence`, `apps/workbench-web`, and
`tools/analysis-restoration/c_pilot`, plus their component registrations.
Content/Game Evidence input export and proposal acceptance remain A-owned;
Stockfish execution, profile and public typed client remain B-owned.

Consumed today: Game Evidence local comparison schema 1, analysis-jobs artifact
1.0.0 / Stockfish wire 1, Intelligence source evidence local schema 1.
A's analysis-export/proposal artifact and B's installed profile/client are pending;
fixture compatibility is not actual producer acceptance. These bindings will be
pinned here before C2 qualification. Shared strict v1 contracts are unchanged.

## New component-local interface

`/v1/analysis/bootstrap`; input manifest/page/finalize; run create/list/status;
paged generation-bound reports; finding detail; durable run/finding commands;
command receipts; informational/executable proposal export. These are NEW.
`tempo-intelligence analysis` supplies the same manual operations. Reads never
request providers, import games or execute an engine. Each explicit run defaults
to 250 learner decisions and retains the remaining positions durably.

Inputs retain workspace, producer/schema, immutable identity/revision, page hashes,
source/publication/actual activation lineage, policy/color, source games and exact
positions, original comparison, positive coverage context and provenance. Missing
activation/policy/completeness is unknown coverage, never a gap. Input identity
reuse with different bytes/semantic digest conflicts. A source correction is a new
input/run revision. Accepted reports remain usable with A/B stopped.

A valid miss consumer uses the actual comparison `status:missed`, genuine card and
decision IDs, actual move and all expected alternatives. A gap consumer needs an
established route before the opponent move plus complete uncovered response scope;
comparison `unknown` alone cannot establish it. An engine consumer compares actual
same-root unrestricted and played-move results at compatible completed depth.

Partial example: accepted source pages contain one unavailable comparison; report
includes its reason and cannot call that game covered. Stale example: newer run
input pending while the prior complete generation remains readable and labeled
stale. Invalid example: workspace or page digest mismatch is rejected. Missing A
export is unavailable input, not an empty successful report.

## Policy and effects

Policy `c-analysis-v1`: 100cp significance, completed equal depth >=18; request
max depth20 / wall10000ms, one execution. Mate stays typed. Actual games, Explorer
counts and Maia model probabilities remain separate. Proactive starting threshold
is 0.05; Explorer requires >=200 source games. An observed supported gap needs
only one actual game. Ranking is distinct-game support, selected source frequency,
supported significance, known proposal size, stable identity. Unknown mass stays
bounded/unknown. No fused/calibrated probability or learning-gain claim.

Finding evidence revision, report generation, dismissal and training/activation
state are separate. Dismissal survives replay/enrichment; three additional distinct
supporting games reopen it. Existing delivery acceptance is not study settlement.
Export is a draft only: Content owns approval/publication and Learning owns
activation/admission. Intelligence never produces remediation obligations.

## Milestones

C1 report-only, C2 actual A+B artifacts, C3 explicitly scoped real data are separate
claims. C3 uses only A's authorized export. No live data, recurring import,
deployment, merge or Actions enablement is authorized. Candidate-specific checks,
artifact identities, limitations and retained evidence will be recorded below.
