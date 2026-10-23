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

## Bounded qualification receipt for #378–#380 (2026-10-23)

**Partial, not an accepted #380 gate.** This section records the original Mac
executor's [#380 report](https://github.com/aaweaver-actuary/tempo/issues/380#issuecomment-6368830975)
and recorded task outcome. This documentation edit did not access the Mac, its
private resource manifest, or its containers, and did not rerun native tests.
PR #408 merged at `cb0ed5910e6bd05894919fd8d49f5dc7bc236975` from head
`f5f7210ee017d27324ed420fba002eb754fd8520`. The fresh disposable run
used `7d3a1b0378d604c1542baa7d830ec995d442264f`, invocation ID
`e9810721d5014b26b07a6d641d5fc181`, and loopback web port `49686`.
This is synthetic wiring evidence, not a live Study, historical migration,
normal launcher, phone deployment, or daily-behaviour result.

The original executor reports PostgreSQL → ownership receipt → bootstrap once
→ Learning → web on the fresh invocation. The receipt matched the owned
container, volume, network, and PostgreSQL cluster identity; bootstrap exited
0; UID 10001 read the four private runtime files. Three long-lived services
were healthy. DB and Learning had no host ports; web bound only to loopback.
Web readiness returned 200. Six read-only credential, workspace, Origin, and
Host probes returned the expected `401, 401, 403, 403, 200, 400`.
Restarting Learning restored health and readiness with bootstrap timestamps
unchanged. The original full request/response transcript and private resource
manifest are not present in this cloud worktree; these are owner-reported
results, not independently reproduced results. The earlier Learning and web
image IDs were reported only in abbreviated form, so this receipt does not
invent full digests for them.

On `f5f7210`, the executor reports 25 focused Python tests passed and direct
Compose isolation rendering passed, including rejection of all seven missing
required inputs. The rebuilt integrated web image was
`sha256:06337b94282f6eeee102fdbee8226e53b7b717de37877ad4e0da1eaace3fa663`.
It served the shell, JS, CSS and manifest; readiness was 200, unauthenticated
API was 401, and authenticated API was 200 with exact configured CORS.
Learning remained healthy and bootstrap was not replayed. The final
`npm run test:unit -- tests/unit/study-compose-contract.test.ts` **did not run**:
task-local `npm ci --offline` was denied under the retained no-install
restriction. Earlier Vitest evidence is historical, not a final-head pass.

The `f5f7210` image result is historical for the later merged `cb0ed591`
tree: that merge also includes #409 retirement changes and is not byte-identical.
Do not call the retained image a build of `cb0ed591` or of a future assembled
qualification branch. Pin that branch's exact source SHA and image IDs and
rebuild changed image inputs before claiming its acceptance. Reuse unchanged
Learning/PostgreSQL evidence only after recording exact source/image/resource
correspondence; otherwise requalify the changed boundary.

**Open acceptance evidence, not completed checks:**

- #378: On the owned disposable database, prove the restricted writer cannot
  create DDL or change the workspace binding, and the reader cannot write.
  Record the exact database, roles, applied migration identity and expected
  permission errors. HTTP credential rejection is not database-role proof.
- #379: Run the named final-source Vitest Compose contract only if its local
  dependencies are already permitted and available; otherwise retain the
  explicit blocked status. Direct Compose rendering is not a Vitest pass.
- #380: Capture exact status and body for `/health/live`, `/health/ready`,
  `sw.js` (including JavaScript MIME, not SPA HTML), and valid workspace
  bootstrap through the web origin. Preserve the credential/workspace/Origin/
  Host denial matrix for the final web image. Stop only the owned Learning
  service, show explicit API failure rather than HTML 200 or legacy fallback,
  then restart the same artifacts and show readiness without bootstrap replay.
  Record exact image IDs, three long-lived services, absence of optional
  containers and host-published DB/API ports, schema identity, and ownership
  labels. A served `sw.js` alone does not prove browser registration.
- Teardown remains unproved. Both disposable projects were deliberately
  retained. After preserving sanitized evidence, verify every selected
  container, volume, network, and private input against that project's original
  ownership manifest before project-scoped teardown, then verify resource
  absence. Missing inputs or uncertain ownership block teardown; never replace
  credentials, replay bootstrap, prune globally, or touch live Tempo.

Before *any* remaining native action, the Mac owner must verify the exact
project and manifest, clean isolated checkout at the pinned source SHA,
running container image IDs against pinned images, unchanged relevant inputs,
and task-owned private paths. A mismatch stops the probe rather than inviting
an inferred repair. No outstanding native probe was executed for this receipt, and #378, #379,
and #380 remain open.

A subsequent cloud attempt on the assembled preparation tree `be91b363` ran
`npm exec vitest -- run tests/unit/study-compose-contract.test.ts` using the
existing lockfile-matching dependencies: **1 passed, 2 failed**. Both rendering
cases could not start Docker (`spawnSync` status `null`, error `ENOENT`), so this
is an environment-blocked Compose proof, not a passing rerun or a demonstrated
application regression. No Docker installation or service action was attempted.
