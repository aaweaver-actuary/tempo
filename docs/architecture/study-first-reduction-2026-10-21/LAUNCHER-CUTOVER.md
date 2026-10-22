# Launcher cutover spec (359-F / issue #369)

Status: **source-backed specification only** (no launcher/runtime mutation in this issue).  
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

Therefore, **no PR may delete legacy implementation while default launcher still routes through this path**.

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
- **Browser authority headers** (`Authorization: Bearer …`, `X-Tempo-Workspace`) must match runtime workspace/origin checks (`app/lib/study-connection.ts`, runtime `api.py` middleware).
- **Secrets** remain file-backed and private; no token/password literal in compose, CLI args, or issue text.
- **Workspace identity** must remain stable across cutover, or cutover is blocked until pending authority-bound records are reconciled.

## 3) File-by-file plan for the later launcher PR

This issue does **not** edit these files; it specifies the required later change.

| File(s) | Required later change | Safety constraint / expected failure |
| --- | --- | --- |
| `tempo` | Keep as thin wrapper to CLI; no policy logic in shell script. | Failure remains explicit Node/CLI error; no hidden fallback launcher. |
| `scripts/tempo-cli.mjs` | Add one-time v1→v2 target migration flow for `start/restart/doctor/backup`; default install/registration must point to Study-only composition after gate passes. | If live fingerprint (#358) or migration readiness (#267) is missing, block with actionable error; do not mutate target. |
| `scripts/tempo-deployment.mjs` | Replace legacy target validation contract with Study-only contract (services, volumes, ports, secrets, allowed origin/host/runtime settings). | Reject mixed legacy/new ownership, disposable-volume rebinding, secret files inside checkout, or mutable image identity. |
| `scripts/tempo-runtime.mjs` | Replace lifecycle service set, readiness checks, backup choreography, and receipt identity to Learning stack; keep maintenance lock/single-writer fencing semantics. | Any unresolved migration guard/history verification keeps writers stopped; never auto-start competing legacy writer. |
| `scripts/tempo-guidance.mjs` | Add cutover-specific blocker guidance (`host fingerprint missing`, `history migration not qualified`, `pending browser authority conflict`). | `doctor` must report blocked state honestly; no “eligible” status without cutover proofs. |
| Root compose default wiring (currently `docker-compose.yml` + maintenance overlay) | Repoint normal launcher to Study-only composition files from #367; legacy compose remains cold historical path only during transition. | No permanent dual-run normal mode; normal `tempo start` must have exactly one default path. |
| `Dockerfile.web` / ingress (`nginx.conf` or Study-specific nginx file) | Default web image path must use #364 Study artifact and proxy to Learning runtime endpoint/health contract. | Fail if API proxy points to legacy `api:8000` or if built artifact identity is not the approved Study build. |
| Docs (`docs/TEMPO-CLI.md`, `docs/POSTGRES-MAINTENANCE.md`) | Document one-time upgrade contract, rollback fence, and operator preflight matrix. | Must distinguish source checks vs host/data-required checks; no implied live acceptance. |

## 4) One-time upgrade sequence (future execution plan)

### Phase A — source-qualified preflight (no host mutation)
1. Freeze exact revision and artifact digests for #364/#365/#367 outputs.
2. Verify launcher references only the selected Study-only composition artifacts.
3. Verify lifecycle code paths contain one writer set (Learning) and one ingress path.

### Phase B — host/device/data preflight (**required, currently blocked/unknown**)
1. Collect actual deployment fingerprint and authority-bound pending state (#358).
2. Confirm #267 latest-history migration/importer is qualified on representative latest data.
3. Confirm both device browsers have reconciled/explicitly retained pending records for original authority.

### Phase C — maintenance window with single-writer fencing
1. Acquire launcher maintenance lock.
2. Stop legacy application writers/readers before any migration write.
3. Keep legacy volumes mounted **only** for verified read/export steps; no disposable stack rebinding.

### Phase D — explicit Learning schema setup
1. Run `tempo-learning migrate` against target Learning DB/workspace/timezone.
2. Apply runtime grants (`grant_runtime`) and verify restricted roles.
3. Verify `/health/ready` prerequisites (migration checksums + workspace binding) before opening traffic.

### Phase E — migrate genuine history via #267
1. Run #267 importer/reconciliation for reviews, schedules, receipts, pending operations/intents.
2. Verify semantic equivalence and idempotent replay on restart.
3. Fail closed on ambiguous identity/history; do not synthesize missing facts.

### Phase F — cut browser authority safely
1. Preserve origin/workspace contract expected by Study connection authority checks.
2. Reconcile or explicitly retain unresolved `tempo-study-intents` / offline pending records before authority switch.
3. Block cutover if retained records belong to foreign/unconfirmed authority.

### Phase G — activate Study-only normal launch
1. Start Study-only stack through normal `tempo start`.
2. Verify readiness, queue/review idempotency path, and post-start receipt identity.
3. Record cutover receipt (revision + image digests + schema/import evidence + authority identity).

## 5) Backup, restore, rollback, and cold-data fencing

1. **Pre-cutover legacy backup**: create + restore-verify backup before import/mutation.
2. **Post-import Learning backup**: create + restore-verify backup after #267 import and before opening user traffic.
3. **Rollback fence**:
   - before first accepted Study-only write: rollback may return to preserved legacy stack;
   - after first accepted Study-only write: do not resume legacy writer; recover forward from Learning backup.
4. **Cold-data rule**: legacy volumes remain preserved and non-writing until migration is proven; no disposable composition may auto-bind them.

## 6) Normal-start compatibility gate (hard merge gate)

No legacy-source deletion PR may merge unless all are true:

1. Normal `tempo install/start/restart/doctor/backup` target Study-only composition by default.
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
| Actual live launcher/build/image/db fingerprint and both-device authority state. | #358 | **UNKNOWN / BLOCKED (no host access)** |
| Latest genuine-history migration + pending-intent reconciliation rehearsal. | #267 | **UNKNOWN / BLOCKED (live-equivalent rehearsal pending)** |
| Final Study-only Compose/ingress interface and cleanup contract. | #367 | **UNKNOWN / PENDING final published interface** |
| Final Study artifact identity and closure proof. | #364 | **UNKNOWN / PENDING final artifact digest** |
| Final Learning runtime image/startup contract proof. | #365 | **UNKNOWN / PENDING final image digest** |

---

This document is intentionally bounded: it specifies the cutover contract and merge gates, but performs no live migration, no launcher mutation, and no deployment action.
