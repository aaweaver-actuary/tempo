# Managing local Tempo

After a merge, run:

```sh
tempo start
```

Tempo checks the latest main revision and its complete GitHub quality gate,
prepares one coherent image set, checks the existing database, and performs
verified maintenance when migrations are needed. It opens the browser after
readiness passes and keeps running in the background. Closing the terminal
does not stop it.

## Install once

From the main study checkout, after this CLI is merged:

```sh
./tempo install
```

Open a new terminal to use `tempo` from any directory. The current terminal
can use `./tempo start`. Installation requires Node.js 22.13 or newer, Git,
Docker Compose, the existing `.env` file, four existing product volumes, and
private secret files outside the checkout. It does not create a database,
import SQLite, install development dependencies, or start the product.

Registration is saved in `~/.config/tempo/config.json`. Deployment records,
operation progress, and sanitized failure logs live under
`~/.local/share/tempo/`. Secret values are never saved there. The installer
adds `~/.local/bin` to the macOS interactive shell path. Existing registrations
and unrelated commands are not overwritten.

## Commands

| Command | Use |
| --- | --- |
| `tempo start` | Start, update, and migrate automatically when safe |
| `tempo start --no-open` | Start without opening a browser |
| `tempo restart` | Perform the same checks and recreate application services |
| `tempo stop` | Gracefully stop the stack, retaining all study data |
| `tempo status` | Show the registered target, recorded revision, schema, and services |
| `tempo doctor` | Read-only diagnosis with the last maintenance result and API error |
| `tempo logs [service] [--follow]` | Inspect recent or live service logs |
| `tempo backup` | Stop writers, take and restore-verify a backup, then restore service state |
| `tempo migrate` | Explicitly run the checked update path without opening the browser |
| `tempo start --plan` | Inspect and preview without updating source, services, or the database |

The Mac launcher and older shell helpers use this same CLI. You no longer
need to decide whether a merge requires a rebuild or a migration.

## What happens during an update

The CLI preserves local edits and accepts only a fast-forward of a clean main
checkout from the verified Tempo repository. It accepts a complete successful
allowed main CI run for that exact revision, even if a later rerun is pending
or failed. Immediately before fast-forwarding, it rechecks the branch, exact
HEAD, and cleanliness; concurrent edits or source movement block the update.
It loads the updated CLI before deployment. Images are built
before stopping services. Before maintenance, resolved Compose configuration
must pin the API to its PostgreSQL reader and both Celery workers to
`postgresql://tempo_writer@postgres:5432/tempo` for reads and writes, without
`TEMPO_DB_PATH`. Each worker must use `/run/secrets/writer_pgpass` backed by its
attached `writer_pgpass` secret; exposed secret modes must be `0400`, and secret
source files must have private permissions. Workers cannot define `PGPASSWORD`
(even empty), `PGHOSTADDR`, or `PGSERVICE`:
these can replace passfile credentials or redirect the connection despite the
explicit DSNs. Ordinary libpq host/port/database/user defaults remain permitted.
Both workers and the scheduler must use `redis://redis:6379/0`. Invalid wiring
blocks deployment before any service
changes. When prepared images/configuration or an explicit
restart may recreate PostgreSQL or Redis, application writers stop before
dependency startup. Ordinary compatible starts reuse recorded image IDs and
forbid dependency recreation; they do not take a migration backup or stop the
application. A dependency startup failure after writer shutdown leaves writers
stopped and records no successful deployment.

Before checking fallback schema compatibility, the CLI compares running
applications (including recurring backups) with the saved deployment's immutable
image IDs, project/service labels, and resolved Compose hashes. An uncommitted,
partial, or mixed rollout is stopped as one application layer. If the old
deployment cannot use the migrated schema, writers stay stopped, the receipt
stays unchanged, and failure is recorded. PostgreSQL is preserved for a fix
forward. Matching committed applications keep running even with a stale journal;
stopped containers do not force another shutdown.

Before writer shutdown or dependency recreation, the CLI runs `postgres --version`
from the selected immutable PostgreSQL image without networking or study-volume
mounts. Its actual server major must match the registered major. This applies to
ordinary tags, registry-qualified tags, digest references, and saved fallback IDs;
tag spelling does not establish compatibility. A mismatch requires the separate
PostgreSQL major-upgrade procedure.

For migrations, application services and recurring backups stop first. A new
custom-format backup gets a checksum, an isolated restore, and a comparison of
every public table. The CLI then atomically saves the original history digest,
counts, column contract, starting/intended schema, target/database identity, and
verified backup reference in `migration-guard.json` before any migration commits.
Every atomic state replacement flushes the file before rename and its containing
directory afterward. A directory-sync failure is a deployment-state durability
error: migrations cannot begin, writers remain stopped, and no success is recorded.
Only then do ordered migrations run. Required schema,
workers, PostgreSQL API health, the web endpoint, and service state must pass
before success is recorded. Database volumes, Redis state, and engine journals
remain intact.

If GitHub is unavailable or the candidate has failed checks, `start` can use
the last recorded, verified image set with its saved Compose definitions. It
clearly reports the blocked update and running revision. Fallback requires
the images to exist and the database schema to match; it never rebuilds old
code from newer source. Before using the no-recreate path, it inspects existing
PostgreSQL/Redis containers for the registered project/service, immutable image
IDs, and saved Compose configuration hashes. Missing or mismatched dependencies
require writer shutdown and container recreation from the saved deployment,
using the existing named volumes without restoring old data. Explicit backup
fails closed on a dependency mismatch and directs you to `tempo start` first.
Before the first verified deployment, a blocked update is an error.

Persistent volumes have fixed owners and destinations: PostgreSQL data belongs
to `postgres:/var/lib/postgresql`, backups to `postgres-backup:/backups`, Redis
data to `redis:/data`, and engine operations to `defense-engine:/state`. Extra,
swapped, relocated, and missing persistent mounts block maintenance. Secrets
and temporary memory mounts are separate from persistent data.

## When something fails

Redis readiness requires an actual `PONG`. Redis health checks reject loading
and error replies; the CLI also waits up to 180 seconds for saved Redis data to
finish loading, including deployments recorded with the older health check.
Temporary connection failures are retried with bounded probes. Authentication,
configuration, or unexpected replies fail immediately. A readiness failure
records the actual redacted reply, elapsed time, `checking_redis` phase, and
failure timestamp; schema checks and application startup have not advanced.
Inspect `tempo logs redis`, address any reported terminal error, then run
`tempo start` again. No Redis data is discarded to make startup succeed.

Read the specific error, then use `tempo doctor` and `tempo logs api`. A missing
volume, conflicting container, invalid history, missing credential, or failed
restore is a stop condition. The CLI does not substitute sample data, silently
create an empty database, discard local changes, or skip release checks.

A failed migration leaves application services stopped. Inspect the failure
and ledger, repair the cause, then explicitly run `tempo migrate --retry`.
Retry continues the same history-verification obligation: it reuses the original
fingerprint and backup, applies only remaining migrations, and verifies the original
history even when the ledger is already current. New candidate revisions require
the same explicit retry and compatible schema/ledger. The guard is atomically
marked verified only after original history and required schema pass, before
application startup. Failed verification retains the original guard and backup,
keeps writers stopped, and cannot publish deployment success. `tempo doctor`
shows this obligation and backup reference. Do not delete the guard to resume;
inspect and fix forward, or use a separate explicit recovery procedure.
An interrupted maintenance run retains its progress and backup location. A
temporary restore database left by failure is retained for inspection; the
CLI drops only the database it created during a successfully verified drill.

After schema changes, fix forward with compatible code. Never restore an
older backup over a database that has accepted newer study writes. Initial
SQLite cutover, destructive restore, and PostgreSQL major upgrades remain
separate procedures in [PostgreSQL maintenance](POSTGRES-MAINTENANCE.md).

## Development validation scope

CLI orchestration affects source updates, target isolation, container lifetime,
and PostgreSQL maintenance. Plausible failures include a wrong target, local
work loss, stale CI evidence, image/schema mismatch, partial maintenance, and
false readiness, dependency recreation with active writers, and rejecting an
intentional legacy queue-label normalization. The smallest proof is the dependency-free CLI regression
suite, its regular Vitest wrapper, and the read-only migration-status pytest
cases. The settled candidate additionally runs the real disposable PostgreSQL
durability gate, lint, and typecheck. CI owns final complete candidate and merge
validation; no runtime gate is run against the live study installation.
