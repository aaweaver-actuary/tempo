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
