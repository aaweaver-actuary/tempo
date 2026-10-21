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

## Source-request policy and serialization extraction — October 21, 2026

This separate bounded slice moves `public` value conversion to
`src/tempo_intelligence/serialization.py` and descriptor preparation, cache
identity and retry eligibility to `src/tempo_intelligence/source_requests.py`.
Analysis responses and the retained native fixture import the application
serializer directly. The Store retains all SQL, transaction/lock ownership,
clock call points and its private retry entry point as a delegation.

Source comparison verifies the serializer, descriptor loop, cache identity and
retry predicate against their original AST. Inlining the new calls reproduces
the complete original `submit` statement order, including descriptor preparation,
binding initialization, sorting, cache processing and binding append. Every other
Store method and every SQL statement literal is unchanged. Provider-origin reads
remain inside the same loop and short-circuit conditions. The original adapter
snapshot is `/tmp/tempo-intelligence-source-requests-before.py`.

**23 distinct selected source-root cases passed**: 17 new pure policy/serializer
cases, five existing request/identity cases and the existing fake-store bounded
HTTP refresh/retry case. The first command passed 22 cases; the separately added
lazy-provider-lookup case then passed on its own. Those counts are distinct, not
repeated runs. The existing Starlette/AnyIO deprecation remains unsuppressed.

```sh
PYTHONPATH=services/intelligence/src:database:contracts/analysis-jobs/src:packages/chess-core/src /tmp/tempo-db-review-venv/bin/python -m pytest services/intelligence/tests/test_source_requests.py services/intelligence/tests/test_models.py::test_source_cache_inputs_ignore_policy_and_repertoire_identity services/intelligence/tests/test_models.py::test_cohort_sets_have_one_canonical_parameter_order services/intelligence/tests/test_models.py::test_distinct_provider_inputs_do_not_share_evidence_identity services/intelligence/tests/test_models.py::test_one_request_has_at_most_one_configuration_per_source_family services/intelligence/tests/test_models.py::test_request_rejects_unknown_fields_and_duplicate_sources services/intelligence/tests/test_api.py::test_caller_reads_and_bounded_refresh_retry_have_explicit_revision_commands -q --rootdir=. -o pythonpath= --import-mode=importlib
PYTHONPATH=services/intelligence/src:database:contracts/analysis-jobs/src:packages/chess-core/src /tmp/tempo-db-review-venv/bin/python -m pytest services/intelligence/tests/test_source_requests.py::test_maia_and_ineligible_cache_paths_do_not_read_provider_configuration -q --rootdir=. -o pythonpath= --import-mode=importlib
/tmp/tempo-db-review-venv/bin/python -m build services/intelligence --no-isolation --outdir /tmp/tempo-intelligence-source-requests-build
```

The first command predates the additional short-circuit case; rerunning it now
selects all 23 cases. Logs are `/tmp/tempo-intelligence-source-requests-tests.log`
and `/tmp/tempo-intelligence-source-requests-lazy-provider.log`. The shared review
environment remains read only. The source distribution rebuilt its wheel; both
include the new helpers and updated owner adapters, with source-matching wheel
bytes and no cache files. Artifacts remain in the build directory above for
integration. The new owner diagram parses using the existing
`/tmp/tempo-db-extraction-diagrams/node_modules` jsdom/Mermaid setup; local owner
links resolve. This is syntax/source evidence, not a browser-rendering claim.

The [remaining mixed-method inventory](../../database/tempo_intelligence_database/adapters/README.md)
still includes request/revision/snapshot admission, source freshness/response
assembly, credential/provider transitions, Maia generation/admission, and Analysis
workflow/report/execution policy. No complete-separation claim, installed-consumer
runtime qualification, native fixture execution, live operation, security change,
full-system suite, CI change, deployment or merge is established by this slice.

## Source admission, inspection and failure blocks — October 21, 2026

This subsequent slice moves five existing blocks from Store into the existing
`source_requests.py`: workspace/payload admission, expected-head revision
validation, full/immutable snapshot preparation and identity, base captured-source
inspection, and failure attempt/state/deadline/retry decisions. The existing
clock and retry callbacks preserve conditional clock invocation and freshness /
retry evaluation order. No new repository, dispatch layer, query port, public
contract or schema is introduced.

All **37 selected source-root cases passed**, including 19 new admission,
snapshot, freshness and failure-decision cases, the previous 17 policy/serializer
cases and one existing fake-store HTTP refresh/retry case. The upstream
Starlette/AnyIO deprecation remains unsuppressed. The complete Store class AST is
restored by inlining these five moved blocks; their bodies match the original
statements except explicit workspace/retry/clock parameters. All earlier policy
helpers and every SQL statement literal remain unchanged. This verifies query,
transaction, lock, callback and binding order without running a database.

```sh
PYTHONPATH=services/intelligence/src:database:contracts/analysis-jobs/src:packages/chess-core/src /tmp/tempo-db-review-venv/bin/python -m pytest services/intelligence/tests/test_source_requests.py services/intelligence/tests/test_api.py::test_caller_reads_and_bounded_refresh_retry_have_explicit_revision_commands -q --rootdir=. -o pythonpath= --import-mode=importlib
/tmp/tempo-db-review-venv/bin/python -m build services/intelligence --no-isolation --outdir /tmp/tempo-intelligence-admission-inspection-build
```

The source distribution rebuilt its wheel, with current helper/adapter bytes and
the updated regular tests included. The owner Mermaid block parses with the
existing jsdom/Mermaid setup, and owner file links resolve. Test output is retained
at `/tmp/tempo-intelligence-admission-inspection-tests.log`; original Store and
policy snapshots are `/tmp/tempo-intelligence-admission-inspection-before.py` and
`/tmp/tempo-intelligence-admission-policy-before.py`. Artifacts remain in the build
directory for integration; the shared review environment remains read only.

Request/snapshot replay checks, expired-cache refresh, Maia profile/job decisions,
provider-specific inspection/final request envelopes, command orchestration and
the documented Analysis workflows still require responsibility extraction. They
remain explicit unfinished work. This focused evidence is not installed-consumer,
native, live, security, full-system, CI, deployment or merge qualification.

## Complete request-inspection workflow — October 21, 2026

`Store.inspect` is now composition for the complete existing application
`source_requests.inspect_request` workflow. A concrete root
`RequestInspection` projection retains the three fixed cache/evidence/provider
SELECTs and delegates the existing request-head and credential-row reads. Store's
new small context wrapper lends its existing `read_only=True` transaction; the
projection opens/commits nothing. Application code receives fixed semantic reads,
retry/status/clock capabilities and facts, with no query/execute port.

**39 selected source-root cases passed**, reusing the previous 37 cases and
adding only Maia-only and mixed-provider inspection cases. Those two cases were
then strengthened to use the actual projection/context wrapper with a fake driver
and both passed again; they are repeated identities, not 41 distinct cases. They
check read-only context ownership, cache/evidence parameter binding, read/clock/
retry order, unavailable retained provider origin, serialization before snapshot
exit and final credential refresh afterward. The final status callback retains
its existing independent read/I/O lifecycle. No real private material or database
is used. The upstream Starlette/AnyIO deprecation remains unsuppressed.

One-time source comparison matches the complete application statement sequence
to the original method after fixed-read/name mapping; all other existing Store
methods and earlier policy helpers remain AST-identical. The complete SQL
inventory is unchanged, including the three exact moved SELECT strings. Tests
exercise the actual bound IDs and context wiring without parsing or pinning SQL.

```sh
PYTHONPATH=services/intelligence/src:database:contracts/analysis-jobs/src:packages/chess-core/src /tmp/tempo-db-review-venv/bin/python -m pytest services/intelligence/tests/test_source_requests.py services/intelligence/tests/test_api.py::test_caller_reads_and_bounded_refresh_retry_have_explicit_revision_commands -q --rootdir=. -o pythonpath= --import-mode=importlib
PYTHONPATH=services/intelligence/src:database:contracts/analysis-jobs/src:packages/chess-core/src /tmp/tempo-db-review-venv/bin/python -m pytest services/intelligence/tests/test_source_requests.py::test_complete_inspection_keeps_one_snapshot_clock_order_and_post_read_credential_refresh -q --rootdir=. -o pythonpath= --import-mode=importlib
/tmp/tempo-db-review-venv/bin/python -m build services/intelligence --no-isolation --outdir /tmp/tempo-intelligence-inspect-workflow-build
```

The source distribution rebuilt its wheel. Current workflow, Store and projection
bytes and the strengthened regular tests are included; no cache files ship. The
updated owner diagram parses with existing jsdom/Mermaid, and owner file links
resolve. Logs are `/tmp/tempo-intelligence-inspect-{workflow,projection}-tests.log`;
original Store/policy snapshots are
`/tmp/tempo-intelligence-inspect-workflow-before.py` and
`/tmp/tempo-intelligence-inspect-policy-before.py`. Artifacts remain in the build
directory for integration; the shared review environment remains read only.

Remaining mixed root policy is [explicitly inventoried](../../database/tempo_intelligence_database/adapters/README.md):
request/snapshot replay and cache refresh, model/Maia generation and admission,
command orchestration, credential/provider decisions, the evidence envelope and
the previously listed Analysis workflows. Inspection is no longer an unfinished
mixed block. Database startup/binding/lease/replay atomic guards and fixed writes
retain their authority. No installed-consumer, native/live, security, full-system,
CI, deployment or merge qualification is claimed.

## Complete refresh/retry command workflow — October 21, 2026

The complete existing `Store._command` application workflow now lives in
`source_requests.command_request`. The root `RequestCommands` adapter retains
six fixed SQL operations, the command advisory lock, receipt-conflict and
request-revision guards, and delegates existing head/generation/Maia work
operations on the same caller-owned connection. Store supplies its unchanged
write transaction. The eligibility callback contains the original expression
`self._retry_eligible(cache,utcnow())`, preserving lazy consultation, method
lookup before clock observation and the original retry-only call point. No
generic SQL port, new transaction or framework is introduced.

**44 selected source-root cases passed**, reusing the previous 39 cases and
adding five actual command-composition/fixed-operation cases with a fake driver.
After tightening callback wiring for exact attribute/clock evaluation, all five
new cases passed again; they are repeated identities, not 49 distinct cases.
Refresh/retry retain sorted locks, terminal/ineligible skips, source-local effects,
existing generation/Maia work calls and receipt-last ordering. Replay, receipt
conflict and stale revision finish before cache work with the same write-context
outcome. Existing source-policy and HTTP assertions are unchanged. The upstream
Starlette/AnyIO deprecation remains unsuppressed.

One-time source comparison matches the complete application sequence after
fixed-operation mapping, separately verifies both root guard ASTs, and verifies
the eligibility callback body exactly matches the original expression. Every
other existing Store method and earlier application helper remains AST-identical;
the complete query inventory retains the six exact moved SQL strings. The
[complete 32-method Store audit](../../database/tempo_intelligence_database/adapters/README.md#complete-store-responsibility-inventory)
accounts for database authority, composition and every remaining mixed block.

```sh
PYTHONPATH=services/intelligence/src:database:contracts/analysis-jobs/src:packages/chess-core/src /tmp/tempo-db-review-venv/bin/python -m pytest services/intelligence/tests/test_request_commands.py services/intelligence/tests/test_source_requests.py services/intelligence/tests/test_api.py::test_caller_reads_and_bounded_refresh_retry_have_explicit_revision_commands -q --rootdir=. -o pythonpath= --import-mode=importlib
PYTHONPATH=services/intelligence/src:database:contracts/analysis-jobs/src:packages/chess-core/src /tmp/tempo-db-review-venv/bin/python -m pytest services/intelligence/tests/test_request_commands.py -q --rootdir=. -o pythonpath= --import-mode=importlib
/tmp/tempo-db-review-venv/bin/python -m build services/intelligence --no-isolation --outdir /tmp/tempo-intelligence-command-build
```

The fresh source distribution rebuilds its wheel and includes current Store,
application workflow, fixed operations and owning tests. Owner Mermaid syntax,
links, Python syntax and scoped whitespace/conflict checks pass. The final source
and artifact hashes, exact file list and method audit are retained in
`/tmp/tempo-intelligence-command-evidence.json`; test logs are
`/tmp/tempo-intelligence-command-tests.log` and
`/tmp/tempo-intelligence-command-final-tests.log`. Original Store/policy snapshots
remain `/tmp/tempo-intelligence-command-before.py` and
`/tmp/tempo-intelligence-command-policy-before.py`; distributions remain in the
build directory for integration. No shared review environment was modified.

Remaining source replay/cache refresh, profile/Maia construction/admission/status,
credential/provider workflows, evidence response and Analysis policy remain
unfinished extraction work, as explicitly inventoried. Fixed database/atomic
guards retain their existing authority. Native durability/concurrency, installed
consumer runtime, live data, credentials/security, full-system behavior, CI,
deployment and merge were not exercised or qualified by this slice.

## Maia/profile/evidence workflows — October 21, 2026

Six complete existing Store application blocks moved: `_profile`, `_maia_job`,
`evidence`, `maia_jobs`, `accept_maia` and `maia_status`. `maia_work.py` owns profile
configuration, immutable work creation, job-list/result/status workflow and
semantic validation; `source_requests.read_evidence` owns the evidence envelope.
The root `MaiaOperations` retains complete bound queries, profile binding and
unknown/stale/replay/transition guards and the same caller-owned connection.
Store supplies its unchanged read/write transaction contexts. There is no new
query port, execution/provider dependency or transaction authority.

**35 selected current-source cases passed**: 11 new actual-composition/fixed-row
boundary cases, 23 existing Maia contract/semantic cases and the existing HTTP
worker-payload case. The same 11 boundary cases also passed against the saved
pre-move Store; this is comparison evidence, not 46 distinct current-source
cases. An initial bookkeeping assertion in the new test's event filtering was
corrected before the final pass; no production behavior correction was needed.
The existing upstream Starlette/AnyIO deprecation remains unsuppressed.

The new boundary cases preserve configured-profile evaluation before the fixed
read, immutable previous-job retry without current workspace/default lookup,
binding iteration order and evidence serialization inside the read snapshot.
Result wire serialization precedes transaction entry; status serialization
follows the job lock. Writer lookup precedes application payload construction;
evidence appends before job completion. Result/status replay skips cache writes,
and failed status updates the cache after the job. The retained native historical
budget test changes only its moved builder import/patch location and was not run.

```sh
PYTHONPATH=services/intelligence/src:database:contracts/analysis-jobs/src:packages/chess-core/src /tmp/tempo-db-review-venv/bin/python -m pytest services/intelligence/tests/test_maia_work.py services/intelligence/tests/test_maia_validation.py services/intelligence/tests/test_api.py::test_valid_versioned_requests_and_worker_payloads_reach_their_own_ports -q --rootdir=. -o pythonpath= --import-mode=importlib
PYTHONPATH=services/intelligence/src:database:contracts/analysis-jobs/src:packages/chess-core/src /tmp/tempo-db-review-venv/bin/python -m pytest /tmp/tempo_intelligence_maia_original_boundary_test.py -q --rootdir=. -o pythonpath= --import-mode=importlib
python3 /tmp/tempo-intelligence-maia-source-proof.py
/tmp/tempo-db-review-venv/bin/python -m build services/intelligence --no-isolation --outdir /tmp/tempo-intelligence-maia-build
python3 /tmp/tempo-intelligence-maia-artifact-proof.py
```

The one-time source proof expands fixed operations at each original call point:
all six complete application sequences, guards and parameter order match; all
26 other existing Store methods and 10 earlier source-request helpers remain
AST-identical. All 13 original query call points (11 distinct SQL strings) remain
in the same sequence, including the shared fixed job lookup used separately by
result and status. The transaction options and lazy configuration/workspace
lookups match. The bound append capability resolves the original writer before
payload construction, also checked by the original/current event proof.

The fresh source distribution rebuilt its wheel. Five current implementation/doc
files match wheel bytes; nine current implementation/test/doc files match source
distribution bytes. Neither archive includes Python caches. An isolated import
smoke loads all four moved/wired modules from that wheel, using source paths only
for the declared pure contract/chess dependencies; it opens no pool/connection.
Two owner Mermaid blocks parse using existing jsdom/Mermaid, and scoped source
syntax, whitespace/conflict and local Markdown links pass. GitHub rendering was
not inspected. This is artifact discovery/import evidence, not installed-consumer
or native runtime qualification.

Original scoped files remain under `/tmp/tempo-intelligence-maia-before/`.
Source proof, artifact proof, original/current case logs and build logs are in
`/tmp/tempo-intelligence-maia-*`; the exact final source/artifact hashes are in
`/tmp/tempo-intelligence-maia-evidence.json`. Distributions remain under the build
directory for integration. The shared review toolchain was not modified.

The [full 34-method Store inventory](../../database/tempo_intelligence_database/adapters/README.md#complete-store-responsibility-inventory)
now identifies seven remaining mixed methods: `submit`, `_credential_status`,
`credential_status`, `credential_reload`, `credential_revoke`, `claim` and
`publish`. The separate AnalysisStore source audit remains outstanding. These are
unfinished extraction work. No native/live database, actual engine/provider,
private credential, security/grant, full-system, CI, deployment or merge action
was exercised or qualified by this slice.

## Complete request admission and provider-source workflows — October 21, 2026

`Store.submit`, `claim` and `publish` now compose complete existing application
workflows in `source_requests.submit_request` and `provider_work.py`. Fixed
`RequestAdmission` and `ProviderOperations` retain parameter-bound SQL,
request/snapshot replay guards, leases/fences and ordered writes on the same
caller-owned connections. Store lends the unchanged write and `background=True`
contexts. This introduces three module paths; fresh package discovery is checked
for those paths, without installing or changing the shared review environment.

**52 current-source cases passed**: 11 new actual-composition/fixed-row cases,
38 existing source-policy cases, one existing HTTP worker-payload case and two
bounded synthetic worker cases. The same 11 new boundary cases also passed
against the saved original Store (comparison evidence, not 63 distinct current
cases). No selected case failed. The upstream Starlette/AnyIO deprecation remains
unsuppressed. All provider/credential-status facts and UUIDs used here are
synthetic; no private file, actual provider/network or native database is accessed.

```sh
PYTHONPATH=services/intelligence/src:database:contracts/analysis-jobs/src:packages/chess-core/src /tmp/tempo-db-review-venv/bin/python -m pytest services/intelligence/tests/test_source_admission.py services/intelligence/tests/test_source_requests.py services/intelligence/tests/test_api.py::test_valid_versioned_requests_and_worker_payloads_reach_their_own_ports services/intelligence/tests/test_worker.py::test_worker_without_claim_performs_no_provider_or_failure_operation services/intelligence/tests/test_worker.py::test_successful_slice_offloads_database_calls_and_keeps_http_on_event_loop -q --rootdir=. -o pythonpath= --import-mode=importlib
PYTHONPATH=services/intelligence/src:database:contracts/analysis-jobs/src:packages/chess-core/src /tmp/tempo-db-review-venv/bin/python -m pytest /tmp/tempo_intelligence_admission_provider_original_test.py -q --rootdir=. -o pythonpath= --import-mode=importlib
python3 /tmp/tempo-intelligence-admission-provider-source-proof.py
/tmp/tempo-db-review-venv/bin/python -m build services/intelligence --no-isolation --outdir /tmp/tempo-intelligence-admission-provider-build
python3 /tmp/tempo-intelligence-admission-provider-artifact-proof.py
```

One-time fixed-operation expansion matches all three complete original method
sequences, including all 18 moved query strings/call points, guards and parameter
order. The other 31 Store methods and all 11 prior source-request helpers remain
AST-identical. The original transaction options and lazy workspace/configuration/
inspection/clock/UUID expressions match. Original/current event proofs retain
sorted cache locks and post-commit inspection; provider lock → clock → cache →
status → lease order and early exits; and publication fence → writer lookup →
UUID → clock → append → registration-result update → release order.

The fresh source distribution rebuilds its wheel. Six current implementation/doc
files match wheel bytes and nine current implementation/test/doc files match
source-distribution bytes; neither archive includes Python caches. An isolated
import smoke loads all five moved/wired modules from the fresh wheel, using source
paths only for declared pure contract/chess dependencies, with no initialization
or connection. Three owner Mermaid blocks parse with the existing tooling;
scoped Python syntax, whitespace/conflict and local Markdown links pass. GitHub
rendering was not inspected; installed-consumer and native runtime qualification
are not established by these checks.

Original files remain under `/tmp/tempo-intelligence-admission-provider-before/`;
logs, one-time proof scripts and distributions remain in the matching
`/tmp/tempo-intelligence-admission-provider-*` paths. Exact final file/artifact
hashes and the 36-method classification are in
`/tmp/tempo-intelligence-admission-provider-evidence.json`. These task-owned
artifacts are retained for integration; the shared toolchain remains unchanged.

The remaining mixed Store methods are `_credential_status`, `credential_status`,
`credential_reload` and `credential_revoke`; their source-only separation is a
later batch. `fail` retains its existing fenced, ordered fixed-effect application
around the already extracted failure decision. The separate AnalysisStore audit
remains outstanding. No native/live database, actual engine/provider, private
credential, security/grant, full-system, CI, deployment or merge action was
performed or qualified here.

## Credential workflow source move and full Analysis audit — October 21, 2026

The four existing credential workflows now live in `credential_work.py` with fixed
`CredentialOperations` and Store-owned read/write contexts. The root retains
complete SQL, generation/retired-registration guards and canonical writes.
Configuration, grants, secrets, private loader/provider behavior and public
method/error identity are unchanged. This was a source-only relocation; all
executed inputs were fake rows and synthetic metadata/material. The real private
loader was substituted on every selected loader path and never executed.

**18 selected current-source cases passed**: 11 new credential composition cases,
two existing complete-inspection cases and five existing provider-claim fake
cases. The same 11 new cases passed against the saved original Store as comparison
evidence, not 29 distinct current-source cases. Missing registration skips expiry;
clock evaluation still precedes terminal-status filtering. The status read closes
before the active-only late import/configuration/substituted-load branch.
Generation conflict stops before registration lookup; replay skips incoming
expiry and returns under the same lock. New registration retains registration →
head → waiting-cache resume → status order, and revoke retains head → optional
write → status inside one transaction. No selected case failed.

```sh
PYTHONPATH=services/intelligence/src:database:contracts/analysis-jobs/src:packages/chess-core/src /tmp/tempo-db-review-venv/bin/python -m pytest services/intelligence/tests/test_credential_work.py services/intelligence/tests/test_source_requests.py::test_complete_inspection_keeps_one_snapshot_clock_order_and_post_read_credential_refresh services/intelligence/tests/test_source_admission.py::test_provider_claim_retains_control_clock_cache_credential_and_lease_order -q --rootdir=. -o pythonpath= --import-mode=importlib
PYTHONPATH=services/intelligence/src:database:contracts/analysis-jobs/src:packages/chess-core/src /tmp/tempo-db-review-venv/bin/python -m pytest /tmp/tempo_intelligence_credential_original_test.py -q --rootdir=. -o pythonpath= --import-mode=importlib
python3 /tmp/tempo-intelligence-credential-source-proof.py
/tmp/tempo-db-review-venv/bin/python -m build services/intelligence --no-isolation --outdir /tmp/tempo-intelligence-credential-build
python3 /tmp/tempo-intelligence-credential-artifact-proof.py
python3 /tmp/tempo-intelligence-analysis-source-audit.py
```

Fixed-operation expansion matches all four complete original method sequences,
eight query call points/seven distinct strings, guards, late import, returns and
lookup/clock order. The other 32 Store methods remain AST-identical. The shared
fixed head lookup serves both original reload/revoke call points without changing
either context. No generic query port or new transaction/registration authority
was introduced.

The two new module paths justify fresh source-distribution → wheel discovery.
Four current implementation/doc files match wheel bytes and seven current
implementation/test/doc files match source-distribution bytes; no cache files
ship. An isolated smoke imports all three moved/wired modules from that wheel
with declared pure source dependencies, confirms the late private-loader module
is not imported and initializes no pool/connection. Four owner Mermaid blocks
parse; scoped Python syntax, whitespace/conflict and local Markdown links pass.
GitHub rendering was not inspected. Actual private-file/security and native
concurrency/durability proofs remain unchanged and were not run.

The [complete source responsibility audit](../../database/tempo_intelligence_database/adapters/README.md#complete-analysisstore-source-audit)
now accounts for every one of the 33 AnalysisStore methods, with 96 direct query
call points. That file was source-read only and remains unchanged. The conservative
inventory identifies three fixed owner/read operations, three existing composition
wrappers, two pure application methods and 25 mixed workflow/response candidates.
Receipt response/digest assembly is included explicitly; its atomic guards/writer
stay root. `_input` and `capture` fact assembly must be reviewed as fixed projection
versus application flow, without forcing a new port. Suggested remaining batches:
read/status/cursor/export response, input/page admission/finalization, run commands/
receipts/dismissal, derivation/publication, and engine claim/result/failure. Initial
estimate is about five coherent batches / 5–7 hours, with the first read group
about 60–90 minutes; this is not authorization for redesign or a native campaign.

The Store's previously inventoried seven mixed workflows are now delegated; its
38-method inventory still explicitly retains startup/workspace validation,
transaction settings, guards, canonical writers and ordered failure effects.
Completion of these Store moves does not complete Intelligence separation.

Original files are retained under `/tmp/tempo-intelligence-credential-before/`;
logs, proof scripts and distributions remain in the corresponding
`/tmp/tempo-intelligence-credential-*` paths. The full read-only Analysis audit is
`/tmp/tempo-intelligence-analysis-source-audit.json`. Exact final file/artifact and
unchanged Analysis source hashes are in
`/tmp/tempo-intelligence-credential-evidence.json`. These task-owned artifacts are
retained for integration; the shared review environment is unchanged. No git,
global-doc, native/live, actual private credential/provider, config/security/grant,
full-system, CI, deployment, issue or merge action was performed.

## Analysis read/cursor/export workflow move — October 21, 2026

Twelve existing AnalysisStore methods now delegate application work to
`analysis/read_work.py`: `_workspace`, `inputs`, `_input`, `_status`, `runs`,
`receipt`, `report`, `finding`, `export`, `remaining`, `engine_result` and
`input_status`. Fixed `AnalysisReads` keeps complete reads, missing-record and
accepted-generation guards, existing row limits and count-cursor extraction.
Store lends the unchanged owner read-only context; `_status` borrows its caller's
same connection. This changes two module paths and no schema, actual foreign key,
write workflow, deadline, public signature, wire contract, provider or engine.

**18 selected current-source cases passed**: 16 new fixed-row/composition cases,
the existing packaged public-schema check and existing legacy ReportPage contract
case. The same 16 new cases also passed against the saved original AnalysisStore
(comparison evidence, not 34 distinct current cases). They preserve input/page
flattening order, original in-read versus post-read return points, borrowed
run-status cursor iteration, historical filtered paging/canonical cursor bytes,
lookahead, malformed/stale rejection timing, pending legacy behavior, finding
visibility and lazy export lookup/original proposal object identity.

```sh
PYTHONPATH=services/intelligence/src:database:contracts/analysis-jobs/src:packages/chess-core/src /tmp/tempo-db-review-venv/bin/python -m pytest services/intelligence/tests/test_analysis_reads.py services/intelligence/tests/test_analysis.py::test_packaged_analysis_v1_schemas_match_public_models services/intelligence/tests/test_repairs.py::test_R1_R2_legacy_report_contract_retains_historical_fields -q --rootdir=. -o pythonpath= --import-mode=importlib
PYTHONPATH=services/intelligence/src:database:contracts/analysis-jobs/src:packages/chess-core/src /tmp/tempo-db-review-venv/bin/python -m pytest /tmp/tempo_intelligence_analysis_reads_original_test.py -q --rootdir=. -o pythonpath= --import-mode=importlib
python3 /tmp/tempo-intelligence-analysis-reads-source-proof.py
/tmp/tempo-db-review-venv/bin/python -m build services/intelligence --no-isolation --outdir /tmp/tempo-intelligence-analysis-reads-build
python3 /tmp/tempo-intelligence-analysis-reads-artifact-proof.py
python3 /tmp/tempo-intelligence-analysis-reads-audit.py
```

Exact fixed-read expansion matches all twelve original complete method sequences,
public signatures, sixteen moved query call points/fifteen distinct strings,
guards, cursor/serialization/hash behavior, nested ReportPage import and lazy
workspace/finding/report lookup. The other 21 AnalysisStore methods and the
optional-lock head query/guard remain AST-identical. Count extraction retains the
original cursor comprehension; no `fetchall` replacement or cursor/query port is
introduced. The existing input read is shared at its two original call points.

The fresh source distribution rebuilds its wheel. Five current implementation/doc
files match wheel bytes; eight current implementation/test/doc files match source
distribution bytes, with no Python caches. Isolated import smoke loads all three
moved/wired modules from the wheel with declared pure source dependencies and no
pool/connection initialization. An initial smoke assertion incorrectly assumed
read models were absent before the method-level late import; the saved original
adapter also loads them through the existing public `analysis_v1` export. That
assertion was corrected to module-origin discovery; no production change was
required. Original/current method-level import-order proof remains valid. Three
Analysis owner Mermaid blocks parse; scoped syntax, whitespace/conflicts and local
Markdown links pass. GitHub rendering was not inspected.

The current [34-method Analysis audit](../../database/tempo_intelligence_database/adapters/README.md#complete-analysisstore-source-audit)
now identifies 15 mixed methods, three fixed owner/read operations and sixteen
composition methods. Remaining batches are input/page admission/finalization;
run commands/dismissal and receipt digest/response helpers; capture/derivation/
publication; and engine claim/result/failure. `_receipt` / `_save_receipt` retain
existing command guards/writes and their unfinished response assembly is counted
explicitly. No whole-owner completion is claimed. Remaining estimate: about four
coherent batches / 4–6 hours; next input admission group about 45–75 minutes.

Original files remain under `/tmp/tempo-intelligence-analysis-reads-before/`.
Logs/proof scripts/distributions remain in the matching
`/tmp/tempo-intelligence-analysis-reads-*` paths, including the import proof log.
The updated full method audit is `/tmp/tempo-intelligence-analysis-reads-audit.json`;
exact final file/artifact hashes are in
`/tmp/tempo-intelligence-analysis-reads-evidence.json`. The preceding 33-method
source audit is preserved unchanged as historical evidence. Task-owned artifacts
remain for integration; the shared toolchain is unchanged. No git/global-doc,
native/live, actual engine/provider/private credential, config/security/grant,
full-system, CI, deployment, issue or merge action was performed.

## Analysis input/page admission and finalization move — October 21, 2026

`manifest`, `page` and `finalize` now compose their complete existing application
workflows in `analysis/input_work.py`. Fixed `AnalysisInputs` retains complete
parameter-bound queries, immutable input/page guards, locked page-set completeness
and accepted-state writes on the caller-owned input transaction. Existing receipt
guards/writers remain the sole root authority, borrowed on that same connection.
Public signatures, schemas/FKs, model/hash behavior and transaction order are
unchanged; no engine/provider/runtime work is introduced.

**13 selected current-source cases passed**: 11 new actual-composition/fake-row
cases plus the existing packaged public-schema and historical optional-metadata
page-digest cases. The same 11 new cases also passed against the saved original
AnalysisStore (comparison evidence, not 24 distinct current cases). They preserve
workspace admission and both model serializations before writes, immutable input/
page rejection, page-reference/game-count admission and post-commit response
lookup. Finalize keeps its prior separate read and early completeness checks,
receipt replay before the input lock, page-set/game-scope rejection, accepted-state
write and receipt last. Private receipt/writer lookup still precedes command JSON
construction, and canonical response digest mutation stays at the writer call
point. No selected case failed.

```sh
PYTHONPATH=services/intelligence/src:database:contracts/analysis-jobs/src:packages/chess-core/src /tmp/tempo-db-review-venv/bin/python -m pytest services/intelligence/tests/test_analysis_inputs.py services/intelligence/tests/test_analysis.py::test_packaged_analysis_v1_schemas_match_public_models services/intelligence/tests/test_analysis.py::test_C3_absent_optional_metadata_preserves_original_input_page_digest -q --rootdir=. -o pythonpath= --import-mode=importlib
PYTHONPATH=services/intelligence/src:database:contracts/analysis-jobs/src:packages/chess-core/src /tmp/tempo-db-review-venv/bin/python -m pytest /tmp/tempo_intelligence_analysis_input_original_test.py -q --rootdir=. -o pythonpath= --import-mode=importlib
python3 /tmp/tempo-intelligence-analysis-input-source-proof.py
/tmp/tempo-db-review-venv/bin/python -m build services/intelligence --no-isolation --outdir /tmp/tempo-intelligence-analysis-input-build
python3 /tmp/tempo-intelligence-analysis-input-artifact-proof.py
python3 /tmp/tempo-intelligence-analysis-input-audit.py
```

Exact fixed-operation expansion matches all three complete original method
sequences, eight query call points/seven distinct strings, guards, model/hash
calls, public signatures and lookup/serialization/return order. All other 31
AnalysisStore methods, including both receipt helpers and dynamic optional-lock
head query, remain AST-identical. Bound receipt properties resolve the original
private methods before command serialization. No generic query port, new model,
independent commit or additional receipt writer is introduced.

The two new module paths justify fresh source-distribution → wheel discovery.
Five current implementation/doc files match wheel bytes; eight current
implementation/test/doc files match source-distribution bytes; no Python caches
ship. An isolated smoke imports all three moved/wired modules from the fresh
wheel with declared pure source dependencies and no owner/pool/connection
initialization. Four Analysis owner Mermaid blocks parse; scoped syntax,
whitespace/conflicts and local Markdown links pass. GitHub rendering and native
locking, rollback durability, installed-consumer runtime and concurrency were not
qualified by these checks.

The [complete current audit](../../database/tempo_intelligence_database/adapters/README.md#complete-analysisstore-source-audit)
accounts for 35 AnalysisStore methods: three fixed owner/read operations, twenty
composition methods and twelve remaining mixed methods. Remaining batches are
run admission/commands/dismissal with receipt digest/response helpers;
capture/derivation/publication; and engine claim/result/failure. Estimated
remaining scope is roughly three coherent batches / 3–5 hours; next run/command
batch 75–105 minutes. Whole-owner separation is still unfinished.

Original sources remain under `/tmp/tempo-intelligence-analysis-input-before/`;
logs, proof scripts and distributions remain in the matching
`/tmp/tempo-intelligence-analysis-input-*` paths. The updated method audit is
`/tmp/tempo-intelligence-analysis-input-audit.json`; exact final file/artifact
hashes are in `/tmp/tempo-intelligence-analysis-input-evidence.json`. Earlier audit
artifacts remain unchanged. Task-owned artifacts are retained for integration;
the shared toolchain is unchanged. No git/global-doc, native/live, actual engine/
provider/private credential, config/security/grant, full-system, CI, deployment,
issue or merge action was performed.

## Analysis run/command/receipt/dismissal move — October 21, 2026

Five complete existing application blocks now compose `analysis/run_work.py`:
`submit`, `_receipt`, `_save_receipt`, `command` and `dismiss`. Fixed
`AnalysisRunOperations` keeps full bound queries, immutable/stale/cancellation/
receipt/dismissal guards and canonical receipt writes on unchanged owner contexts.
Application code owns generation/budget/action choices, position/hash preparation,
receipt response/digest mutation and dismissal responses. Narrow existing-value
callbacks retain original hash/state evaluation at query-parameter points; no
generic dispatcher, SQL port, new model or schema/transaction authority is added.

**26 selected current-source cases passed**: 13 new synthetic fixed-operation/
composition cases, 11 existing input-composition cases, the existing position-order
case and public-schema case. The same 13 new cases passed against the saved
original AnalysisStore (comparison evidence, not 39 distinct current cases).
An initial argument-name collision in the new test receipt factory stopped four
new command cases before production execution; the factory parameter was renamed
and the complete selected set passed. No production repair was required.

The cases preserve generation/hash timing, 64-position stage boundaries, final
head supersession, four command action effect orders, receipt replay before stale
revision, receipt conflict and first/second dismissal replay. Dismissal keeps its
finding read between writes and user-state lock before guarded effects. Result
digest mutation still precedes the canonical writer's binding, and the same
response object returns. Private status/head and receipt/writer lookup order is
retained before argument/model evaluation; existing input finalization exercises
these same moved receipt helpers on its borrowed connection.

```sh
PYTHONPATH=services/intelligence/src:database:contracts/analysis-jobs/src:packages/chess-core/src /tmp/tempo-db-review-venv/bin/python -m pytest services/intelligence/tests/test_analysis_runs.py services/intelligence/tests/test_analysis_inputs.py services/intelligence/tests/test_analysis.py::test_C_partial_scan_truth services/intelligence/tests/test_analysis.py::test_packaged_analysis_v1_schemas_match_public_models -q --rootdir=. -o pythonpath= --import-mode=importlib
PYTHONPATH=services/intelligence/src:database:contracts/analysis-jobs/src:packages/chess-core/src /tmp/tempo-db-review-venv/bin/python -m pytest /tmp/tempo_intelligence_analysis_run_original_test.py -q --rootdir=. -o pythonpath= --import-mode=importlib
python3 /tmp/tempo-intelligence-analysis-run-source-proof.py
/tmp/tempo-db-review-venv/bin/python -m build services/intelligence --no-isolation --outdir /tmp/tempo-intelligence-analysis-run-build
python3 /tmp/tempo-intelligence-analysis-run-artifact-proof.py
python3 /tmp/tempo-intelligence-analysis-run-audit.py
```

Fixed-operation expansion matches all five complete original method sequences,
26 query call points/24 distinct strings, public signatures, guard/hash/staging/
action/dismissal and return order. Other 30 AnalysisStore methods, including the
optional-lock head query/guard, remain AST-identical. Bound-property source proof
retains receipt/writer and status/head binding order. Atomic guards and the sole
canonical receipt writer stay root; response/digest assembly is no longer an
unaccounted mixed helper.

The two new module paths justify fresh source-distribution → wheel discovery.
Five current implementation/doc files match wheel bytes and eight current
implementation/test/doc files match source-distribution bytes, with no Python
caches. Isolated imports load three moved/wired modules from the wheel with
declared pure source dependencies and initialize no owner/pool/connection or
worker. Five Analysis owner Mermaid blocks parse; scoped syntax, whitespace/
conflicts and local Markdown links pass. GitHub rendering, real atomicity,
concurrency, native execution and installed-consumer runtime remain unverified.

The [complete 36-method audit](../../database/tempo_intelligence_database/adapters/README.md#complete-analysisstore-source-audit)
now identifies seven mixed methods: `capture`, `derive`, `derive_next`, `claim`,
`accept`, `failure` and `mark_engine_unavailable`. Remaining batches are report
capture/derivation/publication (60–90 minutes) and engine workflow (75–105 minutes),
roughly 2–3.5 hours total. Whole-owner separation is still unfinished.

Proposal-only simplification observations, with concrete source locations,
benefits and uncertainty, are retained separately in
`/tmp/tempo-intelligence-analysis-run-simplification-proposals.json` for the
integration owner's single simplification document. They concern three identical
nonlocking cache queries and repeated pure cursor byte codecs. No consolidation
or extra repository document was implemented.

Original sources remain under `/tmp/tempo-intelligence-analysis-run-before/`;
logs/proof scripts/distributions remain in matching
`/tmp/tempo-intelligence-analysis-run-*` paths. Current full method status is in
`/tmp/tempo-intelligence-analysis-run-audit.json`; final file/artifact/proposal
hashes are in `/tmp/tempo-intelligence-analysis-run-evidence.json`. Earlier audit
artifacts remain unchanged. Task-owned resources are retained for integration;
the shared toolchain is unchanged. No git/global-doc, native/live, actual engine/
provider/private credential, config/security/grant, full-system, CI, deployment,
issue or merge action was performed.

## Analysis capture/derivation/publication move — October 21, 2026

Three complete existing application blocks now compose `analysis/publication_work.py`:
`capture`, `derive` and `derive_next`. Fixed `AnalysisPublicationOperations` retains
snapshot/result/dismissal extraction, legacy/next reads, staged report items,
user-state locking, revision/generation and captured-dismissal guards, conditional
reopen/sibling-run invalidation and report/head writes. Three explicit composition
contexts lend the original foreground read, background read and background write
transactions; no independent transaction or publication authority was added.

The selected command passed **19 current cases**:

```sh
PYTHONPATH=services/intelligence/src:database:contracts/analysis-jobs/src:packages/chess-core/src \
  /tmp/tempo-db-review-venv/bin/python -m pytest \
  services/intelligence/tests/test_analysis_publication.py \
  services/intelligence/tests/test_findings.py \
  services/intelligence/tests/test_repairs.py::test_R1_R2_legacy_report_contract_retains_historical_fields \
  services/intelligence/tests/test_analysis.py::test_packaged_analysis_v1_schemas_match_public_models \
  -q --rootdir=. -o pythonpath= --import-mode=importlib
```

The same 13 new publication cases also passed against the saved original adapter
(comparison evidence, not 32 distinct current cases). Synthetic fixed-row responses
prove capture cursor iteration and late input read, already-current early return,
65-finding staging in two committed batches, metadata/callback timing, revision/
generation/dismissal fences, conditional reopen propagation, legacy metadata and
evidence preservation, and next-run read closure. The tests do not parse SQL or
freeze query strings. No selected check failed or was skipped.

Task-local exact expansion proof:

```sh
/tmp/tempo-db-review-venv/bin/python /tmp/tempo-intelligence-analysis-publication-source-proof.py
```

It reconstructs all three original method bodies from the application functions
and fixed operations: 14 query call points/14 distinct strings, complete parameter
bindings, finding hash/model/dismissal/metadata/state statements, original 64-item
batches, callback/return points and publication guards all match. The other 33
AnalysisStore methods, including `_derive_evidence`, optional-lock `_head` and
four remaining engine workflows, are AST-identical. Public signatures, original
context keyword options and lazy owner capability/workspace lookups are exact.
The application module has no driver/cursor/query calls.

Fresh artifacts built without installing or changing the shared environment:

```sh
/tmp/tempo-db-review-venv/bin/python -m build services/intelligence --no-isolation \
  --outdir /tmp/tempo-intelligence-analysis-publication-build
/tmp/tempo-db-review-venv/bin/python /tmp/tempo-intelligence-analysis-publication-artifact-proof.py
/tmp/tempo-db-review-venv/bin/python -I /tmp/tempo-intelligence-analysis-publication-wheel-smoke.py
```

Five current implementation/owner-document files match wheel bytes; eight current
implementation/test/document files match sdist bytes. Both contain no bytecode or
cache files. Three moved/composition modules import from the fresh wheel, with
only declared pure contract/chess sources supplied; no owner, pool, connection,
worker, engine or provider is instantiated. Scoped syntax, whitespace/conflicts
and local Markdown links pass. All six Analysis owner Mermaid blocks parse with
the existing `/tmp/tempo-db-extraction-diagrams` parser; hosted GitHub rendering
was not inspected.

- Wheel SHA-256: `ed422325049098221fe09af80675a810398c780453dbd7cc4b4befcce9b94e6e`.
- Source distribution SHA-256: `bf00ad63f5c89c0d3ef5e92bdd466e35ee95121658611f29926ff7fa22b36eae`.

The [complete 39-method audit](../../database/tempo_intelligence_database/adapters/README.md#complete-analysisstore-source-audit)
accounts for three fixed operations, 32 composition methods and four unfinished
mixed engine workflows: `claim`, `accept`, `failure`, `mark_engine_unavailable`.
Their existing job/model validation, stage/comparison, retry/status/backoff and
unavailable decisions still need separation from fixed capacity/lease/replay/
fence/event/result/position/run operations. Estimated next bounded batch:
75–105 minutes. No permanent mixed-root exception is introduced.

No DDL/FK, public model, engine/provider/credential/security behavior or live state
changed. Native PostgreSQL publication/locking/concurrency and deployment/resource
isolation remain unverified; retained native tests were not run. No full-system,
provider, native engine, private material, live database, service, CI or issue
action occurred. Evidence and before-source snapshots are retained under
`/tmp/tempo-intelligence-analysis-publication-*` for integration review, including
source/artifact/fake-test logs and the exact hash manifest. This receipt is not a
claim of whole-owner completion or hosted merge qualification.

## Analysis engine-work move and initial method-audit closure — October 21, 2026

Four complete existing application blocks now compose `analysis/engine_work.py`:
`claim`, `accept`, `failure` and `mark_engine_unavailable`. Fixed
`AnalysisEngineOperations` retains complete SQL, atomic head/capacity/lease/replay/
execution guards and ordered writes on the original background transaction.
Application job/result/status validation, compatibility, comparison, transitions,
retry/backoff/cancellation choices, UUID/hash preparation and responses stay at
the original call points. Fixed bindings execute only the existing application
expressions at their original query-parameter slots; no generic query port,
framework, dispatcher, extra transaction or new lease authority was introduced.
Unused application imports were removed from the composition adapter; nested
contract imports, clock checks and lazy head/workspace lookups remain ordered.

The selected command passed **32 current cases**:

```sh
PYTHONPATH=services/intelligence/src:database:contracts/analysis-jobs/src:packages/chess-core/src \
  /tmp/tempo-db-review-venv/bin/python -m pytest \
  services/intelligence/tests/test_analysis_engine_work.py \
  services/intelligence/tests/test_analysis.py::test_worker_session_is_closed_before_durable_failure \
  services/intelligence/tests/test_analysis.py::test_worker_acknowledged_cancellation_is_not_a_failed_search \
  services/intelligence/tests/test_analysis.py::test_packaged_analysis_v1_schemas_match_public_models \
  services/intelligence/tests/test_repairs.py::test_R5_incompatible_worker_identity_rejected \
  services/intelligence/tests/test_repairs.py::test_R5_completed_depth_and_typed_mate_preserved \
  -q --rootdir=. -o pythonpath= --import-mode=importlib
```

The same 24 new cases also passed against the saved original adapter (comparison
evidence, not 56 distinct current cases). Synthetic fixed-row scripts, fake UUID/
clock and real pure wire models preserve capacity read/lock/recheck, cancellation
recovery, model/input validation before leasing, validation/digest before result
writes, replay before lease/clock/execution guards, root/played write order,
active-claim fencing before failure/status work, event before transition/backoff
writes, cancellation settlement and conditional unavailable generation changes.
Tests ignore SQL strings and do not execute a worker, engine or provider. No
selected check failed or was skipped.

Task-local exact expansion proof:

```sh
/tmp/tempo-db-review-venv/bin/python /tmp/tempo-intelligence-analysis-engine-source-proof.py
```

It reconstructs all four original method bodies, preserving 30 query call points/
23 distinct strings and complete bound parameters, guards, clock short circuits,
contract imports, callback/UUID/model/hash/comparison/status/backoff statements,
transaction and return order. The other 35 AnalysisStore methods are AST-identical;
public signatures are unchanged. Bound head lookup precedes dynamic claim-field
access. Application functions have no driver/cursor/query calls. The original
state/error tuple and event-digest/backoff expressions remain application-owned
and expand to the original query-binding sequence.

Fresh artifacts built without installing or changing the shared environment:

```sh
/tmp/tempo-db-review-venv/bin/python -m build services/intelligence --no-isolation \
  --outdir /tmp/tempo-intelligence-analysis-engine-build
/tmp/tempo-db-review-venv/bin/python /tmp/tempo-intelligence-analysis-engine-artifact-proof.py
/tmp/tempo-db-review-venv/bin/python -I /tmp/tempo-intelligence-analysis-engine-wheel-smoke.py
```

Five current implementation/owner-document files match wheel bytes; eight current
implementation/test/document files match sdist bytes, without bytecode/cache
files. Three moved/composition modules import from the fresh wheel with only the
declared pure contract/chess source dependencies supplied; no owner, pool,
connection, worker, provider or engine is instantiated. The build was repeated
once after trimming only extra final blank lines in the two new modules, so the
final artifacts match their final source. Scoped syntax, whitespace/EOF/conflict
checks and local Markdown links pass. All seven Analysis owner Mermaid blocks
parse using the existing parser; hosted GitHub rendering was not inspected.

- Wheel SHA-256: `cb27acfe79486bc36e3f350c8f20c1fabf36c3994c7dc267e88fca994abffb1f`.
- Source distribution SHA-256: `8e463d6910575aa112992621ad4f49813109a4b95dbb457921de106e96e5c9e3`.

The [complete audit](../../database/tempo_intelligence_database/adapters/README.md#complete-analysisstore-source-audit)
now accounts for all 40 AnalysisStore methods: the original 33 plus seven explicit
transaction contexts; three fixed owner/head/evidence methods and 37 composition
methods, with no remaining mixed method in that initial audit. Only the fixed
optional-lock head query and ordered evidence read remain directly in the class.
The full 38-method Store classification was reread and its source hash remains
identical to the credential-workflow integration: 11 database authority/fixed and
27 composition/ordered fixed-effect methods, with startup/workspace/grant checks,
canonical writers and failure guards explicitly retained.

A bounded source scan also found a **separate unfinished driver remainder**:
`services/intelligence/src/tempo_intelligence/runtime.py:15–33` still contains
`BackgroundConnection(Connection)` and imports Psycopg at line 8, mixed with
application `DatabaseRunner`/deadline orchestration. It was outside the original
two-entry SQL checklist. The next narrow split should move the existing driver
subclass root and retain runner/context orchestration outside, preserving one
shared deadline, wait/close/error behavior and imports. Estimate: 20–40 minutes
including synthetic deadline and discovery checks. No runtime move is included
in this engine batch; this is not a permanent exception or whole-owner completion.
The remaining `worker.py` execute call is an engine-client call, not database SQL.

No DDL/FK, public schema/model, native engine/provider/security behavior or live
state changed. Native PostgreSQL lease/replay/locking/concurrency and deployment/
resource isolation remain unverified; retained native tests were not run. No
actual engine/provider, private material, live database, service, full-system, CI,
issue or git action occurred. Initial method-audit closure does not establish
whole-backend/domain independence or hosted merge qualification. Source snapshots,
proof/test/artifact logs, full audit and exact hash manifest remain under
`/tmp/tempo-intelligence-analysis-engine-*` for integration review.

## Runtime Psycopg subclass move — October 21, 2026

The recorded driver remainder is now centralized:
`BackgroundConnection` moved unchanged from `runtime.py` into
`database/tempo_intelligence_database/adapters/background_connection.py`. Store
imports the same class from its new owner path; its methods, SQL, transaction and
pool settings are unchanged. Runtime keeps its original constants, one deadline
ContextVar, DatabaseRunner and drain/shutdown/cancellation orchestration outside
the database tree. It imports no driver or database package. Current source found
only Store consuming the class, so no compatibility export or import cycle was
needed. Shared context identity, timing, close behavior and public error strings
remain unchanged.

The selected command passed **12 current cases**:

```sh
PYTHONPATH=services/intelligence/src:database:contracts/analysis-jobs/src:packages/chess-core/src \
  /tmp/tempo-db-review-venv/bin/python -m pytest \
  services/intelligence/tests/test_background_connection.py \
  services/intelligence/tests/test_worker.py::test_cancellation_during_executor_join_remains_authoritative \
  services/intelligence/tests/test_worker.py::test_repeated_cancellation_during_final_executor_join_preserves_provider_cancellation \
  services/intelligence/tests/test_worker.py::test_lifespan_cancellation_during_owned_store_close_drains_and_joins_runner \
  services/intelligence/tests/test_worker.py::test_lifespan_startup_cancellation_closes_eventually_created_store_and_runner \
  -q --rootdir=. -o pythonpath= --import-mode=importlib
```

Six new cases use a fake base wait and clock, without the native connection
initializer/finalizer or protocol: no deadline, remaining deadline, caller timeout,
expired deadline and wait-error translation, plus shared context/reset across the
actual application runner's thread. Six existing fake runner lifecycle/cancel/
close/join cases remain selected. The five wait cases also passed against the
saved original class; the new relocated-wiring identity case was intentionally
deselected from that comparison (5 passed, 1 deselected), not counted as an
original-class proof. No selected case failed or was skipped. All credentials,
providers and Stores in the selected lifecycle tests are existing local fakes.

Task-local exact source proof:

```sh
/tmp/tempo-db-review-venv/bin/python /tmp/tempo-intelligence-runtime-driver-source-proof.py
```

The moved class and wait method are AST-identical. Runtime's complete remaining
module is AST-identical after removing only that class and its Psycopg import;
Store's complete module is AST-identical after the one class import relocation.
The application runner/deadline/cancellation statements and every Store query,
transaction and pool option are preserved. The driver imports the same existing
ContextVar; no second declaration or compatibility shim was added.

Fresh artifacts built and checked without installing or changing the shared
environment:

```sh
/tmp/tempo-db-review-venv/bin/python -m build services/intelligence --no-isolation \
  --outdir /tmp/tempo-intelligence-runtime-driver-build
/tmp/tempo-db-review-venv/bin/python /tmp/tempo-intelligence-runtime-driver-artifact-proof.py
/tmp/tempo-db-review-venv/bin/python -I /tmp/tempo-intelligence-runtime-driver-wheel-smoke.py
```

Five current source/owner-document files match wheel bytes; eight current source/
test/document files match sdist bytes, without bytecode/cache files. Three cold
fresh-wheel import orders (runtime first, Store first and driver first) all import
runtime/driver/Store from that wheel and preserve one shared context/class binding;
no Store, pool or native connection is created. Scoped syntax, whitespace/EOF,
conflict and local-link checks pass. The new canonical adapter deadline diagram
and seven existing Analysis diagrams parse using the existing Mermaid parser;
hosted GitHub rendering was not inspected.

- Wheel SHA-256: `e7c10ffff0bdb444fdab09781b44f2413258e23ab8ad4a55b180481cebb181db`.
- Source distribution SHA-256: `bdd60a39467eeac11ff3c4fdc083a0045afd102408a963ca7ebf47df09e91daf`.

The scoped current application source scan finds no Psycopg/SQLite driver import
or SQL cursor call outside the root tree; its one `execute` is the unchanged
Analysis worker engine-client call. Full audits still account for 38 Store and
40 AnalysisStore methods (three fixed/37 composition in Analysis), with the
previously recorded runtime driver edge now resolved. This source audit does not
establish whole-backend/sibling-domain independence or native/deployed acceptance.
No schema/FK, query, model, grant/security/configuration, credential behavior,
actual worker/provider/engine, live data, service, CI or git action changed or ran.
Native blocked-I/O timing, PostgreSQL locking/concurrency and deployment/resource
isolation remain unverified. Task-owned source snapshots, selected logs, source/
artifact proofs, audit and exact manifest remain under
`/tmp/tempo-intelligence-runtime-driver-*` for integration review.
