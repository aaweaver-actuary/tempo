# Retained component contracts

The current catalog contains the Game Evidence wire contracts used by the retained
backend remediation bridge. These are data contracts; importing them creates no provider, database,
application registry or network service.

The independent Learning API, Learning transfer, Content publication and Analysis Jobs packages,
with their exclusive examples and fixtures, are retired. Current Study attempts,
reviews, queue and authored exercise contracts stay in the serving backend; the
review application and remediation libraries retain their existing interfaces.

## Public artifacts and catalog

| Root / distribution | Public import | Scope |
| --- | --- | --- |
| `game-evidence` / `tempo-game-evidence-contracts` | `tempo_game_evidence_contracts.v3` | Observed game evidence, exact source authority and accepted remediation obligations; v1/v2 exports remain supported. |

The [catalog](catalog.json) names every public model, schema artifact and owning
suite. Pydantic 2.12.5 models are authoritative; schemas use draft 2020-12 and are
included in package wheels. `tempo.invalid` IDs identify schemas, not hosted
endpoints. The packages contain no shared ORM or serving lifecycle.

## Evidence and worker boundaries

Game imports preserve provider/game identity, source digest, revision and
observation provenance. Encounters and comparisons bind to observed game facts
and explicit repertoire authority; they are not controlled Study reviews.
Evidence acknowledgements distinguish accepted-pending work from committed
completion. Stable obligation identities prevent retries from inventing new
logical work. Existing v1/v2/v3 wire shapes and receiver semantics are unchanged.

## Verification and compatibility

Each supported version rejects malformed identities, unknown fields and invalid
wire versions. Pydantic cross-field checks and independently maintained TypeScript
semantic checks cover coherence beyond structural JSON Schema validation. The
shared negative fixtures exercise those retained contracts only.

Use `python contracts/run_tests.py` for package and shared suites, adding
`--typescript` for the producer/consumer exchange. `export_schemas.py --check`
checks committed schema parity. `qualify_artifacts.py` separately verifies built
artifacts in isolated environments; local source tests do not imply installation,
native persistence or deployed producer conformance.

See [compatibility](COMPATIBILITY.md), [ownership](OWNERSHIP.md),
[regressions](REGRESSIONS.md) and the dated [validation history](VALIDATION.md).
Historical receipts describe their original source and package set; they are not
fresh validation of this source reduction.
