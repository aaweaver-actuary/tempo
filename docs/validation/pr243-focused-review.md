# PR #243 focused qualification review

## Verdict and scope

**BLOCKED pending fresh required candidate qualification.** Diagnostics synchronization
and its original eight cases are qualified for the historical candidate; the review
found and repaired ownership omissions. Redis/Study order-state attribution is not
fully established. No qualification exception, branch-protection bypass, merge or
deployment is recommended or performed. Current CI results and final revision belong
in the PR handoff; this report does not relabel historical results as a new-head pass.

The PR continues to own diagnostics classification and bounded browser observation,
not a general Redis, Study or qualification-tier redesign. Separate follow-ups:
[Redis fixture/publication #248](https://github.com/aaweaver-actuary/tempo/issues/248)
and [Study readiness #249](https://github.com/aaweaver-actuary/tempo/issues/249).

## Preserved evidence and revision rules

Original run [38398561299](https://github.com/aaweaver-actuary/tempo/actions/runs/38398561299)
binds head `74dcdedbab3f3160b6a373e8a0a96f41b8276c34`, base
`c5f773b18c4a5b30a3abda84ecac01ab625a3902`, integration
`fc3b5a441ea03171e10f50e461ea636a69484e2d`. Eleven downloaded artifact ZIP digests
match GitHub metadata. The original source's `layerFailures`, execution identity,
command/test/scenario validation and shard validators accept frontend, backend,
build, PostgreSQL, lifecycle, visual and browser shards 1/2/4. Shard 3 and aggregate
correctly reject their failed cases. The passing evidence remains valid for its
original exact candidate even though the overall run failed.

The user-added documentation commits end at `d0064e8`, whose Git tree equals
`74dcdedb` (`c58aa1fd615508202c65dd20c4ae668c0cde99db`). This proves identical source
inputs, not formal reuse eligibility: existing rules also bind revision identity.
No older artifact is relabeled as evidence for d0064e8 or the integrated review head.

Main advanced through merged #244, #227 and #242. Their certificate/native evidence,
queue extraction and scheduling boundary work is preserved through merge commits.
Both `certificateSources` and `browserSourceInventory`, both classifiers, and the
fast/browser/certificate hash inputs remain. Queue/scheduling/runtime/qualification
inputs and integration changed, so the final candidate needs its configured fresh CI;
this is not a repeat of old layers merely to erase an unrelated failure.

## Failure-by-failure classification

| Failure | Evidence and classification | Base finding / remaining gap |
| --- | --- | --- |
| Redis recovery cleanup | Confirmed existing fixture assumes a stable projection without settling publication. Ordered head witness: before `refreshing`,generation15,pending1; installed `ready`,generation15,pending0; current `ready`,generation16,pending0. Cleanup correctly refuses overwrite. Fixture-isolation/publication problem; no PR243 product/Redis/fixture implementation change. | Isolated base/head pass. Ordered base passes Redis but later fails queue readiness. Exact cleanup signature not reproduced on base; timing contribution of the changed diagnostics driver cannot yet be excluded. #248 must capture pending task/lease ownership and establish a settled-seed control. |
| FEN-only Study | Original and ordered-head commit acknowledgements are202/retrying. UI polls receipts; ten original polls remain retrying. Audit receipt has attempt_count0 and a future next_retry_at. Five-second root assertion precedes the UI's bounded20×500ms receipt resolution. Test timing plus unfinished execution; execution cause inconclusive. | Isolated base/head pass. Ordered base fails this case earlier, waiting for queue preparation. Shared queue/publication exposure exists on base, but this does not reproduce the exact head import signature. #249 must distinguish legitimate deferral, stalled command execution and earlier fixture contamination. |
| Five Study forms | Test treats `.ok()` (including202) as completed import, then expects a root. Confirmed pre-existing test-contract defect under accepted-operation responses. | Isolated both pass; ordered head fails and ordered base passes. Pending acknowledgements do not establish completed positions on either revision. Why the head cohort reaches retrying still requires the shared-state proof. |
| Study feedback recovery | Test submits import then immediately dereferences positions[0], without confirming the accepted operation. Confirmed pre-existing test-contract defect under202. | Same comparison result as five forms. No Study product repair or weaker assertion was added to PR243. |

Production/API/Redis/Study sources are identical between the historical base and
candidate: the PR changes diagnostics tests and planning. That rules out a direct
product-code change, but not altered sequence timing or test-family partitioning.
No failure is called flaky. The minimum next observation is a base-native pending
publication experiment with projection/task generation/state/lease snapshots around
seed, Redis fault, restoration and accepted Study execution; compare a bounded
settled seed. Do not repeat the complete matrix to obtain that observation.

## Focused hosted execution

Audit workflow lives outside PR243/main on a temporary branch, with pinned base and
head checkout refs, ubuntu-24.04-arm, Node22.23.3, Chromium, the existing disposable
PostgreSQL/Redis runner and serial browser execution. Assertions, product behavior,
server limits and cleanup remain unchanged. Probe overlays add stderr projection
observations and background GET receipt observations; their hashes and exact source
refs are in artifacts. They are diagnostic evidence, not required merge qualification.

Exact execution form: `node scripts/test-postgres-docker.mjs --mode browser
--browser-grep "$TEMPO_AUDIT_GREP"`, with `TEMPO_CI_REPORT` enabled. The committed audit
selection file supplies exact title suffixes. Workflow dispatch uses `pages.yml` on
the audit ref, `verification_only=true`, `diagnostic_retry=false`.

| Hosted run | Base c5f773b1 | Head d0064e8 | Browser / build / startup / cleanup seconds |
| --- | --- | --- | --- |
| [38422528534: four failed identities](https://github.com/aaweaver-actuary/tempo/actions/runs/38422528534) | 4/4 pass | 4/4 pass | Base51.40/103.77/16.96/2.46; head53.13/109.94/16.90/2.77 |
| [38423505954: common ordered shard](https://github.com/aaweaver-actuary/tempo/actions/runs/38423505954) | 39/41 pass; Issue135 and FEN queue-ready setup fail | 37/41 pass; original four failure signatures recur | Base304.89/104.55/17.78/2.52; head276.72/99.89/17.76/2.45 |

Four candidate-only PD240 cases are excluded from both common cohorts. All four common
PD82 diagnostics cases pass in both. The first isolated run establishes sequence/state
sensitivity, not independence. The second is the smallest original-cohort comparison
needed after the isolated controls did not discriminate. Total additional browser
execution is90 cases, not another pair of complete matrices. Failed/unequal runs are
not a performance speedup measurement. All four hosted runner teardowns passed.

## Architecture findings and narrow fixes

- Direct diagnostics sources select their complete family. Segmentation/evidence
  producers select diagnostics plus existing consumers. Unknown paths, shared
  admission/persistence/publication and runtime infrastructure remain conservative.
- **Reproduced omission:** a structurally valid inventory can drop a diagnostics
  owner mapping and still select FAST; dropping the evidence schema mapping can
  preserve STUDY. Required dependency anchors now reject missing/incorrect owner,
  producer or audited caller classification. The ledger remains manually reviewed,
  not an automatic certified reverse-dependency graph.
- **Reproduced callers:** PGN import command and canonical save UI previously select
  FAST despite changing imported/saved presentations. The traced deletion/limit
  callers and shared operation receipt helpers also feed fixture publication or
  evidence delivery; they now retain broad diagnostics qualification. Every new
  dependency file exists. No statistical/read-only comparison path was escalated.
- Journaling attaches before navigation, retains responses arriving during assertions,
  matches list/detail within one repertoire, and consumes buffered responses without
  demanding obsolete loading state. Each read has a nonrenewing15-second admission
  budget; success is rendered within five seconds measured from response arrival.
  Only exact foreground-wait503 retries automatically; database errors remain visible
  and require explicit user action. No synchronization or product change was needed
  beyond the original spec repair.
- Missing/skipped/failed/retried/stale evidence continues to fail aggregation and
  merge-gate. Merged #239 fixture certification cannot override diagnostics escalation
  in a mixed candidate. Isolated statistics and game comparison remain FAST.

Named regressions in the regular fast-lane bridge and tests/REGRESSIONS.md:
`PD-243 incomplete diagnostics owner or producer mappings cannot preserve FAST or STUDY`,
`PD-243 import and canonical publication callers require diagnostics while isolated readers remain FAST`,
and `PD-243 certified fixture companions cannot override diagnostics qualification`.
The direct owner/caller and producer-anchor regressions failed before their fixes.

Focused local evidence, macOS arm64 / Node26.10.0 / Python3.14.8:
`node --test tests/runner/ci-fast-lane.test.mjs`:40 cases passed (2.434s before the
final anchor refinement; regular bridges qualify final inputs).
`make unit-file FILE=tests/unit/ci-fast-lane-regressions.test.ts`:1 bridge passed,
3.29s after current-main integration. `make unit-file
FILE=tests/unit/ci-reliability-regressions.test.ts`:4 bridges passed,16.19s.
Typecheck passed. Lint passed with10 pre-existing warnings; standalone static durations
were not captured. Conflict/migration guards and diff checks pass. No local Docker,
full, visual or unrelated product suite was run. Final unchanged-source evidence and
CI links are recorded in the PR handoff.

## Qualification limitation and cleanup

#239 now explicitly requires test selection and qualification tier to be independent:
reviewed dependency closure selects tests, infrastructure executes that scope, and
fresh complete selected evidence qualifies it; only explicit shared/unknown/migration/
critical-durability exceptions broaden. Merged #244 supplies bounded Redis certificates,
not arbitrary selected diagnostics browser execution. Current infrastructure PRs still
select FULL. This PR does not implement that redesign.

No exception is justified while shared publication attribution and critical Study
readiness remain unresolved. A future candidate-bound exception would need proof of
independence, exact excluded failures, retained owning coverage and an explicitly
reviewed policy change; manual success labels or protection bypasses are unacceptable.

Original and audit artifacts, verified digests, source/probe hashes, commands/timings,
state witnesses and reports are preserved under root
`test-results/pr243-focused-review-20261013/`. Audit code/selection will be archived
on a durable remote tag before its branch is removed. No local Docker resources were
created; bounded inventory timed out and Docker was neither restarted nor pruned.
Remove the task checkout only after remote source/evidence preservation and process/
ignored-state checks. Final removal and any remaining gate belong in the handoff.
