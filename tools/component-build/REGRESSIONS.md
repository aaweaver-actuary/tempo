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

`tests/boundaries.test.mjs` retains the #270 AST/dependency, public API, inventory,
package prerequisite and structured Node reporter protections unchanged.
