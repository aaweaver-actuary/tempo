# Contributing to Tempo

## Ownership and coordination

Start with the adopted [component architecture](docs/architecture/component-reconstruction.md) and the [12-phase index and donor map in #192](https://github.com/aaweaver-actuary/tempo/issues/192). Refresh the relevant issue and current source before implementation. Historical audits are evidence about their dated baselines, not current execution policy.

- Keep each change bounded to one component and its real dependencies. Preserve existing user work and use an isolated branch based on current `main`; do not develop in the live study checkout.
- Assign one owner to each shared contract, transaction boundary, migration boundary, and shared build configuration. Other contributors may implement in parallel against the agreed interface and fixtures.
- Use independent PRs when changes can be reviewed, validated, and merged independently. Use a small explicit stack only when a consumer genuinely depends on a producer's unmerged contract; identify that dependency and validate the resulting integration.
- Work from actual consumer/provider dependencies, not numbered phase order. Unrelated later work may proceed before an earlier phase is complete. Keep global-budget, learning-weight, probability-driven curriculum, and recurring-pattern features outside core reconstruction acceptance.
- Do not install all repository dependencies for a local component task. Select the owning package's setup and tests from [docs/testing.md](docs/testing.md).

## Changes and review

- Apply the project-wide [single-user MVP delivery policy](AGENTS.md#deliver-a-usable-single-user-mvp): prioritize common workflows, preserve essential safeguards, and track non-blocking edge cases with safe fallback, scope, user impact and follow-up acceptance criteria in linked issues. Their existence alone does not block delivery.
- Preserve public behavior unless the task explicitly changes it. Add named regressions for user-reported defects fixed by the change, keep them in their regular owning component suite, and follow [regression ownership and transitional registration](docs/testing.md#regression-ownership-during-reconstruction). Deferred issues are not claims of fixes or regression coverage.
- Explain why a legacy invariant or implementation detail is retired or moved, identify its new owner, and show the replacement proof. Protect user data, durable study state, and truthful error reporting.
- Keep PRs focused. Describe the user-visible or architectural result, affected contracts and consumers, exact validation performed, and checks not run with reasons. Separate unrelated failures and name their evidence and owner; do not silently broaden the task to repair unrelated systems.
- A draft state, issue status, or unrelated advancement of `main` alone does not establish that tests must be repeated. Reuse prior evidence only when the relevant source, dependencies, fixtures, commands, and toolchain inputs are unchanged. Failed, missing, cancelled, or zero-selected required checks never pass.
- Distinguish implementation completion from hosted merge qualification. Documentation does not change workflows, required checks, or branch protection. Never claim a merge, deployment, or runtime activation that did not occur.

### Architecture impact and drawings

Architecture-changing PR acceptance includes updating the affected source-truth
[architecture drawings](docs/architecture/diagrams/README.md), with source links,
ownership and failure boundaries. Cover the changed abstraction levels and key
flows; do not regenerate unaffected views. Use the index's review procedure to
check interfaces, dependency direction, data/transaction/effect ownership,
asynchronous receipt semantics, shutdown and resource limits. Keep proposed work
and unverified deployment claims separate from implemented source.

Place canonical Mermaid blocks directly in the relevant owning directory's
`README.md`, including component and detailed sequence views, so GitHub directory
navigation renders the drawings. The central diagram README owns the system
overview and links to those sections. Give a cross-component view one canonical
README home and link from peers. When relocating a drawing, move its block and
source notes, update links, and retain useful old anchors as pointers; do not copy
the same drawing into multiple files. See the index's canonical placement table.

Include an `Architecture impact:` statement in the PR: link the updated views and
summarize the changed boundary, or write `Architecture impact: none` with a
specific reason. A documentation correction can update a drawing without changing
runtime architecture. Review real coupling, including named legacy exceptions;
do not certify isolation from hidden controls or diagrams alone. In particular,
Study must not call, import or wait for Games or share its lifecycle. Optional
influence belongs at the Learning/queue owner's semantic interface, with no
cross-component writes. Shared pure libraries are permitted.

For documentation-only changes, follow the documentation row in
[test selection](docs/testing.md#select-the-smallest-relevant-proof), validate
links/source correspondence and Mermaid syntax using existing lightweight tooling
when available, and inspect the GitHub rendering. Record unavailable tooling or
rendering checks honestly; do not install a new toolchain or run full-system tests
merely to edit drawings. Existing required checks, permissions and the single-user
MVP policy still apply.

## Task resources and cleanup

- The implementing agent owns cleanup of its disposable resources and eligible development checkout. An open or unmerged PR, pending review, or inconclusive audit is not by itself a reason to retain a redundant checkout.
- Before removing a checkout, verify that required commits and unique local work are preserved on durable refs or in an appropriate verified artifact; inspect uncommitted, untracked, ignored, stashed, and unpushed work. Preserve necessary logs, traces, reports, and reproduction instructions outside the checkout, with the tested revision and commands recorded.
- Confirm that no running agent, process, or mounted container still needs the checkout or resource. Uncertain ownership or unpreserved unique state requires retention with an explicit reason. Use `git worktree remove` for registered worktrees after these checks; do not force-remove dirty worktrees or delete branches as part of checkout cleanup.
- Tear down only verified task-owned disposable resources. Preserve live study data, other tasks' resources, shared base images, and safe reusable caches. Never globally prune Docker resources or erase a volume whose ownership and disposable status are uncertain.
- Keep inspection proportional to the task. Inspect Docker when the task used it or when a specific cleanup target requires checking container references; a documentation-only task does not need a Docker inventory as a prerequisite. An unavailable daemon is not permission to restart it or touch live services, and it need not block independently verified filesystem cleanup.
- Report removed and retained resources, preserved evidence and refs, and any remaining cleanup blocker or retention condition. Perform eligible cleanup after preservation; do not leave it as an unspecified task for the user.

## Handoff

Report the branch/PR, changed ownership boundaries, validation commands and results, remaining implementation or tooling limits, and any exact external blocker. Include removed or retained task resources, preserved evidence and refs, and remaining cleanup conditions. Open one focused PR for one task, or update its existing PR. Do not merge unless the user explicitly requests it.
