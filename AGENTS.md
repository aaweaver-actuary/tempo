# Tempo working rules

## Protect existing work

- Do not develop on `main` or in the live study checkout. Use an isolated task branch based on current `main`; inspect existing development copies first and reuse one only when its owner has released it and it is clean and safe.
- Preserve user changes, task-owned data, and other work. Never use cleanup or test commands that can affect live study data or resources owned by another task. Follow the [task resource and cleanup policy](CONTRIBUTING.md#task-resources-and-cleanup).
- Install only the dependencies needed by the selected component and checks. Do not install the whole repository for a component-local task.
- Report what changed, the exact checks run and their outcomes, and any remaining failures or unverified claims. A focused pass is not a full-system pass; a PR is not merge-qualified unless the actual required checks establish that.

## Naming and scope

- Prefer expressive names. Small local names should identify what they contain or do; widely shared names may be shorter only when their meaning is stable. Never use terseness to hide ambiguity.
- Keep work bounded to its owning component and actual dependencies. Give a shared contract, transaction boundary, migration boundary, or shared build configuration one owner. Coordinate parallel work against agreed interfaces and fixtures.
- Preserve useful algorithms and regression invariants. If a mechanism or invariant moves or is replaced, document the owner and the equivalent proof.
- Every user-reported defect needs a named regression in its regular owning suite. Follow [regression ownership and transitional registration](docs/testing.md#regression-ownership-during-reconstruction). Never hide a failure by skipping, deleting, or weakening a selected check.
- Foreground study and authoring work takes priority over derived-data work. Background jobs must be durable, idempotent, bounded, and yield between short database sections.

## Authoritative guidance

- [Contribution and handoff workflow](CONTRIBUTING.md)
- [Test selection, commands, and current runner behavior](docs/testing.md)
- [Adopted component architecture and migration index](docs/architecture/component-reconstruction.md)
- [12-phase index and component manifest (#192)](https://github.com/aaweaver-actuary/tempo/issues/192)

Read `CONTRIBUTING.md` before behavior changes. The architecture guide distinguishes target boundaries from current implementation and migration work; do not infer that a planned package, service, database, release, or toolchain already exists.
