# Disposable Study Compose stack

This local-only composition connects the Study web image, the Learning runtime,
and an invocation-owned PostgreSQL cluster. It does not change the normal Tempo
launcher. The only published container port is the Study web port on loopback.
Learning and PostgreSQL remain on the Compose network. PostgreSQL has its own
project-scoped data volume; the generated runtime files use a second private
project-scoped volume. The PostgreSQL service, its data volume, and the
project-scoped default network carry `tempo.owner=tempo-study-test-<id>` and
`tempo.disposable=true`; Compose's own resource labels remain intact.

The project name and database identity share one fresh 16–32 character
lowercase hexadecimal invocation ID:

```text
tempo-study-test-<invocation-id>
learning_study_test_<invocation-id>
```

Compose rejects missing run configuration, but it does not validate the ID's
character set. Both the ownership producer and bootstrap validate the ID and
database identity before use. Use a new ID and private invocation directory for
each disposable cluster. Do not reuse a completed invocation directory or
volume.

## Inputs and boundaries

Create a private working directory and a private invocation directory whose
basename is the ID. Keep the admin URL file, PostgreSQL password file, and
explicit Compose environment file outside the repository with mode `0600`;
directories containing them should be mode `0700`. The PostgreSQL password
file and the password in the admin URL file must match. The environment file
contains only non-password configuration and host file paths:

```dotenv
TEMPO_STUDY_RUN_ID=0123456789abcdef
TEMPO_STUDY_WORKSPACE=study-local
TEMPO_STUDY_TIMEZONE=America/New_York
TEMPO_STUDY_PORT=4187
TEMPO_STUDY_ADMIN_URL_FILE=/private/path/admin-url
TEMPO_STUDY_POSTGRES_PASSWORD_FILE=/private/path/postgres-password
TEMPO_STUDY_INVOCATION_DIRECTORY=/private/path/run/0123456789abcdef
```

The admin URL must target `postgres:5432/postgres` and use the same password as
the PostgreSQL password file. The invocation directory must be an existing
private `0700` directory named exactly for the ID. The host receipt producer
creates `ownership.json` inside it with mode `0600`; Compose mounts that exact
directory read-only at a container path ending in the same ID. This preserves
the receipt loader's directory-mode and basename checks.

The Learning database owner currently generates role names by appending
`_writer` and `_reader` to the database identity. Compose uses those exact
restricted-role DSNs on the internal `postgres:5432` service and points the
standard `PGPASSFILE` setting at `runtime.pgpass`. Learning receives no admin
URL, PostgreSQL superuser password, or ownership receipt. The bootstrap profile
alone receives the read-only admin URL, invocation receipt, and database owner
source. The web service receives none of these files and never receives a
privileged token.

The runtime's `TEMPO_LEARNING_HOSTS` allowlist contains only the hostname
`127.0.0.1`, without a port, because Starlette 0.47.3's
`TrustedHostMiddleware` removes the port before comparing the request host to
that list. Keep `TEMPO_LEARNING_ORIGINS` as the full browser origin, including
the scheme and published port, because origin validation is port-specific.

Bootstrap writes `connection.json`, `runtime.pgpass`, `study.token`, and
`operator.token` under the runtime volume. The image's installed Python runs
the mounted database-owner source tree at
`/source/database/tempo_learning_database`; the runtime image's wheel excludes
`tooling`, so the read-only source mount provides the two task-owned tooling
modules while the image supplies Python and its installed driver dependencies.
Bootstrap runs as root only to create the files and then changes ownership of
that private output directory and its four files to `10001:10001`, preserving
directory mode `0700` and file mode `0600`. Learning runs with its image's
fixed Python CLI entrypoint as `10001:10001` and mounts the runtime volume
read-only. No Docker socket or Docker CLI is mounted into bootstrap.

## Invocation

Set shell variables to the private paths represented above. Keep the
environment file explicit on every Compose call and use the same explicit
project name and Compose file each time. Build from the committed Dockerfiles
and lockfiles, then wait for the one-shot bootstrap before starting the runtime
services.

```sh
set -e
PROJECT="tempo-study-test-$TEMPO_STUDY_RUN_ID"
COMPOSE_FILE="$PWD/docker-compose.study.yml"

docker compose --profile bootstrap --env-file "$STUDY_ENV_FILE" -f "$COMPOSE_FILE" -p "$PROJECT" config --format json
docker compose --profile bootstrap --env-file "$STUDY_ENV_FILE" -f "$COMPOSE_FILE" -p "$PROJECT" up -d --wait --no-build --pull never postgres

python3 database/tempo_learning_database/tooling/disposable_ownership.py \
  --compose-file "$COMPOSE_FILE" \
  --env-file "$STUDY_ENV_FILE" \
  --invocation-directory "$TEMPO_STUDY_INVOCATION_DIRECTORY" \
  --invocation-id "$TEMPO_STUDY_RUN_ID" \
  --database "learning_study_test_$TEMPO_STUDY_RUN_ID"

docker compose --profile bootstrap --env-file "$STUDY_ENV_FILE" -f "$COMPOSE_FILE" -p "$PROJECT" build bootstrap study-web
docker compose --profile bootstrap --env-file "$STUDY_ENV_FILE" -f "$COMPOSE_FILE" -p "$PROJECT" up -d --no-build --pull never bootstrap
BOOTSTRAP_ID="$(docker compose --profile bootstrap --env-file "$STUDY_ENV_FILE" -f "$COMPOSE_FILE" -p "$PROJECT" ps -aq bootstrap)"
test -n "$BOOTSTRAP_ID"
test "$(docker wait "$BOOTSTRAP_ID")" = 0
docker compose --profile bootstrap --env-file "$STUDY_ENV_FILE" -f "$COMPOSE_FILE" -p "$PROJECT" up -d --wait --no-deps --no-build --pull never learning
docker compose --profile bootstrap --env-file "$STUDY_ENV_FILE" -f "$COMPOSE_FILE" -p "$PROJECT" up -d --wait --no-deps --no-build --pull never study-web
```

The bootstrap service uses the published flags `--admin-url-file`,
`--ownership-receipt-file`, `--database`, `--workspace`, `--timezone`, and
`--output-directory`. It depends on PostgreSQL health and Learning depends on
successful bootstrap completion. The bootstrap profile is opt-in and the
completed service has `restart: "no"`. The explicit `docker wait` gates
Learning on its successful exit. A later `docker compose up learning` starts
an exited bootstrap dependency again; `--no-deps` prevents that replay, which
the nonempty output guard rejects. Learning reaches health before web starts.

To remove only this disposable project after preserving anything needed from
it, pass the same environment file, Compose file, and `-p "$PROJECT"` to
`docker compose down --volumes --remove-orphans`. A new attempt must use a new
invocation ID and directory.

## Verification boundary

The focused unit contract is
`tests/unit/study-compose-contract.test.ts`. It asks Docker Compose to render
the model as JSON with a temporary explicit environment file, checks the
service/port/isolation/image/authority contract, and verifies that missing
required inputs fail. The test renders configuration without starting
containers.

Native acceptance must run the producer and bootstrap against the actual
Compose-created PostgreSQL container, volume, network, and Learning image,
then verify that the receipt is accepted, bootstrap exits successfully, UID
`10001` can read the private runtime files, and Learning becomes ready. The
mocked tests and configuration rendering do not establish that proof.

## Restricted-role, web and teardown evidence — 2026-10-25

**Partial qualification: #378's subsequent native proof passed; #380 remains
incomplete.** This run used clean main
`f9449a2a9b32e8a86f7ad2030a28231a1d8e1d26` (tree
`9d0831b0fc8f31bbe45ef7ea1fc9692a1acb3064`) in an isolated temporary checkout.
No implementation was changed. The existing bootstrap/ownership producer and
committed Compose/Dockerfiles were used. The bootstrap and Compose unit suites
were not repeated; #408 and #415 retain their owning implementation/test proof.

One new project, `tempo-study-test-8973949d8dd875a8`, ran from
15:04:13 to 15:04:43 UTC. Its database was
`learning_study_test_8973949d8dd875a8`, workspace
`qualification-8973949d8dd875a8`, timezone `Etc/UTC`, and sole published origin
`http://127.0.0.1:53498`. Docker was `29.9.2`, Compose `5.6.1`, and images were
ARM64. Task-private admin/password/environment files were `0600` under a `0700`
directory outside the checkout; no environment or user configuration discovery
was used. An exclusive advisory lock on
`/tmp/tempo-mac-validation-20261025.lock` was held by the parent throughout the
Docker child, including teardown. The lock was opened in append mode without
truncation and was not unlinked or replaced.

### Exact artifacts and retained evidence

The built image identities, also read from the actual owned containers, were:

| Artifact | Image ID / repository digest (`sha256`) |
| --- | --- |
| Learning and one-shot bootstrap | `4be58c90a7cb5f559a494328ad05fe6239f34d816e59eebb9c0161dd0ec6df95` |
| Study web | `4c5d339a9cec23a07a21273601f5414e0923b0582eb7e443d56d4ba6faec6c99` |
| `postgres:18.6-trixie` | `74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336` |
| `node:22-slim` build input | `de170b39439eb0e5a3444ad7d60b015614a381e10fecae2e30ec9e735122f38d` |
| `nginx:1.28-alpine` build input | `a8b39bd9cf0f83869a2162827a0caf6137ddf759d50a171451b335cecc87d236` |
| `python:3.12-slim` build input | `a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704` |
| `ghcr.io/astral-sh/uv:0.12.22` build input | `f513a91fc62fe7c17567eee97230dd198e43edb8a9fbecca843714a4358fe1bc` |

Sanitized local evidence remains outside the checkout at
`/tmp/tempo-378-380-evidence-20261025`: `receipt.json` contains all 47 executed
subprocess argument vectors, exit statuses, log hashes and the 65 completed
assertion checkpoints; `commands.txt` expands every command without shell
abbreviations; HTTP bodies, inspected ownership, schema, roles and service logs
are retained beside it. These are diagnostic artifacts for this invocation,
not a new supported runner or a 65-test suite. The overall runner exited **1**
on the outage observation described below; completed assertions do not make
that run a pass. There were no repeated or skipped unit suites.

| Retained file | SHA-256 |
| --- | --- |
| `receipt.json` | `a7f2c93bd7b637782b4c02031ff8c200b30f4d9ef1602304dffb320433602903` |
| `qualify.py` (executed harness) | `766fa0d95b0b845061e2227a79ad8b66ad1ce09205f435cccd57e169520b5b69` |
| `roles.py` (native assertions) | `d04c48eb8c42f29f31c6a4c2ab7ddf271e9e53ae142e85dfd0c80d1059843c88` |
| `service-logs.log` | `e96fd12b0b90c7ac37ca8c953d83ec88db3049f0a04fe539c2cfb0553cd74104` |
| `roles.log` | `bffd9b34d1bea82c3e0e3381b436ac618d24c8241c402a043fcfc3470aecc921` |

The launch command was
`python3 /tmp/tempo-378-380-evidence-20261025/locked-run.py`. The principal
expanded lifecycle commands below are a transcript: their private input files
were destroyed after teardown, so the old invocation must not be rerun.

```sh
docker compose --profile bootstrap --env-file /tmp/tempo-378-380-evidence-20261025/private/study.env -f /tmp/tempo-378-380-20261025/docker-compose.study.yml -p tempo-study-test-8973949d8dd875a8 config --format json
docker compose --profile bootstrap --env-file /tmp/tempo-378-380-evidence-20261025/private/study.env -f /tmp/tempo-378-380-20261025/docker-compose.study.yml -p tempo-study-test-8973949d8dd875a8 build bootstrap study-web
docker compose --profile bootstrap --env-file /tmp/tempo-378-380-evidence-20261025/private/study.env -f /tmp/tempo-378-380-20261025/docker-compose.study.yml -p tempo-study-test-8973949d8dd875a8 up -d --wait --no-build --pull never postgres
python3 /tmp/tempo-378-380-20261025/database/tempo_learning_database/tooling/disposable_ownership.py --compose-file /tmp/tempo-378-380-20261025/docker-compose.study.yml --env-file /tmp/tempo-378-380-evidence-20261025/private/study.env --invocation-directory /tmp/tempo-378-380-evidence-20261025/private/8973949d8dd875a8 --invocation-id 8973949d8dd875a8 --database learning_study_test_8973949d8dd875a8
docker compose --profile bootstrap --env-file /tmp/tempo-378-380-evidence-20261025/private/study.env -f /tmp/tempo-378-380-20261025/docker-compose.study.yml -p tempo-study-test-8973949d8dd875a8 up -d --no-build --pull never bootstrap
docker compose --profile bootstrap --env-file /tmp/tempo-378-380-evidence-20261025/private/study.env -f /tmp/tempo-378-380-20261025/docker-compose.study.yml -p tempo-study-test-8973949d8dd875a8 up -d --wait --no-deps --no-build --pull never learning
docker compose --profile bootstrap --env-file /tmp/tempo-378-380-evidence-20261025/private/study.env -f /tmp/tempo-378-380-20261025/docker-compose.study.yml -p tempo-study-test-8973949d8dd875a8 up -d --wait --no-deps --no-build --pull never study-web
docker compose --profile bootstrap --env-file /tmp/tempo-378-380-evidence-20261025/private/study.env -f /tmp/tempo-378-380-20261025/docker-compose.study.yml -p tempo-study-test-8973949d8dd875a8 exec -T learning /opt/learning/bin/python -I - < /tmp/tempo-378-380-evidence-20261025/roles.py
docker compose --profile bootstrap --env-file /tmp/tempo-378-380-evidence-20261025/private/study.env -f /tmp/tempo-378-380-20261025/docker-compose.study.yml -p tempo-study-test-8973949d8dd875a8 stop learning
docker compose --profile bootstrap --env-file /tmp/tempo-378-380-evidence-20261025/private/study.env -f /tmp/tempo-378-380-20261025/docker-compose.study.yml -p tempo-study-test-8973949d8dd875a8 down --volumes --remove-orphans
```

### Native restrictions and migration ordering: passed

Bootstrap exited `0`. Before any Learning container existed, `psql` inside the
attested PostgreSQL container read `learning.instance` and
`learning.migration_history`. The four IDs and SHA-256 checksums exactly matched
the committed manifest and migration sources:

| Migration | Checksum |
| --- | --- |
| `learning.runtime/0001` | `0dc465b03c6cd0e2487744bdbb58d9a21d1db8e4aa4037323295fe507d0df26e` |
| `learning.runtime/0002` | `c3a1b03b8f4aad7e026be8a45eaf4ccd6a567ce5da23c9c42390ae6d623ef4ab` |
| `learning.remediation/0001` | `96907361dc003e78703dc57182bd020f96e82532bcea6724083326171242da0d` |
| `learning.bridge/0001` | `c4cde173083334cee565a125daa79e0e111d3b0498c87ca9151a61ac3679be91` |

The cluster system identifier was `7700621498996572202`, matching both
`pg_controldata` and the producer receipt. Learning was started only after
successful bootstrap and this schema check, then reached readiness. This proves
explicit migration before readiness for this run; HTTP startup did not supply
the schema.

`roles.py` ran with the installed image's Python as UID `10001`. It read only
the bootstrap-created restricted-role DSNs and private passfile, and made real
PostgreSQL connections to this disposable server. All attempted changes were
rolled back, including a permitted writer settings update used as a control.

| Assertion | Observed SQLSTATE |
| --- | --- |
| Writer `CREATE TABLE learning.qualification_denied(id integer)` | `42501` |
| Writer `CREATE SCHEMA qualification_denied` | `42501` |
| Writer `ALTER TABLE learning.settings ADD COLUMN qualification_denied integer` | `42501` |
| Writer `UPDATE learning.instance SET workspace_id='qualification-other' WHERE id=1` | `42501` |
| Writer `DELETE FROM learning.instance WHERE id=1` | `42501` |
| Writer `INSERT INTO learning.instance VALUES(2,'qualification-other')` | `42501` |
| Reader `UPDATE learning.settings SET timezone=timezone WHERE id=1` with default read-only transaction | `25006` |
| Reader same `UPDATE`, `INSERT INTO learning.settings(id,timezone) VALUES(2,'Etc/UTC')`, and `DELETE FROM learning.settings WHERE id=1`, each after `SET TRANSACTION READ WRITE` | `42501` for all three |

The workspace binding remained unchanged, instance/settings each still had one
row, and the rejected table did not exist. The runtime directory was `0700`;
its exact four files were `0600`, owned by UID `10001`, and readable by that
runtime user. The sanitized manifest in `roles.log` has schema version `1`,
the database/workspace/timezone above, owner/writer/reader names formed by
appending `_owner`/`_writer`/`_reader` to the database identity, and DSNs containing
only `host=postgres port=5432 dbname=<database> user=<restricted-role>`.
Its remaining values are `pgpass_file=runtime.pgpass`,
`study_token_file=study.token`, and `operator_token_file=operator.token`.
No password, token bytes or admin DSN were retained in the evidence.

### Web-origin wiring: passed; outage and recovery: incomplete

The harness used Python `urllib.request` against `http://127.0.0.1:53498`,
with a 10-second client timeout. Authenticated requests supplied the generated
Study bearer in memory, the exact workspace header and that origin; credentials
were omitted from logs. These actual responses were retained:

| Request/assertion | Observed result |
| --- | --- |
| `GET /` | `200` Study shell; COOP `same-origin`, COEP `require-corp` |
| `GET /assets/index-0TpZHsl1.js`, `GET /assets/index-BoEejwBZ.css` | Both `200`, neither returned the HTML shell |
| `GET /study-composition.json` | `200`, valid JSON |
| `GET /sw.js` | `200`, `application/javascript`; generated build asset list present and worker source excludes `api/` caching |
| `GET /health/live` | `200`, `{"status":"alive"}` |
| `GET /health/ready` | `200`, `{"status":"ready","workspace_id":"qualification-8973949d8dd875a8"}` |
| Valid `GET /api/study/bootstrap` | `200`, schema version `1`, expected workspace and `Etc/UTC` |
| Same bootstrap with wrong bearer | `401`, `{"detail":"Learning workspace credential required"}` |
| Same bootstrap with wrong workspace | `403`, `{"detail":"Workspace scope mismatch"}` |
| Same bootstrap with origin `https://invalid.example` | `403`, `{"detail":"Browser origin is not allowed"}` |
| Authenticated `GET /api/qualification-missing` | `404`, `{"detail":"Not Found"}`; no HTML/`200` fallback |

The worker check establishes delivery and source assertions, not browser
registration, activation, offline cache behavior or fresh-PGN behavior.

Stopping only this project's Learning service succeeded and inspection confirmed
it was no longer running. The first subsequent authenticated
`GET /api/study/bootstrap` **received no HTTP response within 10 seconds**;
the client raised `TimeoutError: timed out`. nginx recorded that request as
`499` with zero response bytes after the client closed. This is not an observed
`502`/`503`/`504`, nor proof of the required explicit outage response/no legacy
fallback. The observation budget was shorter than the proxy's configured
limits; no implementation defect or repair is established by this result.

The cleanup handler then tore down the project. Outage `/health/live`,
`/health/ready`, static continuity, and restart of the same installed artifacts
with recovered readiness/bootstrap were **not reached**. A second project or
reuse after teardown was not attempted under the one-new-project constraint.
Those assertions remain open under #380. Independent review agreed with this
partial result and the cleanup evidence; this receipt does not close #380.

### Ownership-verified teardown: passed

There were exactly three long-lived services: PostgreSQL, Learning and Study
web. Bootstrap was the fourth, successfully exited, container. There were no
optional containers or published PostgreSQL/Learning ports in this project.
Before teardown, project-filtered inventory required exactly these four known
services, the two expected volumes and the default network. Every container
matched its Compose project; PostgreSQL, its data volume and network also
matched `tempo.owner` and `tempo.disposable=true`. The runtime-secrets volume
matched its exact Compose project/volume labels and expected mount; it has no
custom disposable label in the committed model.

The producer receipt's PostgreSQL container ID
`36489c9b5e39324173ad753cde73f68dd0331aad224b0b62f14b486a211b355c`,
network ID `353d2ef4c0beca1223d2413d1a30cdae89e79f8bef01ebd76ff1af5dade58630`,
volume name and cluster identifier all matched the inspected resources before
`down --volumes --remove-orphans`. Mounts referenced only the two owned volumes.
The exact project had no unexpected containers/orphans to remove.

Teardown exited `0`. All captured container IDs, both volume names and the
network ID were subsequently absent (`docker inspect`/volume/network inspect
returned exit `1` and `[]`). Project-filtered `docker ps -aq`, `docker volume
ls -q`, and `docker network ls -q`, each with
`--filter label=com.docker.compose.project=tempo-study-test-8973949d8dd875a8`,
were empty. Both invocation-specific application image tags and all three
private input files were removed. Shared official images/caches and the
advisory lock were preserved. No production database, user history, normal
launcher, installed Study, host settings, deployment or CI configuration was
accessed or changed. This is synthetic component/composition evidence, not
actual-data, deployment or full-system completion.
