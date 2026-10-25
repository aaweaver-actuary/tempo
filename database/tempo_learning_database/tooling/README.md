# Learning runtime database tooling

[run_postgres.py](run_postgres.py) is the existing invocation-owned PostgreSQL native runner. From a full repository checkout, use the Learning runtime environment and optionally supply an evidence directory. It retains bounded disposable resources, exact collection/execution/JUnit checks, role provisioning and verified cleanup. The ordinary [runtime qualification workflow](../../../services/learning/runtime/qualify.py) remains application-owned; native proof selection remains [the runtime tests](../../../services/learning/runtime/tests).

`main(..., fixture_profile="full")` retains the default fixture set. Use `fixture_profile="essential"` for explicit core test files or node IDs in `test_runtime.py`, `test_publications.py`, `test_restart.py` and `test_container.py`. Essential installs Learning and its declared dependencies, including shared contract packages, without hashing or installing the Game Evidence producer, Content service or A bridge. Empty selections and directories are refused before resources are created; tests requiring omitted optional fixtures fail with guidance to select full explicitly. Container file and node selections still build the runtime image.

From the repository root, with the existing installed Learning runtime interpreter selected as `python`, one explicit case is:

```sh
python -c 'from pathlib import Path; import runpy; runpy.run_path("database/tempo_learning_database/tooling/run_postgres.py")["main"](test_paths=[Path("services/learning/runtime/tests/test_runtime.py::test_runtime_ready_authorized_and_ordinary_introduction")], fixture_profile="essential", evidence_directory=Path("/tmp/tempo-learning-essential-evidence"))'
```

Choose an absolute, task-owned evidence directory. Each run records its profile, components, source hashes, selected identities and cleanup receipt; no failed run retries with full fixtures.

[restore_rehearsal.py](restore_rehearsal.py) retains the existing explicitly configured disposable rehearsal restore and copied-review identity repair. Docker ownership/disposable labels, endpoint, workspace, empty-target and unchanged-history guards are unchanged. Its SQL and behavior are byte-identical; relocation grants no permission to run a restore or alter a database.

These tools are included in source distributions only. Runtime wheels exclude `tooling/` and `tests/`; offline before/after package comparison verifies the runtime payload. Native and restore behavior was not executed for this relocation. [Source schema documentation](../README.md) describes the stored tables; live deployment remains unverified.


## Persistent target bootstrap

[bootstrap_persistent.py](bootstrap_persistent.py) is a distinct opt-in owner
operation for separately named persistent targets. It does not bypass the
disposable provision guard or accept/promote disposable ownership receipts.
The [persistent composition](../../../config/study-persistent/README.md)
contains no web service or host ports and keeps automatic maintenance disabled.

Public pure planning is plan_persistent_target(intent, resources). CLI commands
plan and bootstrap consume private --intent-file and --resources-file.
Only bootstrap also needs --admin-url-file, a new --output-directory and explicit
--protected-checkout-root. Admin/private output paths inside that checkout or
the executing source tree are refused.
check-quarantine is a read-only private-file/configuration guard, not admission.
It requires both the exact selected intent and resource-observation SHA256 values;
a stale observation for the same names/intent cannot be silently substituted.

Intent v1 kind is tempo-learning-persistent-target-intent, with exactly:
transition_id, source_revision(full Git SHA), target_identity(explicit opaque
launcher identity), project, database, workspace_id, timezone, ingress_origin,
context, daemon_id, host, port, data_volume, runtime_secrets_volume, network_name,
source_volume_names and images(postgres,learning,study-web immutable IDs/digests),
plus kind/schema_version. Source and both new volumes must be disjoint.

Resource v1 kind is tempo-learning-persistent-resources, with the exact intent
binding fields context/daemon_id/project/target_identity/host/port/data_volume/
runtime_secrets_volume/network_name; intent_sha256; evidence_scope(observed or
synthetic-fixture); evidence_id; timezone-qualified observed_at; container_id and
network_id; system_identifier; postgres_image matching the pinned image; labels
tempo.owner=project, tempo.target-kind=persistent, tempo.disposable=false; and
new_data_volume/new_runtime_secrets_volume=true, plus kind/schema_version.
An independent resource owner supplies these facts; this module does not start
Docker or infer real identity from a proposed name. Synthetic evidence can run
only with explicitly injected fake dependencies and remains labelled synthetic.

Receipt v1 kind is tempo-learning-persistent-bootstrap, status
initialized-quarantine-required. It binds intent/resource digests, evidence_scope,
transition/target/database/project/roles/workspace/timezone/origin/system identity,
declared source/image pins, observed_postgres_image, endpoint, private output
filenames and exact byte hashes (private_output_sha256), and
producer-owned effects. schema contains expected_migrations, applied_migrations,
public schema_identity digest and informational migration_count. The owning
[DDL API](../ddl/migrate.py) verifies actual applied checksums equal the selected
source map. Its public schema_identity(map) uses canonical UTF-8 JSON with sorted
keys, compact separators, ensure_ascii=False and allow_nan=False, then SHA256.
Neither migration count nor receipt schema_version is a database schema version.

Receipt does not attest graph acceptance, actual maintenance/traffic fences,
operator availability or old-authority rejection. Existing launcher maps only
explicitly accepted public producer facts; runtime/graph/admission proof remains
independent. evidenceId is an explicit immutable receipt reference, not guessed
from its target ID. A graph importer cannot obtain create/drop or admission
authority by importing this module.

```mermaid
sequenceDiagram
    participant C as Explicit composition / operator
    participant B as Persistent bootstrap
    participant D as Learning-owned DDL adapter
    C->>B: Intent + independent resource observation
    B->>D: Pin physical server/session; require absent DB/roles
    B->>B: Durably retain private credentials and phase
    B->>D: Recheck identity/absence under lock; create template0 DB
    B->>D: Verify owner and empty target
    B->>D: Ordered schema/workspace + runtime grants
    B->>D: Read back schema, roles and empty content
    B-->>C: Distinct quarantine-required receipt
    Note over B,C: No service, graph import, registration or admission
```

The existing disposable bootstrap/public API and guard are retained.
No Learning schema or table relationship changes are introduced. At executable
commit 65ec53bfee1b3e94a374a9b5dfdcd8fdfff54020, the
[owning fake suite](../../tests/test_learning_persistent_bootstrap.py) passed
25 tests and the existing test_learning_disposable_bootstrap.py suite passed
12 regressions (Python 3.15.0). Both Compose 5.6.1 profiles rendered without daemon
contact, using a deliberately nonexistent DOCKER_HOST. The recovered focused
qualification supersedes the earlier executor outage. Native PostgreSQL
role/schema proof, installed-container runtime behavior and actual admission
remain unverified; no real target or credentials/grants were created.
