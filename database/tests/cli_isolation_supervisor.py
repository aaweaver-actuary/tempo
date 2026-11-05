"""Disposable CLI isolation supervisor. Never included in production images.

This module is only usable from the owned lifecycle harness. The Docker daemon
and host administrator are trusted to exclude competing privileged actors.
"""
from __future__ import annotations

import json
import hashlib
import hmac
import os
import re
import stat
import subprocess
import time
from pathlib import Path

from database.tooling.verify_postgres_cli_state import (
    authenticate_pre62_59, cut62_witness_id, load_cut62_attempt_binding,
    load_cut62_defense_selection, load_cut62_witness, load_pre62_59,
)


def _private_file(path, root):
    """Read one host-owned file below an already validated private root."""
    path, root = Path(path), Path(root)
    if (not path.is_absolute() or not root.is_absolute() or str(path) != os.path.normpath(path) or
            str(root) != os.path.normpath(root) or path == root or root not in path.parents):
        raise RuntimeError('Disposable evidence path is outside its private root')
    for directory in reversed(path.parents):
        if directory == root.parent:
            continue
        if directory != root and root not in directory.parents:
            continue
        info = os.lstat(directory)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise RuntimeError('Disposable evidence directory is not private and owned')
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 1048576:
            raise RuntimeError('Disposable evidence is not a private owned file')
        return stream.read()


def _check_lock(root, lock_pid, lock_token):
    lock = json.loads(_private_file(Path(root) / 'maintenance.lock', root))
    if (not isinstance(lock_pid, int) or lock_pid <= 0 or lock.get('pid') != lock_pid or
            not isinstance(lock_token, str) or not lock_token or lock.get('token') != lock_token):
        raise RuntimeError('Original lifecycle lock is not held by the supervisor parent')
    os.kill(lock_pid, 0)


def verify_candidate_provenance(target, revision):
    """Require the bound test adapter and supervisor to match one commit."""
    if not re.fullmatch(r'[0-9a-f]{40}', revision or ''):
        raise RuntimeError('Disposable candidate revision is invalid')
    checkout = Path(target['root'])
    if checkout != Path(__file__).resolve().parents[2]:
        raise RuntimeError('Disposable candidate checkout differs')
    head = subprocess.run(['git', '-C', str(checkout), 'rev-parse', 'HEAD'],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
    if head.returncode or head.stdout.strip() != revision:
        raise RuntimeError('Disposable candidate revision differs')
    status = subprocess.run(['git', '-C', str(checkout), 'status', '--porcelain', '--untracked-files=no'],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
    if status.returncode or status.stdout:
        raise RuntimeError('Disposable candidate has modified tracked source')
    for relative in ('database/tests/check_postgres_cli_lifecycle.py', 'database/tests/cli_isolation_supervisor.py'):
        path = checkout / relative
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
            raise RuntimeError('Disposable candidate fixture is not owned source')
        source = subprocess.run(['git', '-C', str(checkout), 'show', revision + ':' + relative],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        if source.returncode or not hmac.compare_digest(hashlib.sha256(source.stdout).digest(), hashlib.sha256(path.read_bytes()).digest()):
            raise RuntimeError('Disposable candidate fixture differs from its approved commit')


def authenticate_attempt(*, root, guard_path, selection_path, binding_path, witness_path, pre62_path, lock_pid, lock_token, allow_verified=False):
    """Authenticate original files before any disposable Docker mutation."""
    root = Path(root)
    if not root.is_absolute() or root != Path(os.path.normpath(root)):
        raise RuntimeError('Disposable private root is invalid')
    for ancestor in root.parents:
        info = os.lstat(ancestor)
        mode = stat.S_IMODE(info.st_mode)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.geteuid()) or
                (mode & 0o022 and not (info.st_uid == 0 and mode & stat.S_ISVTX))):
            raise RuntimeError('Disposable private root ancestor is untrusted')
    _check_lock(root, lock_pid, lock_token)
    guard = json.loads(_private_file(guard_path, root))
    witness_name = guard.get('cut62_witness')
    if (Path(guard_path) != root / 'migration-guard.json' or
            not re.fullmatch(r'cut62-[0-9a-f-]{36}\.json', witness_name or '', re.I) or
            witness_name != 'cut62-' + str(guard.get('attempt_id')) + '.json'):
        raise RuntimeError('Original disposable attempt paths differ')
    witness_dir = root / witness_name.removesuffix('.json')
    if (Path(witness_path) != witness_dir / witness_name or Path(selection_path) != witness_dir / 'defense-selection.json' or
            Path(binding_path) != witness_dir / 'binding.json'):
        raise RuntimeError('Original disposable witness paths differ')
    if guard.get('starting_schema') == 59:
        pre_name = 'pre62-' + str(guard.get('attempt_id')) + '.json'
        if (guard.get('pre62_witness') != pre_name or
                Path(pre62_path) != root / pre_name.removesuffix('.json') / pre_name):
            raise RuntimeError('Original schema 59 witness path differs')
    selection_bytes = _private_file(selection_path, root)
    _private_file(binding_path, root)
    _private_file(witness_path, root)
    if guard.get('starting_schema') == 59:
        _private_file(pre62_path, root)
    selection, selection_sha = load_cut62_defense_selection(selection_path)
    binding = load_cut62_attempt_binding(binding_path, selection, selection_sha)
    witness, secret = load_cut62_witness(witness_path)
    witness_id = cut62_witness_id(witness, secret)
    valid_state = guard.get('state') == 'pending' or (allow_verified and guard.get('state') == 'verified' and
        guard.get('verified_schema') == 62 and (guard.get('isolation') or {}).get('phase') == 'reattached')
    if (not selection_bytes or not selection.get('keys') or not valid_state or
            guard.get('version') != 1 or binding != guard.get('defense_binding') or
            binding.get('target') != guard.get('target') or binding.get('attempt_id') != guard.get('attempt_id') or
            binding.get('backup_filename') != (guard.get('backup') or {}).get('filename') or
            not (guard.get('backup') or {}).get('verified') or witness.get('binding') != binding or
            witness.get('selection') != selection or witness_id != (guard.get('cut62_identity') or {}).get('witness_id') or
            (guard.get('cut62_identity') or {}).get('attempt_id') != guard.get('attempt_id') or
            (guard.get('cut62_identity') or {}).get('backup_filename') != binding.get('backup_filename')):
        raise RuntimeError('Original disposable migration evidence differs')
    if guard.get('starting_schema') == 59:
        original, original_secret = load_pre62_59(pre62_path)
        checkpoint = guard.get('cut62_intermediate_verified') or {}
        if (authenticate_pre62_59(original, original_secret) != guard.get('pre62_witness_id') or
                checkpoint.get('schema') != 61 or checkpoint.get('attempt_id') != guard.get('attempt_id') or
                checkpoint.get('backup_filename') != binding.get('backup_filename') or
                checkpoint.get('pre62_witness_id') != guard.get('pre62_witness_id') or
                checkpoint.get('pre62_witness') != guard.get('pre62_witness')):
            raise RuntimeError('Original schema 59 checkpoint differs')
    elif guard.get('starting_schema') != 61:
        raise RuntimeError('Unsupported disposable migration origin')
    return guard


def authenticate_existing_receipt(root, path, guard):
    """Bind an existing receipt to its original attempt and re-sync it.

    The caller must still verify the live schema-62 state using the original
    witness on a fenced pinned session before any admission or reattachment.
    """
    path = Path(path)
    if not os.path.lexists(path):
        return None
    receipt = json.loads(_private_file(path, root))
    isolation, binding = guard.get('isolation') or {}, guard.get('defense_binding') or {}
    identity = isolation.get('identity') or {}
    if (identity.get('trustedDaemon') is not True or identity.get('target') != guard.get('target') or
            any(not isinstance(identity.get(field), str) or not identity[field]
                for field in ('daemonId', 'containerId', 'networkId', 'imageId')) or
            not isinstance(isolation.get('target_oid'), int) or isolation['target_oid'] <= 0 or
            not isinstance(isolation.get('control_oid'), int) or isolation['control_oid'] <= 0 or
            isolation['target_oid'] == isolation['control_oid'] or isolation.get('control_name') != 'postgres' or
            not re.fullmatch(r'[0-9]+', str(isolation.get('system_identifier') or '')) or
            not isolation.get('target_name') or not isolation.get('admin_role')):
        raise RuntimeError('Original disposable receipt identity is incomplete')
    expected = {'version': 1, 'target': guard.get('target'), 'attempt_id': guard.get('attempt_id'),
                'target_oid': isolation.get('target_oid'), 'target_name': isolation.get('target_name'),
                'system_identifier': isolation.get('system_identifier'), 'control_oid': isolation.get('control_oid'),
                'control_name': isolation.get('control_name'), 'admin_role': isolation.get('admin_role'),
                'daemon_id': identity.get('daemonId'), 'container_id': identity.get('containerId'),
                'network_id': identity.get('networkId'), 'image_id': identity.get('imageId'),
                'backup_filename': binding.get('backup_filename'), 'backup_sha256': binding.get('backup_sha256'),
                'selection_sha256': binding.get('selection_sha256'),
                'pre62_witness_id': guard.get('pre62_witness_id') if guard.get('starting_schema') == 59 else None,
                'cut62_witness_id': (guard.get('cut62_identity') or {}).get('witness_id'), 'schema': 62}
    if receipt != expected or isolation.get('phase') not in ('detached', 'verified', 'reattached'):
        raise RuntimeError('Original disposable receipt differs')
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    if json.loads(_private_file(path, root)) != receipt:
        raise RuntimeError('Original disposable receipt changed')
    return receipt


def inspect_owned_topology(target, expected_image, guard, *, trusted_daemon):
    """Inspect the exact project database and original network, without mutation."""
    if trusted_daemon is not True or target.get('disposable') is not True:
        raise RuntimeError('Disposable daemon authority is unavailable')
    context, project = target.get('context'), target.get('project')
    if not isinstance(context, str) or not context or not re.fullmatch(r'tempo-pg-regressions-[a-zA-Z0-9-]+', project or ''):
        raise RuntimeError('Disposable target is invalid')
    if target.get('root') != str(Path(__file__).resolve().parents[2]):
        raise RuntimeError('Disposable target checkout identity differs')

    def docker(*arguments):
        result = subprocess.run(['docker', '--context', context, *arguments], check=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
        return result.stdout.strip()

    daemon_id = docker('info', '--format', '{{.ID}}')
    ids = docker('ps', '-aq', '--no-trunc', '--filter', 'label=com.docker.compose.project=' + project).splitlines()
    containers = json.loads(docker('inspect', *ids)) if ids else []
    databases = [item for item in containers if (item.get('Config', {}).get('Labels') or {}).get('com.docker.compose.service') == 'postgres']
    if len(databases) != 1:
        raise RuntimeError('Owned disposable database identity is ambiguous')
    for item in containers:
        state, labels = item.get('State') or {}, item.get('Config', {}).get('Labels') or {}
        if (state.get('Running') or state.get('Restarting')) and labels.get('com.docker.compose.service') not in ('postgres', 'redis'):
            raise RuntimeError('A disposable writer may still be active')
    database = databases[0]
    intent = (guard.get('isolation') or {}).get('identity')
    networks = database.get('NetworkSettings', {}).get('Networks') or {}
    if intent:
        network_id = intent.get('networkId')
    elif len(networks) == 1:
        network_id = next(iter(networks.values())).get('NetworkID')
    else:
        raise RuntimeError('Disposable original network is ambiguous')
    if not isinstance(network_id, str) or not re.fullmatch(r'[0-9a-f]{64}', network_id):
        raise RuntimeError('Disposable network identity is invalid')
    network = json.loads(docker('network', 'inspect', network_id))[0]
    # Reuse the production inspector rather than maintain a weaker duplicate.
    source = """import {pathToFileURL} from 'node:url';
let input=''; for await (const part of process.stdin) input+=part;
const data=JSON.parse(input); const api=await import(pathToFileURL(data.module).href);
console.log(JSON.stringify(api.inspectDisposableIsolation(data.arguments)));"""
    payload = {'module': str(Path(target['root']) / 'scripts' / 'tempo-runtime.mjs'),
               'arguments': {'target': target, 'daemonId': daemon_id, 'container': database,
                             'network': network, 'expectedImage': expected_image,
                             'trustedDaemon': True, 'intent': intent}}
    result = subprocess.run(['node', '--input-type=module', '-e', source], input=json.dumps(payload),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError('Disposable topology failed the existing identity inspector')
    return json.loads(result.stdout)


def inspect_control_database(target, identity, guard):
    """Read target identity and other backends from the owned local socket."""
    if guard.get('database', {}).get('name') != 'tempo':
        raise RuntimeError('Disposable target database is not the expected fixture')
    query = """SELECT json_build_object(
      'targetOid',(SELECT oid FROM pg_database WHERE datname='tempo'),
      'targetName','tempo','controlOid',(SELECT oid FROM pg_database WHERE datname=current_database()),
      'controlName',current_database(),'adminRole',current_user,
      'systemIdentifier',(SELECT system_identifier::text FROM pg_control_system()),
      'allowConnections',(SELECT datallowconn FROM pg_database WHERE datname='tempo'),
      'backendPids',COALESCE((SELECT json_agg(pid ORDER BY pid) FROM pg_stat_activity WHERE datname='tempo'),'[]'::json),
      'backendDetails',COALESCE((SELECT json_agg(json_build_object('pid',pid,'role',usename,
         'address',host(client_addr),'start',extract(epoch FROM backend_start)::text) ORDER BY pid)
         FROM pg_stat_activity WHERE datname='tempo'),'[]'::json),
      'preparedTransactions',(SELECT count(*) FROM pg_prepared_xacts WHERE database='tempo'))"""
    result = subprocess.run(['docker', '--context', target['context'], 'exec', '--user', 'postgres',
                             identity['containerId'], 'psql', '-X', '-A', '-t', '-v', 'ON_ERROR_STOP=1',
                             '-U', 'postgres', '-d', 'postgres', '-c', query],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError('Disposable control database identity is unavailable')
    try:
        observed = json.loads(result.stdout)
    except (ValueError, TypeError) as error:
        raise RuntimeError('Disposable control database identity is invalid') from error
    if (observed.get('targetName') != 'tempo' or observed.get('controlName') != 'postgres' or
            observed.get('adminRole') != 'postgres' or not isinstance(observed.get('targetOid'), int) or
            not isinstance(observed.get('controlOid'), int) or observed['targetOid'] == observed['controlOid'] or
            not re.fullmatch(r'[0-9]+', str(observed.get('systemIdentifier') or '')) or
            not isinstance(observed.get('allowConnections'), bool) or not isinstance(observed.get('backendPids'), list) or
            not isinstance(observed.get('backendDetails'), list) or
            observed.get('preparedTransactions') != 0):
        raise RuntimeError('Disposable control database identity is unsafe')
    return observed


def set_disposable_admission(target, identity, allow):
    if type(allow) is not bool:
        raise RuntimeError('Disposable admission request is invalid')
    statement = 'ALTER DATABASE tempo ALLOW_CONNECTIONS ' + ('true' if allow else 'false')
    result = subprocess.run(['docker', '--context', target['context'], 'exec', '--user', 'postgres',
                             identity['containerId'], 'psql', '-X', '-v', 'ON_ERROR_STOP=1',
                             '-U', 'postgres', '-d', 'postgres', '-c', statement],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError('Disposable admission could not be changed')


def verify_original_backup(target, guard):
    """Recheck the verified original backup and its sidecar, never replace it."""
    backup, binding = guard.get('backup') or {}, guard.get('defense_binding') or {}
    name = backup.get('filename')
    if (not backup.get('verified') or not re.fullmatch(r'tempo-[0-9A-Za-z-]+\.dump', name or '') or
            name != binding.get('backup_filename') or
            not re.fullmatch(r'[0-9a-f]{64}', binding.get('backup_sha256') or '')):
        raise RuntimeError('Original disposable backup identity is invalid')
    files = target.get('composeFiles')
    if not isinstance(files, list) or not files or any(not isinstance(path, str) or not os.path.isabs(path) for path in files):
        raise RuntimeError('Disposable compose identity is invalid')
    command = ['docker', '--context', target['context'], 'compose', '--project-directory', target['root']]
    if target.get('envFile'):
        command += ['--env-file', target['envFile']]
    command += ['-p', target['project']]
    for path in files:
        command += ['-f', path]
    command += ['--profile', 'maintenance', 'run', '--rm', '--no-deps', '-T', '--entrypoint', 'sh',
                'postgres-backup', '-ec', 'cd /backups; sha256sum -c "$1.sha256" >/dev/null; sha256sum "$1" | cut -d " " -f1',
                'sh', name]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120)
    if result.returncode or result.stdout.strip() != binding['backup_sha256']:
        raise RuntimeError('Original disposable backup checksum differs')


def persist_isolation_intent(target, guard_path, guard, identity, observation):
    """Persist and re-sync the original identity before any network change."""
    if guard.get('isolation'):
        raise RuntimeError('Original isolation intent must not be replaced')
    if observation['backendPids'] or observation['preparedTransactions'] or not observation['allowConnections']:
        raise RuntimeError('Disposable target is not drained and normally admitted')
    arguments = {'guardPath': str(guard_path), 'guard': guard, 'identity': identity,
                 'targetOid': observation['targetOid'], 'systemIdentifier': observation['systemIdentifier'],
                 'controlOid': observation['controlOid'], 'controlName': observation['controlName'],
                 'adminRole': observation['adminRole'], 'targetName': observation['targetName'],
                 'attached': True, 'allowConnections': observation['allowConnections'],
                 'backendPids': observation['backendPids'], 'preparedTransactions': observation['preparedTransactions']}
    source = """import {pathToFileURL} from 'node:url';
let input='';for await(const part of process.stdin)input+=part;
const data=JSON.parse(input);const api=await import(pathToFileURL(data.module).href);
api.syncDisposableIsolationEvidence({guardPath:data.arguments.guardPath,guard:data.arguments.guard});
const next=api.persistDisposableIsolationIntent(data.arguments);
api.syncDisposableIsolationEvidence({guardPath:data.arguments.guardPath,guard:next});
console.log(JSON.stringify(next));"""
    payload = {'module': str(Path(target['root']) / 'scripts' / 'tempo-runtime.mjs'), 'arguments': arguments}
    result = subprocess.run(['node', '--input-type=module', '-e', source], input=json.dumps(payload),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError('Disposable original isolation intent could not be persisted')
    return json.loads(result.stdout)


def sync_original_guard(target, guard_path, guard):
    source = """import {pathToFileURL} from 'node:url';
let input='';for await(const part of process.stdin)input+=part;
const data=JSON.parse(input);const api=await import(pathToFileURL(data.module).href);
api.syncDisposableIsolationEvidence({guardPath:data.guardPath,guard:data.guard});"""
    payload = {'module': str(Path(target['root']) / 'scripts' / 'tempo-runtime.mjs'),
               'guardPath': str(guard_path), 'guard': guard}
    result = subprocess.run(['node', '--input-type=module', '-e', source], input=json.dumps(payload),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError('Original disposable guard could not be re-synced')


def advance_isolation_marker(target, guard_path, guard, identity, attached, observation, *, receipt=None):
    """Record a physically observed phase without replacing original evidence."""
    state = {'identity': identity, 'attached': attached, 'targetOid': observation['targetOid'],
             'systemIdentifier': observation['systemIdentifier'], 'controlOid': observation['controlOid'],
             'controlName': observation['controlName'], 'adminRole': observation['adminRole'],
             'targetName': observation['targetName'], 'allowConnections': observation['allowConnections'],
             'backendPids': observation['backendPids'], 'preparedTransactions': observation['preparedTransactions'],
             'receipt': receipt, 'receiptValidated': receipt is not None}
    source = """import {pathToFileURL} from 'node:url';
let input='';for await(const part of process.stdin)input+=part;
const data=JSON.parse(input);const api=await import(pathToFileURL(data.module).href);
const next=api.advanceDisposableIsolationMarker(data.arguments);
const sync={guardPath:data.arguments.guardPath,guard:next};
if(data.receiptPath){sync.receiptPath=data.receiptPath;sync.receipt=data.arguments.observation.receipt;}
api.syncDisposableIsolationEvidence(sync);console.log(JSON.stringify(next));"""
    receipt_path = (Path(guard_path).parent / guard['cut62_witness'].removesuffix('.json') / 'verified-receipt.json') if receipt else None
    payload = {'module': str(Path(target['root']) / 'scripts' / 'tempo-runtime.mjs'),
               'arguments': {'guardPath': str(guard_path), 'guard': guard, 'observation': state},
               'receiptPath': str(receipt_path) if receipt_path else None}
    result = subprocess.run(['node', '--input-type=module', '-e', source], input=json.dumps(payload),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError('Disposable isolation phase could not be persisted')
    return json.loads(result.stdout)


def prepare_detached_target(*, target, expected_image, root, guard_path, selection_path, binding_path,
                            witness_path, pre62_path, lock_pid, lock_token, trusted_daemon):
    """Validate originals, durably record intent, then detach the owned DB.

    Recovery after a detach-before-marker crash observes the real Docker state
    and records the same original intent. No application is started here.
    """
    arguments = dict(root=root, guard_path=guard_path, selection_path=selection_path,
                     binding_path=binding_path, witness_path=witness_path, pre62_path=pre62_path, lock_pid=lock_pid, lock_token=lock_token)
    guard = authenticate_attempt(**arguments)
    topology = inspect_owned_topology(target, expected_image, guard, trusted_daemon=trusted_daemon)
    identity = topology['original']
    if identity.get('target') != guard.get('target'):
        raise RuntimeError('Disposable target does not match the original attempt')
    verify_original_backup(target, guard)
    control = inspect_control_database(target, identity, guard)
    if control['backendPids'] or control['preparedTransactions']:
        raise RuntimeError('Disposable target still has admitted work')
    if not guard.get('isolation'):
        if not topology['attached'] or not control['allowConnections']:
            raise RuntimeError('Disposable original topology or admission is not normal')
        _check_lock(root, lock_pid, lock_token)
        guard = persist_isolation_intent(target, guard_path, guard, identity, control)
    isolation = guard['isolation']
    if (isolation.get('phase') not in ('intent', 'detached') or isolation.get('identity') != identity or
            isolation.get('target_oid') != control['targetOid'] or isolation.get('target_name') != control['targetName'] or
            isolation.get('system_identifier') != control['systemIdentifier'] or isolation.get('control_oid') != control['controlOid'] or
            isolation.get('control_name') != control['controlName'] or isolation.get('admin_role') != control['adminRole']):
        raise RuntimeError('Original disposable isolation identity differs')
    sync_original_guard(target, guard_path, guard)
    receipt_path = Path(witness_path).parent / 'verified-receipt.json'
    if authenticate_existing_receipt(root, receipt_path, guard) is not None:
        raise RuntimeError('Completed receipt requires authenticated recovery, not detachment')
    if isolation['phase'] == 'detached':
        if topology['attached']:
            raise RuntimeError('Detached disposable database unexpectedly reattached')
        return guard
    if topology['attached']:
        if not control['allowConnections']:
            raise RuntimeError('Attached disposable database admission changed')
        if authenticate_attempt(**arguments) != guard:
            raise RuntimeError('Original disposable guard changed before detach')
        _check_lock(root, lock_pid, lock_token)
        result = subprocess.run(['docker', '--context', target['context'], 'network', 'disconnect',
                                 identity['networkId'], identity['containerId']],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
        if result.returncode:
            raise RuntimeError('Disposable database could not be detached')
    topology = inspect_owned_topology(target, expected_image, guard, trusted_daemon=trusted_daemon)
    control = inspect_control_database(target, identity, guard)
    if topology['attached'] or topology['original'] != identity or control['backendPids'] or control['preparedTransactions']:
        raise RuntimeError('Disposable database did not reach the drained detached state')
    _check_lock(root, lock_pid, lock_token)
    return advance_isolation_marker(target, guard_path, guard, identity, False, control)


def verify_detached_peer(*, target, identity, expected_image, guard, handoff, root, lock_pid, lock_token):
    """Show an owned project-network peer cannot reach the detached database."""
    from database.tests.check_postgres_cli_lifecycle import _write_private_json_once
    _check_lock(root, lock_pid, lock_token)
    control = inspect_control_database(target, identity, guard)
    if not control['allowConnections'] or control['backendPids'] or control['preparedTransactions']:
        raise RuntimeError('Detached peer probe requires open isolated admission and no sessions')
    name = 'tempo-isolation-peer-' + os.urandom(16).hex()
    result = subprocess.run(['docker', '--context', target['context'], 'run', '--detach', '--name', name,
                             '--network', identity['networkId'], '--label', 'tempo.disposable.owner=' + target['project'],
                             '--entrypoint', 'sh', expected_image, '-c', 'sleep 120'],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
    peer_id = result.stdout.strip()
    if result.returncode or not re.fullmatch(r'[0-9a-f]{64}', peer_id):
        raise RuntimeError('Owned detached peer could not be started')
    try:
        inspected = subprocess.run(['docker', '--context', target['context'], 'inspect', peer_id],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30, check=True)
        peer = json.loads(inspected.stdout)[0]
        networks = peer.get('NetworkSettings', {}).get('Networks') or {}
        if (peer.get('Id') != peer_id or peer.get('Image') != expected_image or not peer.get('State', {}).get('Running') or
                (peer.get('Config', {}).get('Labels') or {}).get('tempo.disposable.owner') != target['project'] or
                len(networks) != 1 or next(iter(networks.values())).get('NetworkID') != identity['networkId']):
            raise RuntimeError('Owned detached peer identity differs')
        dns = subprocess.run(['docker', '--context', target['context'], 'exec', peer_id, 'getent', 'hosts', 'redis'],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)
        attempt = subprocess.run(['docker', '--context', target['context'], 'exec', peer_id,
                                  'pg_isready', '-h', 'postgres', '-p', '5432', '-t', '3'],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)
        if dns.returncode != 0 or attempt.returncode != 2:
            raise RuntimeError('Detached project peer did not prove isolation')
        control = inspect_control_database(target, identity, guard)
        if not control['allowConnections'] or control['backendPids'] or control['preparedTransactions']:
            raise RuntimeError('Detached peer probe changed target admission or sessions')
        _check_lock(root, lock_pid, lock_token)
        _write_private_json_once(str(Path(handoff) / 'peer.json'),
            {'version': 1, 'attempt_id': guard['attempt_id'], 'network_id': identity['networkId'],
             'peer_id': peer_id, 'image_id': expected_image, 'admission_open': True, 'blocked': True})
    finally:
        # This ID was just created by this supervisor and verified above; no
        # project or shared resources are removed by the peer cleanup.
        subprocess.run(['docker', '--context', target['context'], 'rm', '--force', peer_id],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30, check=True)


def run_fenced_helper(*, target, expected_image, maintenance_image, maintenance_revision, pgpass_path,
                      root, guard_path, selection_path, binding_path, witness_path, pre62_path,
                      lock_pid, lock_token, trusted_daemon, crash=None):
    """Open one loopback connection, reclose admission, and verify the cut."""
    from database.tests.check_postgres_cli_lifecycle import create_disposable_handoff, _write_private_json_once

    arguments = dict(root=root, guard_path=guard_path, selection_path=selection_path,
                     binding_path=binding_path, witness_path=witness_path, pre62_path=pre62_path, lock_pid=lock_pid, lock_token=lock_token)
    guard = authenticate_attempt(**arguments)
    isolation = guard.get('isolation') or {}
    if isolation.get('phase') not in ('detached', 'verified'):
        raise RuntimeError('Disposable target has not reached a recoverable detached phase')
    topology = inspect_owned_topology(target, expected_image, guard, trusted_daemon=trusted_daemon)
    identity = topology['original']
    if topology['attached'] or identity != isolation.get('identity'):
        raise RuntimeError('Disposable database is not isolated')
    verify_original_backup(target, guard)
    sync_original_guard(target, guard_path, guard)
    receipt_path = Path(witness_path).parent / 'verified-receipt.json'
    prior_receipt = authenticate_existing_receipt(root, receipt_path, guard)
    prior_inode = os.lstat(receipt_path).st_ino if prior_receipt is not None else None
    if isolation['phase'] == 'verified' and prior_receipt is None:
        raise RuntimeError('Verified disposable phase is missing its original receipt')
    control = inspect_control_database(target, identity, guard)
    if (control['backendPids'] or control['preparedTransactions'] or
            control['targetOid'] != isolation['target_oid'] or control['systemIdentifier'] != isolation['system_identifier'] or
            control['controlOid'] != isolation['control_oid'] or control['adminRole'] != isolation['admin_role']):
        raise RuntimeError('Disposable database is not drained or its identity changed')
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', maintenance_image or '') or not maintenance_revision:
        raise RuntimeError('Disposable maintenance image identity is invalid')
    verify_candidate_provenance(target, maintenance_revision)
    inspected = subprocess.run(['docker', '--context', target['context'], 'image', 'inspect', maintenance_image],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
    if inspected.returncode:
        raise RuntimeError('Disposable maintenance image is unavailable')
    image = json.loads(inspected.stdout)[0]
    if image.get('Id') != maintenance_image or (image.get('Config', {}).get('Labels') or {}).get('org.opencontainers.image.revision') != maintenance_revision:
        raise RuntimeError('Disposable maintenance image differs')
    pgpass_path = Path(pgpass_path)
    _private_file(pgpass_path, pgpass_path.parent)
    fixture_path = Path(__file__).with_name('check_postgres_cli_lifecycle.py')
    for path in (Path(root), pgpass_path, fixture_path):
        if not path.is_absolute() or ',' in str(path) or '\n' in str(path) or '\r' in str(path):
            raise RuntimeError('Disposable bind path is unsafe')
    handoff, nonce = create_disposable_handoff(str(root))
    handoff_inside = '/state/' + Path(handoff).relative_to(root).as_posix()
    guard_inside = '/state/' + Path(guard_path).relative_to(root).as_posix()
    selection_inside = '/state/' + Path(selection_path).relative_to(root).as_posix()
    binding_inside = '/state/' + Path(binding_path).relative_to(root).as_posix()
    witness_inside = '/state/' + Path(witness_path).relative_to(root).as_posix()
    pre62_inside = '/state/' + Path(pre62_path).relative_to(root).as_posix() if pre62_path else None
    name = 'tempo-isolation-helper-' + os.urandom(16).hex()
    command = ['docker', '--context', target['context'], 'run', '--detach', '--name', name,
               '--network', 'container:' + identity['containerId'],
               '--mount', 'type=bind,source=' + str(root) + ',destination=/state',
               '--mount', 'type=bind,source=' + str(pgpass_path) + ',destination=/run/secrets/admin_pgpass,readonly',
               '--mount', 'type=bind,source=' + str(fixture_path) + ',destination=/work/database/tests/check_postgres_cli_lifecycle.py,readonly',
               '--env', 'TEMPO_TEST_INSTANCE=disposable', '--env', 'PGPASSFILE=/run/secrets/admin_pgpass',
               '--env', 'TEMPO_POSTGRES_ADMIN_URL=postgresql://postgres@postgres:5432/tempo?hostaddr=127.0.0.1']
    if crash == 'after-commit':
        command += ['--env', 'TEMPO_TEST_ISOLATION_CRASH_AFTER_COMMIT=1']
    elif crash == 'after-receipt':
        command += ['--env', 'TEMPO_TEST_ISOLATION_CRASH_AFTER_RECEIPT=1']
    elif crash is not None:
        raise RuntimeError('Disposable crash point is invalid')
    command += [maintenance_image, '/work/database/tests/check_postgres_cli_lifecycle.py', '--isolation-pinned',
                '--isolation-guard', guard_inside, '--isolation-selection', selection_inside,
                '--isolation-binding', binding_inside, '--isolation-witness', witness_inside,
                '--isolation-handoff', handoff_inside, '--isolation-nonce', nonce]
    if pre62_inside:
        command += ['--isolation-pre62', pre62_inside]
    if not control['allowConnections']:
        _check_lock(root, lock_pid, lock_token)
        set_disposable_admission(target, identity, True)
        control = inspect_control_database(target, identity, guard)
        if not control['allowConnections'] or control['backendPids']:
            raise RuntimeError('Disposable isolated admission did not reopen cleanly')
    if authenticate_attempt(**arguments) != guard or inspect_owned_topology(target, expected_image, guard, trusted_daemon=trusted_daemon)['attached']:
        raise RuntimeError('Disposable original evidence changed before pinning')
    verify_detached_peer(target=target, identity=identity, expected_image=expected_image, guard=guard,
                         handoff=handoff, root=root, lock_pid=lock_pid, lock_token=lock_token)
    _check_lock(root, lock_pid, lock_token)
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
    helper_id = result.stdout.strip()
    if result.returncode or not re.fullmatch(r'[0-9a-f]{64}', helper_id):
        raise RuntimeError('Disposable pinned helper could not be started')
    ready_path = Path(handoff) / 'ready.json'
    deadline = time.monotonic() + 50
    while not os.path.lexists(ready_path):
        if time.monotonic() >= deadline:
            raise RuntimeError('Disposable pinned helper did not become ready; preserve its owned container')
        time.sleep(.1)
    ready = json.loads(_private_file(ready_path, root))
    control = inspect_control_database(target, identity, guard)
    expected = {'version': 1, 'nonce': nonce, 'target': guard['target'], 'attempt_id': guard['attempt_id'],
                'daemon_id': identity['daemonId'], 'container_id': identity['containerId'],
                'network_id': identity['networkId'], 'image_id': identity['imageId'],
                'system_identifier': isolation['system_identifier'], 'target_oid': isolation['target_oid'],
                'control_oid': isolation['control_oid'], 'backup_sha256': guard['defense_binding']['backup_sha256'],
                'selection_sha256': guard['defense_binding']['selection_sha256'],
                'cut62_witness_id': guard['cut62_identity']['witness_id'],
                'pre62_witness_id': guard.get('pre62_witness_id') if guard['starting_schema'] == 59 else None}
    if any(ready.get(key) != value for key, value in expected.items()) or set(ready) != set(expected) | {'pid', 'backend_role', 'backend_address', 'backend_start'}:
        raise RuntimeError('Disposable pinned challenge differs')
    detail = {'pid': ready['pid'], 'role': ready['backend_role'], 'address': ready['backend_address'], 'start': ready['backend_start']}
    if (ready['backend_role'] != isolation['admin_role'] or ready['backend_address'] not in ('127.0.0.1', '::1') or
            control['backendPids'] != [ready['pid']] or control['backendDetails'] != [detail] or not control['allowConnections']):
        raise RuntimeError('Disposable pinned backend is not the only admitted target session')
    _check_lock(root, lock_pid, lock_token)
    set_disposable_admission(target, identity, False)
    control = inspect_control_database(target, identity, guard)
    topology = inspect_owned_topology(target, expected_image, guard, trusted_daemon=trusted_daemon)
    if (control['allowConnections'] or control['backendPids'] != [ready['pid']] or control['backendDetails'] != [detail] or
            topology['attached'] or topology['original'] != identity or authenticate_attempt(**arguments) != guard):
        raise RuntimeError('Disposable pinned fence changed before acknowledgement')
    _check_lock(root, lock_pid, lock_token)
    verify_candidate_provenance(target, maintenance_revision)
    _check_lock(root, lock_pid, lock_token)
    _write_private_json_once(str(Path(handoff) / 'closed.json'), ready)
    waited = subprocess.run(['docker', '--context', target['context'], 'wait', helper_id],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=50)
    if waited.returncode:
        raise RuntimeError('Disposable pinned helper interrupted; keep the original target isolated')
    subprocess.run(['docker', '--context', target['context'], 'rm', helper_id],
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30, check=True)
    if waited.stdout.strip() != '0':
        raise RuntimeError('Disposable pinned helper interrupted; keep the original target isolated')
    control = inspect_control_database(target, identity, guard)
    if control['allowConnections'] or control['backendPids'] or control['preparedTransactions']:
        raise RuntimeError('Disposable pinned helper did not close cleanly')
    receipt = authenticate_existing_receipt(root, receipt_path, guard)
    if receipt is None or (prior_receipt is not None and (receipt != prior_receipt or os.lstat(receipt_path).st_ino != prior_inode)):
        raise RuntimeError('Disposable original receipt is unavailable or was replaced')
    return guard, receipt


def _completion_envelope(guard, witness_path, receipt):
    witness, secret = load_cut62_witness(witness_path)
    if cut62_witness_id(witness, secret) != guard['cut62_identity']['witness_id']:
        raise RuntimeError('Original disposable completion witness differs')
    payload = {'version': 1, 'target': guard['target'], 'attempt_id': guard['attempt_id'],
               'identity': guard['isolation']['identity'], 'target_oid': guard['isolation']['target_oid'],
               'system_identifier': guard['isolation']['system_identifier'],
               'control_oid': guard['isolation']['control_oid'], 'backup': guard['defense_binding'],
               'pre62_witness_id': guard.get('pre62_witness_id') if guard['starting_schema'] == 59 else None,
               'cut62_witness_id': guard['cut62_identity']['witness_id'], 'receipt': receipt}
    canonical = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()
    return {'payload': payload, 'signature': hmac.new(secret, b'tempo-disposable-completion-v1\0' + canonical, hashlib.sha256).hexdigest()}


def publish_detached_completion(*, target, expected_image, root, guard_path, selection_path, binding_path,
                                witness_path, pre62_path, lock_pid, lock_token, trusted_daemon, receipt):
    """Persist the authenticated verification result before reconnecting."""
    from database.tests.check_postgres_cli_lifecycle import _write_private_json_once

    arguments = dict(root=root, guard_path=guard_path, selection_path=selection_path,
                     binding_path=binding_path, witness_path=witness_path, pre62_path=pre62_path, lock_pid=lock_pid, lock_token=lock_token)
    guard = authenticate_attempt(**arguments)
    topology = inspect_owned_topology(target, expected_image, guard, trusted_daemon=trusted_daemon)
    identity = topology['original']
    control = inspect_control_database(target, identity, guard)
    if (topology['attached'] or control['allowConnections'] or control['backendPids'] or
            control['preparedTransactions'] or authenticate_existing_receipt(root, Path(witness_path).parent / 'verified-receipt.json', guard) != receipt):
        raise RuntimeError('Disposable completion lacks a drained verified receipt')
    if guard['isolation']['phase'] == 'detached':
        _check_lock(root, lock_pid, lock_token)
        guard = advance_isolation_marker(target, guard_path, guard, identity, False, control, receipt=receipt)
    if guard['isolation']['phase'] != 'verified':
        raise RuntimeError('Disposable completion phase is invalid')
    completion = _completion_envelope(guard, witness_path, receipt)
    path = Path(witness_path).parent / 'verified-completion.json'
    if os.path.lexists(path):
        if json.loads(_private_file(path, root)) != completion:
            raise RuntimeError('Original disposable completion differs')
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        if json.loads(_private_file(path, root)) != completion:
            raise RuntimeError('Original disposable completion changed')
    else:
        _check_lock(root, lock_pid, lock_token)
        _write_private_json_once(str(path), completion)
    return guard, completion


def authenticate_completion(root, witness_path, guard, receipt):
    path = Path(witness_path).parent / 'verified-completion.json'
    original = _private_file(path, root)
    actual, expected = json.loads(original), _completion_envelope(guard, witness_path, receipt)
    if actual.get('payload') != expected['payload'] or not hmac.compare_digest(actual.get('signature', ''), expected['signature']):
        raise RuntimeError('Original disposable completion differs')
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    if _private_file(path, root) != original:
        raise RuntimeError('Original disposable completion changed')
    return hashlib.sha256(original).hexdigest()


def restore_completed_target(*, target, expected_image, root, guard_path, selection_path, binding_path,
                             witness_path, pre62_path, lock_pid, lock_token, trusted_daemon):
    """Restore the original topology only after the completion is durable."""
    arguments = dict(root=root, guard_path=guard_path, selection_path=selection_path,
                     binding_path=binding_path, witness_path=witness_path, pre62_path=pre62_path, lock_pid=lock_pid, lock_token=lock_token)
    guard = authenticate_attempt(**arguments)
    if (guard.get('isolation') or {}).get('phase') not in ('verified', 'reattached'):
        raise RuntimeError('Disposable completion is not ready to restore')
    topology = inspect_owned_topology(target, expected_image, guard, trusted_daemon=trusted_daemon)
    identity = topology['original']
    receipt = authenticate_existing_receipt(root, Path(witness_path).parent / 'verified-receipt.json', guard)
    if receipt is None:
        raise RuntimeError('Disposable original receipt is missing')
    completion_sha = authenticate_completion(root, witness_path, guard, receipt)
    sync_original_guard(target, guard_path, guard)
    verify_original_backup(target, guard)
    control = inspect_control_database(target, identity, guard)
    isolation = guard['isolation']
    if (identity != isolation['identity'] or control['targetOid'] != isolation['target_oid'] or
            control['systemIdentifier'] != isolation['system_identifier'] or control['controlOid'] != isolation['control_oid'] or
            control['adminRole'] != isolation['admin_role'] or control['backendPids'] or control['preparedTransactions']):
        raise RuntimeError('Disposable target changed before restoration')
    if isolation['phase'] == 'reattached' and (not topology['attached'] or not control['allowConnections']):
        raise RuntimeError('Completed disposable topology regressed')
    if not topology['attached']:
        if isolation['phase'] != 'verified':
            raise RuntimeError('Disposable detached phase is invalid')
        if not control['allowConnections']:
            _check_lock(root, lock_pid, lock_token)
            set_disposable_admission(target, identity, True)
        control = inspect_control_database(target, identity, guard)
        if (not control['allowConnections'] or control['backendPids'] or control['preparedTransactions'] or
                control['targetOid'] != guard['isolation']['target_oid'] or
                control['systemIdentifier'] != guard['isolation']['system_identifier'] or
                control['controlOid'] != guard['isolation']['control_oid'] or control['adminRole'] != guard['isolation']['admin_role']):
            raise RuntimeError('Disposable admission restoration is unsafe')
        if authenticate_attempt(**arguments) != guard or authenticate_completion(root, witness_path, guard, receipt) != completion_sha:
            raise RuntimeError('Original disposable completion changed before reattachment')
        command = ['docker', '--context', target['context'], 'network', 'connect']
        for alias in identity['aliases']:
            command += ['--alias', alias]
        command += [identity['networkId'], identity['containerId']]
        _check_lock(root, lock_pid, lock_token)
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
        if result.returncode:
            raise RuntimeError('Disposable original network could not be restored')
    topology = inspect_owned_topology(target, expected_image, guard, trusted_daemon=trusted_daemon)
    control = inspect_control_database(target, identity, guard)
    if (not topology['attached'] or topology['original'] != identity or not control['allowConnections'] or
            control['backendPids'] or control['preparedTransactions']):
        raise RuntimeError('Disposable restored topology or admission differs')
    if isolation['phase'] == 'verified':
        _check_lock(root, lock_pid, lock_token)
        guard = advance_isolation_marker(target, guard_path, guard, identity, True, control, receipt=receipt)
    if guard['isolation']['phase'] != 'reattached':
        raise RuntimeError('Disposable reattachment marker is invalid')
    return guard, receipt, completion_sha


def complete_original_guard(*, target, root, guard_path, witness_path, guard, receipt, completion_sha, lock_pid, lock_token):
    """Publish the one-time verified transition under the existing target lock."""
    _check_lock(root, lock_pid, lock_token)
    if (guard.get('state') != 'pending' or (guard.get('isolation') or {}).get('phase') != 'reattached' or
            authenticate_completion(root, witness_path, guard, receipt) != completion_sha):
        raise RuntimeError('Original disposable attempt cannot be completed')
    receipt_path = Path(witness_path).parent / 'verified-receipt.json'
    if authenticate_existing_receipt(root, receipt_path, guard) != receipt:
        raise RuntimeError('Original disposable receipt changed before completion')
    source = """import {pathToFileURL} from 'node:url';import {readFileSync} from 'node:fs';
import {isDeepStrictEqual} from 'node:util';
let input='';for await(const part of process.stdin)input+=part;
const data=JSON.parse(input);const api=await import(pathToFileURL(data.runtime).href);
const deployment=await import(pathToFileURL(data.deployment).href);
api.syncDisposableIsolationEvidence({guardPath:data.guardPath,guard:data.guard,receiptPath:data.receiptPath,receipt:data.receipt});
if(!isDeepStrictEqual(JSON.parse(readFileSync(data.guardPath,'utf8')),data.guard))throw Error('guard changed');
const next=structuredClone(data.guard);next.state='verified';next.verified_schema=62;
next.verified_at=new Date().toISOString();next.isolation.completion={filename:'verified-completion.json',sha256:data.completionSha};
deployment.atomicJson(data.guardPath,next);
api.syncDisposableIsolationEvidence({guardPath:data.guardPath,guard:next,receiptPath:data.receiptPath,receipt:data.receipt});
console.log(JSON.stringify(next));"""
    payload = {'runtime': str(Path(target['root']) / 'scripts' / 'tempo-runtime.mjs'),
               'deployment': str(Path(target['root']) / 'scripts' / 'tempo-deployment.mjs'),
               'guardPath': str(guard_path), 'guard': guard, 'receiptPath': str(receipt_path),
               'receipt': receipt, 'completionSha': completion_sha}
    result = subprocess.run(['node', '--input-type=module', '-e', source], input=json.dumps(payload),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError('Original disposable completion could not be published')
    return json.loads(result.stdout)


def supervise(config):
    """Run or resume the single disposable attempt under its parent's lock."""
    if config.get('lock_pid') != os.getppid() or config.get('trusted_daemon') is not True:
        raise RuntimeError('Disposable supervisor parent authority is unavailable')
    keys = ('target', 'expected_image', 'root', 'guard_path', 'selection_path', 'binding_path',
            'witness_path', 'pre62_path', 'lock_pid', 'lock_token', 'trusted_daemon')
    common = {key: config.get(key) for key in keys}
    original = {key: common[key] for key in ('root', 'guard_path', 'selection_path', 'binding_path',
                                             'witness_path', 'pre62_path', 'lock_pid', 'lock_token')}
    guard = authenticate_attempt(**original, allow_verified=True)
    revision = config.get('approved_revision')
    if revision != config.get('maintenance_revision') or revision != guard.get('origin_revision'):
        raise RuntimeError('Disposable original candidate revision differs')
    verify_candidate_provenance(common['target'], revision)
    phase = (guard.get('isolation') or {}).get('phase')
    crash = config.get('crash')
    if crash not in (None, 'after-commit', 'after-receipt', 'before-completion', 'after-completion', 'after-reattach', 'after-guard'):
        raise RuntimeError('Disposable crash point is invalid')
    if guard['state'] == 'verified':
        topology = inspect_owned_topology(common['target'], common['expected_image'], guard,
                                          trusted_daemon=common['trusted_daemon'])
        receipt = authenticate_existing_receipt(common['root'], Path(common['witness_path']).parent / 'verified-receipt.json', guard)
        if (not topology['attached'] or receipt is None or
                authenticate_completion(common['root'], common['witness_path'], guard, receipt) !=
                (guard['isolation'].get('completion') or {}).get('sha256') or
                (guard['isolation'].get('completion') or {}).get('filename') != 'verified-completion.json'):
            raise RuntimeError('Original disposable completion is unavailable')
        control = inspect_control_database(common['target'], topology['original'], guard)
        if (not control['allowConnections'] or control['backendPids'] or control['preparedTransactions'] or
                control['targetOid'] != guard['isolation']['target_oid'] or
                control['targetName'] != guard['isolation']['target_name'] or
                control['systemIdentifier'] != guard['isolation']['system_identifier'] or
                control['controlOid'] != guard['isolation']['control_oid'] or
                control['controlName'] != guard['isolation']['control_name'] or
                control['adminRole'] != guard['isolation']['admin_role']):
            raise RuntimeError('Completed disposable target is not ready for first startup')
        verify_original_backup(common['target'], guard)
        sync_original_guard(common['target'], common['guard_path'], guard)
        return guard
    if phase in (None, 'intent'):
        guard = prepare_detached_target(**common)
        phase = guard['isolation']['phase']
    if phase in ('detached', 'verified'):
        topology = inspect_owned_topology(common['target'], common['expected_image'], guard,
                                          trusted_daemon=common['trusted_daemon'])
        if not topology['attached']:
            guard, receipt = run_fenced_helper(**common, maintenance_image=config.get('maintenance_image'),
                maintenance_revision=config.get('maintenance_revision'), pgpass_path=config.get('pgpass_path'),
                crash=crash if crash in ('after-commit', 'after-receipt') else None)
            if crash == 'before-completion':
                os._exit(79)
            guard, _ = publish_detached_completion(**common, receipt=receipt)
            if crash == 'after-completion':
                os._exit(80)
        elif phase != 'verified':
            raise RuntimeError('Detached disposable phase unexpectedly reattached')
    guard, receipt, completion_sha = restore_completed_target(**common)
    if crash == 'after-reattach':
        os._exit(81)
    guard = complete_original_guard(target=common['target'], root=common['root'], guard_path=common['guard_path'],
        witness_path=common['witness_path'], guard=guard, receipt=receipt, completion_sha=completion_sha,
        lock_pid=common['lock_pid'], lock_token=common['lock_token'])
    if crash == 'after-guard':
        os._exit(82)
    return guard


if __name__ == '__main__':
    import sys
    if len(sys.argv) != 1 or os.environ.get('TEMPO_TEST_INSTANCE') != 'disposable':
        raise SystemExit('Disposable supervisor requires its owned lifecycle harness')
    result = supervise(json.load(sys.stdin))
    print(json.dumps({'state': result['state'], 'attempt_id': result['attempt_id']}))
