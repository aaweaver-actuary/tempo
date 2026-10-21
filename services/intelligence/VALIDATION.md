# Intelligence #109 validation receipt

The original qualification below is historical. The PR #289 repair receipt at the
end records the separately tested repair revision and its fresh evidence.

Starting main: `0c7ef737bb2daacd8f671f252ce5a1d0787b1871`.
Tested executable revision: `03ea13ce81900f1e225b5d0b7f2400bc051c233d`.
Branch: `codex/109-intelligence-sources`.
The follow-up commit adds this receipt only; its final identity is recorded in the
PR and preserved handoff report. Executable/package/test inputs remain unchanged.

## Results

All final selected cases passed, with no skipped cases or remaining failures.
One upstream Starlette/AnyIO deprecation warning remains; it is not suppressed.

| Proof | Actual execution |
|---|---|
| Registered Intelligence deterministic suite | 164 passed |
| Registered Intelligence native PostgreSQL suite | 60 passed |
| Declared chess-core owning dependency suite | 105 passed |
| Declared analysis-jobs contract owning suite | 92 passed |
| Source-free CPython 3.12.12 wheel consumer | 164 passed |
| Source-free CPython 3.12.12 sdist consumer | 164 passed |
| Source-free CPython 3.14.8 wheel consumer | 164 passed |
| Source-free CPython 3.14.8 sdist consumer | 164 passed |
| Installed wheel / Python 3.12 native runtime | 60 passed |
| Installed sdist / Python 3.14 native runtime | 60 passed |

These rows repeat owning cases across artifacts/runtimes; they are not distinct
new regressions. Reports retain actual JUnit identities and execution counts.
Component discovery, import/dependency boundaries, strict nonzero execution,
installed CLI/SQL resources, conflict checks and whitespace checks also passed.

The source-free proof built only Intelligence, chess-core and analysis-job
contracts, removed copied build sources, and installed wheel/sdist consumers with
unrelated service/model/broker distributions absent. Temporary consumers were
removed. It records 32 commands and 92 hashed source inputs.

The actual installed server runs in a separate interpreter with loopback-only
network guards. Its native regression starts without optional services, reports
missing registration, accepts an operator-scoped synthetic private registration,
fetches local provider counts through the background worker, gracefully stops,
and restarts with identical accepted evidence and no repeated provider call.
Native races additionally prove old leases/results cannot replace newer accepted
work, source failures remain independent, refresh rollback retains old evidence,
429 deadlines survive restart, and no pool connection/transaction spans HTTP.

## Exact final commands

Executed from the isolated checkout `/private/tmp/tempo-intelligence-109`:

```sh
UV_CACHE_DIR=/private/tmp/tempo-intelligence-109-uv-cache uv sync --project services/intelligence --locked --offline --no-build-isolation --no-editable --reinstall-package tempo-intelligence
TEMPO_COMPONENT_PYTHON=/private/tmp/tempo-intelligence-109/services/intelligence/.venv/bin/python UV_CACHE_DIR=/private/tmp/tempo-intelligence-109-uv-cache node tools/component-build/test-lane.mjs --component intelligence
UV_CACHE_DIR=/private/tmp/tempo-intelligence-109-uv-cache services/intelligence/.venv/bin/python services/intelligence/integration/qualify_artifacts.py /private/tmp/tempo-intelligence-109-evidence/artifacts-complete
node tools/component-build/check-components.mjs --component intelligence
node tools/component-build/test-lane.mjs --component intelligence --list
npm run check:conflicts
git diff --check
```

The registered lane's complete output is `component-lane-final.log`; discovery
and boundary inventories are `component-discovery.json` and
`component-boundaries.json`. `artifacts-complete/artifacts.json` contains exact
child commands, source/artifact hashes and consumer case identities. Native JUnit,
logs and verified resource-removal receipts accompany each installed native run.

An earlier development lane failed analysis-contract collection because
`jsonschema` was absent. Its pinned test dependency is now declared in the
Intelligence dev group/lock; the final registered 92-case contract run passes.
Earlier intermediate reports remain labelled with their original revisions and
are not relabelled as final evidence.

## Artifact and database inventory

- Wheel SHA-256: `7db5ea3622d5458c05e4f4d268115156574c6ae8b373c0072995fe396081a5ba`.
- Sdist SHA-256: `76a1bdc3416d40de277b1b66c64ef544bff80fd4a06e370f8ecd6afaac74e50e`.
- PostgreSQL fixture image: `postgres:18.6-trixie`, exact image ID
  `sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336`.
- Migration: Intelligence-owned `0001_sources`, packaged ordered manifest and
  startup checksum validation; no automatic startup migration.
- Runtime role: LOGIN, NOINHERIT, no administrative flags/memberships, no foreign
  database CONNECT, schema/table/temp creation or owner escalation. Tests prove
  migration/workspace authority and immutable source records cannot be rewritten.
- Immutable tables: snapshots, caller revisions, accepted evidence, command
  receipts and model-version bindings. Cache/job semantic inputs are protected
  from UPDATE; only named progress/result columns are mutable.

Fixture owners provision their own new cluster, databases, credentials and unique
volume. The fixture grants no Intelligence runtime access to Learning, Content,
Game Evidence, administrative or template databases. The installed native owners
`tempo-intelligence-109-65596a44aae566b1` and
`tempo-intelligence-109-a88e25e96ad78f2f` each report verified container and volume
removal. Other development/registered fixture cleanup receipts are preserved too.

## Preservation and limits

Evidence is preserved outside the disposable checkout at
`/Users/andy/tempo-evidence/intelligence-109`, including distributions, reports,
logs, source/ref preservation, exact final revision and cleanup audit.
Task-owned test resources and the eligible development checkout are removed
after preservation; final cleanup results are in the handoff report.

No live-provider smoke or credential registration, live-data migration,
deployment, merge, Actions/branch-protection change, or whole-product test pass is
claimed. Hosted merge qualification is separate from these local component proofs.

Credential IDs are a trusted immutable-operator contract without stored token
fingerprints. Maia wire v1 relies on an immutable configured model-version/artifact
binding; it cannot independently attest worker artifact bytes, ordered rating
pairs or speed conditioning. Single-FEN inputs cannot prove repetition history.
These supported-subset limits and #108/#110/#156/#4 handoffs are specified in
[INTERFACES.md](INTERFACES.md); no shared wire schema was changed.


## PR #289 repair qualification — October 16, 2026

Starting PR head: `d17c5f687b55c454088deecdf3a6b02dc2184f15`.
Current main included: `0c7ef737bb2daacd8f671f252ce5a1d0787b1871`.
Tested executable: `576d5500e0372ae35da4880d05429b49e3e778cc`.
The following commit changes only this validation receipt. The final published
SHA, remote status, source bundle and verified checkout cleanup are recorded in
`handoff.json` and `cleanup.json` in the repair evidence directory below.

### Repairs and independent review

Cancellation cannot become an ordinary retryable error when lease cleanup fails.
Claim, publication and failure persistence run on an owned serial executor with a
five-second client I/O deadline; the owner drains operation outcomes before pool
closure. Repeated cancellation, eventual claim return and committed publication
are handled explicitly. The complete application teardown is also owned/shielded,
including cancellation during startup, Store closure and executor join.

Request retry processes only locally eligible sources and durably accepts partial
or no-op commands. Inspection and retry share eligibility, including provider
origin restrictions. Command replay, provider cooldowns, generations and retained
evidence remain protected. Maia new-generation budgets are 1024 MiB / 5000 ms;
retries preserve complete historical request contents except execution generation.
No schema, shared wire contract, dependency lock or migration changed.

An independent review found one additional cancellation gap during Store closure.
Its named failing regression was added and the gap repaired. The final independent
review reports no outstanding actionable defects; it independently executed all
31 deterministic lifecycle cases and additional cancellation/closure probes.
`independent-review.json` separates those executions from inspected native evidence.

### Fresh final results

| Proof | Actual result at the tested executable |
|---|---|
| Registered Intelligence deterministic suite | 196 passed |
| Registered Intelligence native PostgreSQL suite | 99 passed |
| Declared chess-core dependency | 105 passed |
| Declared analysis-job contracts | 92 passed |
| Source-free Python 3.12 wheel | 196 passed |
| Source-free Python 3.12 sdist | 196 passed |
| Source-free Python 3.14 wheel | 196 passed |
| Source-free Python 3.14 sdist | 196 passed |
| Installed Python 3.12 wheel native/server suite | 99 passed |
| Installed Python 3.14 sdist native/server suite | 99 passed |
| Component discovery and dependency/public boundaries | Passed |
| Zero-selected owning pytest invocation | Rejected with expected exit 5 |
| Conflict artifacts and whitespace | Passed |

No required cases were skipped, failed, errored or xfailed. The upstream
Starlette/AnyIO BlockingPortal deprecation remains unsuppressed. Final Python
runtime versions, individual warnings, command arguments and case identities are
preserved in the artifact reports/logs. Native server cases include cleanup
failure, real background SQL delay, responsive health, graceful shutdown and
restart. The 32 added deterministic and 39 added native cases run in their regular
owning suites; repeats across consumers are not additional regression identities.

Negative evidence is preserved separately: the original worker/API/CLI produced
17 failures/8 passes in the controlled lifecycle regression selection, and the
original installed Store failed all four mixed-source retry cases at the original
`retry_not_due`/`retry_not_permitted` behavior. The first retry reproduction had a
test-status payload error; it was corrected and rerun to establish the actual
source failures. One development native run had 98 passes/one stale test comparison;
its corrected final runs pass 99. None of those intermediate receipts qualifies
the final executable.

### Maia producer/consumer proof

Current published #290: `d0b5ee725ad3e884fd6eb7b8c5c9042ac679b24b`.
Installed worker artifact SHA-256:
`6c51d0b989d5eddfef72aff6a181285a852888d504d7bf830e8978c772170d2b`.
A fresh, exact Intelligence-generated job completed in 270.66 ms after readiness,
with 20 predictions and probability sum 1.0. Its isolated whole-worker cgroup had
one CPU, 1073741824 bytes memory, zero swap, no network, read-only root, zero OOM
events and exit 0. Cgroup peak was 500711424 bytes. Five runtime/envelope assertions,
shared parsing, exact constructor reconstruction and result validation passed.
The regular native test then accepted and replayed that exact result against the
exact persisted job. The fixture preserves the exchange without installing or
running a model in the Intelligence suite.

The 532 MiB process-RSS benchmark does not measure the same resource as admission's
cgroup hard cap. The published worker requires at least 1 GiB; 512 and 768 MiB job
allowances are unsupported. This proves compatibility for the qualified 1 GiB
profile. Existing legacy 768 MiB Compose entries provision another worker, and no
independent production deployment profile was exercised or changed. Production
capacity remains unverified rather than silently passing.

### Reproduction commands and preserved artifacts

Executed from `/private/tmp/tempo-intelligence-109-repair` with a task-owned clone
of the pre-existing cache:

```sh
UV_CACHE_DIR=/private/tmp/tempo-intelligence-109-repair-uv-cache uv sync --project services/intelligence --locked --offline --no-build-isolation --no-editable --reinstall-package tempo-intelligence
TEMPO_COMPONENT_PYTHON=$PWD/services/intelligence/.venv/bin/python UV_CACHE_DIR=/private/tmp/tempo-intelligence-109-repair-uv-cache node tools/component-build/test-lane.mjs --component intelligence
UV_CACHE_DIR=/private/tmp/tempo-intelligence-109-repair-uv-cache services/intelligence/.venv/bin/python services/intelligence/integration/qualify_artifacts.py /private/tmp/tempo-intelligence-109-repair-evidence/artifacts-final
TEMPO_COMPONENT_PYTHON=$PWD/services/intelligence/.venv/bin/python node tools/component-build/check-components.mjs --component intelligence
node tools/component-build/test-lane.mjs --component intelligence --list
npm run check:conflicts
git diff --check
```

The only Node setup was the component runner's pinned TypeScript dependency:
`npm ci --prefix tools/component-build --workspaces=false --offline`.
`command-manifest.json` records additional baseline, negative, native and lifecycle
checks; the artifact manifest retains all 32 child commands and 96 hashed inputs.

Repair evidence is preserved at:
`/Users/andy/tempo-evidence/intelligence-109-repair/20261016-requalification`.

- Wheel SHA-256: `8e96be4beeaa41b870be573cf40a296660a4dc202fa30775141ba1aedccaaf97`.
- Sdist SHA-256: `ff20c585a14411b0cfcd6cb353021bf67d09aeee718c7f4d9a8b66e5a78d0e32`.
- `artifacts-final/artifacts.json`: exact inputs, artifact hashes, installed guards,
  commands and consumer identities. The manifests describe their build-time inputs;
  the later receipt-only commit does not relabel them as newly executed tests.
- Four consumer JUnit files and both installed-native JUnit/log/resource receipts
  are under `artifacts-final/`; registered native evidence is
  `native-registered-final/`; dependency identities are in `registered-cases.json`.
- `maia-native-exchange-faa7n7ps/`: actual current producer artifact, exchange,
  envelope, command and cleanup proof. The earlier Maia trial remains separately
  labeled with its historical artifact.
- `acceptance-matrix.json`, `independent-review.json`, `source-inputs.json`,
  `source.bundle`, `source.tar.gz`, `checksums.json`, `handoff.json` and
  `cleanup.json`: complete acceptance mapping and preservation/publication audit.

All seven invocation-owned PostgreSQL containers and their seven volumes report
verified removal, including negative/development runs. Both Maia proof containers
and their unique image tags were removed, with no Maia volumes. Final disposable
checkout/cache/consumer removal is separately verified in `cleanup.json` after
publication and preservation; it is not inferred from this receipt.

### Readiness and limits

Local affected-component and producer/consumer qualification is complete. GitHub
Actions remains disabled, and current main has no returned branch protection or
rules; no hosted or release pass is claimed. Current remote head, mergeability and
non-draft status are checked after publication and retained in the handoff.

No known repair-scope defect or mandatory local failure remains. Deliberate v1
limits remain unchanged: trusted operator credential identity, configured Maia
artifact binding without independent result attestation, one rating/no speed pair,
and no repetition history from one FEN. No live-provider check, full-product gate,
live migration, independent production provisioning, deployment or merge occurred.

## Database-free findings extraction — October 21, 2026

This receipt covers the uncommitted extraction in the isolated database worktree,
not a rerun of the historical qualification above. The existing 49-line finding
merge/ranking tail of `AnalysisStore._derive_evidence` moved to
`src/tempo_intelligence/analysis/findings.py`. Its statements are AST-identical
except that `self.workspace` is the explicit `workspace` argument. Every other
`AnalysisStore` method and every adapter SQL statement literal is unchanged.
Source classification still precedes the existing background evidence read,
which precedes merging. No transaction, lock, publication order, schema or public
wire contract changed. [Ownership and composition](src/tempo_intelligence/analysis/README.md#finding-assembly)
and the [remaining mixed adapter inventory](../../database/tempo_intelligence_database/adapters/README.md)
are explicit; this is not a claim of complete separation.

The selected source-root pure/fake tests passed **62 cases**. After tightening one
new evidence-identity assertion, all **four new cases** passed again. These cover
source advice merge/deduplication, draft proposal enrichment/ranking, unknown
repertoire association, incompatible engine profile and the unchanged composition
order; the existing classification/ranking/engine tests retain their assertions.
The existing Starlette/AnyIO deprecation warning remains unsuppressed.

```sh
PYTHONPATH=services/intelligence/src:database:contracts/analysis-jobs/src:packages/chess-core/src /tmp/tempo-db-review-venv/bin/python -m pytest services/intelligence/tests/test_findings.py services/intelligence/tests/test_analysis.py services/intelligence/tests/test_repairs.py -q --rootdir=. -o pythonpath= --import-mode=importlib
PYTHONPATH=services/intelligence/src:database:contracts/analysis-jobs/src:packages/chess-core/src /tmp/tempo-db-review-venv/bin/python -m pytest services/intelligence/tests/test_findings.py -q --rootdir=. -o pythonpath= --import-mode=importlib
/tmp/tempo-db-review-venv/bin/python -m build services/intelligence --no-isolation --outdir /tmp/tempo-intelligence-findings-build
```

The shared review environment was read only; explicit source roots supply only
the declared local dependencies. The source distribution rebuilt its wheel, and
both include the new helper and centralized adapter. Their wheel bytes match
current source and contain no bytecode/cache files. Artifact SHA-256 values:

- Wheel: `298068de27ac326047678023bc158004ded7729fe0757968dc096272bbb2f76d`.
- Source distribution: `a5e15ddad8025c9c97b3e18b9fa6c2f583e8fb0e7e4237fa9330a8077bbe4960`.

Artifacts remain under `/tmp/tempo-intelligence-findings-build` for integration;
the pre-extraction adapter snapshot remains at
`/tmp/tempo-intelligence-findings-before.py` for the statement comparison. These
artifacts predate this receipt-only edit. Owner documentation links resolve.
Mermaid rendering was not checked because this checkout lacks the parser package;
no new documentation toolchain was installed. No installed-consumer runtime,
native PostgreSQL, server, worker, provider, live data, full-system suite,
deployment, security operation or CI change ran for this extraction.
