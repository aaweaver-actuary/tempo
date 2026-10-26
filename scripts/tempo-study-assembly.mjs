// Composition root for the installed Study command path. All identifiers and
// private input paths are selected in the original pinned registration.
import { lstatSync, readFileSync, realpathSync } from "node:fs";
import { isAbsolute, relative, resolve, sep } from "node:path";
import { isDeepStrictEqual } from "node:util";
import { acquireTargetLock, commandExecutor } from "./tempo-deployment.mjs";
import { TempoProblem } from "./tempo-guidance.mjs";
import { createInstalledStudyLauncher } from "./tempo-study-launcher.mjs";

const text = value => typeof value === "string" && value.trim().length > 0;
const object = value => value !== null && typeof value === "object" && !Array.isArray(value);
const fields = ["version", "originalRegistrationPath", "stateDirectory", "lockIdentity", "receiptPath",
  "admissionPath", "persistentIntentPath", "persistentReceiptPath", "runtime"];
const fileFields = ["originalRegistrationPath", "receiptPath", "admissionPath", "persistentIntentPath", "persistentReceiptPath"];
const blocked = (code, message) => { throw new TempoProblem(code, message, {
  action: "Next: supply the exact private installed Study inputs and independently verified admission for this registration.",
}); };
function immutable(value) {
  const result = structuredClone(value), seen = new Set();
  const freeze = entry => {
    if (entry !== null && typeof entry === "object" && !seen.has(entry)) {
      seen.add(entry); Object.values(entry).forEach(freeze); Object.freeze(entry);
    }
    return entry;
  };
  return freeze(result);
}
const inside = (path, directory) => {
  const selected = relative(directory, path);
  return selected === "" || (selected !== ".." && !selected.startsWith(`..${sep}`) && !isAbsolute(selected));
};

export function installedStudySettings(registration, configPath) {
  const input = registration?.installedStudy;
  if (!object(input) || input.version !== 1 || !isDeepStrictEqual(Object.keys(input).sort(), [...fields].sort())
    || !text(input.lockIdentity) || !object(input.runtime) || input.runtime.version !== 1
    || !isAbsolute(configPath ?? "") || !isAbsolute(registration.root ?? "")
    || ![...fileFields, "stateDirectory"].every(name => text(input[name]) && isAbsolute(input[name]))
    || new Set([configPath, ...fileFields.map(name => input[name])].map(path => resolve(path))).size !== fileFields.length + 1)
    blocked("study_installation_inputs_pending", "The pinned v2 registration needs explicit installedStudy v1 paths, original lock identity and runtime descriptor.");
  return immutable(input);
}

function privatePath(path, root, directory = false) {
  let status, selected, checkout;
  try { status = lstatSync(path); selected = realpathSync(path); checkout = realpathSync(root); }
  catch { blocked("study_installation_input_missing", "An explicitly selected private Study installation input is unavailable."); }
  if (status.isSymbolicLink() || (directory ? !status.isDirectory() : !status.isFile())
    || (status.mode & 0o077) !== 0 || (typeof process.getuid === "function" && status.uid !== process.getuid())
    || inside(selected, checkout))
    blocked("study_installation_input_unsafe", "Installed Study inputs must be private, owned, direct files/directories outside the source checkout.");
  return selected;
}

function readPrivateJson(path, root) {
  const selected = privatePath(path, root);
  try { return JSON.parse(readFileSync(selected, "utf8")); }
  catch { blocked("study_installation_input_invalid", "An explicitly selected private Study installation input is not valid JSON."); }
}

async function installedComponents() {
  try {
    const [authority, configuration, runtime, admission] = await Promise.all([
      import("./tempo-study-authority.mjs"), import("./tempo-study-configuration.mjs"),
      import("./tempo-study-runtime.mjs"), import("./tempo-cutover-transition.mjs"),
    ]);
    return { createAuthorityReader: authority.createInstalledAuthorityReader,
      inspectConfiguration: configuration.readStudyServingConfiguration,
      createRuntime: runtime.createInstalledStudyRuntime,
      validateAdmission: admission.verifyInstalledAdmissionPacket };
  } catch {
    blocked("study_provider_unavailable", "An installed Study public component is unavailable; the registered Study path cannot continue.");
  }
}

function registeredEnvironment(environment) {
  if (["DOCKER_HOST", "DOCKER_CONTEXT", "COMPOSE_FILE", "COMPOSE_PROJECT_NAME", "COMPOSE_PROFILES", "COMPOSE_ENV_FILES"]
    .some(name => text(environment[name])))
    blocked("study_environment_override", "Docker/Compose environment overrides conflict with the explicit installed Study target.");
  return environment;
}

function registeredExecutor(request, output, environment) {
  const secrets = Object.entries(environment).filter(([name]) => /TOKEN|PASSWORD|SECRET/i.test(name)).map(([, value]) => value);
  const execute = commandExecutor({ root: request.registration.root, environment: { ...environment }, output, secretValues: secrets });
  return (command, args, options = {}) => execute(command, args, { ...options,
    echo: Boolean(options.echo && (request.flags.includes("--verbose") || request.command === "logs")) });
}

/**
 * The default CLI selects this fixed composition root, never a config/env module.
 * dependencies are semantic test/embedding capabilities, not installation data.
 * Installation data is reread on every inspection, including the inspection
 * under the retained original lock. This root never executes one-time cutover.
 */
export function createDefaultStudyLauncher({ registration, configPath, log = console.log } = {}, dependencies = {}) {
  const selectedRegistration = immutable(registration);
  let current;
  const context = async request => {
    if (!isDeepStrictEqual(request.registration, selectedRegistration) || request.configPath !== configPath)
      blocked("study_installation_selection_changed", "Study assembly was asked to use a different selected registration or configuration path.");
    const settings = installedStudySettings(request.registration, request.configPath);
    const components = dependencies.components ?? await installedComponents();
    if (!["createAuthorityReader", "inspectConfiguration", "createRuntime", "validateAdmission"].every(name => typeof components[name] === "function"))
      blocked("study_provider_unavailable", "A required installed Study public capability is missing.");
    const read = dependencies.readPrivateJson ?? (path => readPrivateJson(path, request.registration.root));
    const records = Object.fromEntries(fileFields.map(name => [name, read(settings[name])]));
    if (!dependencies.readPrivateJson) {
      const selectedPaths = [request.configPath, ...fileFields.map(name => settings[name])]
        .map(path => privatePath(path, request.registration.root));
      const fileIdentities = selectedPaths.map(path => { const status = lstatSync(path); return `${status.dev}:${status.ino}`; });
      if (new Set(selectedPaths).size !== selectedPaths.length || new Set(fileIdentities).size !== fileIdentities.length)
        blocked("study_installation_input_unsafe", "Selected private Study inputs resolve to overlapping files.");
    }
    const environment = registeredEnvironment(dependencies.environment ?? (dependencies.run ? {} : process.env));
    const run = dependencies.run ?? registeredExecutor(request, log, environment);
    const inspectConfiguration = inspected => components.inspectConfiguration(inspected, { run,
      persistentIntent: immutable(records.persistentIntentPath), persistentReceipt: immutable(records.persistentReceiptPath) });
    const runtime = components.createRuntime({ run, inspectConfiguration,
      persistentReceipt: immutable(records.persistentReceiptPath),
      ...(dependencies.fetcher ? { fetcher: dependencies.fetcher } : {}) });
    if (!["inspectRuntime", "prepareDatabase", "verifyRuntimeOutcome"].every(name => typeof runtime?.[name] === "function"))
      blocked("study_runtime_contract_pending", "The installed runtime inspection, database preparation and outcome capabilities are required before service effects.");
    const readAuthority = components.createAuthorityReader({ run,
      originalRegistration: immutable(records.originalRegistrationPath), admission: immutable(records.admissionPath),
      validateAdmission: components.validateAdmission, inspectConfiguration,
      inspectRuntime: (...args) => runtime.inspectRuntime(...args),
      readPublication: inspected => ({ registration: read(inspected.configPath), receipt: read(settings.receiptPath) }) });
    if (typeof readAuthority !== "function") blocked("study_authority_contract_pending", "The installed authority component did not provide its public reader.");
    const state = { lockIdentity: settings.lockIdentity, async withLock(callback) {
      if (dependencies.withLock) return dependencies.withLock(immutable({ stateDirectory: settings.stateDirectory, lockIdentity: settings.lockIdentity }), callback);
      const directory = privatePath(settings.stateDirectory, request.registration.root, true);
      const release = acquireTargetLock(directory);
      try { return await callback(); } finally { release(); }
    } };
    const provider = createInstalledStudyLauncher({ readAuthority, state, run,
      verifyRuntimeOutcome: input => runtime.verifyRuntimeOutcome(input) });
    return { request: immutable(request), provider, runtime, observation: null };
  };
  const selected = request => {
    if (!current?.observation || !isDeepStrictEqual(current.request, request))
      blocked("study_installation_not_inspected", "The exact installed Study command must be inspected before its lifecycle capability is used.");
    return current;
  };
  return Object.freeze({
    executionScope: "installed",
    async inspectRegisteredTarget(request) {
      current = await context(request);
      current.observation = await current.provider.inspectRegisteredTarget(request);
      if (["status", "doctor"].includes(request.command)) {
        const inspected = current.observation.runtimeInspection;
        if (inspected?.status === "verified" && inspected.scope === "observed"
          && isDeepStrictEqual(inspected.binding, request) && ["cold", "running"].includes(inspected.state)) {
          const state = name => inspected.services?.[name]?.running === true ? "running"
            : inspected.services?.[name]?.running === false ? "stopped" : "unknown";
          log(`Study runtime: ${inspected.state}; PostgreSQL ${state("postgres")}; Learning ${state("learning")}; web ${state("study-web")}; schema ${inspected.schemaVerified === true ? "verified" : "unverified"}; health ${inspected.healthVerified === true ? "verified" : "unverified"}.`);
        } else log("Current Study runtime state, schema and health are unverified.");
      }
      return current.observation;
    },
    async withTargetLock(identity, callback) {
      if (!current?.observation) blocked("study_installation_not_inspected", "Study authority must be inspected before acquiring its retained lock.");
      return current.provider.withTargetLock(identity, callback);
    },
    async maintainAuthoritativeTarget(request, observation) {
      const selectedContext = selected(request);
      if (!isDeepStrictEqual(observation, selectedContext.observation))
        blocked("study_installation_observation_changed", "The selected Study observation changed before the command effect.");
      if (["start", "restart"].includes(request.command)) {
        const prepared = await selectedContext.runtime.prepareDatabase(request, observation);
        if (prepared?.status !== "verified" || prepared.scope !== "observed" || prepared.verified !== true
          || !text(prepared.evidenceId) || !isDeepStrictEqual(prepared.binding, request)
          || prepared.clusterIdentityVerified !== true || prepared.schemaVerified !== true)
          blocked("study_database_preparation_unverified", "Registered PostgreSQL preparation did not prove the exact existing cluster and live schema before application start.");
      }
      return selectedContext.provider.maintainAuthoritativeTarget(request, observation);
    },
    async stopAuthoritativeTarget(request, observation) {
      const selectedContext = selected(request);
      if (!isDeepStrictEqual(observation, selectedContext.observation))
        blocked("study_installation_observation_changed", "The selected Study observation changed before stopping services.");
      return selectedContext.provider.stopAuthoritativeTarget(request, observation);
    },
  });
}
