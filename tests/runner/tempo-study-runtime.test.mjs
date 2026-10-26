import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { createInstalledStudyRuntime, StudyRuntimeProblem } from "../../scripts/tempo-study-runtime.mjs";

// Semantic command doubles only. No Docker, database, HTTP or service is used.
const hash = digit => digit.repeat(64), selected = digit => "sha256:" + hash(digit);
function fixture({ running = false, databaseRunning = false, missingPostgres = false } = {}) {
  const runtime = { version: 1, postgres: { volumeKey: "postgres-data", volumeName: "study-data", mountDestination: "/var/lib/postgresql",
    dataDirectory: "/var/lib/postgresql/18/docker", major: 18, systemIdentifier: "1234567890" },
  credentialDigests: { studyTokenSha256: hash("4"), runtimePassfileSha256: hash("5") } };
  const images = { postgres: selected("1"), learning: selected("2"), "study-web": selected("3") };
  const registration = { version: 2, root: "/selected/source", envFile: "/private/serving.env", composeFiles: ["/selected/source/serving.yml"],
    context: "selected-context", daemonId: "selected-daemon", project: "study-fresh", postgresMajor: 18,
    volumes: { "postgres-data": { name: "study-data" }, "runtime-secrets": { name: "study-secrets" } },
    webUrl: "http://127.0.0.1:38127", ports: [{ service: "study-web", host: "127.0.0.1", port: "38127", target: 80 }], installedStudy: { runtime }, cutover: { schema: selected("6"), workspace: "fresh-workspace", images, database: { name: "fresh_learning" } } };
  const configuration = { name: registration.project, services: {
    postgres: { image: images.postgres, environment: { PGDATA: runtime.postgres.dataDirectory }, volumes: [{ type: "volume", source: "postgres-data", target: runtime.postgres.mountDestination }] },
    learning: { image: images.learning, user: "10001:10001", environment: {
      TEMPO_LEARNING_DATABASE_URL: "host=postgres port=5432 dbname=fresh_learning user=fresh_learning_writer",
      TEMPO_LEARNING_READER_URL: "host=postgres port=5432 dbname=fresh_learning user=fresh_learning_reader",
      TEMPO_LEARNING_WORKSPACE: registration.cutover.workspace, TEMPO_LEARNING_TIMEZONE: "America/New_York",
      TEMPO_LEARNING_TOKEN_FILE: "/run/persistent/bootstrap/study.token", PGPASSFILE: "/run/persistent/bootstrap/runtime.pgpass" },
    volumes: [{ type: "volume", source: "runtime-secrets", target: "/run/persistent", read_only: true }] },
    "study-web": { image: images["study-web"], ports: [{ host_ip: "127.0.0.1", published: "38127", target: 80 }] } },
  volumes: { "postgres-data": { external: true, name: "study-data" }, "runtime-secrets": { external: true, name: "study-secrets" } },
  networks: { persistent: { external: true, name: "study-network" } } };
  const labels = { "tempo.owner": registration.project, "tempo.target-kind": "persistent", "tempo.disposable": "false" };
  const volumes = Object.fromEntries(["study-data", "study-secrets"].map(Name => [Name, { Name, CreatedAt: "2026-10-25T00:00:00Z", Labels: { ...labels } }]));
  const network = { Name: "study-network", Id: hash("a"), Labels: { ...labels } };
  const ids = { postgres: hash("7"), learning: hash("8"), "study-web": hash("9") };
  const containers = Object.fromEntries(Object.entries(ids).map(([role, Id]) => [role, {
    Id, Image: images[role], Config: { User: role === "learning" ? "10001:10001" : "", Labels: { "com.docker.compose.project": registration.project, "com.docker.compose.service": role },
      Env: Object.entries(configuration.services[role].environment ?? {}).map(([key, value]) => `${key}=${value}`) },
    State: { Running: role === "postgres" ? running || databaseRunning : running },
    Mounts: role === "postgres" ? [{ Type: "volume", Name: "study-data", Destination: runtime.postgres.mountDestination, RW: true }]
      : role === "learning" ? [{ Type: "volume", Name: "study-secrets", Destination: "/run/persistent", RW: false }] : [],
    HostConfig: { PortBindings: role === "study-web" ? { "80/tcp": [{ HostIp: "127.0.0.1", HostPort: "38127" }] } : {} },
    NetworkSettings: { Ports: role === "study-web" ? { "80/tcp": [{ HostIp: "127.0.0.1", HostPort: "38127" }] } : {}, Networks: { "study-network": { NetworkID: network.Id, IPAddress: role === "postgres" ? "172.22.0.2" : "172.22.0.3" } } },
  }]));
  const request = { registration, command: "start", configPath: "/private/config.json", flags: [], services: [] };
  const state = { daemonId: registration.daemonId, missingPostgres, controlMajor: 18, controlIdentifier: runtime.postgres.systemIdentifier, failHelp: false,
    failControl: false, failUp: false, db: { status: "verified", scope: "observed", evidenceId: "db-read-only-proof", read_only: true,
      schema: registration.cutover.schema, workspace_id: registration.cutover.workspace, timezone: "America/New_York", database: "fresh_learning",
      roles: { writer: "fresh_learning_writer", reader: "fresh_learning_reader" }, server: { hostaddr: "172.22.0.2", port: 5432, system_identifier: runtime.postgres.systemIdentifier, identity_scope: "caller-bound" } },
    http: { ready: { status: "ready", workspace_id: registration.cutover.workspace }, bootstrap: { schema_version: 1, workspace_id: registration.cutover.workspace, timezone: "America/New_York" },
      study_token_sha256: runtime.credentialDigests.studyTokenSha256, runtime_passfile_sha256: runtime.credentialDigests.runtimePassfileSha256 }, ingress: { ok: true, status: 200 } };
  const calls = [], fetchCalls = [];
  const result = value => ({ code: 0, stdout: typeof value === "string" ? value : JSON.stringify(value), stderr: "" });
  const run = async (command, argv, options) => {
    calls.push({ command, argv: [...argv], options });
    assert.equal(command, "docker"); assert.deepEqual(argv.slice(0, 2), ["--context", registration.context]);
    const args = argv.slice(2);
    if (args[0] === "info") return result(state.daemonId);
    if (args[0] === "image") return result([{ Id: args[2], RepoDigests: [] }]);
    if (args[0] === "volume") return result([volumes[args[2]]]);
    if (args[0] === "network") return result([network]);
    if (args[0] === "inspect") return result([Object.values(containers).find(container => container.Id === args[1])]);
    if (args[0] === "compose" && args.includes("config")) return result(configuration);
    if (args[0] === "compose" && args.includes("ps")) return result(Object.entries(ids).filter(([role]) => role !== "postgres" || !state.missingPostgres).map(([Service, ID]) => ({ Service, ID })));
    if (args[0] === "compose" && args.includes("up")) {
      if (state.failUp) return { code: 1, stdout: "", stderr: "synthetic failed start" };
      state.missingPostgres = false; containers.postgres.State.Running = true;
      state.afterUp?.(); return result("");
    }
    if (args.includes("tempo_learning_database.runtime_readback") && args.includes("--help"))
      return state.failHelp ? { code: 1, stdout: "", stderr: "module unavailable" } : result("public read-only verifier help");
    if (args.some(value => String(value).includes("pg_controldata")))
      return state.failControl ? { code: 1, stdout: "", stderr: "no initialized cluster" }
        : result(`TEMPO_PG_VERSION=${state.controlMajor}\nDatabase system identifier: ${state.controlIdentifier}\n`);
    if (args.includes("tempo_learning_database.runtime_readback")) return result(state.db);
    if (args.includes("-c")) return result(state.http);
    throw new Error("Unexpected fake command " + JSON.stringify(args));
  };
  const fetcher = async (url, options) => { fetchCalls.push({ url, options }); return state.ingress; };
  const receipt = { kind: "tempo-learning-persistent-bootstrap", evidence_scope: "observed", database: "fresh_learning", workspace_id: registration.cutover.workspace,
    timezone: "America/New_York", schema: { identity: registration.cutover.schema }, system_identifier: runtime.postgres.systemIdentifier,
    roles: { writer: "fresh_learning_writer", reader: "fresh_learning_reader" }, endpoint: { host: "postgres", port: 5432 },
    private_output_sha256: { "study.token": runtime.credentialDigests.studyTokenSha256, "runtime.pgpass": runtime.credentialDigests.runtimePassfileSha256 } };
  const inspectConfiguration = async () => { if (state.rejectConfiguration) throw new Error("private-password must not escape"); return structuredClone(configuration); };
  const adapter = createInstalledStudyRuntime({ run, fetcher, persistentReceipt: receipt, inspectConfiguration });
  return { request, registration, runtime, configuration, observation: { configuration: structuredClone(configuration) }, state, containers, volumes, network, calls, fetchCalls, adapter,
    starts: () => calls.filter(call => call.argv.includes("up")), helpers: () => calls.filter(call => call.argv[2] === "run") };
}
const rejects = (operation, code) => assert.rejects(operation, error => error instanceof StudyRuntimeProblem && error.code === code);

test("cold runtime inspection reports metadata without creating helpers or claiming current schema/health", async () => {
  const f = fixture(); const proof = await f.adapter.inspectRuntime(f.request, f.observation);
  assert.equal(proof.state, "cold"); assert.equal(proof.scope, "observed"); assert.equal(proof.verified, true);
  assert.equal(proof.clusterIdentityVerified, false); assert.equal(proof.schemaVerified, false); assert.equal(proof.healthVerified, false);
  assert.deepEqual(proof.binding, f.request); assert.equal(f.helpers().length, 0); assert.equal(f.starts().length, 0); assert.equal(f.fetchCalls.length, 0);
});

test("cold start proves existing physical cluster before starting only PostgreSQL and performs restricted DB readback", async () => {
  const f = fixture(); const proof = await f.adapter.prepareDatabase(f.request, f.observation);
  assert.equal(proof.state, "database-ready"); assert.equal(proof.clusterIdentityVerified, true); assert.equal(proof.schemaVerified, true); assert.equal(proof.healthVerified, false);
  assert.equal(f.starts().length, 1);
  assert.deepEqual(f.starts()[0].argv.slice(f.starts()[0].argv.indexOf("up")), ["up", "-d", "--no-build", "--pull", "never", "--no-deps", "--wait", "--wait-timeout", "30", "postgres"]);
  const help = f.calls.findIndex(call => call.argv.includes("--help"));
  const physical = f.calls.findIndex(call => call.argv.some(arg => String(arg).includes("pg_controldata")));
  const start = f.calls.findIndex(call => call.argv.includes("up"));
  assert.ok(help < physical && physical < start);
  const coldRead = f.calls[physical].argv;
  assert.ok(coldRead.includes("none")); assert.ok(coldRead.includes("--read-only")); assert.ok(coldRead.some(arg => arg.includes("readonly,volume-nocopy")));
  for (const helper of f.helpers()) {
    assert.ok(helper.argv.includes("tempo.owner=study-fresh"));
    assert.ok(helper.argv.includes("tempo.target-kind=study-runtime-inspection"));
    assert.ok(helper.argv.includes("tempo.disposable=true"));
    assert.ok(helper.argv.some(arg => arg.startsWith("tempo.invocation=")));
  }
  const db = f.helpers().find(call => call.argv.includes("--expected-database"));
  assert.ok(db.argv.includes("172.22.0.2")); assert.ok(db.argv.includes("--expected-system-identifier"));
  assert.ok(db.argv.some(arg => arg === "type=volume,src=study-secrets,dst=/run/persistent,readonly,volume-nocopy"));
  assert.ok(!f.calls.some(call => call.argv.includes("learning") || call.argv.includes("study-web")));
});

test("existing running PostgreSQL is not recreated or started by preparation", async () => {
  const f = fixture({ databaseRunning: true }); f.request.command = "restart";
  const proof = await f.adapter.prepareDatabase(f.request, f.observation);
  assert.equal(proof.schemaVerified, true); assert.equal(f.starts().length, 0);
  assert.ok(!f.helpers().some(call => call.argv.some(arg => String(arg).includes("pg_controldata"))));
});

test("previously removed PostgreSQL container can be recreated only after its selected existing cluster is proved", async () => {
  const f = fixture({ missingPostgres: true });
  const before = await f.adapter.inspectRuntime(f.request, f.observation);
  assert.equal(before.services.postgres.containerId, null);
  const proof = await f.adapter.prepareDatabase(f.request, f.observation);
  assert.equal(proof.services.postgres.running, true); assert.equal(f.starts().length, 1);
});

for (const command of ["status", "stop", "doctor", "backup", "migrate"])
  test(`runtime preparation cannot execute for ${command}`, async () => {
    const f = fixture(); f.request.command = command;
    await rejects(() => f.adapter.prepareDatabase(f.request, f.observation), "study_runtime_prepare_command_invalid"); assert.equal(f.calls.length, 0);
  });

for (const [name, alter] of [
  ["wrong major", f => { f.state.controlMajor = 17; }],
  ["wrong system identifier", f => { f.state.controlIdentifier = "9999999"; }],
]) test(`cold start rejects ${name} before database start`, async () => {
  const f = fixture(); alter(f);
  await rejects(() => f.adapter.prepareDatabase(f.request, f.observation), "study_runtime_cluster_mismatch"); assert.equal(f.starts().length, 0);
});

test("missing or empty cluster is never initialized or adopted", async () => {
  const f = fixture(); f.state.failControl = true;
  await rejects(() => f.adapter.prepareDatabase(f.request, f.observation), "study_runtime_probe_failed");
  assert.equal(f.starts().length, 0); assert.ok(!f.calls.some(call => call.argv.some(arg => /initdb|CREATE |DROP |migrate|grant/.test(arg))));
});

test("missing installed public DB verifier blocks before PostgreSQL start", async () => {
  const f = fixture(); f.state.failHelp = true;
  await rejects(() => f.adapter.prepareDatabase(f.request, f.observation), "study_runtime_probe_failed");
  assert.equal(f.starts().length, 0);
});

test("changed physical identity after PostgreSQL start blocks before any application effect", async () => {
  const f = fixture(); f.state.afterUp = () => { f.state.controlIdentifier = "999999"; };
  await rejects(() => f.adapter.prepareDatabase(f.request, f.observation), "study_runtime_cluster_mismatch");
  assert.equal(f.starts().length, 1); assert.ok(!f.calls.some(call => call.argv.includes("--expected-database")));
});

test("a failed PostgreSQL command is not blindly retried", async () => {
  const f = fixture(); f.state.failUp = true;
  await rejects(() => f.adapter.prepareDatabase(f.request, f.observation), "study_runtime_probe_failed"); assert.equal(f.starts().length, 1);
});

for (const [name, alter, code] of [
  ["another Docker daemon", f => { f.state.daemonId = "other-daemon"; }, "study_runtime_daemon_mismatch"],
  ["disposable source volume", f => { f.volumes["study-data"].Labels["tempo.disposable"] = "true"; }, "study_runtime_volume_mismatch"],
  ["foreign volume owner", f => { f.volumes["study-data"].Labels["tempo.owner"] = "original-source"; }, "study_runtime_volume_mismatch"],
  ["foreign mounted data", f => { f.containers.postgres.Mounts[0].Name = "old-source-volume"; }, "study_runtime_volume_mismatch"],
  ["foreign service image", f => { f.containers.postgres.Image = selected("f"); }, "study_runtime_container_mismatch"],
  ["another network", f => { f.containers.postgres.NetworkSettings.Networks["study-network"].NetworkID = hash("f"); }, "study_runtime_network_mismatch"],
  ["mutable selected image", f => { f.registration.cutover.images.postgres = "postgres:latest"; }, "study_runtime_contract_pending"],
  ["missing physical identity", f => { delete f.runtime.postgres.systemIdentifier; }, "study_runtime_contract_pending"],
  ["unsafe data path", f => { f.runtime.postgres.dataDirectory = "/var/lib/postgresql/../foreign"; }, "study_runtime_contract_pending"],
]) test(`runtime refuses ${name} without service effects`, async () => {
  const f = fixture(); alter(f);
  await rejects(() => f.adapter.prepareDatabase(f.request, f.observation), code); assert.equal(f.starts().length, 0);
});

for (const [name, alter] of [
  ["schema mismatch", f => { f.state.db.schema = selected("f"); }],
  ["workspace mismatch", f => { f.state.db.workspace_id = "old-workspace"; }],
  ["wrong network address", f => { f.state.db.server.hostaddr = "172.22.0.99"; }],
  ["invented physical server observation", f => { f.state.db.server.identity_scope = "server-observed"; }],
  ["wrong restricted reader", f => { f.state.db.roles.reader = "admin"; }],
  ["writable verifier result", f => { f.state.db.read_only = false; }],
]) test(`pre-application DB readback rejects ${name}`, async () => {
  const f = fixture({ databaseRunning: true }); alter(f);
  await rejects(() => f.adapter.prepareDatabase(f.request, f.observation), "study_runtime_database_unverified"); assert.equal(f.starts().length, 0);
});

test("running outcome uses only concrete public DB and read-only HTTP checks with private tokens retained inside container", async () => {
  const f = fixture({ running: true });
  const proof = await f.adapter.verifyRuntimeOutcome({ request: f.request, observation: f.observation, desiredState: "running" });
  assert.equal(proof.status, "complete"); assert.equal(proof.scope, "observed"); assert.equal(proof.schemaVerified, true); assert.equal(proof.healthVerified, true);
  assert.equal(f.helpers().length, 0); assert.equal(f.starts().length, 0);
  const http = f.calls.find(call => call.argv.includes("-c"));
  assert.ok(http.argv.at(-1).includes("/health/ready")); assert.ok(http.argv.at(-1).includes("/api/study/bootstrap"));
  assert.ok(!http.argv.at(-1).includes("/api/queue"));
  assert.ok(!http.argv.some(arg => arg.includes("Bearer actual-secret")));
  assert.equal(f.fetchCalls[0].url, f.registration.webUrl); assert.equal(f.fetchCalls[0].options.method, "GET");
});

test("recovery identity inspection reports running services without requiring or claiming current schema/health", async () => {
  const f = fixture({ running: true }); f.request.command = "status";
  const proof = await f.adapter.inspectRuntime(f.request, f.observation);
  assert.equal(proof.state, "running"); assert.equal(proof.scope, "observed"); assert.equal(proof.verified, true);
  assert.equal(proof.clusterIdentityVerified, false); assert.equal(proof.schemaVerified, false); assert.equal(proof.healthVerified, false);
  assert.deepEqual(proof.binding, f.request);
  assert.ok(Object.values(proof.services).every(service => service.running && service.containerId));
  assert.equal(f.helpers().length, 0); assert.equal(f.starts().length, 0); assert.equal(f.fetchCalls.length, 0);
  assert.ok(!f.calls.some(call => call.argv[2] === "exec"));
});

for (const command of ["doctor", "logs", "stop", "restart"])
  test(`recovery identity inspection identifies the exact unhealthy target for ${command} without availability probes`, async () => {
    const f = fixture({ running: true }); f.request.command = command;
    f.state.failControl = true; f.state.db = {}; f.state.http = {}; f.state.ingress = { ok: false, status: 503 };
    const proof = await f.adapter.inspectRuntime(f.request, f.observation);
    assert.equal(proof.state, "running"); assert.equal(proof.verified, true);
    assert.equal(proof.clusterIdentityVerified, false); assert.equal(proof.schemaVerified, false); assert.equal(proof.healthVerified, false);
    assert.equal(f.helpers().length, 0); assert.equal(f.starts().length, 0); assert.equal(f.fetchCalls.length, 0);
    assert.ok(!f.calls.some(call => call.argv[2] === "exec"));
  });

for (const command of ["stop", "logs", "restart"])
  test(`recovery identity mismatches still block ${command} before any effect`, async () => {
    for (const [alter, code] of [
      [f => { f.state.daemonId = "foreign-daemon"; }, "study_runtime_daemon_mismatch"],
      [f => { f.containers.learning.Image = selected("f"); }, "study_runtime_container_mismatch"],
      [f => { f.containers.postgres.Mounts[0].Name = "foreign-data"; }, "study_runtime_volume_mismatch"],
      [f => { f.containers["study-web"].HostConfig.PortBindings["80/tcp"][0].HostPort = "49999"; }, "study_runtime_ingress_mismatch"],
    ]) {
      const f = fixture({ running: true }); f.request.command = command; alter(f);
      await rejects(() => f.adapter.inspectRuntime(f.request, f.observation), code);
      assert.equal(f.helpers().length, 0); assert.equal(f.starts().length, 0); assert.equal(f.fetchCalls.length, 0);
      assert.ok(!f.calls.some(call => call.argv[2] === "exec"));
    }
  });

for (const location of ["HostConfig", "NetworkSettings"])
  test(`correct rendered ingress cannot hide a different actual ${location} binding behind unrelated HTTP 200`, async () => {
    const f = fixture({ running: true });
    const field = location === "HostConfig" ? "PortBindings" : "Ports";
    f.containers["study-web"][location][field]["80/tcp"][0].HostPort = "49999";
    assert.equal(f.state.ingress.status, 200);
    await rejects(() => f.adapter.verifyRuntimeOutcome({ request: f.request, observation: f.observation, desiredState: "running" }), "study_runtime_ingress_mismatch");
    assert.equal(f.fetchCalls.length, 0); assert.equal(f.starts().length, 0);
  });

test("private database service actual host exposure is rejected even when rendered configuration is private", async () => {
  const f = fixture({ running: true });
  f.containers.postgres.HostConfig.PortBindings = { "5432/tcp": [{ HostIp: "127.0.0.1", HostPort: "5432" }] };
  await rejects(() => f.adapter.inspectRuntime(f.request, f.observation), "study_runtime_ingress_mismatch");
  assert.equal(f.starts().length, 0);
});

for (const [name, alter] of [
  ["wrong authenticated workspace", f => { f.state.http.bootstrap.workspace_id = "wrong"; }],
  ["wrong token file", f => { f.state.http.study_token_sha256 = hash("f"); }],
  ["wrong role passfile", f => { f.state.http.runtime_passfile_sha256 = hash("f"); }],
  ["unready Learning", f => { f.state.http.ready.status = "unavailable"; }],
  ["unavailable ingress", f => { f.state.ingress = { ok: false, status: 503 }; }],
]) test(`running verification refuses ${name}`, async () => {
  const f = fixture({ running: true }); alter(f);
  await rejects(() => f.adapter.verifyRuntimeOutcome({ request: f.request, observation: f.observation, desiredState: "running" }), "study_runtime_health_unverified");
  assert.equal(f.starts().length, 0);
});

test("stop outcome confirms applications stopped without waking PostgreSQL or claiming live schema", async () => {
  const f = fixture(); f.request.command = "stop";
  const proof = await f.adapter.verifyRuntimeOutcome({ request: f.request, observation: f.observation, desiredState: "stopped" });
  assert.equal(proof.status, "complete"); assert.equal(proof.state, "stopped"); assert.equal(proof.schemaVerified, false); assert.equal(proof.healthVerified, false);
  assert.equal(f.helpers().length, 0); assert.equal(f.starts().length, 0); assert.equal(f.fetchCalls.length, 0);
});

test("stop refuses a still-running application and never falls back to another target", async () => {
  const f = fixture({ running: true }); f.request.command = "stop";
  await rejects(() => f.adapter.verifyRuntimeOutcome({ request: f.request, observation: f.observation, desiredState: "stopped" }), "study_runtime_stop_unverified");
  assert.equal(f.starts().length, 0);
});

for (const uri of ["postgresql://fresh_writer:private-password@postgres/fresh_learning", "postgresql://fresh_writer@postgres/fresh_learning?password=private-password"])
  test(`credential literal or routing override in DSN cannot reach a helper command (${uri.includes("?") ? "query" : "userinfo"})`, async () => {
    const f = fixture(); f.configuration.services.learning.environment.TEMPO_LEARNING_DATABASE_URL = uri; f.observation.configuration = structuredClone(f.configuration); f.state.rejectConfiguration = true;
    await rejects(() => f.adapter.prepareDatabase(f.request, f.observation), "study_runtime_configuration_unverified");
    assert.equal(f.helpers().length, 0); assert.equal(f.starts().length, 0);
  });

test("actual Learning libpq overrides and private-file mount changes fail closed", async () => {
  const f = fixture(); f.containers.learning.Config.Env.push("PGHOSTADDR=foreign-address");
  await rejects(() => f.adapter.inspectRuntime(f.request, f.observation), "study_runtime_credentials_mismatch");
  assert.equal(f.helpers().length, 0);
});

for (const [name, mode, expected] of [["private", 0o600, 0], ["world-readable", 0o644, 1]])
  test(`concrete in-container HTTP probe enforces ${name} token ownership without network or secret output`, async t => {
    const f = fixture({ running: true });
    await f.adapter.verifyRuntimeOutcome({ request: f.request, observation: f.observation, desiredState: "running" });
    const script = f.calls.find(call => call.argv.includes("-c")).argv.at(-1);
    const directory = mkdtempSync(join(tmpdir(), "tempo-runtime-http-"));
    t.after(() => rmSync(directory, { recursive: true }));
    const token = join(directory, "study.token"), passfile = join(directory, "runtime.pgpass");
    writeFileSync(token, "unit-only-private-token\n", { mode }); writeFileSync(passfile, "unit-only-passfile\n", { mode: 0o600 });
    const fakeHttp = `import urllib.request,json\nclass Response:\n    status=200\n    def __init__(self,request): self.request=request\n    def __enter__(self): return self\n    def __exit__(self,*args): pass\n    def read(self,limit):\n        assert self.request.full_url in ('http://127.0.0.1:8001/health/ready','http://127.0.0.1:8001/api/study/bootstrap')\n        assert self.request.get_header('Authorization')=='Bearer unit-only-private-token'\n        return json.dumps({'status':'ready','schema_version':1,'workspace_id':'fresh-workspace','timezone':'America/New_York','settings':{'not_returned':'private-value'}}).encode()\nurllib.request.urlopen=lambda request,timeout:Response(request)\n`;
    const result = spawnSync("python3", ["-c", fakeHttp + script], { encoding: "utf8", env: { ...process.env, TEMPO_LEARNING_TOKEN_FILE: token, PGPASSFILE: passfile, TEMPO_LEARNING_WORKSPACE: "fresh-workspace" } });
    assert.equal(result.status, expected, result.stderr);
    assert.ok(!result.stdout.includes("unit-only-private-token")); assert.ok(!result.stdout.includes("unit-only-passfile")); assert.ok(!result.stdout.includes("private-value"));
    if (expected === 0) assert.equal(JSON.parse(result.stdout).bootstrap.workspace_id, "fresh-workspace");
    else assert.ok(result.stderr.includes("Private runtime file guard failed"));
  });
