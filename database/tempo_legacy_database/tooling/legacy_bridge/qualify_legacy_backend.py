"""Prepared finite backend-native bridge window; no ambient/live target accepted.

Reuses the existing development Docker owner, Deadline and pytest inventory
checker. --describe is pure. A fresh explicit grant and qualified artifacts are
mandatory; no pull, build, install or fallback is implemented here.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location('bridge_qualification', ROOT / 'database/tempo_legacy_database/tooling/legacy_bridge/qualify_legacy_bridge.py')
shared = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shared)
WORK_SECONDS, CLEANUP_SECONDS = 900, 300
CASES = (
    'database/tempo_legacy_database/tests/legacy_bridge/test_native.py::test_native_generated_accept_replay_restart_and_normal_study',
    'database/tempo_legacy_database/tests/legacy_bridge/test_native.py::test_native_prior_issued_attempt_cannot_settle_later_acceptance[prior]',
    'database/tempo_legacy_database/tests/legacy_bridge/test_native.py::test_native_module_effect_and_complete_operation_receipt_roll_back_together',
    'database/tempo_legacy_database/tests/legacy_bridge/test_native.py::test_native_feedback_typed_projection_and_priority_effect_borrow_caller_transaction',
    'database/tempo_legacy_database/tests/legacy_bridge/test_native.py::test_native_feedback_card_and_day_locks_live_until_caller_transaction_ends',
    'database/tempo_legacy_database/tests/legacy_bridge/test_native.py::test_native_feedback_accepted_scope_exclusion_does_not_hide_other_repertoire',
)
IMAGES = dict(helper='sha256:a095f09e905ecb00635b80387e17d195560a5752df83d970f94f1ada902ba3e2',
              postgres=shared.POSTGRES_IMAGE_ID,
              redis='sha256:858f009f9709ce576febc734aa78b8f6d624b82571f9ddb6bda4377c833b3499')
CAPS = dict(helper=dict(memory_mib=768, pids=128, tmpfs_mib=256),
            postgres=dict(memory_mib=384, pids=128, tmpfs_mib=384),
            redis=dict(memory_mib=64, pids=32, tmpfs_mib=32, data_tmpfs_mib=32))


def allocated_caps(cpus):
    allocation = shared.cpu.partition_cpus(cpus, {"helper": 100, "postgres": 75, "redis": 10}) if cpus is not None else {kind: None for kind in CAPS}
    return {kind: {**cap, "cpus": allocation[kind]} for kind, cap in CAPS.items()}


def describe(cpus=None):
    return dict(prepared_only=True, work_seconds=WORK_SECONDS, cleanup_seconds=CLEANUP_SECONDS,
        cases=list(CASES), caps=allocated_caps(cpus), total=dict(containers=3, cpus=cpus, memory_mib=1216, pids=288,
            extra_swap_mib=0, owned_internal_networks=1, published_ports=0),
        candidate_images=IMAGES, missing=['Redis candidate image/mount provenance verified during a later authorized preflight',
            'successful same-head284-case artifact receipt and retained qualified environment',
            'helper-start diagnostic result, independent review and separate native resource grant'],
        no_build_pull_install_or_live_target=True)


def image_id(value):
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', value):
        raise ValueError('An explicitly approved already-local immutable image ID is required')
    return value


def qualified_artifacts(directory, head, helper_image):
    directory = directory.resolve()
    host, inside = (json.loads((directory / name).read_text()) for name in ('host.json', 'inside.json'))
    if (not host.get('complete') or not host.get('cleanup_verified') or host['source_head'] != head
            or host['image'] != helper_image or not inside.get('complete')):
        raise ValueError('Successful cleaned same-head artifact qualification is required')
    if len(inside['artifacts']) != 12 or host['plan']['artifact_test_total'] != 284:
        raise ValueError('The reviewed284-case/six-wheel/six-sdist qualification is required')
    for name, expected in inside['artifacts'].items():
        if Path(name).name != name or hashlib.sha256((directory / 'wheels' / name).read_bytes()).hexdigest() != expected:
            raise ValueError('Qualified artifact bytes changed')
    shared.staging.qualified_inputs(directory, host)
    if shared.staging.verify_backend_environment(directory) != inside.get('backend_dependency_wheels'):
        raise ValueError('Current backend dependency wheel/install proof missing or changed')
    return inside['artifacts']


def docker_arguments(kind, name, image, network, *, caps):
    cap = caps[kind]
    arguments = ['run', '--name', name, '--label', 'tempo.legacy-bridge-owner=' + name,
        '--label', 'tempo.disposable=true', '--cpus', str(cap['cpus']), '--memory', str(cap['memory_mib']) + 'm',
        '--memory-swap', str(cap['memory_mib']) + 'm', '--pids-limit', str(cap['pids']), '--network', network]
    for destination, size in tmpfs_mounts(kind).items():
        arguments += ['--tmpfs', destination + ':rw,size=' + str(size) + 'm']
    return arguments


def tmpfs_mounts(kind):
    result = {'/var/lib/postgresql' if kind == 'postgres' else '/tmp': CAPS[kind]['tmpfs_mib']}
    if kind == 'redis': result['/data'] = CAPS[kind]['data_tmpfs_mib']
    return result


def verify_image_volumes(kind, image):
    declared = set((image.get('Config') or {}).get('Volumes') or {})
    allowed = set(tmpfs_mounts(kind)) | ({'/source', '/qualified', '/evidence'} if kind == 'helper' else set())
    if not declared <= allowed: raise ValueError('Image declares an uncovered persistent volume: ' + kind)
    return sorted(declared)


def verify_container_mounts(kind, container, binds=None):
    """HostConfig owns tmpfs configuration; Mounts owns actual persistent mounts.

    Docker versions need not repeat tmpfs in Mounts. Both inventories must agree
    with our explicit tmpfs/bind plan, and no volume mount is ever permitted.
    """
    verify_image_volumes(kind, container)
    expected, binds = tmpfs_mounts(kind), binds or {}
    actual = container['HostConfig'].get('Tmpfs') or {}
    if set(actual) != set(expected): raise ValueError('Native tmpfs destinations changed: ' + kind)
    for destination, options in actual.items():
        parts = options.split(','); sizes = [item[5:] for item in parts if item.startswith('size=')]
        match = re.fullmatch(r'(\d+)([kmg]?)', sizes[0], re.IGNORECASE) if len(sizes) == 1 else None
        size = int(match[1]) * {'': 1, 'k': 1024, 'm': 1024**2, 'g': 1024**3}[match[2].lower()] if match else None
        if 'ro' in parts or size != expected[destination] * 1024**2:
            raise ValueError('Native tmpfs is unbounded/different: ' + destination)
    seen, observed_binds = set(), {}
    for mount in container['Mounts']:
        destination, kind_name = mount['Destination'], mount['Type']
        if destination in seen: raise ValueError('Duplicate actual mount destination')
        seen.add(destination)
        if kind_name == 'bind': observed_binds[destination] = (mount['Source'], mount['RW'])
        elif kind_name != 'tmpfs' or destination not in expected or mount.get('RW') is not True:
            raise ValueError('Unexpected persistent/unknown native mount: ' + destination)
    if observed_binds != binds: raise ValueError('Native bind inventory changed: ' + kind)
    return dict(tmpfs=actual, mounts=container['Mounts'])


def mounted_volume_names(container):
    names = {item['Name'] for item in container['Mounts'] if item['Type'] == 'volume'}
    if any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,255}', name) for name in names):
        raise ValueError('Unexpected volume identity; cleanup cannot be verified')
    return names


def verify_volume_absence(timer, names):
    for name in sorted(names):
        result = timer.capture(['docker', 'volume', 'inspect', name], check=False)
        if not shared.verified_absence(result, name): raise RuntimeError('Recorded native volume remains/absence unknown: ' + name)
    return sorted(names)


def verify_case_inventory(receipt):
    if sorted(receipt['collected']) != sorted(CASES):
        raise RuntimeError('Exact native node IDs changed; stop and rescope')


verify_installed_wheel = shared.staging.verify_installed_wheel


def inside():
    shared.staging.verify_source(ROOT, os.environ.get('TEMPO_QUALIFICATION_SOURCE_SHA256'))
    shared.verify_helper_caps()
    root, qualified = Path('/evidence'), Path('/qualified')
    manifest = json.loads((root / 'native-manifest.json').read_text())
    if os.environ.get('TEMPO_BRIDGE_NATIVE_OWNED') != manifest['identity']:
        raise RuntimeError('Explicit owned native helper required')
    # Ignore retained bytecode caches while importing wheel-verified source.
    sys.dont_write_bytecode = True
    sys.pycache_prefix = '/tmp/' + manifest['identity'] + '-verified-bytecode'
    sites = list((qualified / 'work/environment/lib').glob(f'python{sys.version_info.major}.{sys.version_info.minor}/site-packages'))
    if len(sites) != 1: raise RuntimeError('Qualified artifact Python ABI/environment missing')
    backend_dependencies = shared.staging.verify_backend_environment(qualified)
    paths = [str(sites[0]), str(ROOT / 'backend'), str(ROOT / 'database'), str(ROOT / 'scripts')]
    sys.path[:0] = paths
    from importlib import metadata
    expected = dict(fsrs='6.3.2', celery='5.6.3', redis='6.4.0', psycopg='3.3.6',
        **{'psycopg-pool': '3.3.3', 'sqlglot': '30.19.0', 'fastapi': '0.116.1',
           'python-multipart': '0.0.20', 'uvicorn': '0.35.0', 'httpx': '0.28.1',
           'pytest': '9.1.1', 'chess': '1.11.2', 'pydantic': '2.12.5',
           'tempo-scheduling': '0.2.0', 'tempo-learning-review': '0.1.0', 'tempo-learning-runtime': '0.1.0'})
    for package, version in expected.items():
        if metadata.version(package) != version: raise RuntimeError('Missing/different native dependency: ' + package)
    origins = {}
    for package in ('tempo-game-evidence-contracts', 'tempo-content-publication-contracts',
                    'tempo-chess-core', 'tempo-learning-remediation', 'tempo-game-evidence', 'tempo-a-bridge'):
        distribution = metadata.distribution(package)
        origin = Path(distribution.locate_file('')).resolve()
        if not origin.is_relative_to(sites[0]): raise RuntimeError('Native helper did not import qualified artifact: ' + package)
        wheels = list((qualified / 'wheels').glob(package.replace('-', '_') + '-*.whl'))
        if len(wheels) != 1: raise RuntimeError('Exactly one qualified wheel required: ' + package)
        wheel = wheels[0]
        if hashlib.sha256(wheel.read_bytes()).hexdigest() != manifest['artifact_hashes'].get(wheel.name):
            raise RuntimeError('Native qualified wheel bytes changed: ' + package)
        origins[package] = dict(version=distribution.version, origin=str(origin),
                               content=verify_installed_wheel(wheel, sites[0]))
    manifest['import_paths'] = paths
    manifest['bytecode_prefix'] = sys.pycache_prefix
    shared.write(root / 'native-manifest.json', manifest)
    report = dict(complete=False, cases=list(CASES), artifact_origins=origins, dependencies=expected,
                  backend_dependency_wheels=backend_dependencies, commands=[])
    shared.write(root / 'inside.json', report)
    timer = shared.Deadline(840, report, receipt=root / 'inside.json')  # Same outer deadline.
    checker = ROOT / 'services/learning/application/qualification.py'
    bootstrap = ('import sys,runpy;sys.pycache_prefix=' + repr(sys.pycache_prefix) + ';sys.path[:0]=' + repr(paths) + ';'
        'sys.argv=[' + repr(str(checker)) + ',*sys.argv[1:]];runpy.run_path(' + repr(str(checker)) + ",run_name='__main__')")
    common = ['-c', str(ROOT / 'backend/pyproject.toml'), '-o', 'addopts=', '--import-mode=importlib', '--rootdir=' + str(ROOT),
              '-p', 'no:cacheprovider', '--maxfail=1', str(ROOT / 'database/tempo_legacy_database/tests/legacy_bridge/test_native.py')]
    try:
        receipts = {}
        for stage, tail in [('collection', ['--collect-only', '-q']),
                            ('execution', ['-q', '--junitxml=' + str(root / 'native.xml'), '--basetemp=' + str(root / 'pytest')])]:
            receipt = root / ('native-' + stage + '.json')
            timer.run([sys.executable, '-I', '-B', '-c', bootstrap, str(receipt), *common, *tail],
                      cwd=root, log=root / ('native-' + stage + '.log'))
            receipts[stage] = json.loads(receipt.read_text())
            verify_case_inventory(receipts[stage])
        inventory = shared.load('backend_native_inventory', checker)
        report['validated_cases'] = inventory.validate_inventory(receipts['collection'], receipts['execution'], root / 'native.xml')
        report['complete'] = True
    finally:
        shared.write(root / 'inside.json', report)


def host(options):
    shared.cpu.require_managed_docker()
    total_cpus = shared.cpu.selected_cpus(options.cpus)
    caps = allocated_caps(total_cpus)
    if not options.approved_window or not os.environ.get('TEMPO_DEVELOPMENT_BUDGET_TOKEN'):
        raise ValueError('Fresh parent-approved finite window and managed budget owner are required')
    started = time.monotonic()
    def expired(*_args): raise TimeoutError('Finite backend native work/cleanup phase expired')
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, WORK_SECONDS)
    if subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True).strip():
        raise ValueError('Native qualification requires a clean pinned feature head')
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    artifacts = qualified_artifacts(options.artifact_run, head, options.helper_image_id)
    identity = 'tempo-bridge-backend-' + uuid.uuid4().hex
    directory = options.evidence.resolve() / identity
    directory.mkdir(parents=True, exist_ok=False)
    network, names = identity + '-network', {kind: identity + '-' + kind for kind in CAPS}
    report = dict(complete=False, cleanup_verified=False, identity=identity, source_head=head, plan=describe(total_cpus),
        images=dict(helper=options.helper_image_id, postgres=options.postgres_image_id, redis=options.redis_image_id),
        artifact_hashes=artifacts, started_utc=datetime.now(timezone.utc).isoformat(), commands=[], names=names, network=network,
        mount_inventory=dict(images={}, containers={}, cleanup={}), container_ids={})
    observed_volumes = set()
    def observe_container(kind, container, phase):
        name = names[kind]
        if container['Config']['Labels'].get('tempo.legacy-bridge-owner') != name:
            raise RuntimeError('Owned container label mismatch')
        if kind in report['container_ids'] and report['container_ids'][kind] != container['Id']:
            raise RuntimeError('Owned container identity changed')
        report['container_ids'][kind] = container['Id']
        report['mount_inventory'][phase][kind] = dict(image=container['Image'],
            declared_volumes=container['Config'].get('Volumes') or {},
            tmpfs=container['HostConfig'].get('Tmpfs'), mounts=container['Mounts'])
        observed_volumes.update(mounted_volume_names(container))
        report['observed_volume_names'] = sorted(observed_volumes)
        shared.write(directory / 'host.json', report)
    timer = shared.Deadline(WORK_SECONDS - (time.monotonic() - started), report, receipt=directory / 'host.json')
    stage = None
    private = None
    try:
        stage = shared.staging.OwnedStaging(identity, head)
        report['staging'] = stage.record
        shared.write(directory / 'host.json', report)
        tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT, text=True).split('\0')
        stage.stage_source(ROOT, filter(None, tracked), shared.SOURCE_PACKAGES)
        prior = json.loads((options.artifact_run / 'host.json').read_text())
        stage.stage_qualified(options.artifact_run.resolve(), shared.staging.qualified_inputs(options.artifact_run.resolve(), prior))
        private = stage.output / 'private'; private.mkdir(mode=0o700)
        password = secrets.token_urlsafe(24)
        (private / 'postgres.env').write_text('POSTGRES_PASSWORD=' + password + '\n')
        (private / 'pgpass').write_text('bridge-postgres:5432:*:postgres:' + password + '\n')
        for path in private.iterdir(): path.chmod(0o600)
        manifest = dict(identity=identity, source_head=head, artifact_hashes=artifacts,
            postgres_host='bridge-postgres', redis_host='bridge-redis',
            admin_dsn='postgresql://postgres@bridge-postgres:5432/postgres', pgpass='/evidence/private/pgpass')
        shared.write(stage.output / 'native-manifest.json', manifest)
        shared.write(directory / 'host.json', report)
        for kind, image in report['images'].items():
            images = json.loads(timer.capture(['docker', 'image', 'inspect', image]).stdout)
            shared.write(directory / (kind + '-image.json'), images)
            if len(images) != 1 or images[0]['Id'] != image: raise RuntimeError('Native image identity changed')
            declared = verify_image_volumes(kind, images[0])
            report['mount_inventory']['images'][kind] = dict(image=image, architecture=images[0]['Architecture'],
                os=images[0]['Os'], declared_volumes=declared)
            shared.write(directory / 'host.json', report)
        timer.run(['docker', 'network', 'create', '--internal', '--label', 'tempo.legacy-bridge-owner=' + identity, network], maximum=15)
        for kind in ('postgres', 'redis'):
            arguments = docker_arguments(kind, names[kind], report['images'][kind], network, caps=caps)
            arguments += ['--detach', '--network-alias', 'bridge-' + kind]
            if kind == 'postgres': arguments += ['--env-file', str(private / 'postgres.env')]
            arguments += [report['images'][kind]]
            arguments += (['postgres', '-c', 'max_connections=32', '-c', 'shared_buffers=32MB'] if kind == 'postgres'
                else ['redis-server', '--save', '', '--appendonly', 'no', '--maxmemory', '32mb', '--maxmemory-policy', 'noeviction'])
            timer.run(['docker', *arguments], log=directory / (kind + '-start.log'), maximum=45)
            container = json.loads(timer.capture(['docker', 'inspect', names[kind]]).stdout)[0]
            observe_container(kind, container, 'containers')
            if container['Image'] != report['images'][kind]: raise RuntimeError('Native service image changed')
            verify_container_mounts(kind, container)
            config = container['HostConfig']
            cap = caps[kind]
            if (config['NanoCpus'], config['Memory'], config['MemorySwap'], config['PidsLimit']) != (
                    round(cap['cpus'] * 1_000_000_000), cap['memory_mib'] * 1024**2, cap['memory_mib'] * 1024**2, cap['pids']):
                raise RuntimeError('Observed native service kernel caps differ: ' + kind)
            report.setdefault('observed_service_caps', {})[kind] = config
        arguments = docker_arguments('helper', names['helper'], options.helper_image_id, network, caps=caps)
        arguments += ['--init', '--mount', f'type=bind,source={stage.source},target=/source,readonly',
            '--mount', f'type=bind,source={stage.qualified},target=/qualified,readonly',
            '--mount', f'type=bind,source={stage.output},target=/evidence', '--workdir', '/evidence',
            '--env', 'TEMPO_QUALIFICATION_SOURCE_SHA256=' + stage.record['source_manifest_sha256'],
            '--env', 'TEMPO_QUALIFICATION_HELPER_CPUS=' + str(caps['helper']['cpus']),
            '--env', 'TEMPO_QUALIFICATION_CPUS=' + str(total_cpus),
            '--env', 'TEMPO_BRIDGE_NATIVE_OWNED=' + identity, '--env', 'PYTHONDONTWRITEBYTECODE=1',
            '--env', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD=1', '--entrypoint', 'python', options.helper_image_id,
            '/source/database/tempo_legacy_database/tooling/legacy_bridge/qualify_legacy_backend.py', '--inside']
        timer.run(['docker', *arguments], log=directory / 'helper.log')
        container = json.loads(timer.capture(['docker', 'inspect', names['helper']]).stdout)[0]
        observe_container('helper', container, 'containers')
        if container['Image'] != options.helper_image_id: raise RuntimeError('Native helper image changed')
        verify_container_mounts('helper', container, {'/source': (str(stage.source), False),
            '/qualified': (str(stage.qualified), False), '/evidence': (str(stage.output), True)})
        if not json.loads((stage.output / 'inside.json').read_text())['complete']: raise RuntimeError('Incomplete native proof')
        report['complete'] = True
    except BaseException as error:
        report['error'] = type(error).__name__ + ': ' + str(error)
    finally:
        cleanup = shared.Deadline(CLEANUP_SECONDS, report)
        signal.setitimer(signal.ITIMER_REAL, CLEANUP_SECONDS)
        try:
            for kind in ('helper', 'redis', 'postgres'):
                name = names[kind]
                probe = cleanup.capture(['docker', 'inspect', name], check=False)
                if probe.returncode == 0:
                    observe_container(kind, json.loads(probe.stdout)[0], 'cleanup')
                    cleanup.run(['docker', 'stop', '--time', '5', name], check=False, maximum=15)
                    cleanup.run(['docker', 'rm', '--force', '--volumes', name], maximum=15)
                elif shared.verified_absence(probe, name):
                    cleanup.run(['docker', 'rm', '--force', '--volumes', name], check=False, maximum=15)
                else: raise RuntimeError('Owned container absence unverified')
                absent = cleanup.capture(['docker', 'inspect', name], check=False)
                if not shared.verified_absence(absent, name): raise RuntimeError('Owned container remains/absence unknown')
            report['verified_volume_absence'] = verify_volume_absence(cleanup, observed_volumes)
            probe = cleanup.capture(['docker', 'network', 'inspect', network], check=False)
            if probe.returncode == 0:
                if json.loads(probe.stdout)[0]['Labels'].get('tempo.legacy-bridge-owner') != identity: raise RuntimeError('Owned network label mismatch')
                cleanup.run(['docker', 'network', 'rm', network], maximum=15)
            elif not shared.verified_absence(probe, network): raise RuntimeError('Owned network absence unverified')
            absent = cleanup.capture(['docker', 'network', 'inspect', network], check=False)
            if not shared.verified_absence(absent, network): raise RuntimeError('Owned network remains/absence unknown')
            report['cleanup_verified'] = True
        except BaseException as error:
            report['cleanup_error'] = type(error).__name__ + ': ' + str(error)
        if report['cleanup_verified']:
            # A failed pytest teardown can leave disposable case passfiles.
            # Remove only private directories under this freshly owned run,
            # after all its cgroups and network are independently absent.
            try:
                for path in ([private, *(stage.output / 'pytest').glob('*/private')] if stage is not None else []):
                    if path is not None and path.exists():
                        if path.is_symlink() or not path.resolve().is_relative_to(stage.output):
                            raise RuntimeError('Private cleanup escaped the owned run')
                        shutil.rmtree(path)
            except BaseException as error:
                report['cleanup_verified'] = False
                report['cleanup_error'] = type(error).__name__ + ': ' + str(error)
        if stage is not None:
            try:
                stage.copy_back(stage.output, directory)
                stage.remove(resources_absent=report['cleanup_verified'])
            except BaseException as error:
                report['complete'] = False
                report['staging_error'] = type(error).__name__ + ': ' + str(error)
                report['staging_retained'] = str(stage.root)
        report['finished_utc'] = datetime.now(timezone.utc).isoformat()
        shared.write(directory / 'host.json', report)
        signal.setitimer(signal.ITIMER_REAL, 0)
    return 0 if report['complete'] and report['cleanup_verified'] else 1


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cpus', type=shared.cpu.positive_cpu, help='Selected total CPU allocation; defaults to central daemon allocation')
    parser.add_argument('--describe', action='store_true'); parser.add_argument('--inside', action='store_true')
    parser.add_argument('--mode', choices=['backend-native'], default='backend-native')
    parser.add_argument('--approved-window', action='store_true')
    parser.add_argument('--helper-image-id', type=image_id); parser.add_argument('--postgres-image-id', type=image_id)
    parser.add_argument('--redis-image-id', type=image_id); parser.add_argument('--artifact-run', type=Path)
    parser.add_argument('--evidence', type=Path)
    options = parser.parse_args(arguments)
    if options.describe: print(json.dumps(describe(options.cpus), indent=2)); return 0
    def interrupted(signum, frame): raise KeyboardInterrupt('Finite qualification interrupted')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    if options.inside: inside(); return 0
    if not all((options.helper_image_id, options.postgres_image_id, options.redis_image_id, options.artifact_run, options.evidence)):
        parser.error('Explicit immutable images, qualified artifact run and owned evidence directory required')
    return host(options)


if __name__ == '__main__':
    raise SystemExit(main())
