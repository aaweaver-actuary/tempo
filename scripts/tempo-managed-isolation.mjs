// Read-only topology and recovery-state ownership for managed isolation.
import { basename, dirname, isAbsolute, join } from 'node:path';
import { closeSync, constants, fstatSync, fsyncSync, lstatSync, openSync, readFileSync } from 'node:fs';
import { isDeepStrictEqual } from 'node:util';
import { atomicJson, targetKey } from './tempo-deployment.mjs';

// Source review and an owned managed qualification are required before the
// nonempty path can be enabled. No CLI flag or environment value opens it.
export function requireManagedIsolationGate() {
  throw new Error('Nonempty Defense cleanup is unavailable without a verified writer and DDL fence.');
}

// Only the exact migration with a version-scoped comparator may cross this cut.
export function cut62WitnessName(guard) {
  const crossing = guard.starting_schema < 62 && guard.intended_schema >= 62;
  if (!crossing) return null;
  if (![59, 61].includes(guard.starting_schema) || guard.intended_schema !== 62 ||
      !/^[0-9a-f-]{36}$/i.test(guard.attempt_id ?? ''))
    throw new Error('Unsupported schema 62 crossing; preserve the original backup and fix forward.');
  return `cut62-${guard.attempt_id}.json`;
}

// These inspections are read-only: neither proves exclusive daemon control nor
// authorizes detachment or a nonempty migration by itself.
export function inspectDisposableIsolation(input) {
  if (!input?.target?.disposable || !/^tempo-pg-regressions-[a-zA-Z0-9-]+$/.test(input.target.project ?? ""))
    throw new Error("Disposable database isolation identity is unsafe or changed.");
  return inspectIsolation(input);
}

export function inspectRegisteredIsolation(input) {
  const target = input?.target;
  if (target?.version !== 1 || !isAbsolute(target.root ?? "") || typeof target.context !== "string" || !target.context ||
      !/^[a-z0-9][a-z0-9_-]*$/.test(target.project ?? "") ||
      (target.disposable !== undefined && typeof target.disposable !== "boolean"))
    throw new Error("Registered database isolation identity is unsafe or changed.");
  return inspectIsolation(input);
}

function inspectIsolation({ target, daemonId, container, network, expectedImage, trustedDaemon, intent = null }) {
  const fail = () => { throw new Error("Database isolation identity is unsafe or changed."); };
  const labels = container?.Config?.Labels ?? {}, host = container?.HostConfig ?? {}, settings = container?.NetworkSettings;
  const object = value => value !== null && typeof value === "object" && !Array.isArray(value);
  const id = value => typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
  const emptyObject = value => value == null || (object(value) && Object.keys(value).length === 0);
  const emptyArray = value => value == null || (Array.isArray(value) && value.length === 0);
  const ports = settings?.Ports, attached = settings?.Networks;
  const projectNetwork = network?.Labels?.["com.docker.compose.project"];
  const mounts = Array.isArray(container?.Mounts) ? container.Mounts : [];
  const destination = "/var/lib/postgresql";
  const overlap = path => path === destination || destination.startsWith(path + "/") || path.startsWith(destination + "/") || path === "/";
  const storage = mounts.filter(m => typeof m?.Destination === "string" && overlap(m.Destination));
  if (trustedDaemon !== true || !target ||
      !target.daemonId || daemonId !== target.daemonId || labels["com.docker.compose.project"] !== target.project ||
      labels["com.docker.compose.service"] !== "postgres" || container?.State?.Running !== true || !id(container.Id) ||
      !/^sha256:[0-9a-f]{64}$/.test(expectedImage ?? "") || container.Image !== expectedImage ||
      typeof host.PublishAllPorts !== "boolean" || host.PublishAllPorts || !Object.hasOwn(host, "PortBindings") ||
      !(host.PortBindings === null || object(host.PortBindings)) || Object.keys(host.PortBindings ?? {}).length ||
      !settings || !Object.hasOwn(settings, "Ports") || !(ports === null || object(ports)) ||
      Object.values(ports ?? {}).some(value => value !== null && (!Array.isArray(value) || value.length !== 0)) ||
      !object(attached) || !id(network?.Id) || typeof network.Name !== "string" || !network.Name ||
      host.NetworkMode !== network.Name || network.Driver !== "bridge" || network.Scope !== "local" ||
      projectNetwork !== target.project || !object(network.Containers) ||
      !target.volumes?.[target.postgresVolumeKey]?.name ||
      mounts.some(m => !object(m) || typeof m.Destination !== "string" || !m.Destination.startsWith("/") ||
        m.Destination.includes("//") || (m.Destination !== "/" && m.Destination.endsWith("/")) ||
        m.Destination.split("/").some(part => part === "." || part === "..")) || storage.length !== 1 ||
      storage[0].Type !== "volume" || storage[0].Destination !== destination || storage[0].RW !== true ||
      storage[0].Name !== target.volumes[target.postgresVolumeKey].name ||
      typeof storage[0].Source !== "string" || !isAbsolute(storage[0].Source)) fail();
  const entries = Object.entries(attached);
  if (entries.length > 1) fail();
  const identityKeys = ["aliases", "containerId", "daemonId", "imageId", "networkId", "networkName", "project", "target", "trustedDaemon", "volumeName", "volumeSource"].sort().join();
  const canonicalIntent = value => object(value) && Object.keys(value).sort().join() === identityKeys &&
    Array.isArray(value.aliases) && value.aliases.includes("postgres") && value.aliases.every(alias => typeof alias === "string" && alias) &&
    new Set(value.aliases).size === value.aliases.length && isDeepStrictEqual(value.aliases, [...value.aliases].sort());
  let original;
  if (entries.length === 1) {
    const [name, endpoint] = entries[0];
    const aliases = endpoint?.Aliases, reciprocal = network.Containers[container.Id];
    if (name !== network.Name || endpoint.NetworkID !== network.Id || !id(endpoint.EndpointID) ||
        reciprocal?.EndpointID !== endpoint.EndpointID || !Array.isArray(aliases) || !aliases.includes("postgres") ||
        aliases.some(alias => typeof alias !== "string" || !alias) || new Set(aliases).size !== aliases.length ||
        !emptyObject(endpoint.IPAMConfig) || !emptyObject(endpoint.DriverOpts) ||
        !emptyArray(endpoint.Links) || (endpoint.GwPriority ?? 0) !== 0) fail();
    original = { target: targetKey(target), project: target.project, daemonId, trustedDaemon: true, containerId: container.Id,
      imageId: expectedImage, networkName: name, networkId: network.Id, aliases: [...aliases].sort(),
      volumeName: storage[0].Name, volumeSource: storage[0].Source };
    if (intent && (!canonicalIntent(intent) || !isDeepStrictEqual(original, intent))) fail();
  } else {
    if (network.Containers[container.Id] || !canonicalIntent(intent) || intent.trustedDaemon !== true ||
        intent.target !== targetKey(target) || intent.project !== target.project || intent.daemonId !== daemonId ||
        intent.containerId !== container.Id || intent.imageId !== expectedImage || intent.networkId !== network.Id ||
        intent.networkName !== network.Name || intent.volumeName !== storage[0].Name || intent.volumeSource !== storage[0].Source) fail();
    original = intent;
  }
  return { attached: entries.length === 1, original };
}

// Classify physical state only after Docker identity, backend facts, original
// witnesses and any durable receipt have been independently authenticated.
// This is not an authority check and does not mutate admission or data.
export function classifyManagedIsolationRecovery({ guard, identity, attached, targetOid, allowConnections,
  backendPids, preparedTransactions, systemIdentifier, controlOid, controlName, adminRole, targetName,
  pinnedPid = null, schema = null, receipt = null, receiptValidated = false }) {
  const fail = () => { throw new Error("Isolation recovery state is unsafe or ambiguous."); };
  const isolation = guard?.isolation;
  const binding = guard?.defense_binding;
  const cut = guard?.cut62_identity;
  const digest = value => typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
  let expectedCutName;
  try { expectedCutName = cut62WitnessName(guard); } catch { fail(); }
  if (guard?.version !== 1 || guard.state !== "pending" || guard.cut62_witness !== expectedCutName || !isolation || !["intent", "detached", "verified", "reattached"].includes(isolation.phase) ||
      !isDeepStrictEqual(isolation.identity, identity) || identity?.trustedDaemon !== true || guard.target !== identity?.target ||
      ![59, 61].includes(guard.starting_schema) || guard.intended_schema !== 62 ||
      !Number.isSafeInteger(targetOid) || targetOid <= 0 || isolation.target_oid !== targetOid ||
      typeof systemIdentifier !== "string" || !/^[0-9]+$/.test(systemIdentifier) || isolation.system_identifier !== systemIdentifier ||
      !Number.isSafeInteger(controlOid) || controlOid <= 0 || controlOid === targetOid || isolation.control_oid !== controlOid ||
      controlName !== "postgres" || isolation.control_name !== controlName ||
      typeof adminRole !== "string" || !/^[a-z_][a-z0-9_]*$/.test(adminRole) || isolation.admin_role !== adminRole ||
      typeof targetName !== "string" || !targetName || isolation.target_name !== targetName || guard.database?.name !== targetName ||
      isolation.original_allow_connections !== true || typeof attached !== "boolean" || typeof allowConnections !== "boolean" ||
      !Array.isArray(backendPids) || backendPids.some(pid => !Number.isSafeInteger(pid) || pid <= 0) ||
      (pinnedPid === null ? backendPids.length !== 0 :
        !Number.isSafeInteger(pinnedPid) || pinnedPid <= 0 || backendPids.length !== 1 || backendPids[0] !== pinnedPid) ||
      !Number.isSafeInteger(preparedTransactions) || preparedTransactions !== 0 ||
      (schema !== null && ![61, 62].includes(schema)) || !guard.backup?.verified ||
      binding?.version !== 1 || binding.target !== guard.target || binding.attempt_id !== guard.attempt_id ||
      binding.backup_filename !== guard.backup.filename || !digest(binding.backup_sha256) || !digest(binding.selection_sha256) ||
      cut?.attempt_id !== guard.attempt_id || cut.backup_filename !== guard.backup.filename || !digest(cut.witness_id) ||
      (guard.starting_schema === 59 && (guard.pre62_witness !== `pre62-${guard.attempt_id}.json` || !digest(guard.pre62_witness_id) ||
        guard.cut62_intermediate_verified?.schema !== 61 ||
        guard.cut62_intermediate_verified.attempt_id !== guard.attempt_id ||
        guard.cut62_intermediate_verified.pre62_witness !== guard.pre62_witness ||
        guard.cut62_intermediate_verified.pre62_witness_id !== guard.pre62_witness_id ||
        guard.cut62_intermediate_verified.backup_filename !== guard.backup.filename))) fail();
  const expectedReceipt = { version: 1, target: guard.target, attempt_id: guard.attempt_id, target_oid: targetOid,
    target_name: targetName, system_identifier: systemIdentifier, control_oid: controlOid, control_name: controlName, admin_role: adminRole,
    daemon_id: identity.daemonId, container_id: identity.containerId, network_id: identity.networkId, image_id: identity.imageId,
    backup_filename: binding.backup_filename, backup_sha256: binding.backup_sha256, selection_sha256: binding.selection_sha256,
    pre62_witness_id: guard.starting_schema === 59 ? guard.pre62_witness_id : null, cut62_witness_id: cut.witness_id, schema: 62 };
  if (receipt && (receiptValidated !== true || !isDeepStrictEqual(receipt, expectedReceipt))) fail();
  if (receipt) {
    if ((schema !== null && schema !== 62) || (pinnedPid !== null && schema !== 62)) fail();
    if (isolation.phase === "intent") fail();
    if (isolation.phase === "detached") {
      if (attached || allowConnections) fail();
      return "record-verified";
    }
    if (isolation.phase === "verified") {
      if (!attached && pinnedPid !== null) {
        if (allowConnections) fail();
        return "close-pinned-after-receipt";
      }
      if (!attached) return "restore-after-receipt";
      if (!allowConnections || pinnedPid !== null) fail();
      return "record-reattached";
    }
    if (isolation.phase === "reattached") {
      if (!attached || !allowConnections || pinnedPid !== null) fail();
      return "complete";
    }
    fail();
  }
  if (isolation.phase === "verified" || isolation.phase === "reattached") fail();
  if (isolation.phase === "intent") {
    if (pinnedPid !== null || schema !== null) fail();
    if (attached) {
      if (!allowConnections) fail();
      return "detach";
    }
    return "record-detached";
  }
  if (attached) fail();
  if (pinnedPid !== null) {
    if (allowConnections || schema === null) fail();
    return schema === 61 ? "verify-and-migrate" : "verify-committed";
  }
  if (schema !== null) fail();
  return allowConnections ? "pin-and-reclose" : "reopen-isolated-and-pin";
}

// Compatibility for the existing disposable proof; both callers use one
// classifier and the alias itself grants no mutation authority.
export const classifyDisposableIsolationRecovery = classifyManagedIsolationRecovery;


export function readOwnedPrivateBytes(path) {
  if (!isAbsolute(path ?? "")) throw new Error("Approved Defense selection is required as an absolute private file.");
  let parent;
  try { parent = lstatSync(dirname(path)); } catch { throw new Error("Approved Defense selection is unavailable."); }
  if (!parent.isDirectory() || parent.uid !== process.getuid() || (parent.mode & 0o777) !== 0o700)
    throw new Error("Approved Defense selection parent must be owned and private.");
  let fd;
  try { fd = openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW); } catch { throw new Error("Approved Defense selection is unavailable."); }
  let bytes;
  try {
    const info = fstatSync(fd);
    if (!info.isFile() || info.uid !== process.getuid() || (info.mode & 0o777) !== 0o600 || info.size > 1048576)
      throw new Error("Approved Defense selection must be an owned private regular file.");
    bytes = readFileSync(fd);
  } finally { closeSync(fd); }
  return bytes;
}

// A lock token is evidence only while the original CLI process continuously
// holds the registered target lock. This check does not acquire that lock.
export function captureManagedTargetLock(stateDirectory) {
  if (!isAbsolute(stateDirectory ?? '')) throw new Error('Managed isolation lock path is invalid.');
  let lock;
  try { lock = JSON.parse(readOwnedPrivateBytes(join(stateDirectory, 'maintenance.lock'))); }
  catch { throw new Error('Managed isolation target lock is unavailable.'); }
  if (Object.keys(lock ?? {}).sort().join() !== 'pid,token' || lock.pid !== process.pid ||
      typeof lock.token !== 'string' || !/^[0-9a-f-]{36}$/i.test(lock.token))
    throw new Error('Managed isolation target lock is not owned by this process.');
  return Object.freeze({ pid: lock.pid, token: lock.token });
}

export function assertManagedTargetLock(stateDirectory, original) {
  const current = captureManagedTargetLock(stateDirectory);
  if (!isDeepStrictEqual(current, original)) throw new Error('Managed isolation target lock changed; keep writers stopped.');
}

export function syncDirectory(path) {
  const fd = openSync(path, "r");
  try { fsyncSync(fd); } finally { closeSync(fd); }
}

// Persist only evidence-backed phase markers; Docker and database actions are
// deliberately separate, so an interrupted action is reconciled by inspection.
function requireCurrentIsolationGuard(guardPath, expectedGuard) {
  if (!isAbsolute(guardPath) || basename(guardPath) !== "migration-guard.json")
    throw new Error("Isolation guard path is invalid.");
  let actual;
  try { actual = JSON.parse(readOwnedPrivateBytes(guardPath)); } catch { throw new Error("Original migration guard is unavailable or invalid."); }
  if (!isDeepStrictEqual(actual, expectedGuard)) throw new Error("Original migration guard changed; keep writers stopped.");
}
// Caller must continuously hold the registered target lock. A prior atomic
// rename may have succeeded before its directory fsync failed; retries sync
// the accepted private files and their parents before any physical action.
export function syncManagedIsolationEvidence({ guardPath, guard, receiptPath = null, receipt = null }) {
  if ((receiptPath === null) !== (receipt === null)) throw new Error("Isolation receipt evidence is incomplete.");
  const entries = [[guardPath, guard], ...(receiptPath === null ? [] : [[receiptPath, receipt]])];
  for (const [path, expected] of entries) {
    if (!isAbsolute(path)) throw new Error("Isolation evidence path is invalid.");
    let fd;
    try {
      const parent = lstatSync(dirname(path));
      if (!parent.isDirectory() || parent.uid !== process.getuid() || (parent.mode & 0o777) !== 0o700) throw new Error();
      fd = openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW);
      const info = fstatSync(fd);
      if (!info.isFile() || info.uid !== process.getuid() || (info.mode & 0o777) !== 0o600 || info.size > 1048576) throw new Error();
      if (!isDeepStrictEqual(JSON.parse(readFileSync(fd)), expected)) throw new Error();
      fsyncSync(fd);
    } catch { throw new Error("Isolation evidence is unavailable, changed or not durable; keep writers stopped."); }
    finally { if (fd !== undefined) closeSync(fd); }
    try { syncDirectory(dirname(path)); } catch { throw new Error("Isolation evidence directory is not durable; keep writers stopped."); }
    let reread;
    try { reread = JSON.parse(readOwnedPrivateBytes(path)); } catch { throw new Error("Isolation evidence is unavailable or changed; keep writers stopped."); }
    if (!isDeepStrictEqual(reread, expected)) throw new Error("Isolation evidence changed; keep writers stopped.");
  }
}

export function persistManagedIsolationIntent({ guardPath, guard, identity, targetOid, systemIdentifier,
  controlOid, controlName, adminRole, targetName, attached, allowConnections, backendPids, preparedTransactions }) {
  requireCurrentIsolationGuard(guardPath, guard);
  if (guard.isolation) throw new Error("Original migration already has an isolation intent.");
  const next = structuredClone(guard);
  next.isolation = { phase: "intent", identity: structuredClone(identity), target_oid: targetOid, target_name: targetName,
    system_identifier: systemIdentifier, control_oid: controlOid, control_name: controlName, admin_role: adminRole,
    original_allow_connections: true };
  if (classifyManagedIsolationRecovery({ guard: next, identity, targetOid, systemIdentifier, controlOid, controlName,
    adminRole, targetName, attached, allowConnections, backendPids, preparedTransactions }) !== "detach")
    throw new Error("Isolation cannot begin from this state.");
  atomicJson(guardPath, next);
  return next;
}
export function advanceManagedIsolationMarker({ guardPath, guard, observation }) {
  requireCurrentIsolationGuard(guardPath, guard);
  const action = classifyManagedIsolationRecovery({ ...observation, guard });
  const phase = { "record-detached": "detached", "record-verified": "verified", "record-reattached": "reattached" }[action];
  if (!phase) throw new Error("No durable isolation marker is valid for this state.");
  const next = structuredClone(guard);
  next.isolation.phase = phase;
  atomicJson(guardPath, next);
  return next;
}

export const syncDisposableIsolationEvidence = syncManagedIsolationEvidence;
export const persistDisposableIsolationIntent = persistManagedIsolationIntent;
export const advanceDisposableIsolationMarker = advanceManagedIsolationMarker;

