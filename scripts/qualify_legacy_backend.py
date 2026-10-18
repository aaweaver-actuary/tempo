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

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bridge_qualification', ROOT / 'scripts/qualify_legacy_bridge.py')
shared = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shared)
WORK_SECONDS, CLEANUP_SECONDS = 900, 300
CASES = (
    'test_native_generated_accept_replay_restart_and_normal_study',
    'test_native_prior_issued_attempt_cannot_settle_later_acceptance',
    'test_native_module_effect_and_complete_operation_receipt_roll_back_together',
)
IMAGES = dict(helper='sha256:a095f09e905ecb00635b80387e17d195560a5752df83d970f94f1ada902ba3e2',
              postgres=shared.POSTGRES_IMAGE_ID, redis=None)
CAPS = dict(helper=dict(cpus=1, memory_mib=768, pids=128, tmpfs_mib=256),
            postgres=dict(cpus=.75, memory_mib=384, pids=128, tmpfs_mib=384),
            redis=dict(cpus=.1, memory_mib=64, pids=32, tmpfs_mib=32))


def describe():
    return dict(prepared_only=True, work_seconds=WORK_SECONDS, cleanup_seconds=CLEANUP_SECONDS,
        cases=list(CASES), caps=CAPS, total=dict(containers=3, cpus=1.85, memory_mib=1216, pids=288,
            extra_swap_mib=0, owned_internal_networks=1, published_ports=0),
        candidate_images=IMAGES, missing=['Redis immutable local image ID/provenance',
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
    return inside['artifacts']


def docker_arguments(kind, name, image, network):
    cap = CAPS[kind]
    arguments = ['run', '--name', name, '--label', 'tempo.legacy-bridge-owner=' + name,
        '--label', 'tempo.disposable=true', '--cpus', str(cap['cpus']), '--memory', str(cap['memory_mib']) + 'm',
        '--memory-swap', str(cap['memory_mib']) + 'm', '--pids-limit', str(cap['pids']), '--network', network]
    if kind == 'postgres':
        arguments += ['--tmpfs', '/var/lib/postgresql:rw,size=384m']
    else:
        arguments += ['--tmpfs', '/tmp:rw,size=' + str(cap['tmpfs_mib']) + 'm']
    return arguments


def inside():
    shared.verify_helper_caps()
    root, qualified = Path('/evidence'), Path('/qualified')
    manifest = json.loads((root / 'native-manifest.json').read_text())
    if os.environ.get('TEMPO_BRIDGE_NATIVE_OWNED') != manifest['identity']:
        raise RuntimeError('Explicit owned native helper required')
    sites = list((qualified / 'work/environment/lib').glob(f'python{sys.version_info.major}.{sys.version_info.minor}/site-packages'))
    if len(sites) != 1: raise RuntimeError('Qualified artifact Python ABI/environment missing')
    paths = [str(sites[0]), str(ROOT / 'backend'), str(ROOT / 'scripts')]
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
        origins[package] = dict(version=distribution.version, origin=str(origin))
    manifest['import_paths'] = paths
    shared.write(root / 'native-manifest.json', manifest)
    report = dict(complete=False, cases=list(CASES), artifact_origins=origins, dependencies=expected, commands=[])
    shared.write(root / 'inside.json', report)
    timer = shared.Deadline(840, report)  # Service startup and host work share the900s outer deadline.
    checker = ROOT / 'services/learning/application/qualification.py'
    bootstrap = ('import sys,runpy;sys.path[:0]=' + repr(paths) + ';'
        'sys.argv=[' + repr(str(checker)) + ',*sys.argv[1:]];runpy.run_path(' + repr(str(checker)) + ",run_name='__main__')")
    common = ['-c', str(ROOT / 'backend/pyproject.toml'), '-o', 'addopts=', '--import-mode=importlib',
              '-p', 'no:cacheprovider', '--maxfail=1', str(ROOT / 'backend/integration/legacy_bridge/test_native.py')]
    receipts = {}
    for stage, tail in [('collection', ['--collect-only', '-q']),
                        ('execution', ['-q', '--junitxml=' + str(root / 'native.xml'), '--basetemp=' + str(root / 'pytest')])]:
        receipt = root / ('native-' + stage + '.json')
        timer.run([sys.executable, '-I', '-c', bootstrap, str(receipt), *common, *tail],
                  cwd=root, log=root / ('native-' + stage + '.log'))
        receipts[stage] = json.loads(receipt.read_text())
        if len(receipts[stage]['collected']) != len(CASES): raise RuntimeError('Native fixed inventory changed')
    inventory = shared.load('backend_native_inventory', checker)
    report['validated_cases'] = inventory.validate_inventory(receipts['collection'], receipts['execution'], root / 'native.xml')
    report['complete'] = True
    shared.write(root / 'inside.json', report)


def host(options):
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
    report = dict(complete=False, cleanup_verified=False, identity=identity, source_head=head, plan=describe(),
        images=dict(helper=options.helper_image_id, postgres=options.postgres_image_id, redis=options.redis_image_id),
        artifact_hashes=artifacts, started_utc=datetime.now(timezone.utc).isoformat(), commands=[], names=names, network=network)
    timer = shared.Deadline(WORK_SECONDS - (time.monotonic() - started), report)
    private = directory / 'private'; private.mkdir(mode=0o700)
    password = secrets.token_urlsafe(24)
    (private / 'postgres.env').write_text('POSTGRES_PASSWORD=' + password + '\n')
    (private / 'pgpass').write_text('bridge-postgres:5432:*:postgres:' + password + '\n')
    for path in private.iterdir(): path.chmod(0o600)
    manifest = dict(identity=identity, source_head=head, postgres_host='bridge-postgres', redis_host='bridge-redis',
        admin_dsn='postgresql://postgres@bridge-postgres:5432/postgres', pgpass='/evidence/private/pgpass')
    shared.write(directory / 'native-manifest.json', manifest)
    shared.write(directory / 'host.json', report)
    try:
        for kind, image in report['images'].items():
            timer.run(['docker', 'image', 'inspect', image], log=directory / (kind + '-image.json'), maximum=15)
        timer.run(['docker', 'network', 'create', '--internal', '--label', 'tempo.legacy-bridge-owner=' + identity, network], maximum=15)
        for kind in ('postgres', 'redis'):
            arguments = docker_arguments(kind, names[kind], report['images'][kind], network)
            arguments += ['--detach', '--network-alias', 'bridge-' + kind]
            if kind == 'postgres': arguments += ['--env-file', str(private / 'postgres.env')]
            arguments += [report['images'][kind]]
            arguments += (['postgres', '-c', 'max_connections=32', '-c', 'shared_buffers=32MB'] if kind == 'postgres'
                else ['redis-server', '--save', '', '--appendonly', 'no', '--maxmemory', '32mb', '--maxmemory-policy', 'noeviction'])
            timer.run(['docker', *arguments], log=directory / (kind + '-start.log'), maximum=45)
            config = json.loads(timer.capture(['docker', 'inspect', names[kind], '--format', '{{json .HostConfig}}']).stdout)
            cap = CAPS[kind]
            if (config['NanoCpus'], config['Memory'], config['MemorySwap'], config['PidsLimit']) != (
                    int(cap['cpus'] * 1_000_000_000), cap['memory_mib'] * 1024**2, cap['memory_mib'] * 1024**2, cap['pids']):
                raise RuntimeError('Observed native service kernel caps differ: ' + kind)
            report.setdefault('observed_service_caps', {})[kind] = config
        arguments = docker_arguments('helper', names['helper'], options.helper_image_id, network)
        arguments += ['--init', '--mount', f'type=bind,source={ROOT},target=/source,readonly',
            '--mount', f'type=bind,source={options.artifact_run.resolve()},target=/qualified,readonly',
            '--mount', f'type=bind,source={directory},target=/evidence', '--workdir', '/evidence',
            '--env', 'TEMPO_BRIDGE_NATIVE_OWNED=' + identity, '--env', 'PYTHONDONTWRITEBYTECODE=1',
            '--env', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD=1', '--entrypoint', 'python', options.helper_image_id,
            '/source/scripts/qualify_legacy_backend.py', '--inside']
        timer.run(['docker', *arguments], log=directory / 'helper.log')
        if not json.loads((directory / 'inside.json').read_text())['complete']: raise RuntimeError('Incomplete native proof')
        report['complete'] = True
    except BaseException as error:
        report['error'] = type(error).__name__ + ': ' + str(error)
    finally:
        cleanup = shared.Deadline(CLEANUP_SECONDS, report)
        signal.setitimer(signal.ITIMER_REAL, CLEANUP_SECONDS)
        try:
            for name in (names['helper'], names['redis'], names['postgres']):
                probe = cleanup.capture(['docker', 'inspect', name, '--format', '{{json .Config.Labels}}'], check=False)
                if probe.returncode == 0:
                    if json.loads(probe.stdout).get('tempo.legacy-bridge-owner') != name: raise RuntimeError('Owned container label mismatch')
                    cleanup.run(['docker', 'stop', '--time', '5', name], check=False, maximum=15)
                    cleanup.run(['docker', 'rm', '--force', name], maximum=15)
                elif shared.verified_absence(probe, name):
                    cleanup.run(['docker', 'rm', '--force', name], check=False, maximum=15)
                else: raise RuntimeError('Owned container absence unverified')
                absent = cleanup.capture(['docker', 'inspect', name], check=False)
                if not shared.verified_absence(absent, name): raise RuntimeError('Owned container remains/absence unknown')
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
                for path in [private, *(directory / 'pytest').glob('*/private')]:
                    if path.exists():
                        if path.is_symlink() or not path.resolve().is_relative_to(directory):
                            raise RuntimeError('Private cleanup escaped the owned run')
                        shutil.rmtree(path)
            except BaseException as error:
                report['cleanup_verified'] = False
                report['cleanup_error'] = type(error).__name__ + ': ' + str(error)
        report['finished_utc'] = datetime.now(timezone.utc).isoformat()
        shared.write(directory / 'host.json', report)
        signal.setitimer(signal.ITIMER_REAL, 0)
    return 0 if report['complete'] and report['cleanup_verified'] else 1


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--describe', action='store_true'); parser.add_argument('--inside', action='store_true')
    parser.add_argument('--mode', choices=['backend-native'], default='backend-native')
    parser.add_argument('--approved-window', action='store_true')
    parser.add_argument('--helper-image-id', type=image_id); parser.add_argument('--postgres-image-id', type=image_id)
    parser.add_argument('--redis-image-id', type=image_id); parser.add_argument('--artifact-run', type=Path)
    parser.add_argument('--evidence', type=Path)
    options = parser.parse_args(arguments)
    if options.describe: print(json.dumps(describe(), indent=2)); return 0
    if options.inside: inside(); return 0
    if not all((options.helper_image_id, options.postgres_image_id, options.redis_image_id, options.artifact_run, options.evidence)):
        parser.error('Explicit immutable images, qualified artifact run and owned evidence directory required')
    return host(options)


if __name__ == '__main__':
    raise SystemExit(main())
