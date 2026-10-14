# Contributing to Tempo

## Ownership and coordination

Start with the adopted [component architecture](docs/architecture/component-reconstruction.md) and the [12-phase index and donor map in #192](https://github.com/aaweaver-actuary/tempo/issues/192). Refresh the relevant issue and current source before implementation. Historical audits are evidence about their dated baselines, not current execution policy.

- Keep each change bounded to one component and its real dependencies. Preserve existing user work and use an isolated branch based on current `main`; do not develop in the live study checkout.
- Assign one owner to each shared contract, transaction boundary, migration boundary, and shared build configuration. Other contributors may implement in parallel against the agreed interface and fixtures.
- Use independent PRs when changes can be reviewed, validated, and merged independently. Use a small explicit stack only when a consumer genuinely depends on a producer's unmerged contract; identify that dependency and validate the resulting integration.
- Work from actual consumer/provider dependencies, not numbered phase order. Unrelated later work may proceed before an earlier phase is complete. Keep global-budget, learning-weight, probability-driven curriculum, and recurring-pattern features outside core reconstruction acceptance.
- Do not install all repository dependencies for a local component task. Select the owning package's setup and tests from [docs/testing.md](docs/testing.md).

## Changes and review

- Preserve public behavior unless the task explicitly changes it. Keep regression tests with their owning component; record every user-reported defect test in `tests/REGRESSIONS.md`.
- Explain why a legacy invariant or implementation detail is retired or moved, identify its new owner, and show the replacement proof. Protect user data, durable study state, and truthful error reporting.
- Keep PRs focused. Describe the user-visible or architectural result, affected contracts and consumers, exact validation performed, and checks not run with reasons. Separate unrelated failures and name their evidence and owner; do not silently broaden the task to repair unrelated systems.
- A draft state, issue status, or unrelated advancement of `main` alone does not establish that tests must be repeated. Reuse prior evidence only when the relevant source, dependencies, fixtures, commands, and toolchain inputs are unchanged. Failed, missing, cancelled, or zero-selected required checks never pass.
- Distinguish implementation completion from hosted merge qualification. Documentation does not change workflows, required checks, or branch protection. Never claim a merge, deployment, or runtime activation that did not occur.

## Handoff

Report the branch/PR, changed ownership boundaries, validation commands and results, remaining implementation or tooling limits, and any exact external blocker. Open one focused PR for one task. Do not merge unless the user explicitly requests it.
