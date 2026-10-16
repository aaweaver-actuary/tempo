# C — Source-backed findings / analysis-v1

## Ownership and checkpoint

Branch `codex/analysis-c-findings`; base `0edf566ad48f51846a43d3341809379ff0469ada`.
Task checkout: `/Users/andy/.codex/worktrees/analysis-c-findings/tempo`.
C owns `services/intelligence`, `apps/workbench-web`, and
`tools/analysis-restoration/c_pilot`, plus their component registrations.
Content/Game Evidence input export and proposal acceptance remain A-owned;
Stockfish execution, profile and public typed client remain B-owned.

At the interface checkpoint C consumed Game Evidence local comparison schema 1, analysis-jobs artifact
1.0.0 / Stockfish wire 1, Intelligence source evidence local schema 1.
A's analysis-export/proposal artifact and B's installed profile/client were pending;
fixture compatibility is not actual producer acceptance. The implemented handoff below pins their accepted artifacts. Shared strict v1 contracts are unchanged.

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

## Implemented handoff — 2026-10-16

Implementation commit: `4dcda0bfc1dc25ccb32b8d944449f6fc277034fb`;
qualification tooling: `906672b8a4d0134527721bb1b5fa3d75759c3c3a`.
The early interface checkpoint is `35d864bb33359bc9b44668cbe18c8f506c24daa6`,
published to #4/#266 before implementation. Later commits contain only the final
actual-delivery assertion, display wording, handoff and evidence index.

The initial “pending” producer statements above describe that checkpoint. Actual
accepted versions are now pinned below. No shared strict v1 contract changed.
C changed only its Intelligence/Workbench/pilot component entries and two scoped
root script keys. A/B entries and all shared suite registrations are preserved.

### Accepted producer artifacts

- A's installed synthetic delivery export, published at
  `4d132d0d984b36cd027f967cd3ecb11a1fe27ae0`, uses
  `tempo-analysis-inputs` schema 1, input
  `22de0c7c92fcecc176d4fe7acd956aae66fd77ddeba86db4a0e4ea3b738b373e`,
  revision 1, manifest digest
  `924b65aa373b5305816daf5a2ddfc2fcf26640f03a15259a34048d03033c267b`.
  Four explicitly synthetic games are the whole selected scope; this is not
  Andy's repertoire or account history. Every original page and its byte hashes
  is preserved. The earlier `ec2f522c… / 78adc89e…` export remains a separate
  accepted example with explicitly unknown, unexported delivery state.
- A now exports the requested positive pre-deviation route/policy, exact resulting
  decision/history, complete native response scopes, authorized re-entry facts,
  and covered as well as uncovered opponent encounters. C consumes these facts;
  it does not reconstruct A's comparison algorithm. The example gap is one
  distinct game out of two eligible encounters; the miss is one out of four.
- B's consumed client release has SHA-256
  `7acac937976ca97aa02d7b449829243d9bedfb66e5dc23e8a32550dad8aa3a17`,
  built from `4e6d83110bc655924d42fb19d9a61ac29fcee0b6`. It supplies the public
  `env` boundary, typed records and digest helper. C deliberately pins B's initial
  qualified image `sha256:713e7d04340e510418a77535b26d3f56421f3b59e9623b579c12f0c390cd242b`.
  Later B releases are separate artifacts, not silently substituted.
- Computational profile:
  `stockfish-19-smallnet:sha256:577f1e0a5fc385a809603142330931ab2353fe92e3b6ab91d927bc39a7af31e4`;
  Node 26.10.0/Linux ARM64, Threads1/Hash32/full strength, one CPU, hard 512MiB,
  no swap/network/application mounts. Startup identity and every result's runtime
  name/version/implementation identity must agree. Domain/provider credentials
  are not inherited by the worker process.

### Results and milestone status

**C1 accepted.** Installed C accepts immutable serialized inputs, produces
inspectable source findings, retains unfinished work, and serves coherent reads.
Workbench uses a caller credential and exact origin allowlist, with generation
and response-identity fences. It recovers unconfirmed commands by server receipt
and reads authoritative state after reload. No GET initiates acquisition or search.

**C2 accepted for the explicit synthetic rehearsal.** Actual A output was accepted
through C's authenticated API into a separately restricted workspace database.
Eight fresh B executions were durably accepted over six learner decisions;
remaining scan count was zero. The resulting report contains one repertoire miss
and one observed gap. An allowed alternative produces no miss. The existing
Game Evidence delivery is `completed`, with its real acknowledgement retained;
Learning settlement remains `unknown`.

A separate actual B missed-mate example proves unrestricted and constrained
played-move results at equal completed depth20, with typed mate significance.
The A/B artifact consumer independently validates all 24 retained B result records,
including full legal history/PVs. C's actual engine-backed response draft passes
A's installed `tempo-content proposal inspect`. That proves schema/legal-line
compatibility; Content approval, publication, activation and live source application
were not performed. Informational export omits the prepared response line.

**C3 unverified.** No authorized A-produced 50–100-game real-data export was supplied.
No account-wide import was inferred. The initial 250-decision policy is implemented,
but this rehearsal has only six learner decisions; it is not a throughput or
real-repertoire quality claim.

### Exact qualification

Run from the task checkout unless an installed consumer directory is specified.
The preserved evidence root is `/Users/andy/.codex/artifacts/analysis-c-findings`.
Each report includes exact argv, executed case identities and source/artifact
hashes; intermediate failures are retained separately from final passes.

| Command / owning lane | Final outcome |
| --- | --- |
| `services/intelligence/.venv/bin/python -I -m pytest -c services/intelligence/pyproject.toml services/intelligence/tests -q -o addopts=` | 224 passed |
| `services/intelligence/.venv/bin/python -I services/intelligence/integration/qualify_artifacts.py EVIDENCE/qualified/intelligence` | Source-removed wheel/sdist, Python3.12/3.14: 224 pure cases each; Python3.12 wheel/Python3.14 sdist: 120 native cases each; all pass |
| `services/intelligence/.venv/bin/python -I tools/analysis-restoration/c_pilot/qualify.py --c-artifacts C_WHEELS --b-client B_WHEEL --requirements LOCKED_REQUIREMENTS EVIDENCE/qualified/pilot` | Source-removed pilot wheel/sdist, Python3.12/3.14: 8 cases each; all pass |
| `node apps/workbench-web/qualify.mjs EVIDENCE/qualified/workbench-display` | Restricted build/install with sources removed; 6 public-client cases plus strict native browser; deliberate zero-selection rejected; temporary install removed |
| `node tools/component-build/test-lane.mjs --component workbench-web` | Registered build/client and browser case accounting passes |
| `TEMPO_C_NATIVE_SELECTION=analysis TEMPO_C_A_EXPORT=A_DIR TEMPO_C_GAME_IDS_FILE=IDS TEMPO_C_STOCKFISH_PROFILE=PROFILE /private/tmp/tempo-c-integration-env/bin/python -I services/intelligence/integration/run_postgres.py EVIDENCE/qualified/native-actual-delivery-AB` | 23 native cases pass, including fresh A/B lanes; database/worker resources removed |
| Explicit environment paths plus `python -I -m pytest tools/analysis-restoration/c_pilot/integration/test_actual_consumers.py --import-mode=importlib -q` | Named `C_actual_AB_consumers` passes against actual A/B artifacts |
| `services/learning/runtime/.venv/bin/python -I tools/analysis-restoration/c_pilot/integration/run_study_off.py EVIDENCE/qualified/study-optional-off STOP_RECEIPT` | Named `C_optional_off_study`: one real F2 save/reload passes after installed C shutdown; owning resources/build removed |
| Component boundary/discovery, schema drift, scoped ESLint/type build, conflict and whitespace checks | Pass; commands/results retained in final-checks.json |

The 28 required C identities are registered in their normal owning inventories.
Additional regressions cover first publication racing a sibling-run dismissal,
retry fairness without resetting age, exact remaining-work cursors, insufficient
quality retry, policy-free engine context, trailing B completion records and
ready-worker provenance. Pure C and browser source fixtures remain explicitly
synthetic; native producer output is generated by installed C and real PostgreSQL.

The initial native startup failures (`owned_postgres_not_ready`), one cancelled
mixed exploratory run, stale-package exploratory failures, and the fixed C pilot
trailing-status defect are retained. They are not counted as passing tests.
No required selected case is skipped or zero-selected in final qualification.
Starlette's existing AnyIO deprecation warning remains. This focused evidence is
not a full-system or hosted merge-qualification claim.

### Manual pilot, continuation and recovery

1. Obtain A's explicitly scoped export directory and exact game-ID list. Verify
   its manifest/pages with A's public verifier. Preserve private exports outside
   the development checkout; never supply usernames as implicit scope.
2. Install the pinned C wheel and its selected dependencies, B's client wheel,
   then the external pilot wheel. Configure C's own restricted database and
   private credential files using the existing Intelligence README. Apply the
   ordered migrations with the owner CLI; runtime never applies migrations.
3. Create private pilot JSON with integer `schema_version:1`, `workspace_id`,
   `a_export_directory`, exact `game_ids`, installed `intelligence_command` argv,
   `run_id`, `position_budget:250`, optional `engine_profile`, and finite
   `max_jobs`. Run `tempo-analysis-pilot --config FILE --evidence PRIVATE_DIR`.
   Credentials do not belong in that document, command arguments or exports.
4. Inspect `tempo-intelligence analysis status RUN`, `report RUN`, and
   `remaining RUN`. Pagination uses opaque `--after` tokens. Select source-native
   ranking with an explicit immutable evidence/cohort ID in a new RunRequest;
   actual games remain the default. Existing source caches are never refreshed
   by opening a report.
5. Run `tempo-analysis-pilot work RUN --profile PROFILE --max-jobs N` explicitly.
   The pinned Docker command uses `--pull=never --rm -i --network=none --cpus=1
   --memory=512m --memory-swap=512m --pids-limit=64 --read-only IMAGE serve`.
   Use a task-owned name/labels. No host application or database mount is needed.
6. For another 250 decisions, create a command JSON with schema1, a new stable
   command_id, `action:"continue"`, expected run revision and `budget:250`, then
   `tempo-intelligence analysis continue RUN --file COMMAND`. `retry` retains
   accepted work and original age, including explicit retry of insufficient
   searches. `cancel` remains requested until termination/session-loss handling;
   use `resume` only after it completes. Recover with `analysis receipt ID` and
   server status reads; never invent a replacement command after a lost receipt.
7. Use `analysis export RUN FINDING --generation N --kind context` for information,
   or `--kind response-proposal` only when the report advertises that action.
   Pass a draft to A's public inspector/operator workflow for explicit review.
   Content and Learning retain all approval/publication/activation/admission work.

### Boundaries and remaining limits

No global curriculum, broad Builder/Settings reconstruction, alternate remediation
producer, probability fusion/calibration, or broader legacy mathematics moved.
Pure classification/bounds/ranking stays C-owned with the documented future #156
seam. Supported engine absence never hides a positively established source gap.
Unexported/ambiguous coverage, missing encounter inventories, unsupported quality
and unavailable capabilities remain explicit. Historical captured activation is
not a claim about a live Learning installation at the time a report is opened.

Actual C3 data, all other engine platforms/profiles, live provider behavior and
hosted required checks remain unverified. A/B retain independent PRs and artifacts;
C installs no A/B implementation to serve an already accepted report. Nothing was
merged, deployed, enabled in Actions, or scheduled for recurring import.


Current qualified artifact SHA-256 values:

- Intelligence wheel: `8910eef3a6cd8b58e092404f58451a2d149aeeddd921f86c965cbf7850774f7c`.
- Intelligence sdist: `1bba26c17638e2fcdf952fd8a42f561b6997ecd543265b8023ecf106fe947acb`.
- Pilot wheel: `677b8e97e44dfd1be9bd4d843b1ef737fe203920506ced99c20143e6e3340bed`.
- Pilot sdist: `d6a61e1e7ebf5a0825a3297c972654451a3c391422fe1b3f4250e6df6e90ffe2`.
- Workbench: `dd9809d578b01deb97c41e6832f25d762ec2cfd794a77deec3d27cab867f306a`.

The final actual-delivery assertion and handoff changes do not alter the qualified
Intelligence runtime or its default matrix suites. The final Workbench wording
was rebuilt and independently qualified. Consumed shared analysis-job tests pass
92 cases; chess-core identity/purity tests pass 105 cases.

## PR295 independent corrective implementation — 2026-10-16

The original handoff above remains historical evidence. Repairs start at
`4c8078b81b35003ac741bf40f9ebb450d55cbdd4`, against unchanged main
`0edf566ad48f51846a43d3341809379ff0469ada`. A remains
`1db44cbdf310f959eb0c839e94166350531cc89b`; B's published head at baseline is
`603822b2c2f3618ddd2055001a6c5afdfecde3f8`. Both remain separate unmerged PRs.
The new task checkout/evidence parent is `/private/tmp/tempo-pr295-repair-20261016`.
Existing development copies and historical C/B artifacts were not reused as
mutable resources.

R1 removes category-first ranking and binds cursor ordering to immutable report
metadata. R2 uses exact decision associations, keeps ambiguous/unassociated engine
findings with null lineage, retains candidate facts, and preserves all explicit
serialized A memberships. R3 removes the configured pilot's extra engine-less work
call. R4 persists immutable commands per origin/workspace, verifies stored request
digests, migrates legacy pending intent, and recovers exact requests. R5 checks
ready/result implementation identities and original job roots, treats mismatch as
permanent without ordinary retries, and recovers via explicit new run revisions.
No shared strict contract or migration payload changes.

Original-source red evidence is retained in `evidence/red-pure.*`, `red-native/`,
`red-pilot.*`, and `red-r4.*`. The latter executes the original Workbench
save-command body and reproduces overwrite after a lost response. The native
baseline has four intended failures and 21 passes. Early exploratory editable
installation, missing parser, and browser fixture-sequence failures are retained;
none is counted as passing qualification.

Fresh initial actual final-image acceptance used
`sha256:21172218ef8fb1c5d1edf6dabae11ab7de13f829b0cdb2c001974a4fdbe07f81`,
the unchanged qualified computational profile and verified client wheel. It
accepted eight actual searches from A's four synthetic games/six decisions and a
separate two-search equal-depth typed-mate comparison. A manifest/page identity is
unchanged (`924b65aa…267b`). The fresh combined exchange is independently consumed
and a real exported response draft passes the installed A Content inspector.
This supersedes neither historical artifact bytes nor producer ownership. C3 and
live-data usefulness remain unverified. Final source-bound package/browser/native
qualification and cleanup accounting are recorded in the completion supplement.

Independent review at `b35dbf10` found one additional P2: ordinary dismissal could
upgrade a legacy revision's attribution and lose its semantic dismissal identity.
The follow-up pins legacy revisions, republishes only their retained user-state
changes, blocks further analysis/proposal use until explicit replacement, and
preserves the reviewed partial legacy read shape. It has a named native seeded-
history regression. B advanced during qualification to `099fa1fbc29efdf747af5da6a1c9a30cbfa189d3`,
with a separately accepted image `sha256:a42a0a8e44fe0db8af81ab7424dca1be0c1f5fff8e93ae517dafb6486ad2c81f`.
The computational profile/client wheel remain unchanged. Earlier final-image
acceptance remains distinct; current-image acceptance is recorded separately.

The follow-up review also checked legacy reopen, unpublished heads and expired
cancellation leases. Their focused corrections reset user state from authoritative
dismissals, exclude unpublished legacy heads from background derivation while
exposing a revision-required read status, and reap cancellation before rejecting
new legacy work. Named native regressions cover all three paths.

Final consumer review removed legacy Continue/Retry/Resume controls before they
could persist predictably rejected commands; Cancel remains available with an
explanation. Explicit cancellation also reaps expired executions directly, so an
unpublished legacy run needs no derivation worker to release expired capacity.

The final R3 transition audit reproduced one further stale availability marker:
no-engine work followed by bounded configured success could leave other pending
positions marked unavailable. A successful accepted result now clears only those
current-run/revision pending absence markers in the same transaction. The named
native regression first failed against the installed `d21bd33b` wheel (34 other
cases passed); it also verifies other runs/failures and a later explicit no-engine
invocation remain truthful. The final artifact matrix is regenerated after this
narrow correction.

## Completion supplement — independently qualified repair

## A. Verdict

**READY TO MERGE — qualified foundation scope.**

R1–R5, required component qualification, current-image synthetic A/B/C acceptance,
Study optional-off acceptance, and independent exact-commit review pass. There are
no remaining mandatory defects or missing proofs in this foundation scope. This
is not a live-analysis-readiness, full-system, or hosted CI claim. C3 is unverified.
No merge or deployment was performed.

## B. PR identity

- PR: https://github.com/aaweaver-actuary/tempo/pull/295; existing remote branch
  `codex/analysis-c-findings`. It remains open and draft.
- Starting head: `4c8078b81b35003ac741bf40f9ebb450d55cbdd4`.
- Base/current main: `0edf566ad48f51846a43d3341809379ff0469ada`.
  Current main is an ancestor of the repair; no rebase or force push is needed.
- Final tested executable: `edaf6df6879d45e86507e61c4f17946670603035`.
- The completion supplement is a later **documentation-only** commit. Its exact
  published head, fast-forward verification, and GitHub mergeability are recorded
  in `publication.json` after publication. Pre-push GitHub mergeability was
  `MERGEABLE`; that structural status is separate from this engineering verdict.
- Task branch: `codex/pr295-repair-20261016`. Task checkout:
  `/private/tmp/tempo-pr295-repair-20261016/tempo`.
- Canonical retained evidence:
  `/Users/andy/.codex/artifacts/analysis-pr295-repair-20261016`.
  Original invocation paths in logs remain
  `/private/tmp/tempo-pr295-repair-20261016/evidence`.

## C. Repair matrix

Paths below are repository-relative. Names are regular owning regression cases;
parameterized execution identities are retained in JUnit/JSON reports.

| ID | Severity | Original defect | Repair / changed files | Named regression | Result |
| --- | --- | --- | --- | --- | --- |
| R1 | P1 | Category preceded documented ranking factors | `services/intelligence/src/tempo_intelligence/analysis/store.py`, `classify.py`, `read_models.py`: global source-specific rank, deterministic support/candidates, generation-bound ordering; historical order/cursors retained | `test_R1_global_cross_category_priority_and_pagination`, `test_R1_global_priority_known_unknown_frequency_and_significance`, `test_R1_only_selected_population_ranks`, `test_R1_source_order_independence` | Pass |
| R2 | P1 | Same-color/earlier/ambiguous context could invent repertoire attribution | `classify.py`, `store.py`, `read_models.py`, generated Finding/ReportPage schemas; `tools/analysis-restoration/c_pilot/src/tempo_analysis_pilot/inputs.py`; Workbench `contracts.ts`, `workbench.ts`: exact supported association or null IDs/lineage; preserve all A memberships; explicit revision for legacy correction | `test_R2_exact_association_and_input_order`, `test_R2_same_color_without_exact_context_has_no_lineage`, `test_R2_finding_and_dismissal_identity_ignore_selection_order`, `test_R2_legacy_revision_preserves_identity_until_explicit_replacement`, `C_native_workbench` | Pass |
| R3 | P2 | Successful bounded pilot was followed by engine-less work | Pilot `cli.py`, Intelligence `analysis/store.py`: one installed configured execution, retained receipt and read-only status; strict job bound; clear only current pending absence markers after accepted configured result | `test_R3_partial_backlog_does_not_invoke_clientless_work`, `test_R3_startup_failure_is_not_success`, `test_R3_invalid_job_bound_fails_before_worker_start`, `test_R3_configured_success_clears_only_current_pending_absence_markers` | Pass |
| R4 | P2 | A second mutation overwrote unresolved browser intent | Workbench `commands.ts`, `client.ts`, `workbench.ts`, `contracts.ts`, `index.ts`; Intelligence `analysis/store.py`: authority-local durable command slot, exact serialized retry, normalized digest-bound receipt, fail-closed persistence and legacy migration | `R4 unresolved command cannot be overwritten and exact retry reuses bytes`, `R4 reload uses authoritative receipt and missing receipt preserves uncertainty`, `test_R4_receipt_binds_original_request_digest`, `C_native_workbench` | Pass |
| R5 | P2 | Root/played profile compatibility and recovery were incomplete | Intelligence `analysis/engine.py`, `worker.py`, `store.py`; pilot `stockfish_client.py`: ready identity fence, acceptance provenance/input checks, permanent mismatch without transient retry charge, preserved root/history, explicit new revision | `test_R5_incompatible_worker_identity_rejected`, `test_R5_original_root_and_restriction_required`, `test_R5_incompatible_result_releases_lease_and_new_revision_recovers`, `test_R5_acceptance_fence_rejects_changed_result_without_consuming_attempt`, `test_R5_compatible_session_restart_at_played_stage_and_replay_conflict`, `test_R5_played_cancellation_and_acceptance_fence_preserve_root` | Pass |

Further review fixes cover legacy reopen, unpublished legacy heads starving new
reports, expired cancellation capacity, and legacy controls that would persist a
predictably rejected command. Their regular native regressions are
`test_R2_unpublished_legacy_run_does_not_starve_new_reports` and
`test_R5_legacy_cancellation_reaps_expired_execution_without_new_work`, with
legacy reopen in the identity test and legacy controls in `C_native_workbench`.
Interfaces, recovery instructions, and all three owning inventories were updated.
Unrelated Content manifest formatting churn was removed; legitimate registrations
remain. No migration payload or shared A/B wire contract changed.

## D. Before/after evidence

The original implementation identity for the initial red runs is
`4c8078b81b35003ac741bf40f9ebb450d55cbdd4`. Regression overlays exercised that
implementation; they were not claimed as an unchanged original test inventory.

| Repair | Original reproduction/result | Passing proof on repaired implementation |
| --- | --- | --- |
| R1 | Native `test_R1_global_cross_category_priority_and_pagination` returned engine support 1 ahead of miss support 20; `red-native/native.xml` | Same named case, retained cursor/stale-generation assertions, plus pure selected-population/frequency/cost/tie cases; final package/native matrix |
| R2 | Native `test_R2_same_color_without_exact_context_has_no_lineage` returned `rep` instead of null. Original pure exact-association cases also lacked the required helper; these missing-helper failures alone are not behavioral proof. `red-native/`, `red-pure.*` | Exact none/one/ambiguous/deduplicated/earlier/diverged/re-entry cases; null-lineage validation; durable identity/dismissal; actual browser null-lineage proof |
| R3 | Configured max_jobs 1 and 2 invoked a second client-less work call: 2 failures, 2 passes, `red-pilot.*` | All 16 pilot cases in each installed consumer and native bounded continuation. Later transition regression failed on installed `d21bd33b` (1 failure, 34 passes) and passes on final executable (35/35 selected native analysis cases) |
| R4 | `node E/red-r4.mjs` executes the original `saveCommand` body: second command replaces the first after lost response. Native receipt case also failed with missing `request_digest`. `red-r4.*`, `red-native/` | 15 installed client cases; native receipt digest/conflict; real desktop/phone browser lost-response/reload, blocked second mutation, dismissal/reopen and null-lineage recovery |
| R5 | Pure differing runtime/version/implementation was accepted; native mismatch produced generic worker failure rather than permanent incompatibility. `red-pure.*`, `red-native/` | Pure pair/input/depth/mate checks; native dispatch and acceptance fences, no attempt charge, lease release, cancellation/replay/revision recovery; fresh compatible real B searches |

Initial red native command was:
`TEMPO_C_NATIVE_SELECTION=analysis services/intelligence/.venv/bin/python -I services/intelligence/integration/run_postgres.py E/red-native`.
Its exact child argv/source inputs are in `red-native/native-resource.json`:
**4 failed, 21 passed, 0 skipped**. The original pure selector invocation used
`python -I -m pytest tests/test_repairs.py --import-mode=importlib -q` with its
then-17-case overlay (**10 failed, 7 passed**). Pilot used the corresponding
`tests/test_cli.py` overlay (**2 failed, 2 passed**). JUnit retains exact expanded
case identities; the R4 standalone command and script are retained verbatim.

Independent review found legacy ordinary dismissal upgrading attribution on
`b35dbf10`; `red-review/` records **1 failed, 31 passed** against that installed
artifact. The final implementation pins legacy derivation, keeps stored report
bytes/IDs/ordering, and requires a new RunRequest revision. It never aliases a
fabricated historical association's dismissal to a corrected identity.

All red/failure evidence remains. Exploratory editable-import guard failure,
one early native server-start timeout, missing frontend parser/workspace setup,
and an initial browser fixture UUID-sequence error are separately retained and
are not passing qualifications. The complete final runs passed without skipped
required cases or relaxed assertions/timeouts.

## E. Validation matrix

Use these exact path bindings to expand the commands below (no live resources):

```sh
T=/private/tmp/tempo-pr295-repair-20261016/tempo
E=/private/tmp/tempo-pr295-repair-20261016/evidence
PY=/private/tmp/tempo-pr295-repair-20261016/integration-env/bin/python
A_EXPORT=/Users/andy/.codex/artifacts/analysis-c-findings/A-installed-delivery/analysis-inputs
IDS=/Users/andy/.codex/artifacts/analysis-c-findings/A-delivery-game-ids.json
PROFILE=$E/stockfish-current-profile.json
B_WHEEL=/private/tmp/tempo-analysis-b-stockfish/evidence/client-handoff/tempo_stockfish_client-1.0.0-py3-none-any.whl
REQ=/Users/andy/.codex/artifacts/analysis-c-findings/requirements-qualification.txt
```

Working directory is `$T`. All matrix rows below have **0 failed, 0 skipped**.
Selected equals passed. `validation-summary.json` retains every JUnit identity;
runner reports retain exact child commands, source hashes and cleanup receipts.

| Owning lane / exact command | Selected / passed | Toolchain / installed identity | Evidence under E |
| --- | --- | --- | --- |
| `services/intelligence/.venv/bin/python -I services/intelligence/integration/qualify_artifacts.py "$E/qualified-release/intelligence"` | 245 pure per 3.12 wheel/sdist and 3.14 wheel/sdist; 134 native per 3.12 wheel and 3.14 sdist | CPython 3.12.12 / 3.14.8; final C wheel/sdist below; source removed before execution, installed import/CLI guard passes | `qualified-release/intelligence/artifacts.json`, four pure XMLs, two native XMLs |
| `services/intelligence/.venv/bin/python -I tools/analysis-restoration/c_pilot/qualify.py --c-artifacts "$E/qualified-release/intelligence/distributions" --b-client "$B_WHEEL" --requirements "$REQ" "$E/qualified-release/pilot"` | 16 per four wheel/sdist consumers | CPython 3.12.12 / 3.14.8; final pilot, exact B client; source removed | `qualified-release/pilot/artifacts.json`, four XMLs |
| `node apps/workbench-web/qualify.mjs "$E/qualified-complete/workbench"` | 15 public client + 1 native browser | Node 26.10.0; restricted artifact below; qualified `d21bd33b` component tree is byte-identical to final executable | `qualified-complete/workbench/restricted.json`, `workbench-evidence-reuse.json` |
| `node tools/component-build/test-lane.mjs --component workbench-web` | 15 client + 1 browser | Node 26.10.0; final executable; TypeScript and production build pass | `workbench-registered-release.log` |
| `TEMPO_C_NATIVE_SELECTION=analysis TEMPO_C_A_EXPORT="$A_EXPORT" TEMPO_C_GAME_IDS_FILE="$IDS" TEMPO_C_STOCKFISH_PROFILE="$PROFILE" "$PY" -I services/intelligence/integration/run_postgres.py "$E/actual-release"` | 37 | CPython 3.14.8; final installed C/pilot; real PostgreSQL; current final B ARM64 image | `actual-release/native-resource.json`, `native.xml`, actual A/B records |
| `TEMPO_C_A_EXPORT="$A_EXPORT" TEMPO_C_GAME_IDS_FILE="$IDS" TEMPO_C_B_EXCHANGE="$E/actual-release/fresh-exchange.json" TEMPO_C_ACTUAL_EVIDENCE="$E/actual-release/independent-consumer.json" "$PY" -I -m pytest tools/analysis-restoration/c_pilot/integration/test_actual_consumers.py --import-mode=importlib -q --junitxml="$E/actual-release/consumer.xml"` | 1 | CPython 3.14.8; installed A/B/C public models; ten fresh actual result records | `actual-release/consumer.xml`, `independent-consumer.json` |
| `/private/tmp/tempo-pr295-repair-20261016/integration-env/bin/tempo-content proposal inspect "$E/actual-release/response-proposal.json"` | 1 inspector invocation, successful (not a pytest count) | Installed A Content wheel `7949f9bd…8ae5` | `actual-release/proposal-inspected.json` |
| `services/learning/runtime/.venv/bin/python -I tools/analysis-restoration/c_pilot/integration/run_study_off.py "$E/study-off-final" "$E/actual-release/optional-stop.json"` | 1 | Installed Learning / Python 3.13.5, production Study / Node 26.10.0; only task-owned C stopped | `study-off-final/native.json` and nested `native.xml`; F2 save/reload and cleanup pass |
| `services/intelligence/.venv/bin/python -I -m pytest contracts/analysis-jobs/tests packages/chess-core/tests --import-mode=importlib -q --junitxml="$E/shared-contracts-chess.xml"` | 197 (92 contracts + 105 chess-core) | Python 3.13.5; unchanged shared contracts/chess-core | `shared-contracts-chess.xml`, `.log` |
| `"$PY" -I "$E/direct_b_boundary.py"` | 1 explicit bounded replay/cancel probe, successful (not a suite count) | Installed B client, same restricted current image | `direct-b-boundary.json` |

Boundary commands also pass:

```sh
node tools/component-build/check-components.mjs --component intelligence --component chess-core --component analysis-jobs-contracts --component workbench-web --component analysis-pilot
node tools/component-build/test-lane.mjs --component intelligence --component chess-core --component analysis-jobs-contracts --component workbench-web --component analysis-pilot --list
npx eslint apps/workbench-web/src apps/workbench-web/tests/browser.mjs apps/workbench-web/tests/client.test.mjs
npm run check:conflicts
git diff --check
git diff --quiet 4c8078b81b35003ac741bf40f9ebb450d55cbdd4 HEAD -- contracts/analysis-jobs packages/chess-core services/intelligence/src/tempo_intelligence/migrations
```

Evidence: `boundaries-release.log`, `discovery-release.log`, `lint-release.log`,
`conflicts-release.log`. Generated schema drift is checked in the complete pure
inventory. Restricted Workbench deliberately rejects a zero-selected run; this
negative control is not counted as a passing selected suite. No required selected
case is cancelled, missing or skipped. Heavy qualifications ran serially.

### Supplied scenario coverage

- R1-A: native cross-category priority; R1-B/C: known/unknown frequency,
  significance/cost and typed-mate pure cases; R1-D: four selected populations;
  R1-E: native generation/cursor cases plus legacy ordering; R1-F: source-order
  independence and native identity/selection-order cases.
- R2-A/B/C/D/E/F: exact-association parameter cases plus native no-context and
  deterministic identity; R2-G: `test_engine_opportunity_without_repertoire_color_mapping`;
  R2-H: native finding/dismissal stability and explicit legacy revision replacement;
  R2-I: `C_native_workbench` unscoped board, detail, dismissal and reload.
- R3-A/B/E: installed controlled pilot bounds 1/2/no-engine plus native configured
  availability transition; R3-C: startup failure plus retained accepted-state
  native worker failures; R3-D: `test_C_worker_restart` and compatible played-stage
  loss/recovery; R3-F: immutable input/run replay and repeated bounded native work.
- R4-A/B/C: client loss/block/retry and actual browser recovery; R4-D: native
  `test_R4_receipt_binds_original_request_digest` changed-request conflict;
  R4-E/F: workspace/origin partition and stale-client delivery fences;
  R4-G/H: auth/receipt mismatch and storage failure cases; R4-I: native-produced
  dismissal/reopen/reload; R4-J: each continue/retry/cancel/resume client case and
  browser normal/legacy control gating. Starting analysis shares the pending fence.
- R5-A: fresh actual same-root unrestricted/restricted acceptance; R5-B/C:
  parameterized identity and native dispatch/acceptance fences; R5-D: depth20/19
  insufficient quality; R5-E: explicit new revision preserves old root/reports;
  R5-F: native root/played session loss and compatible/incompatible restart;
  R5-G: native played cancellation/acceptance race plus actual B termination probe;
  R5-H: native exact replay/different-payload conflict plus actual public B replay;
  R5-I: incomplete/incompatible proposal gating and installed A inspection.

## F. A/B/C integration

- A head: `1db44cbdf310f959eb0c839e94166350531cc89b`, still unmerged at final
  remote check. Export producer: `4d132d0d984b36cd027f967cd3ecb11a1fe27ae0`.
  Producer-to-head differences are documentation/inventory, not export behavior.
- A manifest: `924b65aa373b5305816daf5a2ddfc2fcf26640f03a15259a34048d03033c267b`;
  all seven pages verified. Explicitly synthetic four-game scope, six learner
  decisions. No real account/history import.
- B initial head: `603822b2c2f3618ddd2055001a6c5afdfecde3f8`; final head:
  `099fa1fbc29efdf747af5da6a1c9a30cbfa189d3`, qualified executable
  `c652abadc9a0abcd022995447014d42e9e88d411`.
- Exact B client wheel SHA-256:
  `7acac937976ca97aa02d7b449829243d9bedfb66e5dc23e8a32550dad8aa3a17`.
- Current B image:
  `sha256:a42a0a8e44fe0db8af81ab7424dca1be0c1f5fff8e93ae517dafb6486ad2c81f`.
  Implementation:
  `sha256:c9c9a625c099b58f0c05b103d68c84f1a25cc1b6c374694ae2ca74ea6c93cd64`.
- Computational profile:
  `stockfish-19-smallnet:sha256:577f1e0a5fc385a809603142330931ab2353fe92e3b6ab91d927bc39a7af31e4`.
  Runtime is `node-stockfish-wasm` / `26.10.0`. Executed Linux ARM64 only.
- Historical C2 used image `sha256:713e7d04340e510418a77535b26d3f56421f3b59e9623b579c12f0c390cd242b`.
  This task separately tested the next published image
  `sha256:21172218ef8fb1c5d1edf6dabae11ab7de13f829b0cdb2c001974a4fdbe07f81`.
  Those results do not stand in for the fresh current-image result above.
- Final C wheel SHA-256:
  `b560ba0c4183959fd9e7296656ef43dc54c0e15f0518ba20d459ea9d358d4a3a`.
  C sdist:
  `5c675da89668965cd0a4fdb7f60db49a6a7e45cc37359fc8dc30d8f59411d6ac`.
- Pilot wheel:
  `ba5a82058ffbf2aff2396ecb84000adddfc6429e2e3a946c1a1531706d9ba3a9`.
  Pilot sdist:
  `76329e035772481dff980f4f563f34cba8693f58fbb9f904c377cae5ebcc6f18`.
- Restricted Workbench package:
  `cc23305bae981c502340ed139f867f101869f12a0b7264179f5caa60a5ac9970`.

Fresh final C accepted **8 actual A-backed unrestricted/restricted searches** and
**2 additional equal-depth mate-example searches**, with no worker-failure events
in the A/B success lane. A report contains one source-backed practical gap and one
repertoire miss; the separate mate report retains one useful unscoped engine
opportunity. The independently installed consumer validates all ten fresh results,
original histories, restrictions, digests, perspectives and legal PVs. A real
engine-supported response draft from that report passes A's Content inspector.
The standalone B probe executes one completed job, replays its identical result,
then terminates a second in-flight job; it is separate from C's ten accepted jobs.

Worker restrictions were preserved: one CPU, 512 MiB memory and swap ceiling,
network none, read-only container, no application/database mounts, bounded pids,
sanitized environment and B's qualified internal thread/hash configuration. Inputs,
results, receipts, profile argv and provenance are retained; no synthetic result
was substituted for actual execution. Permanent profile mismatch requires an
explicit new RunRequest revision using the same immutable accepted input and newly
selected profile. The old revision, results and reports remain inspectable.

## G. Architecture preservation

A retains comparison/export/source/publication ownership. B retains engine/session
execution and its public client. C retains immutable inputs, scheduling, accepted
results, findings, reports and user commands. Shared analysis-job/chess-core
contracts and existing migrations are unchanged by the repair. There are no
cross-service SQL joins or imports of A/B private implementation into Intelligence.

Content inspection does not approve or publish. Learning alone admits/activates
and schedules training; analysis creates no remediation obligation. Study's real
production save/reload passes with optional C stopped. No recurring acquisition,
engine loop, broad Builder/Settings reconstruction or alternate activation path
was added.

## H. Remaining limitations

C1 and explicitly synthetic C2 are freshly established. **C3 is unverified**:
50–100 selected actual games and real-game quality/usefulness remain a separate,
explicitly authorized rehearsal. Six synthetic learner decisions are not a
throughput or useful-repertoire quality assessment.

The current image ran on Linux ARM64; other platforms/profiles are unqualified.
Hosted Actions were neither enabled nor run. GitHub reported an empty check rollup;
read-only ruleset/protection inspection did not establish any hosted required-check
pass. This task does not claim a full-system qualification. No production database
migration, deployment, Content approval/publication, Learning activation/admission,
live provider operation, real account import, merge or protection-setting change
occurred. These limits are outside the qualified foundation scope, not silently
passing tests. Historical results remain historical and legacy attribution remains
unverified until explicit replacement.

## I. Cleanup

All new resources belong to this task: isolated checkout, selected component
virtual environments/builds, integration/red consumer environments, disposable
PostgreSQL resources, restricted B worker processes, and the owning Study/Learning
proof assembly. Runner receipts confirm removal of their containers/volumes,
source-free temporary consumers, Study test data/build and temporary image tag.
No task process or container references the checkout at the pre-cleanup audit.

After successful fast-forward publication and verified evidence preservation, the
eligible task checkout and task consumer environments are removed. Exact outcomes,
pre/post resource identity comparison, retained refs, and checksums are recorded in
`cleanup.json` and `SHA256SUMS`. The final C/pilot/Workbench packages, public input
artifacts, failure logs, successful XML/JSON reports, screenshots and this handoff
remain outside the checkout. The original compact invocation evidence is also
retained. B's qualified image, shared caches, original A/B/C evidence, unrelated
worktrees/resources and the pre-existing main stash are retained. No global prune
or deletion of another task's resources is permitted or needed.

## J. Recommended next step

Review the published repair and exact-commit evidence, then merge only the
qualified foundation scope through the normal repository workflow when its owners
choose to do so. Any hosted checks required by that workflow must be satisfied;
this local report does not replace them. Keep A/B artifact revisions pinned.
After the foundation is accepted, separately authorize an A-produced export of
50–100 explicitly selected real games for C3, evaluate attribution and usefulness,
and inspect drafts through Content's normal approval path. Do not infer Content
publication or Learning admission from findings.
