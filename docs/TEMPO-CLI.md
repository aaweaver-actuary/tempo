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
checkout from the verified Tempo repository. It checks complete CI for that
exact revision and loads the updated CLI before deployment. Images are built
before stopping services. When prepared images/configuration or an explicit
restart may recreate PostgreSQL or Redis, application writers stop before
dependency startup. Ordinary compatible starts reuse recorded image IDs and
forbid dependency recreation; they do not take a migration backup or stop the
application. A dependency startup failure after writer shutdown leaves writers
stopped and records no successful deployment.

For migrations, application services and recurring backups stop first. A new
custom-format backup gets a checksum, an isolated restore, and a comparison of
every public table. Only then do ordered migrations run. Required schema,
workers, PostgreSQL API health, the web endpoint, and service state must pass
before success is recorded. Database volumes, Redis state, and engine journals
remain intact.

If GitHub is unavailable or the candidate has failed checks, `start` can use
the last recorded, verified image set with its saved Compose definitions. It
clearly reports the blocked update and running revision. Fallback requires
the images to exist and the database schema to match; it never rebuilds old
code from newer source. Before the first verified deployment, a blocked update
is an error.

## When something fails

Read the specific error, then use `tempo doctor` and `tempo logs api`. A missing
volume, conflicting container, invalid history, missing credential, or failed
restore is a stop condition. The CLI does not substitute sample data, silently
create an empty database, discard local changes, or skip release checks.

A failed migration leaves application services stopped. Inspect the failure
and ledger, repair the cause, then explicitly run `tempo migrate --retry`.
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
