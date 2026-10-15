# Game Evidence first restoration (#41)

This increment installs `tempo-game-evidence==0.1.0` independently, registers its
real build and owning suites, and supplies PGN/account import, source revision
history, legal position indexes, repertoire findings, a read API/CLI, offline
source-bound report fixtures, durable jobs and atomic delivery intent. It never
imports backend/Learning implementations or accesses another domain's database.

The branch is a genuine dependency stack on **PR #281**, B's evidence-v2 contract
at `f614e122cb1dcc7d73a3109779f08447b9f5916e`. This PR changes no shared contracts.
The producer explicitly consumes contract artifact 2.0.0 / wire v2, Content
1.0.0 / wire v1 and chess-core 0.1.0. Learning/scheduling remain their own owners.
The original main baseline is `f43b5f7fb4d7ddafeb3124a95f4fa3d64556abc4`.

## Exact final development evidence

Tested implementation revision: `8d6845e4de5adf132f2333b5c0b55c02b7b5481b`.
The handoff documentation added afterward does not alter the package, dependency,
fixture, runner, manifest or toolchain inputs. The adjacent JSON records source
hashes and exact artifact identities; local raw evidence is retained outside the
checkout under `test-results/game-evidence-41/`.

Executed from the isolated checkout (Python 3.14.8, Node 26.10.0):

- `TEMPO_COMPONENT_PYTHON=/Users/andy/tempo/.dev-copies/game-evidence-41/services/game-evidence/.venv/bin/python node tools/component-build/test-lane.mjs --component game-evidence`: **27 unit + 23 native PostgreSQL cases passed**, zero failures/skips/TODOs. This is the actual tracked component selection, including JUnit case identities. Unit suite 0.60s; native tests 12.00s; native setup/execution/verified teardown 13.80s.
- `contracts/.venv/bin/python -m pytest -c contracts/pytest.ini contracts/game-evidence/tests contracts/content-publication/tests -q --junitxml=/Users/andy/tempo/test-results/game-evidence-41/final-contracts.xml`: **187 passed**, 0.92s. A separate contract-only environment installed the declared schema-test dependencies and the two affected artifacts. Contract source remained B's pinned input.
- `UV_CACHE_DIR=/Users/andy/tempo/test-results/game-evidence-41/uv-cache services/game-evidence/.venv/bin/python services/game-evidence/integration/qualify_artifacts.py /Users/andy/tempo/test-results/game-evidence-41/qualified-artifacts`: **27 cases passed in each of four clean installs**: wheel and source distribution on Python 3.12 and 3.14. CLI startup and installed migration resources passed; unrelated implementation source was absent.
- `node tools/component-build/check-components.mjs --component game-evidence`: passed manifest, dependency, public export and source boundary checks.
- `npm run check:conflicts` and `git diff --check f614e122`: passed.

Native proofs include PGN-to-findings/CLI output, comparison/outbox rollback,
commit-before-send, response loss and fresh-process replay, pending/rejected/stale
acknowledgements, explicit corrections including withdrawal-only empty results,
partial-snapshot non-withdrawal, source reversion, bounded cursor/expired-lease
resumption, source/snapshot changes during computation, actual database restart,
restricted runtime credentials, workspace-filtered read APIs, source checkpoints,
Chess.com month reuse, cancellation and durable Retry-After.

The strict Learning HTTP receiver is a producer-isolation fixture. Passing these
tests does not establish actual C-to-B runtime adoption or authoritative study
effects. F retains that proof. Python 3.14 emitted existing upstream chess,
Starlette/FastAPI deprecation warnings; no assertion was weakened or skipped.

Earlier development failures are retained and superseded only in their affected
scope: the transposition regression first failed on an irrelevant en-passant
marker; a native restart run failed after its random Docker port changed; and an
initial combined test command failed collection without the contract suite's
configuration/schema dependencies. Separate position/comparison keys, a fixed
random loopback mapping and the dedicated contract invocation corrected those
causes. Final source passed the scopes above. Interrupted artifact execution is
not counted as a pass; all four final installs completed freshly.

## Support and remaining issue scope

Working paths are completed standard/from-position PGNs with an explicit player,
bounded public Lichess exports and Chess.com monthly archives, explicit local
Content snapshot files and v2 delivery to a configured receiver. Provider tests
use synthetic records; no live account sync or authentication flow was exercised.
The read service defaults to loopback and requires an authenticated gateway before
exposure to untrusted clients.

The local snapshot adapter is explicit fixture/file input, not a claim that Content
has been extracted. Acceptance receipts are supplied and trusted; no cross-domain
query authenticates them. Missing acceptance/route metadata stays unknown.
Incomplete scope or ambiguous unchanged observations cannot withdraw prior facts.
Native Content publication/route integration, actual Learning endpoint adoption,
and legacy Games UI integration remain their owners' work. Source deletion and
exclusion commands, Intelligence/global denominator recomputation, and the rest
of #40 activity/history policy remain outside this bounded increment.

No browser behavior changed, so browser/full-product/model suites were not run.
No hosted required-check qualification, merge, deployment, live migration,
study setting change, Actions activation or live-analysis restoration is claimed.

## Preservation and cleanup

Every native invocation used a fresh labelled PostgreSQL container and durable
labelled volume; its runner verified teardown. The final pinned base image is
recorded in the adjacent JSON and remains a shared cache. Native logs/XML,
artifact distributions/source hashes and local commands are preserved outside
the development checkout. Disposable fixture credentials are redacted in reports.
The interrupted temporary checkout was recovered from its preserved source
distribution into a persistent task checkout and checkpointed on the task branch.
Final checkout cleanup and remote ref verification are recorded in the handoff.
