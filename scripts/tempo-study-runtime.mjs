// Installed Study runtime adapter. Source fixtures qualify its command contract;
// this module never provisions, imports, grants, migrates, or selects authority.
import { createHash, randomUUID } from "node:crypto";
import { posix } from "node:path";
import { isDeepStrictEqual } from "node:util";

export class StudyRuntimeProblem extends Error {
  constructor(code, message) { super(message); this.name = "StudyRuntimeProblem"; this.code = code; }
}
const fail = (code, message) => { throw new StudyRuntimeProblem(code, message); };
const text = value => typeof value === "string" && value.trim().length > 0;
const object = value => value !== null && typeof value === "object" && !Array.isArray(value);
const digest = value => createHash("sha256").update(JSON.stringify(value)).digest("hex");
const immutable = value => typeof value === "string" && /^(?:sha256:[a-f0-9]{64}|[^\s]+@sha256:[a-f0-9]{64})$/.test(value);
const roles = ["postgres", "learning", "study-web"];
const normalizedPorts = values => values.map(value => ({ host: value.host, port: String(value.port), target: Number(value.target) }))
  .sort((left, right) => JSON.stringify(left).localeCompare(JSON.stringify(right)));
function actualPorts(bindings) {
  const values = [];
  for (const [target, ports] of Object.entries(bindings ?? {})) {
    if (ports == null) continue;
    if (!Array.isArray(ports) || !/^[0-9]+\/tcp$/.test(target)) fail("study_runtime_ingress_mismatch", "The actual service port mapping is not the registered TCP ingress.");
    for (const port of ports) values.push({ host: port.HostIp, port: port.HostPort, target: target.split("/")[0] });
  }
  return normalizedPorts(values);
}
const freeze = value => {
  const result = structuredClone(value);
  const visit = entry => { if (entry && typeof entry === "object") { Object.values(entry).forEach(visit); Object.freeze(entry); } };
  visit(result); return result;
};
const environment = values => Array.isArray(values)
  ? Object.fromEntries(values.map(value => { const split = value.indexOf("="); return [value.slice(0, split), value.slice(split + 1)]; }))
  : values ?? {};
const absolute = value => text(value) && value.startsWith("/") && posix.normalize(value) === value && !/[,\n\r]/.test(value);
const controlScript = 'set -eu; test -f "$1/PG_VERSION"; test -f "$1/global/pg_control"; printf "TEMPO_PG_VERSION="; cat "$1/PG_VERSION"; exec pg_controldata "$1"';
const httpScript = `import hashlib,json,os,pathlib,stat,urllib.request
def private_file(name):
    path=pathlib.Path(os.environ[name]); metadata=path.lstat()
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) not in (0o400,0o600) or metadata.st_uid!=os.geteuid():
        raise RuntimeError('Private runtime file guard failed')
    return path.read_bytes()
token=private_file('TEMPO_LEARNING_TOKEN_FILE')
passfile=private_file('PGPASSFILE')
headers={'Authorization':'Bearer '+token.decode().strip(),'X-Tempo-Workspace':os.environ['TEMPO_LEARNING_WORKSPACE']}
def read(path):
    request=urllib.request.Request('http://127.0.0.1:8001'+path,headers=headers)
    with urllib.request.urlopen(request,timeout=5) as response:
        if response.status!=200: raise RuntimeError('read-only readiness unavailable')
        return json.loads(response.read(1048577))
ready=read('/health/ready'); bootstrap=read('/api/study/bootstrap')
print(json.dumps({'ready':{key:ready.get(key) for key in ('status','workspace_id')},'bootstrap':{key:bootstrap.get(key) for key in ('schema_version','workspace_id','timezone')},'study_token_sha256':hashlib.sha256(token).hexdigest(),'runtime_passfile_sha256':hashlib.sha256(passfile).hexdigest()}))`;

/** Concrete runtime commands only. The caller owns actual admission/configuration
 * verification and the original lock; prepareDatabase is for start/restart ONLY
 * inside that lock. A cold observation never claims a current schema or health.
 */
export function createInstalledStudyRuntime({ run, fetcher = globalThis.fetch, inspectConfiguration, persistentReceipt } = {}) {
  const receipt = object(persistentReceipt) ? freeze(persistentReceipt) : null;
  const helper = (request, probe) => ["run", "--rm", "--read-only", "--label", `tempo.owner=${request.registration.project}`,
    "--label", "tempo.target-kind=study-runtime-inspection", "--label", "tempo.disposable=true", "--label", `tempo.invocation=${randomUUID()}`,
    "--label", `tempo.probe=${probe}`];
  const invoke = async (request, args) => {
    if (typeof run !== "function") fail("study_runtime_executor_pending", "The public command executor is required.");
    let result;
    try { result = await run("docker", ["--context", request.registration.context, ...args], { echo: false }); }
    catch { fail("study_runtime_probe_failed", "The selected runtime command failed; preserve target evidence before retrying."); }
    if (result?.code !== 0 || typeof result.stdout !== "string")
      fail("study_runtime_probe_failed", "The selected runtime command did not complete successfully.");
    return result.stdout;
  };
  const json = async (request, args) => {
    const output = await invoke(request, args);
    if (Buffer.byteLength(output) > 1024 * 1024) fail("study_runtime_probe_failed", "The runtime metadata response exceeded its bound.");
    try { return JSON.parse(output); } catch { fail("study_runtime_probe_failed", "The runtime metadata response was not JSON."); }
  };
  const compose = request => {
    const target = request.registration;
    if (!text(target.root) || !text(target.envFile) || !Array.isArray(target.composeFiles) || !target.composeFiles.length)
      fail("study_runtime_configuration_pending", "Supply explicit registered source, environment and Compose paths.");
    return ["compose", "--project-directory", target.root, "--env-file", target.envFile, "-p", target.project,
      ...target.composeFiles.flatMap(path => ["-f", path])];
  };
  const descriptor = request => {
    const target = request?.registration, runtime = target?.installedStudy?.runtime, pg = runtime?.postgres;
    if (target?.version !== 2 || !text(target.context) || !text(target.daemonId) || !text(target.project)
      || !/^sha256:[a-f0-9]{64}$/.test(target.cutover?.schema ?? "")
      || !roles.every(role => immutable(target.cutover?.images?.[role]))
      || !Array.isArray(target.ports) || !target.ports.length || target.ports.some(port => port.service !== "study-web" || port.host !== "127.0.0.1"
        || !Number.isInteger(Number(port.port)) || Number(port.port) < 1 || Number(port.port) > 65535
        || !Number.isInteger(Number(port.target)) || Number(port.target) < 1 || Number(port.target) > 65535)
      || receipt?.kind !== "tempo-learning-persistent-bootstrap" || receipt.evidence_scope !== "observed"
      || receipt.schema?.identity !== target.cutover.schema || receipt.workspace_id !== target.cutover.workspace
      || receipt.database !== target.cutover.database?.name || !text(receipt.database)
      || receipt.endpoint?.host !== "postgres" || !Number.isInteger(receipt.endpoint.port) || receipt.endpoint.port < 1
      || !["writer", "reader"].every(role => receipt.roles?.[role] === `${receipt.database}_${role}`)
      || runtime?.version !== 1 || !text(pg?.volumeKey) || !/^[A-Za-z0-9][A-Za-z0-9_.-]+$/.test(pg.volumeName ?? "")
      || target.volumes?.[pg.volumeKey]?.name !== pg.volumeName
      || !absolute(pg.mountDestination) || !absolute(pg.dataDirectory)
      || !(pg.dataDirectory === pg.mountDestination || pg.dataDirectory.startsWith(pg.mountDestination + "/"))
      || !Number.isInteger(pg.major) || pg.major < 1 || pg.major !== target.postgresMajor
      || !/^[1-9][0-9]*$/.test(pg.systemIdentifier ?? "") || pg.systemIdentifier !== receipt.system_identifier
      || ![runtime.credentialDigests?.studyTokenSha256, runtime.credentialDigests?.runtimePassfileSha256].every(value => /^[a-f0-9]{64}$/.test(value ?? "")))
      fail("study_runtime_contract_pending", "Supply the exact bound persistent runtime, physical cluster and private-file identities.");
    return runtime;
  };
  const image = async (request, selected) => {
    const values = await json(request, ["image", "inspect", selected]), value = values?.[0];
    if (values?.length !== 1 || !/^sha256:[a-f0-9]{64}$/.test(value?.Id ?? "")
      || !(selected === value.Id || value.RepoDigests?.includes(selected)))
      fail("study_runtime_image_mismatch", "The selected immutable runtime image is unavailable or identifies another image.");
    return value;
  };
  const ownedVolume = async (request, name) => {
    const values = await json(request, ["volume", "inspect", name]), value = values?.[0];
    if (values?.length !== 1 || value?.Name !== name || !text(value.CreatedAt)
      || value.Labels?.["tempo.owner"] !== request.registration.project
      || value.Labels?.["tempo.target-kind"] !== "persistent" || value.Labels?.["tempo.disposable"] !== "false")
      fail("study_runtime_volume_mismatch", "The exact independently owned persistent volume is required; no adoption or initialization is allowed.");
    return value;
  };
  const base = async (request, observation) => {
    const runtime = descriptor(request), target = request.registration, pg = runtime.postgres;
    if ((await invoke(request, ["info", "--format", "{{.ID}}"])).trim() !== target.daemonId)
      fail("study_runtime_daemon_mismatch", "The selected Docker context identifies another daemon.");
    if (typeof inspectConfiguration !== "function") fail("study_runtime_configuration_pending", "The public serving-configuration validator is required.");
    let configuration;
    try { configuration = await inspectConfiguration(freeze(request)); }
    catch { fail("study_runtime_configuration_unverified", "The serving-configuration owner did not validate the selected private database and runtime settings."); }
    if (!object(configuration) || configuration.name !== target.project || !isDeepStrictEqual(Object.keys(configuration.services ?? {}).sort(), [...roles].sort())
      || (observation?.configuration && !isDeepStrictEqual(configuration, observation.configuration)))
      fail("study_runtime_configuration_mismatch", "The current registered serving configuration changed or includes unrelated services.");
    const images = {};
    for (const role of roles) {
      if (configuration.services[role].image !== target.cutover.images[role])
        fail("study_runtime_image_mismatch", "Serving configuration does not pin the published runtime images.");
      images[role] = await image(request, target.cutover.images[role]);
    }
    if (environment(configuration.services.postgres.environment).PGDATA !== pg.dataDirectory
      || configuration.volumes?.[pg.volumeKey]?.external !== true || configuration.volumes[pg.volumeKey].name !== pg.volumeName)
      fail("study_runtime_volume_mismatch", "The registered PostgreSQL data path and external volume must match exactly.");
    const configuredMount = configuration.services.postgres.volumes?.find(mount => mount.source === pg.volumeKey);
    if (configuredMount?.type !== "volume" || configuredMount.target !== pg.mountDestination || configuredMount.read_only === true)
      fail("study_runtime_volume_mismatch", "PostgreSQL must use only its selected initialized data mount.");
    const dataVolume = await ownedVolume(request, pg.volumeName);
    const learningEnvironment = environment(configuration.services.learning.environment);
    // The public configuration owner validates bootstrap's keyword DSNs. Their
    // Psycopg semantics are verified by the installed database owner; this
    // adapter neither invents a URL alternative nor parses libpq syntax.
    if (!/^[1-9][0-9]*(?::[1-9][0-9]*)?$/.test(configuration.services.learning.user ?? "")
      || learningEnvironment.TEMPO_LEARNING_WORKSPACE !== target.cutover.workspace
      || learningEnvironment.TEMPO_LEARNING_TIMEZONE !== receipt.timezone
      || !absolute(learningEnvironment.PGPASSFILE) || !absolute(learningEnvironment.TEMPO_LEARNING_TOKEN_FILE))
      fail("study_runtime_credentials_mismatch", "Selected workspace, timezone, user and private paths must match the admitted configuration.");
    const secretsMount = configuration.services.learning.volumes?.find(mount => mount.type === "volume" && mount.read_only === true
      && learningEnvironment.PGPASSFILE.startsWith(mount.target + "/") && learningEnvironment.TEMPO_LEARNING_TOKEN_FILE.startsWith(mount.target + "/"));
    const secrets = configuration.volumes?.[secretsMount?.source];
    if (!secretsMount || secrets?.external !== true || !text(secrets.name) || secrets.name === pg.volumeName)
      fail("study_runtime_credentials_mismatch", "The selected external runtime-secret volume must be separate and mounted read-only.");
    const secretVolume = await ownedVolume(request, secrets.name);
    const networkEntries = Object.entries(configuration.networks ?? {});
    if (networkEntries.length !== 1 || networkEntries[0][1].external !== true || !/^[A-Za-z0-9][A-Za-z0-9_.-]+$/.test(networkEntries[0][1].name ?? ""))
      fail("study_runtime_network_mismatch", "One explicit independently owned serving network is required.");
    const networkName = networkEntries[0][1].name, networks = await json(request, ["network", "inspect", networkName]), network = networks?.[0];
    if (networks?.length !== 1 || network?.Name !== networkName || !/^[a-f0-9]{64}$/.test(network.Id ?? "")
      || network.Labels?.["tempo.owner"] !== target.project || network.Labels?.["tempo.target-kind"] !== "persistent"
      || network.Labels?.["tempo.disposable"] !== "false")
      fail("study_runtime_network_mismatch", "The current serving network does not match the owned persistent target.");
    const output = await invoke(request, [...compose(request), "ps", "--all", "--format", "json"]);
    let rows;
    try { rows = output.trim().startsWith("[") ? JSON.parse(output) : output.trim().split("\n").filter(Boolean).map(line => JSON.parse(line)); }
    catch { fail("study_runtime_probe_failed", "The current service inventory was not JSON."); }
    if (!Array.isArray(rows) || rows.some(row => !roles.includes(row.Service))
      || roles.some(role => rows.filter(row => row.Service === role).length > 1))
      fail("study_runtime_container_mismatch", "The registered target has an ambiguous or unrelated service inventory.");
    const containers = {};
    for (const role of roles) {
      const row = rows.find(row => row.Service === role);
      if (!row) { containers[role] = null; continue; }
      const id = row.ID ?? row.Id, inspected = await json(request, ["inspect", id]), container = inspected?.[0];
      if (inspected?.length !== 1 || !/^[a-f0-9]{64}$/.test(id ?? "") || container?.Id !== id || container.Image !== images[role].Id
        || container.Config?.Labels?.["com.docker.compose.project"] !== target.project
        || container.Config?.Labels?.["com.docker.compose.service"] !== role)
        fail("study_runtime_container_mismatch", "The registered service container, owner or immutable image does not match.");
      const selectedPorts = role === "study-web" ? normalizedPorts(target.ports) : [];
      if (!isDeepStrictEqual(actualPorts(container.HostConfig?.PortBindings), selectedPorts)
        || (container.State?.Running === true && !isDeepStrictEqual(actualPorts(container.NetworkSettings?.Ports), selectedPorts)))
        fail("study_runtime_ingress_mismatch", "The actual container host bindings do not match the registered Study-only loopback ingress.");
      const attachment = container.NetworkSettings?.Networks?.[networkName];
      if (attachment?.NetworkID !== network.Id)
        fail("study_runtime_network_mismatch", "The selected service is attached to another target network.");
      if (role === "postgres") {
        const mount = container.Mounts?.find(value => value.Destination === pg.mountDestination);
        if (mount?.Type !== "volume" || mount.Name !== pg.volumeName || mount.RW !== true
          || environment(container.Config.Env).PGDATA !== pg.dataDirectory)
          fail("study_runtime_volume_mismatch", "The actual PostgreSQL container is not bound to the selected data volume and path.");
      }
      if (role === "learning") {
        const actualEnvironment = environment(container.Config.Env);
        if (container.Config.User !== configuration.services.learning.user) fail("study_runtime_credentials_mismatch", "The actual Learning runtime user differs from its selected private-file owner.");
        for (const key of ["TEMPO_LEARNING_DATABASE_URL", "TEMPO_LEARNING_READER_URL", "TEMPO_LEARNING_WORKSPACE", "TEMPO_LEARNING_TIMEZONE", "TEMPO_LEARNING_TOKEN_FILE", "PGPASSFILE"])
          if (actualEnvironment[key] !== learningEnvironment[key]) fail("study_runtime_credentials_mismatch", "The actual Learning container uses another runtime credential or workspace configuration.");
        if (Object.keys(actualEnvironment).some(key => /^PG/.test(key) && key !== "PGPASSFILE"))
          fail("study_runtime_credentials_mismatch", "Unexpected libpq routing overrides are not accepted.");
        const mount = container.Mounts?.find(value => value.Destination === secretsMount.target);
        if (mount?.Type !== "volume" || mount.Name !== secrets.name || mount.RW !== false)
          fail("study_runtime_credentials_mismatch", "The actual Learning runtime secrets are not the selected read-only volume.");
      }
      containers[role] = container;
    }
    return { runtime, configuration, images, dataVolume, secretVolume, secretsMount, network, networkName, containers, learningEnvironment,
      database: receipt.database, writerRole: receipt.roles.writer, readerRole: receipt.roles.reader, port: receipt.endpoint.port };
  };
  const control = async (request, context, offline = false) => {
    const pg = context.runtime.postgres;
    const args = offline
      ? [...helper(request, "postgres-control"), "--network", "none", "--pull", "never", "--mount", `type=volume,src=${pg.volumeName},dst=${pg.mountDestination},readonly,volume-nocopy`, "--env", "LC_ALL=C", "--entrypoint", "/bin/sh", request.registration.cutover.images.postgres, "-ec", controlScript, "tempo-read-cluster", pg.dataDirectory]
      : ["exec", "--env", "LC_ALL=C", context.containers.postgres.Id, "/bin/sh", "-ec", controlScript, "tempo-read-cluster", pg.dataDirectory];
    const result = await invoke(request, args), major = result.match(/^TEMPO_PG_VERSION=([0-9]+)\s*$/m)?.[1], identifier = result.match(/^Database system identifier:\s*([0-9]+)\s*$/m)?.[1];
    if (Number(major) !== pg.major || identifier !== pg.systemIdentifier)
      fail("study_runtime_cluster_mismatch", "The existing data files do not identify the exact selected PostgreSQL cluster. No initialization or adoption is permitted.");
    return { major: Number(major), systemIdentifier: identifier };
  };
  const dbArguments = (request, context) => {
    const hostaddr = context.containers.postgres?.NetworkSettings.Networks[context.networkName].IPAddress;
    if (!text(hostaddr)) fail("study_runtime_database_unavailable", "The selected PostgreSQL container has no current target-network address.");
    return ["--expected-database", context.database, "--expected-workspace", request.registration.cutover.workspace,
      "--expected-schema", request.registration.cutover.schema, "--expected-system-identifier", context.runtime.postgres.systemIdentifier,
      "--expected-hostaddr", hostaddr, "--expected-port", String(context.port), "--expected-timezone", context.learningEnvironment.TEMPO_LEARNING_TIMEZONE];
  };
  const db = async (request, context, existingLearning) => {
    const command = ["/opt/learning/bin/python", "-I", "-m", "tempo_learning_database.runtime_readback", ...dbArguments(request, context)];
    const args = existingLearning ? ["exec", context.containers.learning.Id, ...command]
      : [...helper(request, "database-readback"), "--network", context.networkName, "--pull", "never", "--user", context.configuration.services.learning.user,
        "--mount", `type=volume,src=${context.secretVolume.Name},dst=${context.secretsMount.target},readonly,volume-nocopy`,
        ...["TEMPO_LEARNING_DATABASE_URL", "TEMPO_LEARNING_READER_URL", "TEMPO_LEARNING_WORKSPACE", "TEMPO_LEARNING_TIMEZONE", "PGPASSFILE"].flatMap(key => ["--env", `${key}=${context.learningEnvironment[key]}`]),
        "--entrypoint", command[0], request.registration.cutover.images.learning, ...command.slice(1)];
    const proof = await json(request, args), address = context.containers.postgres.NetworkSettings.Networks[context.networkName].IPAddress;
    if (proof?.status !== "verified" || proof.scope !== "observed" || !text(proof.evidenceId) || proof.read_only !== true
      || proof.database !== context.database || proof.schema !== request.registration.cutover.schema || proof.workspace_id !== request.registration.cutover.workspace
      || proof.timezone !== context.learningEnvironment.TEMPO_LEARNING_TIMEZONE || proof.server?.hostaddr !== address || proof.server.port !== context.port
      || proof.server.system_identifier !== context.runtime.postgres.systemIdentifier || proof.server.identity_scope !== "caller-bound"
      || proof.roles?.reader !== context.readerRole || proof.roles.writer !== context.writerRole)
      fail("study_runtime_database_unverified", "The public database verifier did not prove the exact restricted roles, schema, workspace and caller-bound physical target.");
    return proof;
  };
  const http = async (request, context) => {
    const result = await json(request, ["exec", context.containers.learning.Id, "/opt/learning/bin/python", "-I", "-c", httpScript]);
    if (result?.ready?.status !== "ready" || result.ready.workspace_id !== request.registration.cutover.workspace
      || result.bootstrap?.schema_version !== 1 || result.bootstrap.workspace_id !== request.registration.cutover.workspace
      || result.bootstrap.timezone !== context.learningEnvironment.TEMPO_LEARNING_TIMEZONE
      || result.study_token_sha256 !== context.runtime.credentialDigests.studyTokenSha256
      || result.runtime_passfile_sha256 !== context.runtime.credentialDigests.runtimePassfileSha256)
      fail("study_runtime_health_unverified", "Read-only Learning health, authenticated workspace metadata or private-file identities did not match.");
    let response;
    try { response = await fetcher(request.registration.webUrl, { method: "GET", redirect: "error", signal: AbortSignal.timeout(5000) }); }
    catch { fail("study_runtime_health_unverified", "The exact registered Study ingress did not answer its read-only check."); }
    if (!response?.ok || response.status !== 200) fail("study_runtime_health_unverified", "The exact registered Study ingress was unavailable.");
    return result;
  };
  const verdict = (request, context, state, facts = {}) => freeze({ status: "verified", scope: "observed", verified: true,
    evidenceId: digest({ request, state, runtime: context.runtime, containers: Object.fromEntries(roles.map(role => [role, context.containers[role]?.Id ?? null])), facts }),
    binding: request, state, runtime: context.runtime, clusterIdentityVerified: false, schemaVerified: false, healthVerified: false,
    services: Object.fromEntries(roles.map(role => [role, { containerId: context.containers[role]?.Id ?? null, running: context.containers[role]?.State?.Running === true }])), ...facts });
  const running = async (request, context) => {
    if (!roles.every(role => context.containers[role]?.State?.Running === true))
      fail("study_runtime_services_unavailable", "The exact PostgreSQL, Learning and Study services are not all running.");
    await control(request, context); const proof = await db(request, context, true); await http(request, context); await control(request, context);
    return verdict(request, context, "running", { clusterIdentityVerified: true, schemaVerified: true, healthVerified: true, databaseEvidenceId: proof.evidenceId });
  };
  return Object.freeze({
    async inspectRuntime(request, observation) {
      const context = await base(request, observation);
      return roles.every(role => context.containers[role]?.State?.Running === true) ? running(request, context) : verdict(request, context, "cold");
    },
    async prepareDatabase(request, observation) {
      if (!["start", "restart"].includes(request.command)) fail("study_runtime_prepare_command_invalid", "Only an admitted start/restart under the original lock may prepare PostgreSQL.");
      let context = await base(request, observation);
      // A missing verifier/image is detected before any database service start.
      await invoke(request, [...helper(request, "verifier-availability"), "--network", "none", "--pull", "never", "--entrypoint", "/opt/learning/bin/python",
        request.registration.cutover.images.learning, "-I", "-m", "tempo_learning_database.runtime_readback", "--help"]);
      await control(request, context, context.containers.postgres?.State?.Running !== true);
      if (context.containers.postgres?.State?.Running !== true) {
        await invoke(request, [...compose(request), "up", "-d", "--no-build", "--pull", "never", "--no-deps", "--wait", "--wait-timeout", "30", "postgres"]);
        context = await base(request, observation);
      }
      if (context.containers.postgres?.State?.Running !== true) fail("study_runtime_database_unavailable", "The exact existing PostgreSQL target did not become ready.");
      await control(request, context); const proof = await db(request, context, false); await control(request, context);
      return verdict(request, context, "database-ready", { clusterIdentityVerified: true, schemaVerified: true, databaseEvidenceId: proof.evidenceId });
    },
    async verifyRuntimeOutcome({ request, observation, desiredState }) {
      const context = await base(request, observation);
      if (desiredState === "running") {
        const proof = await running(request, context);
        return freeze({ ...proof, status: "complete" });
      }
      if (desiredState !== "stopped" || ["learning", "study-web"].some(role => context.containers[role]?.State?.Running === true))
        fail("study_runtime_stop_unverified", "The selected Study application services are not confirmed stopped.");
      return freeze({ ...verdict(request, context, "stopped"), status: "complete" });
    },
  });
}
