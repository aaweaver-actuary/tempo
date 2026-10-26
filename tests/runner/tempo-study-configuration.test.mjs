import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { targetKey } from "../../scripts/tempo-deployment.mjs";
import { readStudyServingConfiguration, validateStudyServingConfiguration } from "../../scripts/tempo-study-configuration.mjs";

const copy = value => structuredClone(value);
const digest = character => character.repeat(64);
// These are semantic protocol fixtures, not observed installed-target evidence.
function fixture() {
  const images = { postgres: `sha256:${digest("1")}`, learning: `sha256:${digest("2")}`, "study-web": `sha256:${digest("3")}` };
  const volumes = { "postgres-data": { name: "fixture-data", external: true }, "runtime-secrets": { name: "fixture-secrets", external: true } };
  const cutover = { mode: "graph-preserving-fresh-start", transitionId: "fixture-transition", revision: "a".repeat(40),
    schema: `sha256:${digest("7")}`, images: copy(images), database: { name: "fixture_learning", volume: "fixture-data" },
    workspace: "fixture-workspace", ingress: { origin: "http://127.0.0.1:38127" },
    composeFiles: ["/fixture/release/config/study-persistent/serving.compose.yml"], volumes: copy(volumes) };
  const registration = { version: 2, root: "/fixture/release", context: "fixture-context", daemonId: "fixture-daemon", project: "fixture-study",
    envFile: "/fixture/private/serving.env", composeFiles: copy(cutover.composeFiles), volumes: copy(volumes),
    postgresVolumeKey: "postgres-data", postgresMajor: 18, postgresImage: images.postgres, webUrl: cutover.ingress.origin,
    ports: [{ service: "study-web", host: "127.0.0.1", port: "38127", target: 80 }], cutover,
    installedStudy: { version: 1, runtime: { version: 1,
      postgres: { volumeKey: "postgres-data", volumeName: "fixture-data", mountDestination: "/var/lib/postgresql",
        dataDirectory: "/var/lib/postgresql/18/docker", major: 18, systemIdentifier: "fixture-system-identifier" },
      credentialDigests: { studyTokenSha256: digest("4"), runtimePassfileSha256: digest("5") } } } };
  const persistentIntent = { kind: "tempo-learning-persistent-target-intent", schema_version: 1,
    transition_id: cutover.transitionId, target_identity: targetKey(registration), context: registration.context, daemon_id: registration.daemonId,
    project: registration.project, source_revision: cutover.revision, images: copy(images), database: cutover.database.name,
    workspace_id: cutover.workspace, ingress_origin: registration.webUrl, timezone: "America/New_York",
    data_volume: "fixture-data", runtime_secrets_volume: "fixture-secrets", network_name: "fixture-network", source_volume_names: ["fixture-preserved-source"] };
  const persistentReceipt = { kind: "tempo-learning-persistent-bootstrap", schema_version: 1, evidence_scope: "observed",
    status: "initialized-quarantine-required", intent_sha256: digest("8"), resource_observation_sha256: digest("9"),
    transition_id: cutover.transitionId, target_identity: targetKey(registration), project: registration.project,
    declared_source_revision: cutover.revision, declared_images: copy(images), observed_postgres_image: images.postgres,
    database: persistentIntent.database, workspace_id: cutover.workspace, ingress_origin: registration.webUrl, timezone: persistentIntent.timezone,
    roles: { owner: "fixture_learning_owner", writer: "fixture_learning_writer", reader: "fixture_learning_reader" },
    endpoint: { host: "postgres", port: 5432 }, schema: { identity: cutover.schema }, system_identifier: "fixture-system-identifier",
    private_output_sha256: { "study.token": digest("4"), "runtime.pgpass": digest("5"), "connection.json": digest("6"), "operator.token": digest("a") } };
  const labels = { "tempo.owner": registration.project, "tempo.target-kind": "persistent", "tempo.disposable": "false" };
  const common = service => ({ image: images[service], pull_policy: "never", restart: "no", labels: copy(labels), networks: { persistent: null }, entrypoint: null });
  const configuration = { name: registration.project, services: {
    postgres: { ...common("postgres"), command: ["postgres", "-c", "max_locks_per_transaction=512"],
      environment: { POSTGRES_DB: "postgres", POSTGRES_USER: "postgres", POSTGRES_PASSWORD_FILE: "/run/secrets/persistent-postgres-password", PGDATA: "/var/lib/postgresql/18/docker" },
      volumes: [{ type: "volume", source: "postgres-data", target: "/var/lib/postgresql", volume: {} }],
      secrets: [{ source: "persistent-postgres-password", target: "persistent-postgres-password", mode: "0400" }],
      healthcheck: { test: ["CMD-SHELL", "pg_isready -U postgres -d postgres"] } },
    learning: { ...common("learning"), user: "10001:10001", read_only: true, tmpfs: ["/tmp"],
      command: ["serve", "--host", "0.0.0.0", "--port", "8001"],
      environment: { TEMPO_LEARNING_WORKSPACE: cutover.workspace, TEMPO_LEARNING_DATABASE_URL: "host=postgres port=5432 dbname=fixture_learning user=fixture_learning_writer",
        TEMPO_LEARNING_READER_URL: "host=postgres port=5432 dbname=fixture_learning user=fixture_learning_reader",
        TEMPO_LEARNING_TOKEN_FILE: "/run/persistent/bootstrap/study.token", TEMPO_LEARNING_ORIGINS: registration.webUrl,
        TEMPO_LEARNING_HOSTS: "127.0.0.1,learning", TEMPO_LEARNING_TIMEZONE: persistentIntent.timezone,
        TEMPO_LEARNING_MAINTENANCE: "1", PGPASSFILE: "/run/persistent/bootstrap/runtime.pgpass" },
      volumes: [{ type: "volume", source: "runtime-secrets", target: "/run/persistent", read_only: true, volume: {} }],
      depends_on: { postgres: { condition: "service_healthy", required: true } },
      healthcheck: { test: ["CMD", "/opt/learning/bin/python", "-I", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health/ready', timeout=2).close()"] } },
    "study-web": { ...common("study-web"), depends_on: { learning: { condition: "service_healthy", required: true } },
      ports: [{ host_ip: "127.0.0.1", published: "38127", target: 80, protocol: "tcp", mode: "ingress" }] },
  }, volumes: copy(volumes), networks: { persistent: { name: "fixture-network", external: true, ipam: {} } },
  secrets: { "persistent-postgres-password": { name: "fixture-study_persistent-postgres-password", file: "/fixture/private/postgres-password" } } };
  return { registration, configuration, persistentIntent, persistentReceipt };
}

test("persistent serving config binds the existing public receipts and returns independent frozen ownership", () => {
  const input = fixture(); const before = copy(input); const result = validateStudyServingConfiguration(input);
  assert.deepEqual(result, input.configuration); assert.notEqual(result, input.configuration);
  assert.ok(Object.isFrozen(result.services.learning.environment)); assert.deepEqual(input, before);
  assert.equal(result.services.learning.environment.TEMPO_LEARNING_MAINTENANCE, "1");
});

for (const [name, alter] of [
  ["synthetic bootstrap cannot become serving admission", f => { f.persistentReceipt.evidence_scope = "synthetic-fixture"; }],
  ["another target receipt", f => { f.persistentReceipt.target_identity = "other-target"; }],
  ["different source revision", f => { f.persistentReceipt.declared_source_revision = "b".repeat(40); }],
  ["mutable matching image tags", f => { f.registration.cutover.images.learning = f.persistentIntent.images.learning = f.persistentReceipt.declared_images.learning = f.configuration.services.learning.image = "learning:latest"; }],
  ["different daemon", f => { f.persistentIntent.daemon_id = "different-daemon"; }],
  ["different schema map identity", f => { f.persistentReceipt.schema.identity = `sha256:${digest("f")}`; }],
  ["different workspace", f => { f.persistentReceipt.workspace_id = "other-workspace"; }],
  ["foreign writer role", f => { f.persistentReceipt.roles.writer = "foreign_writer"; }],
  ["source-volume reuse", f => { f.persistentIntent.source_volume_names.push("fixture-data"); }],
  ["different observed PostgreSQL image", f => { f.persistentReceipt.observed_postgres_image = `sha256:${digest("f")}`; }],
  ["different credential hash descriptor", f => { f.registration.installedStudy.runtime.credentialDigests.studyTokenSha256 = digest("f"); }],
  ["different passfile hash descriptor", f => { f.registration.installedStudy.runtime.credentialDigests.runtimePassfileSha256 = digest("f"); }],
  ["different cluster identifier descriptor", f => { f.registration.installedStudy.runtime.postgres.systemIdentifier = "other-cluster"; }],
  ["data directory outside registered mount", f => { f.registration.installedStudy.runtime.postgres.dataDirectory = "/other/cluster"; }],
  ["different external network", f => { f.configuration.networks.persistent.name = "source-network"; }],
  ["implicit data volume creation", f => { f.configuration.volumes["postgres-data"].external = false; }],
  ["extra source data volume", f => { f.configuration.volumes.source = { name: "source-data", external: true }; }],
  ["bootstrap dependency", f => { f.configuration.services.learning.depends_on.bootstrap = { condition: "service_completed_successfully" }; }],
  ["optional Content service dependency", f => { f.configuration.services.content = { image: "content:latest" }; }],
  ["build side effect", f => { f.configuration.services.learning.build = { context: "/fixture/release" }; }],
  ["automatic pull", f => { f.configuration.services.learning.pull_policy = "always"; }],
  ["automatic restart bypass", f => { f.configuration.services.learning.restart = "unless-stopped"; }],
  ["bind-mounted source checkout", f => { f.configuration.services.learning.volumes.push({ type: "bind", source: "/fixture/release", target: "/source" }); }],
  ["writable runtime secrets", f => { f.configuration.services.learning.volumes[0].read_only = false; }],
  ["different PGDATA", f => { f.configuration.services.postgres.environment.PGDATA = "/other/cluster"; }],
  ["host-published PostgreSQL", f => { f.configuration.services.postgres.ports = [{ host_ip: "127.0.0.1", published: "5432", target: 5432 }]; }],
  ["host-published Learning", f => { f.configuration.services.learning.ports = [{ host_ip: "127.0.0.1", published: "8001", target: 8001 }]; }],
  ["secret inside source checkout", f => { f.configuration.secrets["persistent-postgres-password"].file = "/fixture/release/password"; }],
  ["private environment inside checkout", f => { f.registration.envFile = "/fixture/release/serving.env"; }],
  ["admin credential mounted for Learning", f => { f.configuration.services.learning.secrets = [{ source: "persistent-postgres-password" }]; }],
  ["inline writer password", f => { f.configuration.services.learning.environment.TEMPO_LEARNING_DATABASE_URL += " password=secret"; }],
  ["libpq host routing override", f => { f.configuration.services.learning.environment.PGHOSTADDR = "1.2.3.4"; }],
  ["writer reused for reader", f => { f.configuration.services.learning.environment.TEMPO_LEARNING_READER_URL = f.configuration.services.learning.environment.TEMPO_LEARNING_DATABASE_URL; }],
  ["queue GET as readiness probe", f => { f.configuration.services.learning.healthcheck.test = ["CMD", "curl", "http://learning:8001/api/queue"]; }],
  ["operator credential added to ordinary serving", f => { f.configuration.services.learning.environment.TEMPO_LEARNING_OPERATOR_TOKEN_FILE = "/run/persistent/bootstrap/operator.token"; }],
  ["different workspace environment", f => { f.configuration.services.learning.environment.TEMPO_LEARNING_WORKSPACE = "other-workspace"; }],
  ["wildcard runtime hosts", f => { f.configuration.services.learning.environment.TEMPO_LEARNING_HOSTS = "*"; }],
  ["unadmitted origin", f => { f.configuration.services.learning.environment.TEMPO_LEARNING_ORIGINS = "http://elsewhere.invalid"; }],
  ["nonloopback ingress", f => { f.configuration.services["study-web"].ports[0].host_ip = "0.0.0.0"; }],
  ["UDP ingress", f => { f.configuration.services["study-web"].ports[0].protocol = "udp"; }],
  ["missing registered ingress", f => { f.configuration.services["study-web"].ports = []; }],
  ["extra registered ingress", f => { f.registration.ports.push({ service: "study-web", host: "127.0.0.1", port: "38128", target: 80 }); }],
  ["nginx image entrypoint replaced", f => { f.configuration.services["study-web"].entrypoint = ["sh"]; }],
]) test(`persistent serving rejects ${name}`, () => {
  const f = fixture(); alter(f);
  assert.throws(() => validateStudyServingConfiguration(f), error => error.code?.startsWith("study_serving_") === true);
});

for (const directory of ["/var/lib/postgresql/.", "/var/lib/postgresql/", "/var/lib/postgresql/18/.."]) {
  test(`persistent serving PGDATA must be a strict resolved descendant: ${directory}`, () => {
    const f = fixture();
    f.registration.installedStudy.runtime.postgres.dataDirectory = directory;
    f.configuration.services.postgres.environment.PGDATA = directory;
    assert.throws(() => validateStudyServingConfiguration(f), { code: "study_serving_runtime_binding_mismatch" });
  });
}

test("serving configuration reader uses only selected Compose config and no service operation", async () => {
  const f = fixture(); const calls = [];
  const result = await readStudyServingConfiguration({ registration: f.registration }, { ...f,
    run: async (...args) => { calls.push(args); return { code: 0, stdout: JSON.stringify(f.configuration) }; } });
  assert.deepEqual(result, f.configuration); assert.equal(calls.length, 1);
  assert.deepEqual(calls[0], ["docker", ["--context", "fixture-context", "compose", "--project-directory", "/fixture/release",
    "--env-file", "/fixture/private/serving.env", "-p", "fixture-study", "-f", "/fixture/release/config/study-persistent/serving.compose.yml", "config", "--format", "json"], { echo: false }]);
});

for (const [name, result] of [["failed rendering", { code: 1, stdout: "" }], ["invalid JSON", { code: 0, stdout: "not-json" }]])
  test(`serving reader reports ${name} without another operation`, async () => {
    const f = fixture(); let calls = 0;
    await assert.rejects(readStudyServingConfiguration({ registration: f.registration }, { ...f,
      run: async () => { calls++; return result; } }), { code: "study_serving_configuration_unavailable" });
    assert.equal(calls, 1);
  });

test("asynchronous configuration rendering cannot share mutable selected-target inputs", async () => {
  const f = fixture(); let release;
  const pending = readStudyServingConfiguration({ registration: f.registration }, { ...f,
    run: () => new Promise(resolve => { release = () => resolve({ code: 0, stdout: JSON.stringify(f.configuration) }); }) });
  f.registration.project = "caller-mutated-project"; f.persistentIntent.project = "caller-mutated-project";
  release(); const result = await pending; assert.equal(result.name, "fixture-study");
});

test("serving source remains separate from quarantine and reuses the already built Study ingress", () => {
  const source = readFileSync(new URL("../../config/study-persistent/serving.compose.yml", import.meta.url), "utf8");
  assert.doesNotMatch(source, /\n  bootstrap:|\n\s+build:|\n\s+profiles:|bootstrap_disposable|bootstrap_persistent|study-admin-url/);
  assert.match(source, /TEMPO_LEARNING_MAINTENANCE: "1"/);
  assert.doesNotMatch(source, /TEMPO_LEARNING_OPERATOR_TOKEN_FILE|TEMPO_LEARNING_EVIDENCE_TOKEN_FILE/);
  const quarantine = readFileSync(new URL("../../config/study-persistent/quarantine.compose.yml", import.meta.url), "utf8");
  assert.doesNotMatch(quarantine, /\n  study-web:/); assert.match(quarantine, /TEMPO_LEARNING_MAINTENANCE: "0"/);
});
