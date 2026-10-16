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

`registered Stockfish container qualification acquires Docker admission while listing remains lightweight` owns explicit `requiresDocker` suite admission. The registered Stockfish suite retains its Node reporter and receives the shared managed Docker adapter and cleanup verification.

`standalone Learning suite retains its native owner without the legacy dependency graph` proves the registered `learning` suite is exactly the component’s native owner. This permits its broker-free installed runtime to execute independently of legacy backend assembly tests; the owning command and inputs may not drift.
