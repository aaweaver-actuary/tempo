// Public configuration boundary for the existing persistent Study composition.
// A matching configuration proves no admission, current DB identity or fence.
import { isDeepStrictEqual } from "node:util";
import { isAbsolute, relative, resolve } from "node:path";
import { portsFromConfig, targetKey } from "./tempo-deployment.mjs";
import { TempoProblem } from "./tempo-guidance.mjs";

const object = value => value !== null && typeof value === "object" && !Array.isArray(value);
const text = value => typeof value === "string" && value.trim().length > 0;
const hash = value => typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
const image = value => typeof value === "string" && /^(?:sha256:[0-9a-f]{64}|[^\s@]+@sha256:[0-9a-f]{64})$/.test(value);
const services = ["learning", "postgres", "study-web"];
const pgHealth = ["CMD-SHELL", "pg_isready -U postgres -d postgres"];
const learningHealth = ["CMD", "/opt/learning/bin/python", "-I", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health/ready', timeout=2).close()"];
function requireFact(condition, code, message) {
  if (!condition) throw new TempoProblem(code, message, {
    action: "Preserve the target and registration. Supply the exact admitted persistent serving inputs; this command did not provision, import or admit a target.",
  });
}
function exactKeys(value, keys) {
  return object(value) && isDeepStrictEqual(Object.keys(value).sort(), [...keys].sort());
}
function freeze(value) {
  if (object(value) || Array.isArray(value)) { Object.values(value).forEach(freeze); Object.freeze(value); }
  return value;
}
function within(path, parent) {
  const difference = relative(resolve(parent), resolve(path));
  return difference === "" || (!difference.startsWith("..") && !isAbsolute(difference));
}
function dsn(value, database, role) {
  if (typeof value !== "string") return false;
  const fields = {};
  for (const word of value.trim().split(/\s+/)) {
    const match = /^([a-z_]+)=([a-zA-Z0-9_.-]+)$/.exec(word);
    if (!match || Object.hasOwn(fields, match[1])) return false;
    fields[match[1]] = match[2];
  }
  return isDeepStrictEqual(fields, { host: "postgres", port: "5432", dbname: database, user: role });
}
function networkOnly(service) {
  return exactKeys(service.networks, ["persistent"]) && service.networks.persistent === null;
}
function mount(value, source, target, readOnly) {
  return object(value) && value.type === "volume" && value.source === source && value.target === target
    && Boolean(value.read_only) === readOnly && !value.bind && !value.consistency
    && Object.keys(value).every(key => ["type", "source", "target", "read_only", "volume"].includes(key))
    && (!value.volume || Object.keys(value.volume).length === 0);
}
function dependencies(value, owner) {
  return exactKeys(value, [owner]) && object(value[owner]) && value[owner].condition === "service_healthy"
    && value[owner].required !== false && value[owner].restart !== true;
}
const normalizedPorts = ports => ports.map(port => ({ service: port.service, host: port.host,
  port: String(port.port), target: Number(port.target) })).sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));

/** Existing #462 public intent/receipt are inputs, not newly invented authority.
 * Returns an independent frozen copy of the rendered configuration. Admission,
 * current daemon/cluster identity, private byte/mode checks and original-writer
 * fencing remain the owning authority/runtime readers' required observations.
 */
export function validateStudyServingConfiguration({ registration, configuration, persistentIntent: intent, persistentReceipt: receipt } = {}) {
  const cutover = registration?.cutover, runtime = registration?.installedStudy?.runtime;
  requireFact(registration?.version === 2 && cutover?.mode === "graph-preserving-fresh-start"
    && /^[a-f0-9]{40}$/.test(cutover.revision ?? "") && /^sha256:[a-f0-9]{64}$/.test(cutover.schema ?? "")
    && exactKeys(cutover.images, services) && Object.values(cutover.images).every(image)
    && text(registration.root) && isAbsolute(registration.root) && text(registration.envFile) && isAbsolute(registration.envFile)
    && !within(registration.envFile, registration.root)
    && Array.isArray(registration.composeFiles) && registration.composeFiles.length > 0
    && registration.composeFiles.every(path => typeof path === "string" && isAbsolute(path))
    && isDeepStrictEqual(registration.composeFiles, cutover.composeFiles)
    && isDeepStrictEqual(registration.volumes, cutover.volumes) && registration.webUrl === cutover.ingress?.origin,
  "study_serving_registration_invalid", "Supply the complete pinned v2 registration before reading serving configuration.");
  requireFact(intent?.kind === "tempo-learning-persistent-target-intent" && intent.schema_version === 1
    && receipt?.kind === "tempo-learning-persistent-bootstrap" && receipt.schema_version === 1
    && receipt.evidence_scope === "observed" && receipt.status === "initialized-quarantine-required"
    && intent.target_identity === targetKey(registration) && receipt.target_identity === intent.target_identity
    && intent.project === registration.project && receipt.project === intent.project
    && intent.context === registration.context && intent.daemon_id === registration.daemonId
    && intent.transition_id === cutover.transitionId && receipt.transition_id === intent.transition_id
    && intent.source_revision === cutover.revision && receipt.declared_source_revision === cutover.revision
    && isDeepStrictEqual(intent.images, cutover.images) && isDeepStrictEqual(receipt.declared_images, cutover.images)
    && receipt.observed_postgres_image === cutover.images.postgres
    && receipt.schema?.identity === cutover.schema && hash(receipt.intent_sha256) && hash(receipt.resource_observation_sha256),
  "study_serving_persistent_binding_mismatch", "Existing persistent intent and bootstrap receipt must bind the same pinned registration. Bootstrap is not admission.");
  requireFact(intent.database === cutover.database?.name && receipt.database === intent.database
    && intent.workspace_id === cutover.workspace && receipt.workspace_id === intent.workspace_id
    && intent.ingress_origin === registration.webUrl && receipt.ingress_origin === intent.ingress_origin
    && intent.timezone === receipt.timezone && text(intent.timezone)
    && exactKeys(receipt.roles, ["owner", "writer", "reader"])
    && ["owner", "writer", "reader"].every(role => receipt.roles[role] === `${intent.database}_${role}`)
    && receipt.endpoint?.host === "postgres" && receipt.endpoint.port === 5432,
  "study_serving_database_binding_mismatch", "Serving database, roles, workspace, timezone and ingress must match the public persistent receipt.");
  requireFact(exactKeys(registration.volumes, ["postgres-data", "runtime-secrets"])
    && registration.postgresVolumeKey === "postgres-data"
    && registration.volumes["postgres-data"]?.name === intent.data_volume
    && registration.volumes["runtime-secrets"]?.name === intent.runtime_secrets_volume
    && intent.data_volume === cutover.database.volume && intent.data_volume !== intent.runtime_secrets_volume
    && Object.values(registration.volumes).every(value => object(value) && value.external === true)
    && Array.isArray(intent.source_volume_names) && !intent.source_volume_names.some(name => [intent.data_volume, intent.runtime_secrets_volume].includes(name)),
  "study_serving_volume_binding_mismatch", "Serving uses only the two distinct registered external persistent volumes, disjoint from source volumes.");
  const postgres = runtime?.postgres, credentials = runtime?.credentialDigests;
  requireFact(runtime?.version === 1 && postgres?.volumeKey === "postgres-data" && postgres.volumeName === intent.data_volume
    && postgres.mountDestination === "/var/lib/postgresql" && isAbsolute(postgres.dataDirectory ?? "")
    && resolve(postgres.dataDirectory) !== resolve(postgres.mountDestination) && within(postgres.dataDirectory, postgres.mountDestination)
    && postgres.major === registration.postgresMajor && Number.isInteger(postgres.major) && postgres.major > 0
    && postgres.systemIdentifier === receipt.system_identifier && text(postgres.systemIdentifier)
    && hash(credentials?.studyTokenSha256) && credentials.studyTokenSha256 === receipt.private_output_sha256?.["study.token"]
    && hash(credentials.runtimePassfileSha256) && credentials.runtimePassfileSha256 === receipt.private_output_sha256?.["runtime.pgpass"],
  "study_serving_runtime_binding_mismatch", "The installed runtime descriptor must bind existing cluster layout and private output digests from the persistent receipt.");
  const config = configuration;
  requireFact(object(config) && config.name === registration.project && exactKeys(config.services, services)
    && Object.keys(config).every(key => ["name", "services", "volumes", "networks", "secrets"].includes(key))
    && exactKeys(config.volumes, ["postgres-data", "runtime-secrets"])
    && Object.entries(config.volumes).every(([key, value]) => exactKeys(value, ["name", "external"])
      && value.external === true && value.name === registration.volumes[key].name)
    && exactKeys(config.networks, ["persistent"]) && object(config.networks.persistent) && config.networks.persistent.external === true
    && config.networks.persistent.name === intent.network_name
    && Object.keys(config.networks.persistent).every(key => ["name", "external", "ipam"].includes(key))
    && (!config.networks.persistent.ipam || exactKeys(config.networks.persistent.ipam, [])),
  "study_serving_resources_mismatch", "Only the registered three-service composition and exact external persistent resources may serve Study.");
  const allowed = ["image", "pull_policy", "command", "entrypoint", "user", "labels", "environment", "networks", "secrets", "volumes", "healthcheck", "restart", "read_only", "tmpfs", "depends_on", "ports"];
  for (const name of services) {
    const service = config.services[name];
    requireFact(object(service) && service.image === cutover.images[name] && service.pull_policy === "never" && service.restart === "no"
      && Object.keys(service).every(key => allowed.includes(key)) && service.entrypoint == null && networkOnly(service)
      && isDeepStrictEqual(service.labels, { "tempo.owner": registration.project, "tempo.target-kind": "persistent", "tempo.disposable": "false" }),
    "study_serving_service_mismatch", "Serving services must keep pinned images, explicit lifecycle ownership and the selected private network without extra capabilities.");
  }
  const pg = config.services.postgres, learning = config.services.learning, web = config.services["study-web"];
  requireFact(isDeepStrictEqual(pg.command, ["postgres", "-c", "max_locks_per_transaction=512"])
    && isDeepStrictEqual(pg.environment, { POSTGRES_DB: "postgres", POSTGRES_USER: "postgres", POSTGRES_PASSWORD_FILE: "/run/secrets/persistent-postgres-password", PGDATA: postgres.dataDirectory })
    && pg.volumes?.length === 1 && mount(pg.volumes[0], "postgres-data", postgres.mountDestination, false)
    && isDeepStrictEqual(pg.healthcheck?.test, pgHealth) && !pg.ports?.length && !pg.depends_on && !pg.tmpfs && !pg.user
    && pg.secrets?.length === 1 && pg.secrets[0].source === "persistent-postgres-password"
    && pg.secrets[0].target === "persistent-postgres-password" && ["0400", "256"].includes(String(pg.secrets[0].mode))
    && exactKeys(config.secrets, ["persistent-postgres-password"])
    && object(config.secrets["persistent-postgres-password"])
    && Object.keys(config.secrets["persistent-postgres-password"]).every(key => ["name", "file"].includes(key))
    && isAbsolute(config.secrets["persistent-postgres-password"].file ?? "")
    && !within(config.secrets["persistent-postgres-password"].file, registration.root),
  "study_serving_postgres_mismatch", "PostgreSQL must retain the exact existing cluster mount, private file-backed credential and non-mutating readiness probe.");
  const env = learning.environment;
  const expectedEnv = { TEMPO_LEARNING_WORKSPACE: cutover.workspace,
    TEMPO_LEARNING_TOKEN_FILE: "/run/persistent/bootstrap/study.token", TEMPO_LEARNING_ORIGINS: registration.webUrl,
    TEMPO_LEARNING_HOSTS: "127.0.0.1,learning", TEMPO_LEARNING_TIMEZONE: intent.timezone,
    TEMPO_LEARNING_MAINTENANCE: "1", PGPASSFILE: "/run/persistent/bootstrap/runtime.pgpass" };
  requireFact(object(env) && Object.entries(expectedEnv).every(([key, value]) => env[key] === value)
    && exactKeys(env, [...Object.keys(expectedEnv), "TEMPO_LEARNING_DATABASE_URL", "TEMPO_LEARNING_READER_URL"])
    && dsn(env.TEMPO_LEARNING_DATABASE_URL, intent.database, receipt.roles.writer)
    && dsn(env.TEMPO_LEARNING_READER_URL, intent.database, receipt.roles.reader)
    && learning.user === "10001:10001" && learning.read_only === true && isDeepStrictEqual(learning.tmpfs, ["/tmp"])
    && isDeepStrictEqual(learning.command, ["serve", "--host", "0.0.0.0", "--port", "8001"])
    && learning.volumes?.length === 1 && mount(learning.volumes[0], "runtime-secrets", "/run/persistent", true)
    && dependencies(learning.depends_on, "postgres") && isDeepStrictEqual(learning.healthcheck?.test, learningHealth)
    && !learning.ports?.length && !learning.secrets,
  "study_serving_learning_mismatch", "Learning serving authority, credential-free DSNs, private mounts and ready-only health probe must match the exact admitted target.");
  let url;
  try { url = new URL(registration.webUrl); }
  catch { requireFact(false, "study_serving_ingress_mismatch", "Study serving requires the registered loopback HTTP origin."); }
  requireFact(url.protocol === "http:" && url.hostname === "127.0.0.1" && url.origin === registration.webUrl
    && !url.username && !url.password && !url.search && !url.hash
    && dependencies(web.depends_on, "learning") && !web.command && !web.environment && !web.volumes && !web.secrets && !web.healthcheck
    && web.ports?.length === 1 && web.ports[0].protocol === "tcp"
    && isDeepStrictEqual(normalizedPorts(portsFromConfig(config)), normalizedPorts(registration.ports ?? []))
    && web.ports[0].host_ip === "127.0.0.1" && Number(web.ports[0].target) === 80
    && String(web.ports[0].published) === (url.port || "80"),
  "study_serving_ingress_mismatch", "Study ingress must be the exact registered same-origin loopback HTTP endpoint from the pinned Study image.");
  return freeze(structuredClone(config));
}

/** Read-only configuration rendering through the existing command executor. */
export async function readStudyServingConfiguration(request, { run, persistentIntent, persistentReceipt } = {}) {
  const selection = structuredClone({ registration: request?.registration, persistentIntent, persistentReceipt });
  const { registration } = selection;
  requireFact(typeof run === "function" && registration?.version === 2
    && text(registration.context) && text(registration.project) && isAbsolute(registration.root ?? "")
    && isAbsolute(registration.envFile ?? "") && Array.isArray(registration.composeFiles)
    && registration.composeFiles.length > 0 && registration.composeFiles.every(path => typeof path === "string" && isAbsolute(path)),
  "study_serving_input_pending", "Supply the explicit registered target and existing public command executor.");
  const result = await run("docker", ["--context", registration.context, "compose", "--project-directory", registration.root,
    "--env-file", registration.envFile, "-p", registration.project, ...registration.composeFiles.flatMap(path => ["-f", path]),
    "config", "--format", "json"], { echo: false });
  requireFact(result?.code === 0, "study_serving_configuration_unavailable", "Compose configuration could not be read; no service operation was started.");
  let configuration;
  try { configuration = JSON.parse(result.stdout); }
  catch { requireFact(false, "study_serving_configuration_unavailable", "Compose configuration returned invalid JSON; no service operation was started."); }
  return validateStudyServingConfiguration({ registration, configuration,
    persistentIntent: selection.persistentIntent, persistentReceipt: selection.persistentReceipt });
}
