# Agent D foundation integration — October 15, 2026

Refs #263 and #201; coordination #251; contract definitions #264.
Starting main: `6b99085f8978346a959b5d4a6ea44b6e56f05dd6`.

| Boundary | Before this increment | Delivered registration | Production adoption |
| --- | --- | --- | --- |
| Five contract distributions | Merged #271, manifest planned | Implemented catalog-backed 1.0.0 / wire v1 artifacts | Not asserted |
| Chess-line and scheduling | Implemented #270 artifacts | Existing interfaces plus scoped validation/build/test selection | Existing legacy adapters only |
| Eight app/service/worker roots | Planned extraction targets | Remain planned on this baseline | Not asserted |
| Backend interpreter metadata | >=3.14 / selector 3.14 versus image 3.12 | >=3.12,<3.15; selector/baseline 3.12; normally regenerated lock | Image and running installation unchanged |

The contract catalog and each package's metadata remain authoritative. The root
adapter invokes the existing contract AST checker, checks public module paths and
artifact inclusion, and verifies installed namespaces/resources. It does not copy
wire validation or reference semantics. Analysis jobs retains its public worker
modules. Shared compatibility and native ownership suites have one registration
each and explicit working directories; native ownership remains opt-in.

Selectors are repeatable `--component <id>`; `--list` is collection only.
Existing default checker and legacy lane commands remain usable; the restricted
builder retains its two-package default. Current-source consumer checks remain
separate from explicitly selected package independence. See the executable
commands and registration examples in [component tooling](../../tools/component-build/README.md).

The backend lock retains all eighteen package identities/versions, including
scheduling 0.1.0 and pinned boundary tools. Python 3.12 parses the 191 existing
backend modules, but package/seam qualification is not full-backend compatibility.
C owns subsequent scheduling/chess-core releases and their exact registration;
B owns Learning application integration; A owns the supported browser lane.

## Acceptance and remaining owners

- #263: consumes the five merged artifacts and extends existing restricted contexts
  and negative import/export proofs. Complete artifact/build boundaries for future
  apps, services, workers, CLI and integration remain their extraction owners.
- #201: reconciles backend metadata and minimal setup, provides scoped collection
  and execution with local inventory registration, and preserves legacy discovery.
  Per-component environments for unimplemented roots remain later extraction work.
- #264: the contract-definition deliverable is satisfied by merged #271's scoped
  packages, semantics, producer/consumer matrix and native authorization fixture
  evidence. Production adoption is a separate implementation responsibility under
  #224/#193/#265/#41/#109/#43/#110 and related reconstruction work. This integration
  does not claim a fresh semantic audit or a fresh PostgreSQL result.

No migrations, production image changes, deployment, live-resource manipulation,
Actions activation or merge are part of this increment. Existing #271 native
ownership evidence is historical; reuse requires verifying the fixture/test and
requirements inputs against its preserved source binding.

## Source-bound validation

The PR handoff records the exact tested source, commands, actual case identities,
counts, wall times, artifact hashes, unrun checks and retained risks. Raw evidence
is preserved outside the disposable checkout under
`test-results/d-foundation-integration-20261015` in the primary repository.
