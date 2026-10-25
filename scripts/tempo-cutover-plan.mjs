// Pure preparation for the accepted launcher transition; this module performs no
// filesystem, Docker, database, browser, or activation operations.
import { isDeepStrictEqual } from "node:util";
import { isAbsolute, relative, resolve } from "node:path";
import { targetKey } from "./tempo-deployment.mjs";

// Existing public service names in docker-compose.study.yml (#367), not a
// discovered live target or permission to adopt its disposable volumes/settings.
const studyServices = ["study-web", "learning", "postgres"];

/**
 * @typedef {object} CutoverIdentity
 * @property {string} transitionId Caller-selected identity, never generated here.
 * @property {'quarantined'} phase Publication must precede activation-started.
 * @property {string} revision Full approved Git commit, shared by every image.
 * @property {Record<string,string>} images Exact service-to-image IDs/digests.
 * @property {{name:string,volume:string}} database Credential-free identity.
 * @property {string} workspace Stable authority, checked by the evidence owner.
 * @property {string[]} composeFiles Explicit retained composition paths.
 * @property {Record<string,{name:string,external:boolean}>} volumes
 * @property {{origin:string,hosts:string[]}} ingress Browser authority identity.
 * @property {string} schema Immutable identity of the exact Learning migration ID/checksum map.
 * @property {'graph-preserving-fresh-start'} mode Explicit operator-selected workflow.
 * @property {string} graphSourceReference Pinned public graph/repertoire source identity.
 * @property {string} graphReceipt Opaque final public graph verification reference.
 * @property {string} freshInitReceipt Opaque final clean-Learning verification reference.
 */

/**
 * Plan final graph-preserving fresh-start publication after graph import and
 * clean-Learning initialization, never the initial lifecycle intent. The original
 * database remains untouched. No old history or pending-work transfer is needed.
 *
 * `proposed` contains explicit candidate v2 registration and deployment records;
 * this function does not infer actual target values from an opt-in test stack.
 * Both records carry the same CutoverIdentity in `cutover`, extending existing
 * registration/deployment fields without changing the current launcher reader.
 * Graph and fresh-initialization public inputs remain opaque. Structural
 * correspondence does not establish graph closure, clean initialization or new
 * browser authority isolation. Actual public readers below fail closed until
 * their provider contracts are available; real cutover still needs confirmation.
 *
 * @param {{transitionId:string,freshStartChoice:'graph-preserving-fresh-start',
 *   current:{registration:object,receipt:object|null}, proposed:{registration:object,receipt:object},
 *   graphSource:object,graphVerification:object,freshInitialization:object}} input
 * @returns {{status:'blocked'|'planned',blockers:{code:string,message:string}[],
 *   plan:null|{version:1,transitionId:string,revision:string,sourceIdentity:string,
 *     targetIdentity:string,lockIdentity:string,registration:object,receipt:object,
 *     original:{registration:object,receipt:object|null},freshStartChoice:string,
 *     graphSource:object,graphVerification:object,freshInitialization:object}}}
 */
export function planLauncherFreshStart(input = {}) {
  const blockers = [];
  const block = (code, message) => blockers.push({ code, message });
  let supplied;
  try {
    // Durable records must be JSON data. Reject undefined, non-finite numbers,
    // functions, prototypes and cycles instead of silently changing originals.
    if (!record(input)) throw new Error("Input record required");
    assertJson(input);
    supplied = JSON.parse(JSON.stringify(input));
  } catch {
    return freeze({ status: "blocked", blockers: [{ code: "invalid_input", message: "Supply finite JSON records and explicit transition inputs." }], plan: null });
  }
  const { transitionId, current, proposed, freshStartChoice, graphSource, graphVerification, freshInitialization } = supplied;
  const original = current?.registration;
  const priorReceipt = current?.receipt;
  const registration = proposed?.registration;
  const receipt = proposed?.receipt;
  const identity = registration?.cutover;

  if (!text(transitionId)) block("transition_identity_missing", "Supply the pinned transition identity.");
  if (!record(original) || original.version !== 1)
    block("existing_v1_required", "Supply the original v1 registration identity; unregistered install is a separate workflow.");
  if (!record(current) || !Object.hasOwn(current, "receipt"))
    block("prior_publication_unknown", "Supply the prior receipt record or explicit null for known absence; unknown publication state cannot be inferred.");
  else if (priorReceipt !== null && (!record(priorReceipt) || priorReceipt.version !== 1))
    block("original_receipt_invalid", "The explicit prior receipt must be a v1 record or null for known absence.");
  if (!targetShape(original)) block("source_identity_missing", "Supply the complete original registration identity.");
  if (!record(registration) || registration.version !== 2 || !record(receipt) || receipt.version !== 2)
    block("candidate_v2_required", "Supply explicit proposed v2 registration and deployment receipt records.");
  if (!targetShape(registration)) block("target_identity_missing", "Supply explicit target context, daemon, paths, ports and volume identities.");
  if (!record(identity) || !record(receipt?.cutover) || !isDeepStrictEqual(identity, receipt.cutover))
    block("publication_identity_mismatch", "Proposed registration and receipt must contain one identical cutover identity.");
  if (freshStartChoice !== "graph-preserving-fresh-start")
    block("fresh_start_choice_required", "Explicitly select the graph-preserving fresh-start workflow.");
  if (!record(graphSource) || !Object.keys(graphSource).length || !record(graphVerification) || !Object.keys(graphVerification).length
    || !text(identity?.graphSourceReference) || !text(identity?.graphReceipt))
    block("graph-contract-pending", "Supply explicit graph/repertoire source and final public graph verification inputs; the actual provider contract is pending.");
  if (!record(freshInitialization) || !Object.keys(freshInitialization).length || !text(identity?.freshInitReceipt))
    block("fresh-initialization-contract-pending", "Supply final clean-Learning initialization verification; the actual provider contract is pending.");

  if (record(identity)) {
    if (identity.transitionId !== transitionId || identity.phase !== "quarantined" || identity.mode !== "graph-preserving-fresh-start")
      block("publication_phase_invalid", "Pin the same transition in quarantined phase before any activation.");
    if (!/^[a-f0-9]{40}$/.test(identity.revision ?? ""))
      block("revision_unpinned", "Supply one exact approved full Git revision.");
    if (!record(identity.images) || !Object.keys(identity.images).length
      || Object.values(identity.images).some(image => !immutableImage(image)))
      block("images_unpinned", "Supply immutable image IDs or repository digests for every selected artifact.");
    if (studyServices.some(service => !Object.hasOwn(identity.images ?? {}, service)))
      block("required_artifact_missing", "Select immutable Study web, Learning and PostgreSQL artifacts using the existing composition service identities.");
    if (["api", "redis", "foreground-worker", "background-worker", "background-scheduler", "defense-engine", "maia-worker"]
      .some(service => Object.hasOwn(identity.images ?? {}, service)))
      block("legacy_service_mixture", "Study publication must not retain legacy application or optional worker service dependencies.");
    if (!text(identity.workspace) || !record(identity.database) || !text(identity.database.name) || !text(identity.database.volume)
      || !schemaIdentity(identity.schema))
      block("authority_identity_missing", "Supply explicit new database, workspace and schema identities.");
    if (!paths(identity.composeFiles) || !volumeMap(identity.volumes) || !ingressShape(identity.ingress))
      block("composition_identity_missing", "Supply explicit Compose, volume, ingress origin and allowed host identities.");
    if (!isDeepStrictEqual(identity.composeFiles, registration?.composeFiles)
      || !isDeepStrictEqual(identity.volumes, registration?.volumes)
      || identity.database?.volume !== registration?.volumes?.[registration?.postgresVolumeKey]?.name
      || origin(registration?.webUrl) !== identity.ingress?.origin)
      block("registration_identity_mismatch", "Registration paths, volumes, database and ingress must match its cutover identity.");
    if (receipt?.revision !== identity.revision || receipt?.schema !== identity.schema
      || !isDeepStrictEqual(receipt?.images, identity.images) || !isDeepStrictEqual(receipt?.composeFiles, identity.composeFiles))
      block("receipt_identity_mismatch", "Deployment revision, schema, images and Compose paths must match its cutover identity.");
    if (immutableImage(registration?.postgresImage) && identity.images?.postgres !== registration.postgresImage)
      block("postgres_image_mismatch", "The registered PostgreSQL image must match the selected postgres service artifact.");
    if (!immutableImage(registration?.postgresImage))
      block("postgres_image_unpinned", "Supply the immutable PostgreSQL image identity; tags alone are insufficient.");
    if (Array.isArray(registration?.ports)) {
      if (registration.ports.some(port => !Object.hasOwn(identity.images ?? {}, port?.service)))
        block("port_service_unselected", "Every published port must belong to a selected service artifact.");
      if (!registration.ports.some(port => studyIngressPort(port, registration.webUrl)))
        block("ingress_service_mismatch", "The registered web authority must be published by the selected study-web ingress service.");
    }
  }
  if (targetShape(original) && targetShape(registration)) {
    if (original.daemonId !== registration.daemonId || original.context !== registration.context)
      block("daemon_change_unqualified", "A daemon/context move requires its separate accepted identity contract.");
    const preserved = new Set(Object.values(original.volumes).map(volume => volume.name));
    if (Object.values(registration.volumes).some(volume => preserved.has(volume.name)))
      block("preserved_volume_rebinding", "The new target must not rebind preserved legacy data or backup volumes.");
    if (Boolean(original.disposable) !== Boolean(registration.disposable))
      block("disposable_authority_mismatch", "A disposable installation cannot be rebound as a real authority or the reverse.");
    if (inside(registration.envFile, registration.root))
      block("private_configuration_in_checkout", "Reference private file-backed Learning configuration outside the checkout.");
  }
  if (containsSecret(proposed))
    block("secret_material_forbidden", "Candidate records must contain file references and credential-free identities, never secret literals.");
  if (blockers.length) return freeze({ status: "blocked", blockers, plan: null });

  const sourceIdentity = targetKey(original);
  return freeze({ status: "planned", blockers, plan: { version: 1, transitionId, revision: identity.revision,
    sourceIdentity, targetIdentity: targetKey(registration), lockIdentity: sourceIdentity,
    registration, receipt, original: current, freshStartChoice, graphSource, graphVerification, freshInitialization } });
}

/** Fail closed: no actual public graph verification schema/reader is accepted yet. */
export function readGraphTransferVerification(input) {
  void input; // No graph fields, aliases or acceptance semantics are guessed.
  return freeze({ status: "blocked", verification: null, blockers: [{ code: "graph-contract-pending",
    message: "The public graph/repertoire closure and verification reader are pending; opaque or synthetic packets cannot qualify an actual graph." }] });
}

/** Fail closed: clean-state and new-authority isolation need their public owner. */
export function readFreshInitializationVerification(input) {
  void input;
  return freeze({ status: "blocked", verification: null, blockers: [{ code: "fresh-initialization-contract-pending",
    message: "The public clean-Learning initialization and new-authority isolation reader are pending; structural target identities cannot establish them." }] });
}

const record = value => value !== null && typeof value === "object" && !Array.isArray(value);
const text = value => typeof value === "string" && value.trim().length > 0;
const paths = value => Array.isArray(value) && value.length > 0 && value.every(path => text(path) && isAbsolute(path));
const schemaIdentity = value => typeof value === "string" && /^sha256:[a-f0-9]{64}$/.test(value);
const immutableImage = value => typeof value === "string" && /^(?:sha256:[a-f0-9]{64}|[^\s@]+@sha256:[a-f0-9]{64})$/.test(value);
const volumeMap = value => record(value) && Object.keys(value).length > 0
  && Object.values(value).every(volume => record(volume) && text(volume.name) && typeof volume.external === "boolean")
  && new Set(Object.values(value).map(volume => volume.name)).size === Object.keys(value).length;
function origin(value) {
  try { const parsed = new URL(value); return !parsed.username && !parsed.password && ["http:", "https:"].includes(parsed.protocol) ? parsed.origin : null; }
  catch { return null; }
}
function ingressShape(value) {
  return record(value) && origin(value.origin) === value.origin && Array.isArray(value.hosts) && value.hosts.length > 0
    && value.hosts.every(text) && new Set(value.hosts).size === value.hosts.length;
}
function studyIngressPort(port, webUrl) {
  try {
    const url = new URL(webUrl);
    const published = url.port || (url.protocol === "https:" ? "443" : "80");
    return port?.service === "study-web" && port.host === url.hostname.replace(/^\[|\]$/g, "") && port.port === published && port.target === 80;
  } catch { return false; }
}
function targetShape(value) {
  return record(value) && ["root", "envFile"].every(key => text(value[key]) && isAbsolute(value[key]))
    && ["context", "daemonId", "project", "postgresVolumeKey"].every(key => text(value[key]))
    && paths(value.composeFiles) && volumeMap(value.volumes) && Boolean(value.volumes[value.postgresVolumeKey])
    && Number.isInteger(value.postgresMajor) && value.postgresMajor > 0 && Boolean(origin(value.webUrl))
    && Array.isArray(value.ports) && value.ports.length > 0 && value.ports.every(port => record(port)
      && ["service", "host", "port"].every(key => text(port[key])) && Number.isInteger(port.target) && port.target > 0);
}
function inside(path, directory) {
  const difference = relative(resolve(directory), resolve(path));
  return difference === "" || (!difference.startsWith("..") && !isAbsolute(difference));
}
function containsSecret(value) {
  if (typeof value === "string") return /(?:postgres(?:ql)?|https?):\/\/[^/\s@]+:[^/\s@]+@|Bearer\s+\S+/i.test(value);
  if (!value || typeof value !== "object") return false;
  return Object.entries(value).some(([key, entry]) => /^(?:password|token|secret|credentials|PGPASSWORD|TEMPO_LEARNING_DATABASE_URL|TEMPO_LEARNING_READER_URL)$|_(?:TOKEN|PASSWORD|SECRET)$/i.test(key)
    || containsSecret(entry));
}
function assertJson(value, ancestors = new Set()) {
  if (value === null || typeof value === "string" || typeof value === "boolean" || (typeof value === "number" && Number.isFinite(value))) return;
  if (!value || typeof value !== "object" || ancestors.has(value)
    || (!Array.isArray(value) && Object.getPrototypeOf(value) !== Object.prototype)) throw new Error("Not JSON data");
  ancestors.add(value);
  const entries = Reflect.ownKeys(value).filter(key => !(Array.isArray(value) && key === "length"));
  if (Array.isArray(value) && entries.length !== value.length) throw new Error("Sparse or decorated array");
  for (const key of entries) {
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    if (typeof key !== "string" || !descriptor.enumerable || !Object.hasOwn(descriptor, "value")) throw new Error("Not JSON data");
    assertJson(descriptor.value, ancestors);
  }
  ancestors.delete(value);
}
function freeze(value) {
  if (value && typeof value === "object") { for (const entry of Object.values(value)) freeze(entry); Object.freeze(value); }
  return value;
}
