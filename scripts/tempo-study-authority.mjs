// Read-only installed-authority observation. Admission production, serving
// configuration and runtime lineage remain singular public owner capabilities.
import { createHash } from "node:crypto";
import { isDeepStrictEqual } from "node:util";
import { targetKey } from "./tempo-deployment.mjs";
import { TempoProblem } from "./tempo-guidance.mjs";
import { readinessBinding } from "./tempo-cutover-readiness.mjs";

const text = value => typeof value === "string" && value.trim().length > 0;
const object = value => value !== null && typeof value === "object" && !Array.isArray(value);
const digest = value => createHash("sha256").update(JSON.stringify(value)).digest("hex");
function blocked(code, message) {
  throw new TempoProblem(code, message, { action: "Preserve both authorities and supply the exact owning installed evidence; no old-writer fallback or admission was performed." });
}
function snapshot(value) {
  const copy = structuredClone(value);
  const freeze = item => {
    if (item && typeof item === "object") { Object.values(item).forEach(freeze); Object.freeze(item); }
    return item;
  };
  return freeze(copy);
}
function volumeNames(registration) {
  if (!object(registration?.volumes) || !Object.keys(registration.volumes).length
    || Object.values(registration.volumes).some(volume => !text(volume?.name)))
    blocked("study_authority_target_incomplete", "Explicit original and fresh persistent volume identities are required.");
  return Object.values(registration.volumes).map(volume => volume.name).sort();
}

/** Current Docker metadata only. No container start/exec/update or DB request. */
export async function observeOriginalStudyFence(originalRegistration, { run } = {}) {
  if (typeof run !== "function" || !text(originalRegistration?.context)
    || !text(originalRegistration.daemonId) || !text(originalRegistration.project))
    blocked("study_source_observer_pending", "The explicit original target and read-only command executor are required.");
  const source = snapshot(originalRegistration);
  const docker = async args => {
    let result;
    try { result = await run("docker", ["--context", source.context, ...args], { echo: false }); }
    catch { blocked("study_source_observation_failed", "Current original-authority metadata could not be read."); }
    if (result?.code !== 0 || typeof result.stdout !== "string")
      blocked("study_source_observation_failed", "Current original-authority metadata could not be read.");
    return result.stdout;
  };
  if ((await docker(["info", "--format", "{{.ID}}"])).trim() !== source.daemonId)
    blocked("study_source_daemon_mismatch", "The original Docker context identifies another daemon.");
  const ids = (await docker(["ps", "--all", "--filter", `label=com.docker.compose.project=${source.project}`, "--format", "{{.ID}}"])).trim().split(/\s+/).filter(Boolean);
  if (new Set(ids).size !== ids.length || ids.some(id => !/^[a-f0-9]{12,64}$/.test(id)))
    blocked("study_source_observation_invalid", "Original project container identities are malformed or repeated.");
  const observed = [];
  if (ids.length) {
    let containers;
    try { containers = JSON.parse(await docker(["inspect", ...ids])); }
    catch (error) {
      if (error instanceof TempoProblem) throw error;
      blocked("study_source_observation_invalid", "Original project container inspection was not valid JSON.");
    }
    if (!Array.isArray(containers) || containers.length !== ids.length)
      blocked("study_source_observation_invalid", "Every original project container needs an inspection result.");
    const seen = new Set();
    for (const container of containers) {
      const matching = ids.find(id => typeof container?.Id === "string" && container.Id.startsWith(id));
      const labels = container?.Config?.Labels, state = container?.State;
      if (!matching || seen.has(matching) || labels?.["com.docker.compose.project"] !== source.project
        || !text(labels?.["com.docker.compose.service"]) || typeof state?.Running !== "boolean"
        || typeof state.Restarting !== "boolean")
        blocked("study_source_observation_invalid", "Original project inspection did not bind every requested container and state.");
      seen.add(matching);
      const service = labels["com.docker.compose.service"];
      const sourceDatabase = source.volumes?.[source.postgresVolumeKey]?.name;
      const retainedDatabase = service === "postgres" && text(sourceDatabase)
        && container.Mounts?.some(mount => mount.Type === "volume" && mount.Name === sourceDatabase);
      if (!retainedDatabase && (state.Running || state.Restarting
        || !["no", "unless-stopped"].includes(container.HostConfig?.RestartPolicy?.Name)))
        blocked("study_original_writer_unfenced", "An original application container can run or automatically reopen; installed Study authority is blocked.");
      observed.push({ id: container.Id, service, retainedDatabase, running: state.Running,
        restarting: state.Restarting, restartPolicy: container.HostConfig?.RestartPolicy?.Name ?? null });
    }
  }
  return snapshot({ scope: "observed", verified: true, sourceIdentity: targetKey(source),
    evidenceId: `sha256:${digest({ daemonId: source.daemonId, project: source.project, containers: observed })}`,
    originalWriterFenced: true });
}

/**
 * Uses the existing public readiness packet {plan,evidence,facts}. The owning
 * admission producer validates its completed observed fact through validateAdmission;
 * confirmation alone, a quarantine publication and synthetic journals cannot do so.
 * readPublication(request) returns the current parsed {registration,receipt}.
 * inspectConfiguration returns its owner's validated rendered configuration.
 * inspectRuntime returns a bound observed verdict with an honest cold/running state.
 */
export function createInstalledAuthorityReader({ originalRegistration, admission, readPublication,
  validateAdmission, inspectConfiguration, inspectRuntime, run } = {}) {
  const original = snapshot(originalRegistration ?? null), packet = snapshot(admission ?? null);
  return async requestInput => {
    const request = snapshot(requestInput), registration = request?.registration, plan = packet?.plan;
    if (typeof validateAdmission !== "function")
      blocked("production-admission-contract-pending", "The owning completed installed-admission producer/validator is missing.");
    if (!object(plan) || !object(original) || !object(registration) || original.version !== 1 || registration.version !== 2
      || !object(registration.installedStudy?.runtime) || !object(plan.receipt)
      || !object(original.volumes) || !object(registration.volumes)
      || Object.values(original.volumes).some(volume => !text(volume?.name))
      || Object.values(registration.volumes).some(volume => !text(volume?.name))
      || plan.version !== 1 || plan.freshStartChoice !== "graph-preserving-fresh-start"
      || !isDeepStrictEqual(plan.original?.registration, original)
      || !isDeepStrictEqual(plan.registration, registration)
      || plan.sourceIdentity !== targetKey(original) || plan.lockIdentity !== plan.sourceIdentity
      || plan.targetIdentity !== targetKey(registration) || plan.sourceIdentity === plan.targetIdentity
      || (original.daemonId === registration.daemonId && original.project === registration.project))
      blocked("study_admission_plan_mismatch", "The exact finalized plan must retain the original source/lock and this independently registered fresh authority.");
    const sourceVolumes = volumeNames(original), targetVolumes = volumeNames(registration);
    if (targetVolumes.some(name => sourceVolumes.includes(name)))
      blocked("study_authority_volume_reuse", "The fresh target cannot reuse any original registered volume.");
    let expectedBinding;
    try { expectedBinding = readinessBinding(plan, "before-admission", packet.evidence?.binding?.gateContext); }
    catch { blocked("study_admission_plan_mismatch", "The completed admission needs the existing canonical finalized-plan binding."); }
    const completed = await validateAdmission(snapshot(packet));
    if (completed?.scope !== "observed" || completed.verified !== true || completed.phase !== "complete"
      || !text(completed.evidenceId) || !isDeepStrictEqual(completed.binding, expectedBinding)
      || completed.evidenceId !== packet.facts?.admission?.evidenceId
      || completed.sourceIdentity !== plan.sourceIdentity || completed.lockIdentity !== plan.lockIdentity
      || completed.targetIdentity !== plan.targetIdentity || completed.transitionId !== plan.transitionId)
      blocked("study_admission_incomplete", "Only the owning observed completed-admission result can establish prior installed admission.");
    if (typeof readPublication !== "function" || typeof inspectConfiguration !== "function"
      || typeof inspectRuntime !== "function" || typeof run !== "function")
      blocked("study_installed_observers_pending", "The installed publication, configuration, runtime and read-only command observers are required.");
    const publication = await readPublication(snapshot(request));
    if (!isDeepStrictEqual(publication?.registration, registration)
      || !isDeepStrictEqual(publication?.receipt, plan.receipt))
      blocked("study_publication_mismatch", "Current registration and deployment receipt must equal the finalized approved public plan.");
    const daemon = await run("docker", ["--context", registration.context, "info", "--format", "{{.ID}}"], { echo: false });
    if (daemon?.code !== 0 || daemon.stdout?.trim() !== registration.daemonId)
      blocked("study_target_daemon_mismatch", "The fresh Docker context identifies another daemon.");
    const fence = await observeOriginalStudyFence(original, { run });
    const configuration = await inspectConfiguration(snapshot(request));
    if (!object(configuration)) blocked("study_configuration_unverified", "The serving-configuration owner did not return its validated configuration.");
    const runtime = await inspectRuntime(snapshot(request));
    if (runtime?.scope !== "observed" || runtime.status !== "verified" || !text(runtime.evidenceId)
      || !isDeepStrictEqual(runtime.binding, request)
      || !["cold", "running"].includes(runtime.state)
      || !isDeepStrictEqual(runtime.runtime, registration.installedStudy?.runtime))
      blocked("study_runtime_lineage_unverified", "The runtime owner must observe this exact pinned target and report its honest cold/running lineage.");
    // A cold status observation is metadata, not a live schema/health/control proof.
    // Under the original lock, prepareDatabase must establish physical cluster
    // identity before any cold PostgreSQL start; the runtime outcome owns health.
    const authority = { phase: "complete", sourceIdentity: plan.sourceIdentity, lockIdentity: plan.lockIdentity,
      targetIdentity: plan.targetIdentity, originalWriterFenced: true, evidenceId: completed.evidenceId };
    return snapshot({ status: "verified", scope: "observed", binding: request,
      evidenceId: `sha256:${digest({ admission: completed.evidenceId, fence: fence.evidenceId,
        runtime: runtime.evidenceId, configuration, publication })}`,
      receipt: publication.receipt, configuration, authority, runtime: runtime.runtime,
      runtimeInspection: runtime, sourceFence: fence });
  };
}
