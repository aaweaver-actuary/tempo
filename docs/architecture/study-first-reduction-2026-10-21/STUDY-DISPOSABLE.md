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
