# Component integration regression ownership

Regular owner command: `npm run test:components` (or the documented tool-local Node command).
The existing global packaging wrapper remains discoverable during transition.

`tests/integration.test.mjs` owns named D regressions for implemented contract
source, artifact names/versions, public namespaces, schema exports, undeclared lazy
imports, selector rejection, suite cwd, planned executable claims, source-independent
TypeScript validation, listing without setup, isolated contract execution, shared
suite deduplication and zero-case execution. The fixtures run real subprocesses
and preserve their exit status and structured case evidence.

The #200 pure-package registration regression validates both actual implemented
packages and lists their owning suites with Python disabled, preserving explicit
working directories and discovery without setup.

`F1 main adoption discovers Game Evidence alongside independent Learning and Study owners`
checks both actual service artifacts and lists their regular unit/PostgreSQL suites
without starting Python or services. It also preserves the merged browser-intents
and study-session registrations. This catches the missing-main producer regression
after #283 merged into its former dependency branch instead of main.

`tests/boundaries.test.mjs` retains the #270 AST/dependency, public API, inventory,
package prerequisite and structured Node reporter protections unchanged.

`M02 manifest rejects duplicate missing escaping and false implemented components`
selects a genuine planned component by identity for its false-implemented fixture;
the manifest's first entry may already be implemented. All five rejection cases
remain mandatory as implemented component registrations advance.

The same file owns `M02 unrelated lanes execute without controlled browser modules`,
`M02 controlled browser inventory distinguishes absent lanes from broken ownership`,
and `M02 controlled browser dispatch awaits and catches asynchronous failures`.
These preserve frontend/integration build ordering and runner independence without
browser dependencies, reject missing/empty/duplicate/stale registrations, and prove
lazy browser dispatch completes or exits nonzero on import/execution failure.

## Affected-input selector core (#202)

`tests/affected.test.mjs` owns the `A202-*` regressions. It is discovered recursively
by `npm run test:components` (the existing `test-node.mjs` aggregate) and the existing component-packaging compatibility
wrapper. No additional legacy file registration is needed.

- `A202-production-chain`, `A202-interface-boundary`, `A202-persistence-native`,
  `A202-ui-rendering`, `A202-worker-private`, and `A202-worker-protocol` preserve
  explicit owner/consumer proofs and resource requirements. Persistence metadata
  cannot substitute a pure/mock suite for the owning PostgreSQL boundary.
- `A202-test-only`, `A202-shared-fixture`, `A202-sensitivity-is-not-ownership`,
  `A202-consumed-markdown`, `A202-local-lockfile`, `A202-root-lockfile`,
  `A202-shared-toolchain`, and `A202-manifest-only` distinguish production effects
  from test/build sensitivity; unknown ownership never becomes an empty pass.
- Rename/move/copy/root-rename, deleted/renamed required test, removed component,
  ownership, dependency and proof cases retain base obligations. A removed proof
  needs one explicitly declared replacement; base prerequisites remain reasons,
  rather than automatically becoming head execution targets.
- Diamond/cycle, Python alias, external dependency, shuffled input and duplicate
  match cases preserve deterministic identities, deduplicated selection and
  bounded causal graphs. `A202-linear-work` checks indexed entry/edge/reason counts
  on growing synthetic chains, rather than asserting a machine-dependent speed.
- Path/status/projection validation, overlapping/empty-root ownership, missing
  internal target, planned and false-implemented fixtures, unavailable base,
  missing cwd/resources and unsupported symlink/gitlink cases produce actionable
  blocked diagnostics. The named false-implemented fixture does not assume that
  the first registered component is planned; the existing boundary regression
  remains unchanged.
- Legacy identity, scoped evidence material and browser-subset cases retain exact
  command/cwd/input/scope requirements. Passing evidence is not evaluated here;
  observed results remain absent, and existing structured runners still own
  failed/missing/cancelled/zero-case rejection.
- `A202-no-environment` preserves a Node-builtins-only module with no filesystem,
  subprocess, product, service or environment initialization.

This is an injected-snapshot algorithm increment, not production manifest
coverage, a Git/CLI adapter, evidence cache or runner integration. C3 retains the
shared manifest; #263/#201 own coordinated canonical metadata and discovery;
#205/domain owners supply equivalent narrow/native proofs; the study-priority
stack retains execution/accounting/resource seams. Those follow-ons require their
owners' handoff. Schema-v1 build/test inputs are not promoted to exclusive
production ownership by this core.
