# Contributing to Tempo

## Ownership and coordination

Start with [AGENTS.md](AGENTS.md), [SPEC.md](SPEC.md), and the assigned acceptance slice. Historical architecture programmes are references, not automatic prerequisites.

Use an isolated task branch. Agree ownership for shared contracts, migrations, transaction boundaries, and build configuration. Prefer independent PRs; use a small explicit stack only for a real unmerged dependency. Name its base and the owner who will integrate it. Do not continually rebase or add newer dependencies merely because main advanced; actual conflict and qualification requirements still apply.

Component workers own bounded delivery. The designated feature owner resolves dependencies and proves the assembled journey. A component worker does not inherit unrelated integration or installation work.

## Changes and review

Review the assigned behavior, changed contracts, affected consumers, and applicable safety invariants. For every proposed blocker, identify the violated acceptance requirement, concrete correctness/safety defect, or actual required gate. A concrete defect discovered during review remains actionable even if the original plan missed it.

Use one coherent initial review. After repairs, review the changed portions and affected dependencies; do not restart unrelated audits or request additional reviewers without a new finding, material change, or actual review requirement. Do not impose an arbitrary review limit that prevents correcting real defects.

Formatting preferences, alternate designs, speculative risks, and unrelated cleanup are not new release gates. Accessibility, broken user behavior, security, and enforced checks are not dismissed as formatting. Separate functional defects, harness/environment problems, and gate failures. Do not call an uncertain failure pre-existing without evidence.

Use [docs/testing.md](docs/testing.md) for selection, regressions, and evidence reuse. An intentional requirement change can change an obsolete expectation; record the requirement and replacement proof. Do not weaken still-applicable assertions to obtain green results. A legitimate fixture repair must preserve what the test is meant to prove.

### Architecture impact and drawings

Update affected canonical diagrams only for a materially changed boundary under [the drawing procedure](docs/architecture/diagrams/README.md#maintenance-and-review-procedure). The PR states the changed boundary and links its view, or `Architecture impact: none` with a brief reason. Instruction-only changes do not themselves change runtime architecture. Do not duplicate the procedure here or require unrelated drawings and rendering audits.

## Task resources and cleanup

The implementing agent owns its disposable resources and eligible checkout. Before deletion, preserve unique committed, uncommitted, untracked, ignored, stashed, or unpushed work as appropriate, and verify that no active agent, process, or mounted container still needs the target. Preserve necessary evidence outside a checkout being removed, with revision and reproduction commands.

Use git worktree removal for registered worktrees. Never force-remove uncertain/dirty work, delete another owner's branch, globally prune Docker, remove live volumes, or discard reusable shared caches. An open PR alone does not justify retaining a redundant checkout; uncertain ownership or unique state does.

Inspect only resources used by the task or implicated by the cleanup target. Documentation work does not require Docker inspection, daemon restart, or a host-wide inventory. Safely finish independent cleanup and report any precise retention condition.

## Handoff

Maintain one current summary in the existing PR/task:

- Implemented: result and revision.
- Demonstrated: assigned behavior/checks, environment, and tested revision.
- Merge-qualified: actual applicable required-check status, or not established.
- Installed: exact installed result, or not installed.
- Remaining: blocker/limitation and next owner, including cleanup where relevant.

Link concise commands/results and retained evidence; do not duplicate full logs or require another report file. Update stale readiness statements rather than adding competing summaries. Safe limitations may remain in the existing task; create separate issues only for useful independently tracked work.

Component evidence is not assembled acceptance; synthetic acceptance is not genuine-data or installation evidence. A documentation-only result has no runtime demonstration. A draft PR is not merge-qualified merely because local checks pass.

Open or update the assigned PR within existing permissions. Do not merge, deploy, or operate live services without authorization. Once local acceptance and scoped cleanup are complete, publish the handoff; a remaining gate does not assign more feature polishing.
