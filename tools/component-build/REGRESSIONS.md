# Component integration regression ownership

Regular owner command: `npm run test:components` (or the documented tool-local Node command).
The existing global packaging wrapper remains discoverable during transition.

`tests/interface-boundaries.test.mjs` is discovered by that same regular command.
Its focused source-only command is
`node --test --test-concurrency=1 tools/component-build/tests/interface-boundaries.test.mjs`.
It owns component-owned public/private import and export checks in Python and
TypeScript, conditional/deferred/namespace bypass controls, interface README and
purity checks, source ownership/escape rejection, dependency cycles, table-write
ownership, and intake eligibility/preparation/promotion checks through direct
calls, aliases, helper chains and method references. Disposable negative controls
prove an intentional violation exits nonzero and the corrected source passes.
The tests start no application, native database, worker or engine. Actual
acceptance/refresh-failure/producer-absent/replay/attempt behavior remains with
the runtime owner and must be registered in its required affected-component suite.

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

`registered Stockfish container qualification acquires Docker admission while listing remains lightweight` owns explicit `requiresDocker` suite admission. The registered Stockfish suite retains its Node reporter and receives the shared managed Docker adapter and cleanup verification.

`standalone Learning suite retains its native owner without the legacy dependency graph` proves the registered `learning` suite is exactly the component’s native owner. This permits its broker-free installed runtime to execute independently of legacy backend assembly tests; the owning command and inputs may not drift.

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
- PR301 review repairs add 20 regular cases (94 A202 cases total):
  `A202-removed-effect-trigger`, `A202-effect-trigger-replacement`,
  `A202-changed-interface-trigger`, and `A202-unchanged-effect-triggers` preserve
  changed/removed effect-specific base obligations, require head coverage and real
  native replacements, and avoid activating unchanged triggers for other metadata.
- `A202-inventory-only-edit`, `A202-deleted-inventory-only-test`,
  `A202-renamed-inventory-only-test`, `A202-shared-inventory`,
  `A202-inventory-exclusions`, and `A202-inventory-copy-source` select collection
  readers outside suite sensitivity, check missing head inputs, and preserve
  exclusions, deduplication and copy-source provenance without production cascades.
- `A202-implicit-production-metadata` (reassignment/removal),
  `A202-implicit-interface-metadata`, `A202-explicit-rule-metadata`, and
  `A202-documentation-metadata` share path/metadata role effects, including implicit
  artifact/interface impact, explicit security and interface-only boundaries.
- `A202-shared-owner-proof-evidence` (dependency and unrelated-source cases),
  `A202-shared-owner-proof-closure`, `A202-explicit-rule-proof-evidence`, and
  `A202-shared-owner-proof-dependency` retain associated owners' source/dependency
  closure, changed object IDs, multiple-owner determinism and unsupported-input
  checks without adding execution prerequisites or unrelated component evidence.
- Independent re-review adds 14 regular cases (108 A202 cases total).
  `A202-retained-native-suite-metadata` covers same-ID kind, resource, owner and
  combined downgrades. `A202-native-suite-metadata`, `A202-unit-suite-metadata`
  and `A202-suite-metadata-contract` retain base/head trigger obligations and
  grouped companion proofs while preserving valid command/runtime/scope edits
  and avoiding production cascades or unrelated native work. The shared pure
  fixture declares non-persistence effects; the native fixture explicitly adds
  persistence instead of advertising PostgreSQL coverage through a Node proof.
- `A202-shared-owner-declaration` covers external npm/Python dependencies and
  root/artifact metadata. `A202-shared-owner-declarations` covers normalized
  multiple owners, deterministic output and unrelated metadata exclusion.
  `A202-explicit-rule-owner-declaration` retains declarations associated through
  rules as well as owning triggers. Evidence keeps associated component
  declarations separate from execution prerequisites and dependency graph roots.
- Endpoint provenance adds 40 regular cases (148 A202 cases total).
  `A202-lifecycle-{owner,suite,effect}-{added,retained,modified,replaced,removed}`
  is a 30-case table over pure security and native persistence contracts. Base
  requirements survive into head; head-only additions never require historical
  owners, proofs or effects. Explicit suite replacements retain base proof IDs;
  removed owners, missing replacements and reclassified/removed base effects
  continue to block. Unchanged effect contracts do not activate native work for
  unrelated owner metadata.
- `A202-lifecycle-native-{owner,effect}-added` checks six head-only native
  kind/resource/owner failures. `A202-head-only-source-effect` checks security
  and persistence path additions without invented base obligations;
  `A202-lifecycle-added-owner` preserves unknown-source rejection for both.
  Source seeds, component/rule/boundary deltas and suite metadata contracts all
  use the same directional endpoint-retention rule.
- Component-state transitions add 29 regular cases (177 A202 cases total).
  `A202-component-state-{absent,planned,implemented}-to-{absent,planned,implemented}`
  covers the complete 3×3 state table, with artifact and persistence variants
  wherever an implemented endpoint is present (14 cases). Planned metadata alone
  does not originate artifact execution; promotions validate the implemented head,
  while actual implemented demotions/removals remain blocked.
- `A202-planned-promotion-{base-source,base-unknown,base-proof,head-unknown}`,
  `A202-planned-removal-base-source`, `A202-planned-native-promotion` and
  `A202-planned-head-source` supply 15 controls. They preserve genuine changed or
  deleted source inputs, explicit base proof identities, unknown-mapping rejection,
  native head kind/resource/owner requirements and planned-target rejection for
  actual selected inputs. All earlier assertions are retained unchanged.

This is an injected-snapshot algorithm increment, not production manifest
coverage, a Git/CLI adapter, evidence cache or runner integration. C3 retains the
shared manifest; #263/#201 own coordinated canonical metadata and discovery;
#205/domain owners supply equivalent narrow/native proofs; the study-priority
stack retains execution/accounting/resource seams. Those follow-ons require their
owners' handoff. Schema-v1 build/test inputs are not promoted to exclusive
production ownership by this core.
