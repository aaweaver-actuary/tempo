# Launcher cutover spec (359-F / issue #369)

Historical programme reference. This document records the October 21–25 Study-first reduction/migration work. Current product scope and delivery order come from [SPEC](../../../SPEC.md); execution comes from [AGENTS](../../../AGENTS.md). Its retirement defaults, replacement-runtime requirements, migration sequence, source-reduction targets, and then-current task queue are not prerequisites or assignments for ordinary product features. Reuse a particular procedure only when the current assignment explicitly selects it and reconciles it with current requirements. Historical evidence and safety conditions are retained.

Status: **historical source-backed proposal only** (no launcher/runtime mutation in this issue).
Baseline reviewed: [`7d77ed1b25198dcd320e0265f777cc630f23775e`](https://github.com/aaweaver-actuary/tempo/commit/7d77ed1b25198dcd320e0265f777cc630f23775e).
Upstream dependencies: #358 (live fingerprint), #267 (latest-history migration), #364 (Study artifact), #365 (Learning runtime), #367 (opt-in Study-only Compose).

## 1) Why the current normal launcher cannot survive legacy source removal

Current normal launch path (`tempo` → `scripts/tempo-cli.mjs`) is still coupled to the legacy root stack:

| Evidence | Current behavior | Cutover consequence |
| --- | --- | --- |
| `tempo`, `scripts/tempo-cli.mjs` (`install`) | Registers `docker-compose.yml` + `docker-compose.postgres-maintenance.yml`, project `tempo`, web `127.0.0.1:3000`. | Deleting legacy runtime files before launcher cutover breaks normal `tempo start`. |
| `scripts/tempo-deployment.mjs` (`validateTarget`) | Requires root stack service/volume contract (`api`, Celery workers, Redis, engine volume, passfiles). | Current validation rejects a Study-only composition. |
| `scripts/tempo-runtime.mjs` (`applicationServices`) | Lifecycle assumes legacy services for stop/start/readiness/receipt identity. | Normal `start/restart/backup` cannot manage Study-only service identity yet. |
| `Dockerfile.web` | Uses `npm run build:local`. | Default web build still targets legacy entry path, not guaranteed Study closure. |
| `nginx.conf` | Proxies `/api/` to `api:8000`. | Incompatible with Learning runtime endpoint (`8001` contract from #365). |

Therefore, the launcher-dependent legacy backend/worker sources cannot be deleted while the default launcher still routes through this path.

## 2) Target of the one-time normal-launcher upgrade

The future normal launcher must move from “legacy-by-default” to a **single Study-only default composition**:

1. **Study web artifact** from #364 (`TEMPO_TARGET=study`) with emitted `study-composition.json`.
2. **Learning runtime image** from #365 (`tempo_learning_runtime.cli` entrypoint; explicit migration/readiness contract).
3. **Study-only Compose/ingress** from #367 (new compose + nginx mapping to Learning, not legacy `api:8000`).
4. **Data migration path** from #267 (genuine latest history + pending intents, not schema-only migration).

Artifact selection rule at cutover time:
- select one exact Git revision;
- build/pull all selected artifacts from that revision;
- record immutable image digests/IDs;
- reject mutable/tag-only evidence as cutover proof.

## 2.1) Credential, host, and proxy contract for cutover

The default launcher cutover must switch from legacy backend credential wiring to Learning runtime authority wiring:

- **Learning runtime configuration** must provide `TEMPO_LEARNING_WORKSPACE`, `TEMPO_LEARNING_DATABASE_URL`, `TEMPO_LEARNING_READER_URL`, `TEMPO_LEARNING_TOKEN_FILE`, `TEMPO_LEARNING_ORIGINS`, and `TEMPO_LEARNING_HOSTS` (plus optional operator/evidence token files), matching `services/learning/runtime/src/tempo_learning_runtime/config.py`.
- **Ingress proxy** must route `/api/*` to the Learning service endpoint (contract: `serve --port 8001`), not legacy `api:8000`.
- **Browser authority headers** (`Authorization: Bearer <token>`, `X-Tempo-Workspace`) must match runtime workspace/origin checks (`app/lib/study-connection.ts`, runtime `api.py` middleware).
- **Secrets** remain file-backed and private; no token/password literal in compose, CLI args, or issue text.
- **Workspace identity** must remain stable across cutover, or cutover is blocked until pending authority-bound records are reconciled.

## 3) Command responsibilities and later file touchpoints

This issue does **not** edit launcher/runtime files; it specifies the required later change.

### 3.1) Command responsibility table (single transition authority)

| Command surface | Required behavior | Must never do |
| --- | --- | --- |
| `doctor`, `status`, and read-only planning modes | Inspect/report only. Explain blockers and current registration/receipt state without side effects. | Rewrite registration, apply schema/imports, start Docker/services, activate a target, or perform cutover writes. |
| `backup` | Snapshot + restore-verify the **currently registered authoritative target**. | Perform one-time upgrade/import, silently switch authority, or choose a different database because it looks newer/emptier. |
| Separately authorized mutating `start` / `restart` / migration operation | Execute the one-time transition sequence only when required proofs are present. Missing proofs block the operation without changing registration. | Route through diagnostic commands, bypass maintenance lock, or mutate registration after a failed gate. |
| Fresh `install` registration vs v1→v2 upgrade | Treat as distinct workflows with explicit operator choice and provenance. | Attach/initialize an existing volume only because a name appears to match; infer upgrade intent from naming alone. |

### 3.2) File-by-file plan for the later launcher PR

| File(s) | Required later change | Safety constraint / expected failure |
| --- | --- | --- |
| `tempo` | Keep as thin wrapper to CLI; no policy logic in shell script. | Failure remains explicit Node/CLI error; no hidden fallback launcher. |
| `scripts/tempo-cli.mjs` | Implement command routing in §3.1: diagnostics remain read-only, `backup` stays snapshot-only, and only an explicitly authorized mutating path can execute v1→v2 transition + publication. | If live fingerprint (#358) or migration readiness (#267) is missing, block with actionable error; do not mutate target/registration. |
| `scripts/tempo-deployment.mjs` | Replace legacy target validation contract with Study-only contract (services, volumes, ports, secrets, allowed origin/host/runtime settings). | Reject mixed legacy/new ownership, disposable-volume rebinding, secret files inside checkout, or mutable image identity. |
| `scripts/tempo-runtime.mjs` | Replace lifecycle service set, readiness checks, backup choreography, and receipt identity to Learning stack; keep maintenance lock/single-writer fencing semantics. | Any unresolved migration guard/history verification keeps writers stopped; never auto-start competing legacy writer. |
| `scripts/tempo-guidance.mjs` | Add cutover-specific blocker guidance (`host fingerprint missing`, `history migration not qualified`, `pending browser authority conflict`). | `doctor`/`status` must report blocked state honestly; no “eligible” status without cutover proofs. |
| Root compose default wiring (currently `docker-compose.yml` + maintenance overlay) | Repoint normal launcher to Study-only composition files from #367; legacy compose remains cold historical path only during transition. | No permanent dual-run normal mode; normal mutating launch path must have exactly one writer authority. |
| `Dockerfile.web` / ingress (`nginx.conf` or Study-specific nginx file) | Default web image path must use #364 Study artifact and proxy to Learning runtime endpoint/health contract. | Fail if API proxy points to legacy `api:8000` or if built artifact identity is not the approved Study build. |
| Docs (`docs/TEMPO-CLI.md`, `docs/POSTGRES-MAINTENANCE.md`) | Document one-time upgrade contract, rollback fence, and operator preflight matrix. | Must distinguish source checks vs host/data-required checks; no implied live acceptance. |

## 4) One crash-recoverable transition sequence (future execution plan)

Source-qualified preflight (artifact pinning + single-writer path) and host/device/data preflight (#358 + #267 + browser authority reconciliation) remain required gates before mutation.
Do **not** recursively launch another `tempo start` while the outer lifecycle owns the maintenance lock; the already authorized invocation continues with its pinned target.

1. Pin the approved source revision and exact artifacts. The mutation phase must not fetch a newer remote `main` or select different images.
2. Acquire the existing target maintenance lock. Persist transition intent and original registration/receipt identity before changing either target. Record the old and proposed target identities; never store secret values in the receipt.
3. Fence ingress and all legacy application writers, including scheduled/background work and competing launch attempts. Verify in-flight writes are drained or explicitly reconciled. Pending browser work must remain recoverable under its original authority; unresolved unsupported reconciliation blocks activation.
4. After that fence, capture the final consistent source snapshot and restore-verify the legacy recovery backup. The final importer consumes this fenced state, not merely the earlier rehearsal snapshot. Rehearsal equivalence alone does not establish latest-state equivalence.
5. Provision/migrate/grant the new Learning target explicitly and run #267’s idempotent import and semantic verification. Keep normal target maintenance and user/write-capable traffic disabled during this step. Do not mutate or rebind the preserved legacy source.
6. Start target validation in quarantine: maintenance disabled and only non-mutating health/metadata checks allowed. Readiness must check the intended target credentials/workspace/schema, but readiness alone is not permission to open traffic. Do not invoke queue endpoints or manufacture reviews against the authoritative imported data as a smoke test; use a disposable copy for mutating checks.
7. Create and restore-verify the post-import target backup. Verify browser authority handling and all release gates before proceeding.
8. Publish the new version-2 registration and matching deployment/cutover receipt durably. The following identities must agree across both records: approved revision/image identities, target database/workspace, Compose/volume identities, ingress authority, schema/import evidence, and transition phase. Treat exact upstream artifact values as pending inputs, not invented constants. If only one record persisted, mismatch blocks startup until repaired; never guess which writer is authoritative.
9. Persist `activation-started` before enabling target maintenance or traffic. Keep legacy writers fenced. Mark completion only after the selected target and registration/receipt agree.

### 4.1) Interruption matrix

| Interruption point | Authority allowed to write | Next launcher invocation | Legacy rollback permissible? |
| --- | --- | --- | --- |
| Failure before fencing | Legacy authority only. Target remains non-authoritative. | Re-run preflight and restart transition from step 1 with pinned artifacts; no publication changes. | **Yes**, because no target-only acceptance exists. |
| Failure after fencing but before import completes | No user writer. Legacy stays fenced; target still non-authoritative. | Re-acquire lock, confirm fence + pending-browser recoverability, then resume/restart import from fenced snapshot state. | **Conditional yes** only if no target-only accepted effects are proven and registration/receipt can be restored consistently. |
| Successful import before registration publication | Target may be read for quarantine checks only; no mutating traffic. Legacy remains fenced. | Create/verify post-import backup, re-run release gates, then attempt durable registration+receipt publication. | **Conditional yes** with the same proof requirements as above. |
| Crash between registration and receipt publication | Neither writer is reopened automatically. | Detect registration/receipt mismatch and block startup until repaired to a single authoritative pair. | **No automatic rollback**; rollback requires explicit proof of zero accepted target-only effects. |
| Crash after both registration and receipt publications but before `activation-started` | Neither writer reopens automatically; legacy remains fenced and target remains quarantined. | Re-acquire the lock; verify the matching registration/receipt pair and quarantine state, then prove zero target-only accepted effects before either resuming activation or explicitly restoring legacy authority. | **No automatic rollback**; explicit restoration requires proof of zero target-only accepted effects. |
| Activation begun with uncertain write acceptance | Treat Learning target as the only candidate authority; legacy remains fenced. | Prohibit automatic fallback; repair/restart the authoritative Learning target with compatible artifacts and evidence. | **No**, unless explicit evidence later proves no accepted target-only effects. |
| Confirmed post-activation writes | Learning target only. Legacy remains preserved and non-writing. | Continue forward recovery on Learning authority; any restore happens to a separate destination pending promotion proof. | **No** (lossy rollback is out of scope for this spec). |

## 5) Backup, restore, rollback, and cold-data fencing

A **write** includes durable intents/receipts, issuance, queue state changes, and essential maintenance effects—not only a completed review. Queue GET endpoints can write state; HTTP method alone is not a read-only safety guarantee.

Maintain both restore-verified checkpoints: pre-cutover legacy backup and post-import Learning backup.

| Recovery rule | Required behavior |
| --- | --- |
| 1. Before target activation | Rollback to legacy is permitted only after proving no target-only application effects were accepted, fencing/stopping the target, retaining pending browser operations, and restoring the matching legacy registration/receipt and untouched authoritative source. A timeout or process crash is not proof of no accepted write. |
| 2. Activation-started boundary | Persist `activation-started` before enabling target maintenance, write-capable routes, or traffic. From that point, or whenever acceptance is uncertain, automatic legacy fallback is prohibited. Default safe path is repair/restart of the existing authoritative Learning database with compatible artifacts, not restoration of an older snapshot. |
| 3. Restore destination and promotion proof | Restore to a separate recovery destination; never overwrite the authoritative target as an automatic fallback. Promotion requires evidence that all accepted effects since the snapshot are preserved/recovered. A separately verified replay/WAL mechanism may provide that evidence, but this spec does not assume one exists or require implementing a new recovery platform. |
| 4. Unproven preservation boundary | Without preservation evidence, stop and report the unresolved recovery/data-loss boundary. Never silently discard post-backup writes or claim zero-loss recovery. Any knowingly lossy recovery requires a separate explicit decision; this spec does not authorize it. |

## 6) Normal-start compatibility gate (hard merge gate)

No PR may delete launcher-dependent backend or worker legacy sources unless all are true. Genuinely independent optional frontend deletions—including [#406](https://github.com/aaweaver-actuary/tempo/pull/406) and [#407](https://github.com/aaweaver-actuary/tempo/pull/407)—are outside this gate when they preserve accepted Study behavior and remove no artifact or interface required by the launcher cutover.

1. Normal command set (`tempo install/start/restart/status/doctor/backup`) follows §3.1 responsibilities and targets Study-only composition by default.
2. Default build/proxy path no longer depends on legacy `build:local` + `api:8000` contract.
3. One-time migration gate enforces #267-qualified history transfer and blocks otherwise.
4. #358 host fingerprint and pending-authority evidence are recorded for the real installation.
5. Lifecycle proof shows no permanent dual-run mode and no competing writer path.

## 7) Preflight matrix

### 7.1 Source-qualified checks (can be proven now)

| Check | Evidence | Status |
| --- | --- | --- |
| Default launcher still uses legacy root composition contract. | `tempo`, `scripts/tempo-cli.mjs`, `scripts/tempo-deployment.mjs`, `scripts/tempo-runtime.mjs`. | **PASS (observed)** |
| Default web image/proxy still targets legacy API path. | `Dockerfile.web` (`build:local`), `nginx.conf` (`proxy_pass http://api:8000`). | **PASS (observed)** |
| Learning runtime requires explicit migration and strict authority checks. | `services/learning/runtime/src/.../cli.py`, `api.py`, `config.py`, `database/tempo_learning_database/ddl/migrate.py`. | **PASS (observed)** |
| `tempo-learning migrate` is schema/workspace setup, not historical data migration. | `database/tempo_learning_database/ddl/migrate.py`; #267 scope. | **PASS (observed)** |

### 7.2 Host/device/data-required checks (cannot be proven from source alone)

| Check | Owner | Status |
| --- | --- | --- |
| Actual live launcher/build/image/db fingerprint and both-device authority state. | #358 | **BLOCKED / PENDING** (actual host/device fingerprint remains unavailable). |
| Latest genuine-history migration + pending-intent reconciliation rehearsal. | #267 | **PENDING** (latest real-history transfer and pending-intent reconciliation remain unaccepted). |
| Final Study-only Compose/ingress interface and cleanup contract. | #367 | **PENDING** (final Compose/ingress integration and cleanup contract remain outstanding; #378–#380 are not complete). |
| Final Study artifact identity and closure proof. | #364 | **MERGED** (bounded source artifact/closure qualification; no live cutover or host deployment proof). |
| Final Learning runtime image/startup contract proof. | #365 | **MERGED** (bounded runtime artifact/startup qualification; no live cutover or host deployment proof). |

---

This document is intentionally bounded: it specifies the cutover contract and merge gates, but performs no live migration, no launcher mutation, and no deployment action.
