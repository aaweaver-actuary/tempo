// Normal CLI composition boundary for an already published Study authority.
// This module does not supply a production provider or perform cutover effects.
import { isDeepStrictEqual } from "node:util";
import { portsFromConfig, targetKey } from "./tempo-deployment.mjs";
import { TempoProblem } from "./tempo-guidance.mjs";

const text = value => typeof value === "string" && value.trim().length > 0;
const object = value => value !== null && typeof value === "object" && !Array.isArray(value);
const immutableImage = value => typeof value === "string" && /^(?:sha256:[0-9a-f]{64}|[^\s]+@sha256:[0-9a-f]{64})$/.test(value);
const reject = (code, message) => { throw new TempoProblem(code, message, {
  action: "Next: supply the independently qualified installed Study provider and exact published authority evidence. Preserve the registration and target; do not restart the legacy writer.",
}); };
function snapshot(value) {
  const result = structuredClone(value);
  const freeze = entry => {
    if (entry !== null && typeof entry === "object") { Object.values(entry).forEach(freeze); Object.freeze(entry); }
    return entry;
  };
  return freeze(result);
}

function verifyObservation(observation, request) {
  const { registration } = request, identity = registration.cutover;
  const receipt = observation?.receipt, authority = observation?.authority;
  if (observation?.status !== "verified" || observation.scope !== "observed"
    || !text(observation.evidenceId) || !isDeepStrictEqual(observation.binding, request))
    reject("study_authority_unverified", "Study inspection must verify this exact registration, command and configuration path with current observed evidence.");
  if (receipt?.version !== 2 || !isDeepStrictEqual(receipt.cutover, identity)
    || receipt.revision !== identity.revision || receipt.schema !== identity.schema
    || !isDeepStrictEqual(receipt.images, identity.images)
    || !isDeepStrictEqual(receipt.composeFiles, identity.composeFiles))
    reject("study_publication_mismatch", "Study registration and deployment receipt must publish the same pinned target, graph, schema and artifacts.");
  if (!text(authority?.evidenceId) || authority.targetIdentity !== targetKey(registration)
    || !text(authority.sourceIdentity) || authority.lockIdentity !== authority.sourceIdentity
    || authority.phase !== "complete" || authority.originalWriterFenced !== true)
    reject("study_admission_unverified", "A quarantined publication or disposable bootstrap is not admission. Verify completed Study authority and its retained original maintenance-lock identity.");
  return snapshot(observation);
}

/**
 * An outer installed composition supplies only semantic public capabilities:
 * inspectRegisteredTarget(request) is read-only and returns a matching v2 receipt
 * plus observed completed-authority facts bound to the entire request.
 * withTargetLock({lockIdentity}, callback) retains the original transition lock.
 * backupAuthoritativeTarget / stopAuthoritativeTarget / maintainAuthoritativeTarget
 * own their effects; none may provision, import, publish or select another target.
 * No provider is selected from an environment variable, config path or fixture.
 */
export async function runRegisteredStudyCommand({ options, registration, log = console.log }, provider) {
  const identity = registration?.cutover;
  if (registration?.version !== 2 || !object(identity) || identity.mode !== "graph-preserving-fresh-start"
    || !/^[0-9a-f]{40}$/.test(identity.revision ?? "") || !/^sha256:[0-9a-f]{64}$/.test(identity.schema ?? "")
    || !["transitionId", "workspace", "graphSourceReference", "graphReceipt", "freshInitReceipt"].every(key => text(identity[key]))
    || !object(registration.volumes) || !Object.keys(registration.volumes).length
    || !object(identity.images) || !isDeepStrictEqual(Object.keys(identity.images).sort(), ["learning", "postgres", "study-web"])
    || !Object.values(identity.images).every(immutableImage)
    || !text(registration.daemonId) || !text(registration.context) || !text(registration.project)
    || !isDeepStrictEqual(registration.composeFiles, identity.composeFiles)
    || !isDeepStrictEqual(registration.volumes, identity.volumes) || registration.webUrl !== identity.ingress?.origin)
    reject("study_registration_incomplete", "Study registration must contain the complete explicit graph-preserving v2 publication identity.");
  if (provider?.executionScope !== "installed" || typeof provider.inspectRegisteredTarget !== "function")
    reject("study_provider_unavailable", "The installed Study launcher provider is unavailable. No legacy runtime, Docker or target effect was started.");
  if (options.flags.has("--no-ci") || options.flags.has("--no-wait") || options.flags.has("--retry"))
    reject("study_option_unavailable", "Legacy candidate-selection and migration-retry flags cannot bypass pinned Study authority. Use the explicit owning recovery procedure.");
  const request = snapshot({ registration, command: options.command, configPath: options.configPath,
    flags: [...options.flags].sort(), services: options.services });
  const readOnly = ["status", "doctor", "logs"].includes(request.command) || request.flags.includes("--plan");
  const inspect = async () => verifyObservation(await provider.inspectRegisteredTarget(request), request);
  const observation = await inspect();
  if (readOnly) {
    // Inspection owns any read-only diagnostic output, including bounded logs.
    log(`Study ${request.command}: published authority verified at ${identity.revision.slice(0, 12)}${request.flags.includes("--plan") ? "; plan only, no effects" : ""}.`);
    return 0;
  }
  const capability = request.command === "backup" ? "backupAuthoritativeTarget"
    : request.command === "stop" ? "stopAuthoritativeTarget"
      : ["start", "restart", "migrate"].includes(request.command) ? "maintainAuthoritativeTarget" : null;
  if (!capability || typeof provider[capability] !== "function" || typeof provider.withTargetLock !== "function")
    reject("study_command_unavailable", "The installed Study provider does not supply this command and original-authority lock capability.");
  let calls = 0, completed = false, lockOpen = true, result;
  try { result = await provider.withTargetLock(snapshot({ lockIdentity: observation.authority.lockIdentity }), async () => {
    if (!lockOpen) reject("study_lock_callback_invalid", "The original-target lock callback cannot run after its owning invocation returned.");
    if (++calls !== 1) reject("study_lock_callback_invalid", "The original-target lock capability invoked its command callback more than once.");
    const current = await inspect();
    if (!lockOpen) reject("study_lock_callback_invalid", "The original-target lock capability released before inspection completed.");
    if (!isDeepStrictEqual(current.receipt, observation.receipt) || !isDeepStrictEqual(current.authority, observation.authority))
      reject("study_authority_changed", "Study authority changed while acquiring the maintenance lock; no command effect was started.");
    const result = await provider[capability](request, current);
    if (result?.status !== "complete" || result.scope !== "observed" || !text(result.evidenceId) || !isDeepStrictEqual(result.binding, request))
      reject("study_command_unverified", "The Study command did not return verified completion for this exact authoritative target. Preserve its evidence and inspect before retrying.");
    completed = true;
    return 0;
  }); } finally { lockOpen = false; }
  if (calls !== 1 || !completed || result !== 0)
    reject("study_lock_callback_invalid", "The original-target lock capability did not retain and complete exactly one verified command callback.");
  return 0;
}

/**
 * Bounded source composition for an installed provider. readAuthority is REQUIRED:
 * an owning public reader must independently verify completed real admission and
 * supply the approved serving configuration. Neither a bootstrap receipt nor
 * createCutoverState.readJournal() is that capability. state reuses only the
 * existing createCutoverState {lockIdentity,withLock} public lock interface.
 * run is the existing commandExecutor capability. No command below builds,
 * pulls, imports, provisions, publishes, restores, or mutates the source target.
 * verifyRuntimeOutcome is a read-only public runtime proof, never a queue GET.
 */
export function createInstalledStudyLauncher({ readAuthority, state, run, verifyRuntimeOutcome, backupTarget } = {}) {
  const compose = request => {
    const target = request.registration;
    return ["--context", target.context, "compose", "--project-directory", target.root,
      "--env-file", target.envFile, "-p", target.project, ...target.composeFiles.flatMap(path => ["-f", path])];
  };
  const checkConfiguration = async (request, observation) => {
    if (typeof run !== "function" || !object(observation.configuration))
      reject("study_configuration_contract_pending", "Supply the public command executor and independently approved exact persistent serving configuration.");
    const result = await run("docker", [...compose(request), "config", "--format", "json"], { echo: false });
    let configuration;
    try { configuration = JSON.parse(result.stdout); }
    catch { reject("study_configuration_unverified", "The selected Compose configuration could not be read safely."); }
    if (result.code !== 0 || !isDeepStrictEqual(configuration, observation.configuration)
      || configuration.name !== request.registration.project
      || !isDeepStrictEqual(Object.keys(configuration.services ?? {}).sort(), ["learning", "postgres", "study-web"]))
      reject("study_configuration_unverified", "Current serving configuration must match the independently approved Study-only configuration without extra or bootstrap services.");
    for (const service of ["learning", "postgres", "study-web"])
      if (configuration.services[service].image !== observation.receipt.images[service])
        reject("study_configuration_unverified", "Every selected service must use the immutable image in the authoritative publication.");
    for (const [key, volume] of Object.entries(request.registration.volumes))
      if (configuration.volumes?.[key]?.name !== volume.name || configuration.volumes[key].external !== true)
        reject("study_configuration_unverified", "Serving volumes must retain the exact external persistent target identities.");
    if (["learning", "postgres"].some(service => configuration.services[service].ports?.length))
      reject("study_configuration_unverified", "Learning and PostgreSQL must not expose host ports.");
    const normalized = ports => ports.map(port => ({ service: port.service, host: port.host,
      port: String(port.port), target: Number(port.target) })).sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));
    const renderedPorts = normalized(portsFromConfig(configuration));
    const registeredPorts = normalized(request.registration.ports ?? []);
    if (!renderedPorts.length || renderedPorts.some(port => port.service !== "study-web" || port.host !== "127.0.0.1")
      || !isDeepStrictEqual(renderedPorts, registeredPorts))
      reject("study_configuration_unverified", "Study ingress must expose only the exact registered loopback ports.");
    return configuration;
  };
  const complete = async (request, observation, desiredState) => {
    const result = await verifyRuntimeOutcome(snapshot({ request, observation, desiredState }));
    if (result?.status !== "complete" || result.scope !== "observed" || !text(result.evidenceId)
      || !isDeepStrictEqual(result.binding, request))
      reject("study_runtime_outcome_unverified", "The command effect requires independent read-only verification on the exact current target; no legacy fallback or blind retry is permitted.");
    return snapshot(result);
  };
  const requireOutcome = () => {
    if (typeof verifyRuntimeOutcome !== "function")
      reject("study_runtime_outcome_contract_pending", "The installed public read-only runtime verification capability is missing; no service effect was started.");
  };
  const effect = async (request, args) => {
    const result = await run("docker", [...compose(request), ...args], { echo: true });
    if (result.code !== 0)
      reject("study_command_failed", "The selected Study service command failed. Preserve current-target evidence and inspect before retrying; no legacy fallback was attempted.");
  };
  const provider = {
    executionScope: "installed",
    async inspectRegisteredTarget(request) {
      if (typeof readAuthority !== "function")
        reject("production-admission-contract-pending", "The owning completed installed-authority reader is missing. Synthetic journals, quarantined receipts and disposable rehearsals do not supply it.");
      const observation = verifyObservation(await readAuthority(snapshot(request)), request);
      await checkConfiguration(request, observation);
      if (request.command === "logs") {
        const service = request.services[0];
        if (service && !["learning", "postgres", "study-web"].includes(service))
          reject("study_service_unknown", "Only registered Study services have a log route.");
        await run("docker", [...compose(request), "logs", "--no-color", "--tail", "100",
          ...(request.flags.includes("--follow") ? ["--follow"] : []), ...(service ? [service] : [])], { echo: true });
      }
      return observation;
    },
    async withTargetLock({ lockIdentity }, callback) {
      if (!text(state?.lockIdentity) || state.lockIdentity !== lockIdentity || typeof state.withLock !== "function")
        reject("study_lock_contract_pending", "Supply the original transition maintenance-lock capability; the fresh target key is not a replacement lock.");
      return state.withLock(callback);
    },
    async maintainAuthoritativeTarget(request, observation) {
      requireOutcome(); await checkConfiguration(request, observation);
      if (request.command === "migrate")
        reject("study_schema_update_contract_pending", "This publication pins an exact schema. A new schema/release needs its separately qualified public update contract; no migration or grants were started.");
      await effect(request, ["up", "-d", "--no-build", "--pull", "never", "--no-deps",
        ...(request.command === "restart" ? ["--force-recreate"] : []), "learning", "study-web"]);
      return complete(request, observation, "running");
    },
    async stopAuthoritativeTarget(request, observation) {
      requireOutcome(); await checkConfiguration(request, observation);
      await effect(request, ["stop", "study-web", "learning"]);
      return complete(request, observation, "stopped");
    },
  };
  if (typeof backupTarget === "function") provider.backupAuthoritativeTarget = async (request, observation) => {
    await checkConfiguration(request, observation);
    // Only this already-registered authority is supplied. No import, promotion,
    // provisioning or restoration capability is exposed by the launcher.
    return backupTarget(snapshot(request), snapshot(observation));
  };
  return Object.freeze(provider);
}
