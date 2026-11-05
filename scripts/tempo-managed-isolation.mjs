// Read-only topology and recovery-state ownership for managed isolation.
import { basename, dirname, isAbsolute, join } from 'node:path';
import { closeSync, constants, existsSync, fstatSync, fsyncSync, lstatSync, openSync, readFileSync, unlinkSync } from 'node:fs';
import { isDeepStrictEqual } from 'node:util';
import { atomicJson, targetKey, validateContainers, validateTarget } from './tempo-deployment.mjs';
import { createHash, createHmac, randomBytes, timingSafeEqual } from 'node:crypto';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';

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

function inspectIsolation({ target, daemonId, container, network, expectedImage, trustedDaemon, intent = null, allowStopped = false }) {
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
      labels["com.docker.compose.service"] !== "postgres" ||
      !(container?.State?.Running === true || (allowStopped === true && container?.State?.Running === false && container?.State?.Restarting === false)) || !id(container.Id) ||
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

// Capture is performed by the packaged owner. Its returned ID is recorded
// once against the existing attempt; retries may only resync and compare it.
export function persistManagedHistoryWitnessIdentity({ stateDirectory, lock, guard, witnessId, newCapture = false }) {
  assertManagedTargetLock(stateDirectory, lock);
  const guardPath = join(stateDirectory, 'migration-guard.json');
  requireCurrentIsolationGuard(guardPath, guard);
  const binding = guard?.defense_binding;
  if (guard?.state !== 'pending' || !guard.cut62_capture_started ||
      guard.cut62_witness !== cut62WitnessName(guard) || !guard.backup?.verified ||
      binding?.version !== 1 || binding.target !== guard.target || binding.attempt_id !== guard.attempt_id ||
      binding.backup_filename !== guard.backup.filename || !/^[0-9a-f]{64}$/.test(binding.backup_sha256 ?? '') ||
      !/^[0-9a-f]{64}$/.test(binding.selection_sha256 ?? '') || !/^[0-9a-f]{64}$/.test(witnessId ?? ''))
    throw new Error('Original managed history witness binding is invalid; keep writers stopped.');
  const expected = { witness_id: witnessId, attempt_id: guard.attempt_id, backup_filename: guard.backup.filename };
  if (guard.cut62_identity && !isDeepStrictEqual(guard.cut62_identity, expected))
    throw new Error('Original managed history witness identity differs; keep writers stopped.');
  if (!guard.cut62_identity) {
    if (newCapture !== true) throw new Error('Original managed history witness identity is missing; keep writers stopped.');
    const next = structuredClone(guard);
    next.cut62_identity = expected;
    assertManagedTargetLock(stateDirectory, lock);
    requireCurrentIsolationGuard(guardPath, guard);
    atomicJson(guardPath, next);
    guard = next;
  }
  syncManagedIsolationEvidence({ guardPath, guard });
  assertManagedTargetLock(stateDirectory, lock);
  return guard;
}

// The owner authenticates the persisted witness and supplies its stable ID;
// schema 61 checks the original source and schema 62 checks the projection.
export function managedHistoryRetryCheck({ guard, schema, witnessId }) {
  const identity = guard?.cut62_identity;
  if (guard?.state !== 'pending' || ![61, 62].includes(schema) ||
      !/^[0-9a-f]{64}$/.test(witnessId ?? '') || identity?.witness_id !== witnessId ||
      identity.attempt_id !== guard.attempt_id || identity.backup_filename !== guard.backup?.filename ||
      guard.defense_binding?.attempt_id !== guard.attempt_id ||
      guard.defense_binding?.backup_filename !== guard.backup?.filename)
    throw new Error('Original managed history witness or schema differs; keep writers stopped.');
  return schema === 61 ? 'check-source' : 'check-projection';
}

// Read only. The caller must hold the original target lock and explicitly
// assume exclusive Docker/host-administrator control for the entire cut.
export async function inspectManagedFence({ target, configuration, expectedImage, guard, stateDirectory, lock, docker, exclusiveAdministrator }) {
  if (exclusiveAdministrator !== true || typeof docker !== 'function')
    throw new Error('Managed isolation requires exclusive administrator control.');
  assertManagedTargetLock(stateDirectory, lock);
  validateTarget(configuration, target);
  if (!(guard?.state === 'pending' || (guard?.state === 'verified' && guard.verified_schema === 62 && guard.isolation?.phase === 'reattached')) ||
      guard.target !== targetKey(target) || guard.database?.name !== 'tempo' ||
      guard.database?.volume !== target.volumes?.[target.postgresVolumeKey]?.name)
    throw new Error('Original managed isolation target differs.');
  const admin = configuration.services.postgres.environment.POSTGRES_USER ?? 'postgres';
  if (!/^[a-z_][a-z0-9_]*$/.test(admin) || configuration.services.postgres.environment.POSTGRES_DB !== 'tempo')
    throw new Error('Managed isolation database credentials are invalid.');
  const daemonId = (await docker(['info', '--format', '{{.ID}}'])).stdout.trim();
  assertManagedTargetLock(stateDirectory, lock);
  const ids = (await docker(['ps', '-aq'])).stdout.trim().split(/\s+/).filter(Boolean);
  if (!ids.length) throw new Error('Managed isolation container inventory is unavailable.');
  const containers = JSON.parse((await docker(['inspect', ...ids])).stdout);
  if (!Array.isArray(containers) || containers.length !== ids.length || ids.some(id => !containers.some(item => item.Id?.startsWith(id))))
    throw new Error('Managed isolation container inventory is incomplete.');
  validateContainers(containers, target);
  const project = containers.filter(item => item.Config?.Labels?.['com.docker.compose.project'] === target.project);
  for (const item of project) if ((item.State?.Running || item.State?.Restarting) &&
      !['postgres', 'redis'].includes(item.Config?.Labels?.['com.docker.compose.service']))
    throw new Error('Managed isolation has an active application or maintenance container.');
  const databases = project.filter(item => item.Config?.Labels?.['com.docker.compose.service'] === 'postgres');
  if (databases.length !== 1) throw new Error('Managed isolation database container is ambiguous.');
  const container = databases[0], intent = guard?.isolation?.identity ?? null;
  const attached = Object.values(container.NetworkSettings?.Networks ?? {});
  const networkId = intent?.networkId ?? (attached.length === 1 ? attached[0]?.NetworkID : null);
  if (!/^[0-9a-f]{64}$/.test(networkId ?? '')) throw new Error('Managed isolation network identity is invalid.');
  const networks = JSON.parse((await docker(['network', 'inspect', networkId])).stdout);
  if (!Array.isArray(networks) || networks.length !== 1) throw new Error('Managed isolation network inventory is invalid.');
  const network = networks[0];
  const topology = inspectRegisteredIsolation({ target, daemonId, container, network, expectedImage,
    trustedDaemon: exclusiveAdministrator, intent });
  for (const peer of Object.keys(network.Containers ?? {})) {
    const owner = containers.find(item => item.Id === peer);
    if (!owner || owner.Config?.Labels?.['com.docker.compose.project'] !== target.project)
      throw new Error('Managed isolation network contains an unregistered peer.');
  }
  const sql = `SELECT json_build_object('targetOid',(SELECT oid::bigint FROM pg_database WHERE datname='tempo'),
    'targetName','tempo','controlOid',(SELECT oid::bigint FROM pg_database WHERE datname=current_database()),
    'controlName',current_database(),'adminRole',current_user,
    'systemIdentifier',(SELECT system_identifier::text FROM pg_control_system()),
    'allowConnections',(SELECT datallowconn FROM pg_database WHERE datname='tempo'),
    'backendPids',COALESCE((SELECT json_agg(pid ORDER BY pid) FROM pg_stat_activity WHERE datname='tempo'),'[]'::json),
    'backendDetails',COALESCE((SELECT json_agg(json_build_object('pid',pid,'role',usename,'address',host(client_addr),
      'start',extract(epoch FROM backend_start)::text) ORDER BY pid) FROM pg_stat_activity WHERE datname='tempo'),'[]'::json),
    'preparedTransactions',(SELECT count(*) FROM pg_prepared_xacts WHERE database='tempo'))`;
  let control;
  try { control = JSON.parse((await docker(['exec', '--user', 'postgres', container.Id, 'psql', '-X', '-A', '-t',
    '-v', 'ON_ERROR_STOP=1', '-U', admin, '-d', 'postgres', '-c', sql])).stdout); }
  catch (error) {
    if (/database system is starting up|connection refused|No such file or directory/i.test(error?.message ?? ''))
      throw new Error('Managed isolation control database is starting.');
    throw new Error('Managed isolation control database is unavailable.');
  }
  assertManagedTargetLock(stateDirectory, lock);
  if (control.targetName !== 'tempo' || control.controlName !== 'postgres' || control.adminRole !== admin ||
      !Number.isSafeInteger(control.targetOid) || !Number.isSafeInteger(control.controlOid) ||
      control.targetOid <= 0 || control.controlOid <= 0 || control.targetOid === control.controlOid ||
      !/^[0-9]+$/.test(control.systemIdentifier ?? '') || typeof control.allowConnections !== 'boolean' ||
      !Array.isArray(control.backendPids) || control.backendPids.some(pid => !Number.isSafeInteger(pid) || pid <= 0) ||
      !Array.isArray(control.backendDetails) || control.backendDetails.length !== control.backendPids.length ||
      control.backendDetails.some((item, index) => item?.pid !== control.backendPids[index] || typeof item.role !== 'string' ||
        (item.address !== null && typeof item.address !== 'string') || typeof item.start !== 'string') ||
      control.preparedTransactions !== 0)
    throw new Error('Managed isolation control database identity is unsafe.');
  if (guard.isolation && (guard.isolation.target_oid !== control.targetOid || guard.isolation.target_name !== control.targetName ||
      guard.isolation.system_identifier !== control.systemIdentifier || guard.isolation.control_oid !== control.controlOid ||
      guard.isolation.control_name !== control.controlName || guard.isolation.admin_role !== control.adminRole))
    throw new Error('Original managed isolation control identity differs.');
  return { ...topology, ...control };
}

// A completed attempt authenticates its immutable binding without requiring
// ordinary application sessions or accepted Study writes to disappear.
export async function inspectManagedCompletedTarget({ target, configuration, expectedImage, guard, stateDirectory, lock, docker, exclusiveAdministrator, allowStopped = false }) {
  if (exclusiveAdministrator !== true || guard?.isolation?.phase !== 'reattached' ||
      !['pending', 'verified'].includes(guard.state)) throw new Error('Managed completion identity is invalid.');
  assertManagedTargetLock(stateDirectory, lock);
  validateTarget(configuration, target);
  const identity = guard.isolation.identity;
  const daemonId = (await docker(['info', '--format', '{{.ID}}'])).stdout.trim();
  const ids = (await docker(['ps', '-aq'])).stdout.trim().split(/\s+/).filter(Boolean);
  if (!ids.length) throw new Error('Managed completion container inventory is unavailable.');
  const containers = JSON.parse((await docker(['inspect', ...ids])).stdout);
  if (!Array.isArray(containers) || containers.length !== ids.length || ids.some(id => !containers.some(item => item.Id?.startsWith(id))))
    throw new Error('Managed completion container inventory is incomplete.');
  validateContainers(containers, target);
  const originals = containers.filter(item => item.Id === identity.containerId);
  const networks = JSON.parse((await docker(['network', 'inspect', identity.networkId])).stdout);
  if (originals.length !== 1 || !Array.isArray(networks) || networks.length !== 1)
    throw new Error('Original managed completion topology is unavailable.');
  const topology = inspectRegisteredIsolation({ target, daemonId, container: originals[0], network: networks[0],
    expectedImage, trustedDaemon: exclusiveAdministrator, intent: identity, allowStopped });
  if (!topology.attached) throw new Error('Original managed completion topology differs.');
  assertManagedTargetLock(stateDirectory, lock);
  return topology;
}

// A stopped isolated target may only be started by its exact registered ID.
// This path is gated pending review and does not claim daemon-restart safety.
export async function startManagedStoppedDetachedTarget({ target, configuration, expectedImage, guard,
  stateDirectory, lock, docker, exclusiveAdministrator, verifyOriginalBackup }) {
  requireManagedIsolationGate();
  if (exclusiveAdministrator !== true || guard?.state !== 'pending' ||
      !['intent', 'detached', 'verified'].includes(guard.isolation?.phase) || guard.isolation.original_allow_connections !== true ||
      typeof verifyOriginalBackup !== 'function')
    throw new Error('Original stopped isolation state is invalid.');
  assertManagedTargetLock(stateDirectory, lock);
  validateTarget(configuration, target);
  const guardPath = join(stateDirectory, 'migration-guard.json'), identity = guard.isolation.identity;
  syncManagedIsolationEvidence({ guardPath, guard });
  if (existsSync(join(stateDirectory, guard.cut62_witness.slice(0, -5), 'pinned-helper.json')))
    throw new Error('Original managed helper must be reconciled before starting PostgreSQL.');
  const backupSha = await verifyOriginalBackup(guard.backup);
  inspectManagedOriginalEvidence({ target, stateDirectory, guard, verifiedBackupSha256: backupSha });
  const inspect = async () => {
    assertManagedTargetLock(stateDirectory, lock);
    const daemonId = (await docker(['info', '--format', '{{.ID}}'])).stdout.trim();
    const ids = (await docker(['ps', '-aq'])).stdout.trim().split(/\s+/).filter(Boolean);
    if (!ids.length) throw new Error('Original stopped container inventory is unavailable.');
    const containers = JSON.parse((await docker(['inspect', ...ids])).stdout);
    if (!Array.isArray(containers) || containers.length !== ids.length || ids.some(id => !containers.some(item => item.Id?.startsWith(id))))
      throw new Error('Original stopped container inventory is incomplete.');
    validateContainers(containers, target);
    const originals = containers.filter(item => item.Id === identity.containerId);
    if (originals.length !== 1 || containers.some(item => item.State?.Running && item.Id !== identity.containerId &&
        ((typeof item.HostConfig?.NetworkMode === 'string' && item.HostConfig.NetworkMode.startsWith('container:') &&
          (identity.containerId.startsWith(item.HostConfig.NetworkMode.slice(10)) || item.HostConfig.NetworkMode.slice(10) === originals[0]?.Name?.replace(/^\//, ''))) ||
         (item.Config?.Labels?.['com.docker.compose.project'] === target.project &&
          !['postgres', 'redis'].includes(item.Config?.Labels?.['com.docker.compose.service'])))))
      throw new Error('Original stopped target has an active helper or writer.');
    const networks = JSON.parse((await docker(['network', 'inspect', identity.networkId])).stdout);
    if (!Array.isArray(networks) || networks.length !== 1) throw new Error('Original stopped network is unavailable.');
    const topology = inspectRegisteredIsolation({ target, daemonId, container: originals[0], network: networks[0],
      expectedImage, trustedDaemon: exclusiveAdministrator, intent: identity, allowStopped: true });
    if (topology.attached) throw new Error('Original stopped target is unexpectedly attached.');
    return originals[0];
  };
  const original = await inspect();
  if (original.State?.Running !== false || original.State?.Restarting !== false)
    throw new Error('Original target is not stopped.');
  if (!isDeepStrictEqual(JSON.parse(readOwnedPrivateBytes(guardPath)), guard) ||
      await verifyOriginalBackup(guard.backup) !== backupSha)
    throw new Error('Original managed evidence changed before start.');
  const contain = async inspectionError => {
    // If Docker unexpectedly attached the original database, close admission
    // before containment. Never terminate an unknown backend or prepared work.
    let contained = false;
    for (let attempt = 0; attempt < 60; attempt++) {
      try {
        assertManagedTargetLock(stateDirectory, lock);
        if ((await docker(['info', '--format', '{{.ID}}'])).stdout.trim() !== target.daemonId)
          throw new Error('Original Docker daemon changed during containment.');
        const admin = configuration.services.postgres.environment.POSTGRES_USER ?? 'postgres';
        if (!/^[a-z_][a-z0-9_]*$/.test(admin)) throw new Error('Managed administrator identity is invalid.');
        const psql = ['exec', '--user', 'postgres', identity.containerId, 'psql', '-X', '-A', '-t', '-v', 'ON_ERROR_STOP=1', '-U', admin, '-d', 'postgres', '-c'];
        const factsSql = `SELECT json_build_object('system',(SELECT system_identifier::text FROM pg_control_system()),
          'target',(SELECT oid::bigint FROM pg_database WHERE datname='tempo'),
          'control',(SELECT oid::bigint FROM pg_database WHERE datname=current_database()),'name',current_database(),'role',current_user,
          'clients',(SELECT count(*) FROM pg_stat_activity WHERE pid<>pg_backend_pid() AND NOT COALESCE((
            (backend_type IN ('background writer','checkpointer','walwriter','autovacuum launcher','logical replication launcher','archiver','startup','walreceiver') AND datname IS NULL AND client_addr IS NULL) OR
            (backend_type='io worker' AND datname IS NULL AND usename IS NULL AND client_addr IS NULL)),false)),
          'prepared',(SELECT count(*) FROM pg_prepared_xacts))`;
        const facts = JSON.parse((await docker([...psql, factsSql])).stdout);
        if (facts.system !== guard.isolation.system_identifier || facts.target !== guard.isolation.target_oid ||
            facts.control !== guard.isolation.control_oid || facts.name !== guard.isolation.control_name || facts.role !== guard.isolation.admin_role)
          throw new Error('Original cluster identity differs during containment.');
        await docker([...psql, 'ALTER DATABASE tempo ALLOW_CONNECTIONS false']);
        const after = JSON.parse((await docker([...psql, factsSql])).stdout);
        if (after.system !== facts.system || after.target !== facts.target || after.control !== facts.control ||
            after.name !== facts.name || after.role !== facts.role || after.clients !== 0 || after.prepared !== 0)
          throw new Error('Original cluster has admitted or unknown work; preserve it with admission closed.');
        assertManagedTargetLock(stateDirectory, lock);
        await docker(['stop', '--time', '10', identity.containerId]);
        const rows = JSON.parse((await docker(['inspect', identity.containerId])).stdout);
        if (!Array.isArray(rows) || rows.length !== 1 || rows[0].Id !== identity.containerId || rows[0].State?.Running !== false)
          throw new Error('Original target containment could not be confirmed.');
        contained = true;
        break;
      } catch (error) {
        if (!/database system is starting up|connection refused|No such file or directory/i.test(error?.message ?? '') || attempt === 59)
          throw new Error('Original target start was unsafe; preserve its guard and investigate containment.', { cause: error });
        await new Promise(resolve => setTimeout(resolve, 500));
      }
    }
    if (!contained) throw new Error('Original target containment is unresolved.');
    throw new Error('Original target start changed topology; database was contained.', { cause: inspectionError });
  };
  let current;
  try {
    assertManagedTargetLock(stateDirectory, lock);
    const started = (await docker(['start', identity.containerId])).stdout.trim();
    if (started.length < 12 || !identity.containerId.startsWith(started))
      throw new Error('Original managed container start identity differs.');
    current = await inspect();
  } catch (error) { await contain(error); }
  if (!current.State?.Running) throw new Error('Original managed target did not start.');
  let observed;
  for (let attempt = 0; attempt < 60; attempt++) {
    try { observed = await inspectManagedFence({ target, configuration, expectedImage, guard, stateDirectory,
      lock, docker, exclusiveAdministrator }); break; }
    catch (error) {
      if (error?.message !== 'Managed isolation control database is starting.') await contain(error);
      if (attempt === 59) throw error;
      await new Promise(resolve => setTimeout(resolve, 500));
    }
  }
  if (observed.attached || observed.backendPids.length || observed.preparedTransactions || typeof observed.allowConnections !== 'boolean')
    await contain(new Error('Original managed started target is not isolated and drained.'));
  return observed;
}

// The caller obtains this observation after re-closing admission. It must
// independently authenticate the helper's ready challenge and hold the lock.
export function assertManagedPinnedBackend({ guard, observation, backend }) {
  const isolation = guard?.isolation;
  if (guard?.state !== 'pending' || !['detached', 'verified'].includes(isolation?.phase) ||
      !isDeepStrictEqual(observation?.original, isolation?.identity) || observation?.attached !== false ||
      observation.allowConnections !== false || observation.preparedTransactions !== 0 ||
      !backend || Object.keys(backend).sort().join() !== 'address,pid,role,start' ||
      !Number.isSafeInteger(backend.pid) || backend.pid <= 0 || backend.role !== isolation.admin_role ||
      !['127.0.0.1', '::1'].includes(backend.address) || typeof backend.start !== 'string' || !backend.start ||
      !isDeepStrictEqual(observation.backendPids, [backend.pid]) ||
      !isDeepStrictEqual(observation.backendDetails, [backend]))
    throw new Error('Managed isolation pinned backend is unsafe or changed.');
}

// A challenge binds the helper to the original attempt and topology. It is
// not authorization by itself: the lock, private files, Docker state and the
// pinned database session must all be independently checked by the owner.
export function managedPinnedChallenge(guard, nonce) {
  const isolation = guard?.isolation, identity = isolation?.identity, binding = guard?.defense_binding, cut = guard?.cut62_identity;
  const digest = value => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
  if (guard?.state !== 'pending' || !['detached', 'verified'].includes(isolation?.phase) ||
      ![59, 61].includes(guard.starting_schema) || guard.intended_schema !== 62 ||
      !/^[0-9a-f-]{36}$/i.test(guard.attempt_id ?? '') || (guard.starting_schema === 59 && !digest(guard.pre62_witness_id)) ||
      !/^[0-9a-f]{32}$/.test(nonce ?? '') || identity?.target !== guard.target || identity?.trustedDaemon !== true ||
      binding?.target !== guard.target || binding?.attempt_id !== guard.attempt_id ||
      binding?.backup_filename !== guard.backup?.filename || !guard.backup?.verified ||
      !digest(binding.backup_sha256) || !digest(binding.selection_sha256) || !digest(cut?.witness_id) ||
      cut.attempt_id !== guard.attempt_id || cut.backup_filename !== guard.backup.filename ||
      !Number.isSafeInteger(isolation.target_oid) || isolation.target_oid <= 0 ||
      !Number.isSafeInteger(isolation.control_oid) || isolation.control_oid <= 0 || isolation.target_oid === isolation.control_oid ||
      !/^[0-9]+$/.test(isolation.system_identifier ?? '') ||
      ['daemonId', 'containerId', 'networkId', 'imageId'].some(key => typeof identity[key] !== 'string' || !identity[key]))
    throw new Error('Original managed pinned challenge is invalid.');
  return { version: 1, nonce, target: guard.target, attempt_id: guard.attempt_id, daemon_id: identity.daemonId,
    container_id: identity.containerId, network_id: identity.networkId, image_id: identity.imageId,
    system_identifier: isolation.system_identifier, target_oid: isolation.target_oid, control_oid: isolation.control_oid,
    backup_sha256: binding.backup_sha256, selection_sha256: binding.selection_sha256, cut62_witness_id: cut.witness_id,
    pre62_witness_id: guard.starting_schema === 59 ? guard.pre62_witness_id : null };
}

export function assertManagedPinnedChallenge({ guard, nonce, ready, observation }) {
  const expected = managedPinnedChallenge(guard, nonce);
  const keys = ['backend_address', 'backend_role', 'backend_start', 'pid'];
  if (!ready || Object.keys(ready).sort().join() !== [...Object.keys(expected), ...keys].sort().join() ||
      Object.entries(expected).some(([key, value]) => ready[key] !== value))
    throw new Error('Managed pinned challenge differs from the original attempt.');
  assertManagedPinnedBackend({ guard, observation, backend: { pid: ready.pid, role: ready.backend_role,
    address: ready.backend_address, start: ready.backend_start } });
}

// The production gate deliberately remains closed. This phase documents the
// concrete first physical transition; later phases must be completed and
// reviewed before the gate can be changed.
export async function prepareManagedDetachedTarget({ target, configuration, expectedImage, stateDirectory,
  lock, docker, exclusiveAdministrator, verifyOriginalBackup }) {
  requireManagedIsolationGate();
  if (typeof verifyOriginalBackup !== 'function') throw new Error('Original backup verification is unavailable.');
  const guardPath = join(stateDirectory, 'migration-guard.json');
  const readGuard = () => JSON.parse(readOwnedPrivateBytes(guardPath));
  let guard = readGuard();
  const originalBackupSha = await verifyOriginalBackup(guard.backup);
  assertManagedTargetLock(stateDirectory, lock);
  inspectManagedOriginalEvidence({ target, stateDirectory, guard, verifiedBackupSha256: originalBackupSha });
  let observed = await inspectManagedFence({ target, configuration, expectedImage, guard, stateDirectory,
    lock, docker, exclusiveAdministrator });
  if (observed.backendPids.length || observed.preparedTransactions)
    throw new Error('Managed isolation target still has admitted work.');
  if (!guard.isolation) {
    if (!observed.attached || !observed.allowConnections)
      throw new Error('Managed isolation original topology or admission is unsafe.');
    assertManagedTargetLock(stateDirectory, lock);
    guard = persistManagedIsolationIntent({ guardPath, guard, identity: observed.original,
      targetOid: observed.targetOid, systemIdentifier: observed.systemIdentifier, controlOid: observed.controlOid,
      controlName: observed.controlName, adminRole: observed.adminRole, targetName: observed.targetName,
      attached: observed.attached, allowConnections: observed.allowConnections, backendPids: observed.backendPids,
      preparedTransactions: observed.preparedTransactions });
  }
  syncManagedIsolationEvidence({ guardPath, guard });
  assertManagedTargetLock(stateDirectory, lock);
  if (!isDeepStrictEqual(readGuard(), guard) || await verifyOriginalBackup(guard.backup) !== originalBackupSha)
    throw new Error('Original managed migration evidence changed; keep writers stopped.');
  inspectManagedOriginalEvidence({ target, stateDirectory, guard, verifiedBackupSha256: originalBackupSha });
  observed = await inspectManagedFence({ target, configuration, expectedImage, guard, stateDirectory,
    lock, docker, exclusiveAdministrator });
  const action = classifyManagedIsolationRecovery({ guard, identity: observed.original, attached: observed.attached,
    targetOid: observed.targetOid, systemIdentifier: observed.systemIdentifier, controlOid: observed.controlOid,
    controlName: observed.controlName, adminRole: observed.adminRole, targetName: observed.targetName,
    allowConnections: observed.allowConnections, backendPids: observed.backendPids,
    preparedTransactions: observed.preparedTransactions });
  if (action === 'detach') {
    assertManagedTargetLock(stateDirectory, lock);
    if (!isDeepStrictEqual(readGuard(), guard)) throw new Error('Original migration guard changed; keep writers stopped.');
    await docker(['network', 'disconnect', observed.original.networkId, observed.original.containerId]);
    observed = await inspectManagedFence({ target, configuration, expectedImage, guard, stateDirectory,
      lock, docker, exclusiveAdministrator });
  } else if (action !== 'record-detached') {
    throw new Error('Managed isolation is not ready to record detachment.');
  }
  assertManagedTargetLock(stateDirectory, lock);
  if (observed.attached || observed.backendPids.length || observed.preparedTransactions ||
      await verifyOriginalBackup(guard.backup) !== originalBackupSha)
    throw new Error('Managed isolation did not reach the original drained detached target.');
  inspectManagedOriginalEvidence({ target, stateDirectory, guard, verifiedBackupSha256: originalBackupSha });
  guard = advanceManagedIsolationMarker({ guardPath, guard, observation: { ...observed, identity: observed.original } });
  syncManagedIsolationEvidence({ guardPath, guard });
  return guard;
}

// The private pipe carries a fresh challenge, not independent authorization.
// The lock, daemon topology and sole backend are inspected by the host before
// it acknowledges the pinned child. The production gate remains closed.
export async function runManagedPinnedChild({ target, configuration, expectedImage, maintenanceImage, maintenanceRevision,
  stateDirectory, adminPassfile, lock, docker, exclusiveAdministrator, verifyOriginalBackup }) {
  requireManagedIsolationGate();
  const guardPath = join(stateDirectory, 'migration-guard.json');
  const guard = JSON.parse(readOwnedPrivateBytes(guardPath));
  if (guard?.state !== 'pending' || !['detached', 'verified'].includes(guard.isolation?.phase) ||
      !/^sha256:[0-9a-f]{64}$/.test(maintenanceImage ?? '') || !/^[0-9a-f]{40}$/.test(maintenanceRevision ?? '') ||
      [stateDirectory, adminPassfile].some(path => !isAbsolute(path ?? '') || /[,\n\r]/.test(path)))
    throw new Error('Managed pinned child configuration is invalid.');
  if (guard.isolation.phase === 'verified') {
    const directory = join(stateDirectory, guard.cut62_witness.slice(0, -5));
    try { readOwnedPrivateBytes(join(directory, 'verified-receipt.json')); readOwnedPrivateBytes(join(directory, 'verified-completion.json')); }
    catch { throw new Error('Original managed completion is missing; keep writers stopped.'); }
  }
  const pass = lstatSync(adminPassfile);
  if (!pass.isFile() || pass.uid !== process.getuid() || (pass.mode & 0o077))
    throw new Error('Managed administrator passfile is not private.');
  const backupSha = await verifyOriginalBackup(guard.backup);
  const image = JSON.parse((await docker(['image', 'inspect', maintenanceImage])).stdout);
  if (!Array.isArray(image) || image.length !== 1 || image[0].Id !== maintenanceImage ||
      image[0].Config?.Labels?.['org.opencontainers.image.revision'] !== maintenanceRevision)
    throw new Error('Original managed maintenance image differs.');
  inspectManagedHistorySelectionBinding({ target, stateDirectory, guard, verifiedBackupSha256: backupSha });
  syncManagedIsolationEvidence({ guardPath, guard });
  let observed = await inspectManagedFence({ target, configuration, expectedImage, guard, stateDirectory,
    lock, docker, exclusiveAdministrator });
  const journalDirectory = join(stateDirectory, guard.cut62_witness.slice(0, -5));
  const helperJournal = join(journalDirectory, 'pinned-helper.json');
  const inspectOwnedHelper = async journal => {
    if (Object.keys(journal ?? {}).sort().join() !== 'attempt_id,database_container,helper_id,image,name,target,version' ||
        journal?.version !== 1 || journal.attempt_id !== guard.attempt_id || journal.target !== guard.target ||
        journal.database_container !== guard.isolation.identity.containerId || journal.image !== maintenanceImage ||
        !/^tempo-managed-pinned-[0-9a-f]{32}$/.test(journal.name ?? '') ||
        (journal.helper_id !== null && !/^[0-9a-f]{64}$/.test(journal.helper_id ?? '')))
      throw new Error('Original managed helper journal is invalid.');
    const inspected = await docker(['inspect', journal.name], { allowFailure: true });
    if (inspected.code !== 0) {
      if (inspected.code === 1 && /No such (?:object|container)/i.test(inspected.stderr ?? '')) return null;
      throw new Error('Original managed helper cannot be inspected.');
    }
    const rows = JSON.parse(inspected.stdout), item = rows?.[0];
    if (!Array.isArray(rows) || rows.length !== 1 || !/^[0-9a-f]{64}$/.test(item?.Id ?? '') ||
        (journal.helper_id && journal.helper_id !== item.Id) || item.Image !== journal.image ||
        item.Config?.Labels?.['tempo.managed.attempt'] !== guard.attempt_id ||
        item.Config?.Labels?.['tempo.managed.target'] !== guard.target ||
        item.HostConfig?.NetworkMode !== `container:${journal.database_container}`)
      throw new Error('Original managed helper identity differs.');
    return item;
  };
  if (existsSync(helperJournal)) {
    const previous = JSON.parse(readOwnedPrivateBytes(helperJournal));
    syncManagedIsolationEvidence({ guardPath, guard, receiptPath: helperJournal, receipt: previous });
    assertManagedTargetLock(stateDirectory, lock);
    const helper = await inspectOwnedHelper(previous);
    if (helper?.State?.Running) {
      assertManagedTargetLock(stateDirectory, lock);
      await docker(['stop', '--time', '10', helper.Id]);
    }
    const remaining = await inspectOwnedHelper(previous);
    if (remaining?.State?.Running) throw new Error('Original managed helper remains active.');
    observed = await inspectManagedFence({ target, configuration, expectedImage, guard, stateDirectory,
      lock, docker, exclusiveAdministrator });
    if (observed.attached || observed.backendPids.length || observed.preparedTransactions)
      throw new Error('Original managed helper has not drained; keep writers stopped.');
    assertManagedTargetLock(stateDirectory, lock);
    unlinkSync(helperJournal); syncDirectory(journalDirectory);
  }
  if (observed.attached || observed.backendPids.length || observed.preparedTransactions)
    throw new Error('Managed pinned target is not isolated and drained.');
  if (!observed.allowConnections) {
    assertManagedTargetLock(stateDirectory, lock);
    await docker(['exec', '--user', 'postgres', observed.original.containerId, 'psql', '-X', '-v', 'ON_ERROR_STOP=1',
      '-U', observed.adminRole, '-d', observed.controlName, '-c', 'ALTER DATABASE tempo ALLOW_CONNECTIONS true']);
    observed = await inspectManagedFence({ target, configuration, expectedImage, guard, stateDirectory,
      lock, docker, exclusiveAdministrator });
    if (observed.attached || !observed.allowConnections || observed.backendPids.length || observed.preparedTransactions)
      throw new Error('Managed isolated admission could not be opened safely.');
  }
  const nonce = randomBytes(16).toString('hex'), challenge = managedPinnedChallenge(guard, nonce);
  const attemptDirectory = `/state/${guard.cut62_witness.slice(0, -5)}`;
  const request = { challenge, guard_path: '/state/migration-guard.json', selection_path: `${attemptDirectory}/defense-selection.json`,
    binding_path: `${attemptDirectory}/binding.json`, witness_path: `${attemptDirectory}/${guard.cut62_witness}`,
    pre62_path: guard.starting_schema === 59 ? `/state/${guard.pre62_witness.slice(0, -5)}/${guard.pre62_witness}` : null,
    receipt_path: `${attemptDirectory}/verified-receipt.json` };
  const name = `tempo-managed-pinned-${randomBytes(16).toString('hex')}`;
  let helperIdentity = { version: 1, name, helper_id: null, attempt_id: guard.attempt_id,
    target: guard.target, database_container: observed.original.containerId, image: maintenanceImage };
  assertManagedTargetLock(stateDirectory, lock);
  if (existsSync(helperJournal)) throw new Error('Original managed helper journal already exists.');
  atomicJson(helperJournal, helperIdentity);
  const args = ['--context', target.context, 'run', '--rm', '-i', '--name', name,
    '--label', `tempo.managed.attempt=${guard.attempt_id}`, '--label', `tempo.managed.target=${guard.target}`,
    '--network', `container:${observed.original.containerId}`,
    '--mount', `type=bind,source=${stateDirectory},destination=/state`,
    '--mount', `type=bind,source=${adminPassfile},destination=/run/secrets/admin_pgpass,readonly`,
    '--env', 'PGPASSFILE=/run/secrets/admin_pgpass', '--env',
    `TEMPO_POSTGRES_ADMIN_URL=postgresql://${observed.adminRole}@postgres:5432/tempo?hostaddr=127.0.0.1`,
    maintenanceImage, '-c', 'from database.tooling.managed_isolation import managed_pinned_child; managed_pinned_child()'];
  assertManagedTargetLock(stateDirectory, lock);
  const child = spawn('docker', args, { stdio: ['pipe', 'pipe', 'pipe'] });
  const childClosed = new Promise((resolve, reject) => { child.once('error', reject); child.once('close', resolve); });
  childClosed.catch(() => {});
  child.stdin.on('error', () => {});
  const lines = createInterface({ input: child.stdout })[Symbol.asyncIterator]();
  const nextLine = async () => {
    let timer;
    try {
      const item = await Promise.race([lines.next(), new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('Managed pinned child timed out.')), 50000); })]);
      if (item.done) throw new Error('Managed pinned child ended unexpectedly.');
      return JSON.parse(item.value);
    } finally { clearTimeout(timer); }
  };
  // Drain stderr without exposing database details or unbounded buffering.
  child.stderr.resume();
  try {
    child.stdin.write(`${JSON.stringify(request)}\n`);
    const ready = await nextLine();
    const helper = await inspectOwnedHelper(helperIdentity);
    if (!helper?.State?.Running) throw new Error('Managed pinned helper is not running.');
    helperIdentity = { ...helperIdentity, helper_id: helper.Id };
    assertManagedTargetLock(stateDirectory, lock);
    atomicJson(helperJournal, helperIdentity);
    observed = await inspectManagedFence({ target, configuration, expectedImage, guard, stateDirectory,
      lock, docker, exclusiveAdministrator });
    if (observed.attached || !observed.allowConnections || observed.backendPids.length !== 1 ||
        observed.backendPids[0] !== ready.pid || observed.preparedTransactions)
      throw new Error('Managed pinned child is not the sole target backend.');
    assertManagedTargetLock(stateDirectory, lock);
    await docker(['exec', '--user', 'postgres', observed.original.containerId, 'psql', '-X', '-v', 'ON_ERROR_STOP=1',
      '-U', observed.adminRole, '-d', observed.controlName, '-c', 'ALTER DATABASE tempo ALLOW_CONNECTIONS false']);
    observed = await inspectManagedFence({ target, configuration, expectedImage, guard, stateDirectory,
      lock, docker, exclusiveAdministrator });
    assertManagedPinnedChallenge({ guard, nonce, ready, observation: observed });
    if (!isDeepStrictEqual(JSON.parse(readOwnedPrivateBytes(guardPath)), guard) ||
        await verifyOriginalBackup(guard.backup) !== backupSha)
      throw new Error('Original managed evidence changed before acknowledgement.');
    assertManagedTargetLock(stateDirectory, lock);
    child.stdin.end(`${JSON.stringify(ready)}\n`);
    const result = await nextLine();
    if (result.schema !== 62 || result.witness_id !== guard.cut62_identity.witness_id)
      throw new Error('Managed pinned result differs from the original witness.');
    const exitCode = await childClosed;
    if (exitCode !== 0) throw new Error('Managed pinned child failed; keep writers stopped.');
    observed = await inspectManagedFence({ target, configuration, expectedImage, guard, stateDirectory,
      lock, docker, exclusiveAdministrator });
    if (observed.attached || observed.allowConnections || observed.backendPids.length || observed.preparedTransactions)
      throw new Error('Managed pinned child did not leave the target isolated and drained.');
    assertManagedTargetLock(stateDirectory, lock);
    unlinkSync(helperJournal); syncDirectory(journalDirectory);
    const directory = join(stateDirectory, guard.cut62_witness.slice(0, -5));
    const receiptPath = join(directory, 'verified-receipt.json'), completionPath = join(directory, 'verified-completion.json');
    const receipt = JSON.parse(readOwnedPrivateBytes(receiptPath)), completionBytes = readOwnedPrivateBytes(completionPath);
    const completion = JSON.parse(completionBytes), witnessPath = join(directory, guard.cut62_witness);
    const fd = openSync(witnessPath, constants.O_RDONLY | constants.O_NOFOLLOW);
    let envelope;
    try {
      const info = fstatSync(fd);
      if (!info.isFile() || info.uid !== process.getuid() || (info.mode & 0o777) !== 0o600 || info.size > 64 * 1024 * 1024) throw new Error();
      envelope = JSON.parse(readFileSync(fd));
    } finally { closeSync(fd); }
    if (!/^[0-9a-f]{64}$/.test(envelope?.secret ?? '')) throw new Error('Original managed witness secret is invalid.');
    const payload = { version: 1, target: guard.target, attempt_id: guard.attempt_id, identity: guard.isolation.identity,
      target_oid: guard.isolation.target_oid, system_identifier: guard.isolation.system_identifier,
      control_oid: guard.isolation.control_oid, backup: guard.defense_binding,
      pre62_witness_id: guard.starting_schema === 59 ? guard.pre62_witness_id : null,
      cut62_witness_id: guard.cut62_identity.witness_id, receipt };
    const sorted = value => Array.isArray(value) ? value.map(sorted) : value && typeof value === 'object'
      ? Object.fromEntries(Object.keys(value).sort().map(key => [key, sorted(value[key])])) : value;
    const canonical = JSON.stringify(sorted(payload)).replace(/[\u007f-\uffff]/g, character => `\\u${character.charCodeAt(0).toString(16).padStart(4, '0')}`);
    const signature = createHmac('sha256', Buffer.from(envelope.secret, 'hex')).update('tempo-disposable-completion-v1\0').update(canonical).digest();
    const supplied = /^[0-9a-f]{64}$/.test(completion.signature ?? '') ? Buffer.from(completion.signature, 'hex') : Buffer.alloc(0);
    if (!isDeepStrictEqual(completion.payload, payload) || Object.keys(completion).sort().join() !== 'payload,signature' ||
        supplied.length !== signature.length || !timingSafeEqual(supplied, signature) ||
        createHash('sha256').update(completionBytes).digest('hex') !== result.completion_sha256)
      throw new Error('Original managed completion authentication failed.');
    const action = classifyManagedIsolationRecovery({ guard, identity: observed.original, attached: false,
      targetOid: observed.targetOid, systemIdentifier: observed.systemIdentifier, controlOid: observed.controlOid,
      controlName: observed.controlName, adminRole: observed.adminRole, targetName: observed.targetName,
      allowConnections: false, backendPids: [], preparedTransactions: 0, schema: 62, receipt, receiptValidated: true });
    if (!['record-verified', 'restore-after-receipt'].includes(action)) throw new Error('Managed receipt state is invalid.');
    syncManagedIsolationEvidence({ guardPath, guard, receiptPath, receipt });
    syncManagedIsolationEvidence({ guardPath, guard, receiptPath: completionPath, receipt: completion });
    assertManagedTargetLock(stateDirectory, lock);
    const verifiedGuard = action === 'record-verified' ? advanceManagedIsolationMarker({ guardPath, guard, observation: { ...observed,
      identity: observed.original, schema: 62, receipt, receiptValidated: true } }) : guard;
    syncManagedIsolationEvidence({ guardPath, guard: verifiedGuard, receiptPath: completionPath, receipt: completion });
    return { result, guard: verifiedGuard, receipt, completion, receiptPath, completionPath };
  } finally {
    child.stdin.destroy();
    if (child.exitCode === null) {
      const helper = await inspectOwnedHelper(helperIdentity);
      if (helper?.State?.Running) await docker(['stop', '--time', '10', helper.Id]);
      let timer;
      try { await Promise.race([childClosed.catch(() => {}), new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error('Managed helper did not exit; preserve its journal.')), 15000);
      })]); } finally { clearTimeout(timer); }
    }
    let final = await inspectManagedFence({ target, configuration, expectedImage, guard, stateDirectory,
      lock, docker, exclusiveAdministrator });
    if (final.attached) throw new Error('Managed helper target unexpectedly reattached; preserve its journal.');
    if (final.allowConnections) {
      assertManagedTargetLock(stateDirectory, lock);
      await docker(['exec', '--user', 'postgres', final.original.containerId, 'psql', '-X', '-v', 'ON_ERROR_STOP=1',
        '-U', final.adminRole, '-d', final.controlName, '-c', 'ALTER DATABASE tempo ALLOW_CONNECTIONS false']);
      final = await inspectManagedFence({ target, configuration, expectedImage, guard, stateDirectory,
        lock, docker, exclusiveAdministrator });
    }
    if (final.allowConnections || final.backendPids.length || final.preparedTransactions)
      throw new Error('Managed helper target has not drained; preserve its journal.');
  }
}

// Restore only the original attachment after the independently authenticated
// post-commit evidence is durable. The managed gate stays closed until the
// complete pinned process and this transition have been reviewed together.
export async function restoreManagedCompletedTarget({ target, configuration, expectedImage, stateDirectory,
  lock, docker, exclusiveAdministrator, verifyOriginalBackup, authenticateCompletion }) {
  requireManagedIsolationGate();
  if (typeof verifyOriginalBackup !== 'function' || typeof authenticateCompletion !== 'function')
    throw new Error('Original managed completion verification is unavailable.');
  const guardPath = join(stateDirectory, 'migration-guard.json');
  const readGuard = () => JSON.parse(readOwnedPrivateBytes(guardPath));
  let guard = readGuard();
  if (guard?.state !== 'pending' || !['verified', 'reattached'].includes(guard.isolation?.phase))
    throw new Error('Managed completion is not ready to restore.');
  const verify = async () => {
    assertManagedTargetLock(stateDirectory, lock);
    if (!isDeepStrictEqual(readGuard(), guard)) throw new Error('Original migration guard changed; keep writers stopped.');
    const sha = await verifyOriginalBackup(guard.backup);
    inspectManagedOriginalEvidence({ target, stateDirectory, guard, verifiedBackupSha256: sha });
    const evidence = await authenticateCompletion(guard);
    if (!evidence || typeof evidence.receiptPath !== 'string' || !evidence.receipt ||
        typeof evidence.completionPath !== 'string' || !evidence.completion)
      throw new Error('Original managed completion is unavailable.');
    syncManagedIsolationEvidence({ guardPath, guard, receiptPath: evidence.receiptPath, receipt: evidence.receipt });
    syncManagedIsolationEvidence({ guardPath, guard, receiptPath: evidence.completionPath, receipt: evidence.completion });
    assertManagedTargetLock(stateDirectory, lock);
    return evidence;
  };
  let evidence = await verify();
  let observed = await inspectManagedFence({ target, configuration, expectedImage, guard, stateDirectory,
    lock, docker, exclusiveAdministrator });
  const classify = () => classifyManagedIsolationRecovery({ guard, identity: observed.original, attached: observed.attached,
    targetOid: observed.targetOid, systemIdentifier: observed.systemIdentifier, controlOid: observed.controlOid,
    controlName: observed.controlName, adminRole: observed.adminRole, targetName: observed.targetName,
    allowConnections: observed.allowConnections, backendPids: observed.backendPids,
    preparedTransactions: observed.preparedTransactions, schema: 62, receipt: evidence.receipt, receiptValidated: true });
  let action = classify();
  if (action === 'restore-after-receipt') {
    if (!observed.allowConnections) {
      assertManagedTargetLock(stateDirectory, lock);
      await docker(['exec', '--user', 'postgres', observed.original.containerId, 'psql', '-X', '-v', 'ON_ERROR_STOP=1',
        '-U', observed.adminRole, '-d', observed.controlName, '-c', 'ALTER DATABASE tempo ALLOW_CONNECTIONS true']);
      observed = await inspectManagedFence({ target, configuration, expectedImage, guard, stateDirectory,
        lock, docker, exclusiveAdministrator });
      if (classify() !== 'restore-after-receipt' || !observed.allowConnections)
        throw new Error('Managed admission restoration is unsafe; keep writers stopped.');
    }
    evidence = await verify();
    assertManagedTargetLock(stateDirectory, lock);
    const args = ['network', 'connect'];
    for (const alias of observed.original.aliases) args.push('--alias', alias);
    args.push(observed.original.networkId, observed.original.containerId);
    await docker(args);
    observed = await inspectManagedFence({ target, configuration, expectedImage, guard, stateDirectory,
      lock, docker, exclusiveAdministrator });
    action = classify();
  }
  if (action === 'record-reattached') {
    evidence = await verify();
    guard = advanceManagedIsolationMarker({ guardPath, guard, observation: { ...observed, identity: observed.original,
      schema: 62, receipt: evidence.receipt, receiptValidated: true } });
    syncManagedIsolationEvidence({ guardPath, guard });
  } else if (action !== 'complete') {
    throw new Error('Managed completion topology is unsafe; keep writers stopped.');
  }
  return guard;
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


export function privateSelection(path, expectedTarget) {
  const bytes = readOwnedPrivateBytes(path);
  let value;
  try { value = JSON.parse(bytes); } catch { throw new Error("Approved Defense selection is invalid."); }
  const keys = value?.keys;
  if (Object.keys(value ?? {}).sort().join() !== "cohort,expected,keys,target,version" || value.version !== 1 ||
      value.target !== expectedTarget || value.cohort !== "defense-no-activity" || !Array.isArray(keys) || keys.length > 11 ||
      Object.keys(value.expected ?? {}).sort().join() !== "count,reference_digest" || value.expected.count !== keys.length ||
      !/^[0-9a-f]{64}$/.test(value.expected.reference_digest ?? "") ||
      keys.some(pair => !Array.isArray(pair) || pair.length !== 2 || pair.some(item => typeof item !== "string" || !item)) ||
      keys.some((pair, index) => index && (Buffer.compare(Buffer.from(keys[index - 1][0]), Buffer.from(pair[0])) > 0 ||
        (keys[index - 1][0] === pair[0] && Buffer.compare(Buffer.from(keys[index - 1][1]), Buffer.from(pair[1])) >= 0))))
    throw new Error("Approved Defense selection is invalid for this target.");
  return { bytes, digest: createHash("sha256").update(bytes).digest("hex"), count: keys.length };
}

function parseUniqueJson(text) {
  let index = 0;
  const fail = () => { throw new Error('Approved Defense history selection is invalid.'); };
  const whitespace = () => { while (/[\x20\t\n\r]/.test(text[index] ?? '') && index < text.length) index++; };
  const string = () => {
    const start = index;
    if (text[index++] !== '"') fail();
    while (index < text.length) {
      const character = text[index++];
      if (character === '"') {
        try { return JSON.parse(text.slice(start, index)); } catch { fail(); }
      }
      if (character === '\\') {
        if (index >= text.length) fail();
        index++;
      }
    }
    fail();
  };
  const value = () => {
    whitespace();
    if (text[index] === '"') { string(); return; }
    if (text[index] === '{') {
      index++; whitespace(); const keys = new Set();
      if (text[index] === '}') { index++; return; }
      while (true) {
        whitespace(); const key = string(); if (keys.has(key)) fail(); keys.add(key);
        whitespace(); if (text[index++] !== ':') fail(); value(); whitespace();
        const separator = text[index++]; if (separator === '}') return; if (separator !== ',') fail();
      }
    }
    if (text[index] === '[') {
      index++; whitespace(); if (text[index] === ']') { index++; return; }
      while (true) {
        value(); whitespace(); const separator = text[index++];
        if (separator === ']') return; if (separator !== ',') fail();
      }
    }
    const token = /^(?:true|false|null|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)/.exec(text.slice(index));
    if (!token) fail(); index += token[0].length;
  };
  value(); whitespace(); if (index !== text.length) fail();
  try { return JSON.parse(text); } catch { fail(); }
}

// V2 is deliberately separate from the deployed v1/zero path. The exact
// reviewed file bytes are hashed; strings are neither normalized nor rewritten.
export function privateDefenseHistorySelection(path, expectedTarget) {
  const bytes = readOwnedPrivateBytes(path);
  let value;
  try {
    if (bytes.length >= 3 && bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf) throw new Error();
    value = parseUniqueJson(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
  } catch { throw new Error('Approved Defense history selection is invalid for this target.'); }
  const keys = value?.keys, groups = value?.expected?.groups;
  const groupNames = ['no_activity', 'recognition_only', 'reviewed'];
  const wellFormed = item => typeof item === 'string' && item.length > 0 &&
    [...item].every(character => !(character.length === 1 && character.charCodeAt(0) >= 0xd800 && character.charCodeAt(0) <= 0xdfff));
  if (Object.keys(value ?? {}).sort().join() !== 'cohort,expected,keys,target,version' || value.version !== 2 ||
      value.target !== expectedTarget || value.cohort !== 'defense-history' || !Array.isArray(keys) || keys.length > 15 ||
      Object.keys(value.expected ?? {}).sort().join() !== 'count,groups,reference_digest' ||
      !groups || Object.keys(groups).sort().join() !== groupNames.join() ||
      groupNames.some(name => !Number.isSafeInteger(groups[name]) || groups[name] < 0) ||
      value.expected.count !== keys.length || !/^[0-9a-f]{64}$/.test(value.expected.reference_digest ?? '') ||
      keys.some(tuple => !Array.isArray(tuple) || tuple.length !== 3 || !wellFormed(tuple[0]) || !wellFormed(tuple[1]) || !groupNames.includes(tuple[2])) ||
      new Set(keys.map(tuple => tuple[0])).size !== keys.length || new Set(keys.map(tuple => tuple[1])).size !== keys.length ||
      keys.some((tuple, index) => index && (Buffer.compare(Buffer.from(keys[index - 1][0]), Buffer.from(tuple[0])) > 0 ||
        (keys[index - 1][0] === tuple[0] && Buffer.compare(Buffer.from(keys[index - 1][1]), Buffer.from(tuple[1])) >= 0))) ||
      groupNames.some(name => groups[name] !== keys.filter(tuple => tuple[2] === name).length))
    throw new Error('Approved Defense history selection is invalid for this target.');
  return { bytes, digest: createHash('sha256').update(bytes).digest('hex'), count: keys.length, groups: { ...groups } };
}

// V2 selection is checked against the independently persisted attempt guard
// and the freshly verified original backup digest. It is not activated here.
export function inspectManagedHistorySelectionBinding({ target, stateDirectory, guard, verifiedBackupSha256 }) {
  if (!isAbsolute(stateDirectory ?? '') || /[:,\n\r]/.test(stateDirectory) || guard?.version !== 1 ||
      guard.state !== 'pending' || guard.target !== targetKey(target) || guard.intended_schema !== 62 ||
      ![59, 61].includes(guard.starting_schema) || !Array.isArray(guard.starting_versions) ||
      guard.starting_versions.length !== guard.starting_schema ||
      guard.starting_versions.some((version, index) => version !== index + 1) ||
      guard.database?.name !== 'tempo' || guard.database?.volume !== target.volumes?.[target.postgresVolumeKey]?.name ||
      !guard.backup?.verified || !/^tempo-[0-9A-Za-z-]+\.dump$/.test(guard.backup.filename ?? '') ||
      !/^[0-9a-f]{64}$/.test(verifiedBackupSha256 ?? ''))
    throw new Error('Original managed history selection evidence is invalid; keep writers stopped.');
  const witnessName = cut62WitnessName(guard);
  if (guard.cut62_witness !== witnessName) throw new Error('Original managed witness identity differs; keep writers stopped.');
  const directory = join(stateDirectory, witnessName.slice(0, -5));
  const selectionPath = join(directory, 'defense-selection.json'), bindingPath = join(directory, 'binding.json');
  const selection = privateDefenseHistorySelection(selectionPath, guard.target);
  let binding;
  try { binding = JSON.parse(readOwnedPrivateBytes(bindingPath)); }
  catch { throw new Error('Original managed history binding is unavailable; keep writers stopped.'); }
  if (!isDeepStrictEqual(binding, guard.defense_binding) || Object.keys(binding ?? {}).sort().join() !==
      'attempt_id,backup_filename,backup_sha256,selection_sha256,target,version' || binding.version !== 1 ||
      binding.target !== guard.target || binding.attempt_id !== guard.attempt_id ||
      binding.backup_filename !== guard.backup.filename || binding.backup_sha256 !== verifiedBackupSha256 ||
      binding.selection_sha256 !== selection.digest)
    throw new Error('Original managed history selection, backup or attempt binding differs; keep writers stopped.');
  return { selectionPath, bindingPath, selectionCount: selection.count, groups: selection.groups, selectionSha256: selection.digest };
}

// Validate the original paths and binding before any topology change. The
// pinned database owner separately authenticates the witness and source rows.
export function inspectManagedOriginalEvidence({ target, stateDirectory, guard, verifiedBackupSha256 }) {
  if (!isAbsolute(stateDirectory ?? '') || /[:,\n\r]/.test(stateDirectory) || guard?.version !== 1 ||
      guard.state !== 'pending' || guard.target !== targetKey(target) || guard.intended_schema !== 62 ||
      ![59, 61].includes(guard.starting_schema) || !Array.isArray(guard.starting_versions) ||
      guard.starting_versions.length !== guard.starting_schema ||
      guard.starting_versions.some((version, index) => version !== index + 1) ||
      guard.database?.name !== 'tempo' || guard.database?.volume !== target.volumes?.[target.postgresVolumeKey]?.name ||
      !guard.backup?.verified || !/^tempo-[0-9A-Za-z-]+\.dump$/.test(guard.backup.filename ?? '') ||
      !/^[0-9a-f]{64}$/.test(verifiedBackupSha256 ?? ''))
    throw new Error('Original managed migration evidence is invalid; keep writers stopped.');
  const witnessName = cut62WitnessName(guard), witnessDirectory = join(stateDirectory, witnessName.slice(0, -5));
  if (guard.cut62_witness !== witnessName) throw new Error('Original managed witness identity differs; keep writers stopped.');
  const selectionPath = join(witnessDirectory, 'defense-selection.json'), bindingPath = join(witnessDirectory, 'binding.json');
  const witnessPath = join(witnessDirectory, witnessName), selection = privateDefenseHistorySelection(selectionPath, guard.target);
  let binding;
  try {
    binding = JSON.parse(readOwnedPrivateBytes(bindingPath));
    const fd = openSync(witnessPath, constants.O_RDONLY | constants.O_NOFOLLOW);
    try {
      const info = fstatSync(fd);
      if (!info.isFile() || info.uid !== process.getuid() || (info.mode & 0o777) !== 0o600 || info.size > 64 * 1024 * 1024) throw new Error();
    } finally { closeSync(fd); }
  }
  catch { throw new Error('Original managed witness files are unavailable; keep writers stopped.'); }
  if (!isDeepStrictEqual(binding, guard.defense_binding) || binding.version !== 1 || binding.target !== guard.target ||
      binding.attempt_id !== guard.attempt_id || binding.backup_filename !== guard.backup.filename ||
      binding.backup_sha256 !== verifiedBackupSha256 || binding.selection_sha256 !== selection.digest ||
      guard.cut62_identity?.attempt_id !== guard.attempt_id || guard.cut62_identity?.backup_filename !== guard.backup.filename ||
      !/^[0-9a-f]{64}$/.test(guard.cut62_identity?.witness_id ?? ''))
    throw new Error('Original managed selection, backup or witness binding differs; keep writers stopped.');
  let pre62Path = null;
  if (guard.starting_schema === 59) {
    const preName = `pre62-${guard.attempt_id}.json`, checkpoint = guard.cut62_intermediate_verified;
    if (guard.pre62_witness !== preName || !/^[0-9a-f]{64}$/.test(guard.pre62_witness_id ?? '') ||
        checkpoint?.schema !== 61 || checkpoint.attempt_id !== guard.attempt_id || checkpoint.pre62_witness !== preName ||
        checkpoint.pre62_witness_id !== guard.pre62_witness_id || checkpoint.backup_filename !== guard.backup.filename)
      throw new Error('Original managed schema 59 checkpoint differs; keep writers stopped.');
    pre62Path = join(stateDirectory, preName.slice(0, -5), preName);
    try { readOwnedPrivateBytes(pre62Path); } catch { throw new Error('Original managed schema 59 witness is unavailable; keep writers stopped.'); }
  }
  return { witnessName, witnessPath, pre62Path, selectionPath, bindingPath, selectionCount: selection.count };
}

