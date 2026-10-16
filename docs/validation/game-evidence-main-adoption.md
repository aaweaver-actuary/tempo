# Game Evidence main adoption: F1 prerequisite

## Result and boundary

PR #283 was merged into `codex/learning-remediation` at
`3af50215198c8d6889ca9aeda0dd75e402ebcaef`, after #281 had already merged into
main. It therefore did not put the producer on main. Main `11db198a` still
declared Game Evidence planned and contained only its placeholder README.

This prerequisite merges that existing history into current main without
rewriting it. The automatic component-manifest merge retains independent
Learning, browser-intents and study-session registrations. Game Evidence source,
fixtures, migrations, tests, metadata and lockfile are byte-identical to #283's
merged implementation. Chess-core, both consumed contracts and remediation inputs
also remain identical to that implementation. No producer algorithm is replaced.

The new regular component-tooling regression is
`F1 main adoption discovers Game Evidence alongside independent Learning and Study owners`.
It validates the actual two service artifacts and their dependency closure, checks
that both Study package registrations remain implemented, and lists the regular
producer/consumer suites without invoking Python or starting services. It also
checks shared dependency suites are listed exactly once. With main's old manifest
it fails specifically because Game Evidence is planned; with the merged manifest
the complete owning tooling suite passes. The old manifest is restored only
temporarily inside this task's isolated checkout for that reproduction.

## Source-bound validation

Clean tested merge: `239065578fe56cc39715860f64f3582c9e0a0baf`.
The subsequent handoff commit adds only this document, outside the selected
packages' and tooling tests' executable inputs. Raw evidence is retained at
`/Users/andy/tempo/test-results/f1-producer-main-20261015`.

Commands below run from the isolated checkout root. `P` is the absolute path to
its installed `services/game-evidence/.venv/bin/python`; `E` is the evidence
directory above. Component setup used only Game Evidence's locked dependencies,
the pinned contract-test requirements, the component checker's TypeScript parser,
and its chess-line fixture dependencies. No whole-repository install was run.

| Exact check | Result |
| --- | --- |
| `TEMPO_COMPONENT_PYTHON="$P" UV_CACHE_DIR=/private/tmp/tempo-f1-producer-uv-cache node tools/component-build/test-lane.mjs --component game-evidence` | 33 producer unit, 51 native PostgreSQL, 105 chess-core, 99 Content contract and 99 Game Evidence contract cases passed; owning lane exit 0 |
| `UV_CACHE_DIR=/private/tmp/tempo-f1-producer-uv-cache "$P" services/game-evidence/integration/qualify_artifacts.py "$E/artifacts"` | Clean wheel and sdist consumers on Python 3.12.12 and 3.14.8; 33 cases in each of four installs; installed imports, packaged migrations and CLI checks passed |
| `TEMPO_COMPONENT_PYTHON="$P" node tools/component-build/test-node.mjs tools/component-build/tests` | 77 passed, zero failures/skips/TODOs, including the new discovery regression |
| `TEMPO_COMPONENT_PYTHON="$P" node tools/component-build/check-components.mjs --component game-evidence --component learning` | Both actual artifacts and their five shared/transitive component dependencies pass |
| `"$P" -m pytest -c contracts/game-evidence/pyproject.toml contracts/game-evidence/tests --import-mode=importlib -q --junitxml="$E/evidence-contract-complete.xml"` | 99 passed |
| `"$P" -m pytest -c contracts/content-publication/pyproject.toml contracts/content-publication/tests --import-mode=importlib -q --junitxml="$E/content-contract-complete.xml"` | 99 passed |
| `npm run check:conflicts` and `git diff --check` | Passed |
| `git diff 3af50215198c8d6889ca9aeda0dd75e402ebcaef HEAD -- services/game-evidence packages/chess-core contracts/content-publication contracts/game-evidence services/learning/remediation` | Empty: adopted package/dependency inputs unchanged |

The registered native runner created a fresh PostgreSQL 18.6 container and volume,
with separate evidence/remediation fixture databases and restricted roles. Its
report confirms container and volume removal. It never accepts a caller database
target. Its actual-remediation tests remain a synthetic component boundary,
not adoption into the independent Learning HTTP runtime. A pre-existing Starlette
deprecation warning remains. The chess-core lane selected its own CPython 3.13.5
environment; this is recorded separately from the four 3.12/3.14 artifact proofs.

Initial sandboxed dependency installation failed on package-host DNS and was
retried with network access. Early contract collection lacked jsonschema; the
pinned contract-test requirements were then installed and both entire suites
passed. An initial tool invocation supplied a file where its wrapper expects a
directory. The first draft of the new regression incorrectly expected selection
to exclude dependencies; its assertion was corrected to the actual dependency
closure before the complete suite passed. These failed setup/development attempts
remain in the evidence directory and are not passing validation results.

## Next integration and F2

This is prerequisite adoption, not F1 runtime activation. The independent Learning
runtime still uses its v1 default intake/settlement; its separately installed
remediation module is not activated. Legacy Games/Study application paths are
unchanged. Nothing here establishes production activation, hosted/release
qualification, Learning independence through the full Study application, live
provider access or an application data migration.

After this prerequisite merges, F1 branches from refreshed main and owns v2 HTTP
delivery authentication, Learning-owned accepted decision facts, packaged migration
registration, durable remediation maintenance, single-writer admission, issuance
frontier binding, transactionally required settlement and real process/browser
independence proofs. Preserve historical receipts; historical v1 obligation
conversion remains explicit #224 cutover work. #194/#224 and architecture issues
remain open.

F2 then switches the full Study application to the agreed independent Learning
path. It retains existing immutable IndexedDB intents and receipt-first recovery;
only issued heads or receipt-authorized continuations install a board. Its
validation must cover authentication/origins, saved-next-load failure, true reload,
two tabs and provider/broker absence without mocked authoritative persistence.

No merge authorization, deployment, live migration, history modification,
frozen-rank recomputation or Actions/policy change is part of this prerequisite.
