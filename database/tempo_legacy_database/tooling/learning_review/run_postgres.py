"""One invocation-owned PostgreSQL cluster; never accepts a database URL."""
from pathlib import Path
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
APPLICATION = ROOT / "services/learning/application"
sys.path.insert(0, str(APPLICATION))
from qualification import controlled_environment, junit_destination, validate_inventory, write_json


def docker(*arguments, check=True):
    return subprocess.run(['docker', *arguments], capture_output=True, text=True, check=check)


def matching_containers(identity):
    # A failed daemon query is not evidence that the resource is absent.
    return docker('container', 'ls', '--all', '--filter', 'name=^/'+identity+'$',
                  '--format', '{{.ID}}').stdout.split()


def cleanup_container(identity, resource):
    if not resource['creation_attempted']:
        return
    resource['cleanup_verified'] = False
    for container in matching_containers(identity):
        labels = json.loads(docker('inspect', container, '--format', '{{json .Config.Labels}}').stdout)
        if not labels or labels.get('tempo.owner') != identity or labels.get('tempo.disposable') != 'true':
            raise RuntimeError('Refusing cleanup after ownership/disposable label mismatch')
        docker('rm', '--force', container)
        resource['container_removed'] = True
    if matching_containers(identity):
        raise RuntimeError('Owned test container survived cleanup')
    resource.update(container_absent=True, cleanup_verified=True)


def provision_owned_cluster(identity, password):
    port = docker('inspect', identity, '--format', '{{(index (index .NetworkSettings.Ports "5432/tcp") 0).HostPort}}').stdout.strip()
    import psycopg
    from psycopg.conninfo import make_conninfo
    admin = make_conninfo(host='127.0.0.1',port=port,dbname='postgres',user='postgres',password=password)
    deadline = time.monotonic() + 30
    while True:
        try:
            with psycopg.connect(admin, connect_timeout=1) as ready:
                ready.execute('SELECT 1')
            break
        except psycopg.OperationalError:
            if time.monotonic() > deadline:
                raise RuntimeError('Owned PostgreSQL TCP endpoint did not become ready') from None
            time.sleep(.1)
    sys.path[:0] = [str(ROOT), str(ROOT/'scripts'), str(ROOT/'backend'), str(ROOT/'database')]
    from database.tooling.apply_postgres_migrations import apply_migrations
    from database.tooling.provision_postgres_roles import provision
    apply_migrations(admin)
    reader_password, writer_password = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    provision(admin, reader_password, writer_password)
    with psycopg.connect(admin) as database:
        database.execute('INSERT INTO settings(id) VALUES(1)')
    environment = {key:value for key,value in controlled_environment().items()
                   if not key.startswith(('TEMPO_DATABASE_', 'TEMPO224_', 'PG', 'TEMPO_REDIS_URL', 'CELERY_'))}
    environment.update(TEMPO224_FIXTURE=identity, TEMPO224_ADMIN=admin,
        TEMPO_DATABASE_READ_URL=make_conninfo(admin,user='tempo_reader',password=reader_password),
        TEMPO_DATABASE_WRITE_URL=make_conninfo(admin,user='tempo_writer',password=writer_password),
        TEMPO_TEST_INSTANCE='disposable', TEMPO_POSTGRES_POOL_SIZE='8',
        TEMPO_REDIS_URL='redis://127.0.0.1:1/0',
        PYTHONPATH=os.pathsep.join((str(ROOT/'backend'),str(ROOT/'scripts'),str(ROOT/'database'),str(ROOT))))
    return environment


def run_required_tests(environment, run_directory, external_junit, report):
    junit = external_junit or run_directory/'native.xml'
    junit.parent.mkdir(parents=True, exist_ok=True)
    arguments = ['-c',str(APPLICATION/'pyproject.toml'),'-o','addopts=',
                 '-p','no:cacheprovider',str(ROOT/'database/tempo_legacy_database/tests/learning_review/test_postgres.py')]
    receipts = {}
    for stage, extra in (('collected',['--collect-only','-q']),
                         ('executed',['-v','--tb=short','--junitxml='+str(junit)])):
        receipt = run_directory/('native-'+stage+'.json')
        command = [sys.executable,str(APPLICATION/'qualification.py'),str(receipt),*arguments,*extra]
        result = subprocess.run(command, env=environment, cwd=ROOT, capture_output=True,text=True)
        (run_directory/('native-'+stage+'.log')).write_text(result.stdout+result.stderr)
        report['commands'].append(command)
        report['collection_exit_code' if stage == 'collected' else 'pytest_exit_code'] = result.returncode
        print(result.stdout, end='')
        print(result.stderr, end='', file=sys.stderr)
        if stage == 'executed' and junit.exists():
            if junit != run_directory/'native.xml':
                shutil.copy2(junit, run_directory/'native.xml')
            shutil.copy2(run_directory/'native.xml', run_directory.parents[1]/'native.xml')
        if result.returncode:
            raise RuntimeError(f'Native {stage} failed with exit {result.returncode}; see invocation logs')
        receipts[stage] = json.loads(receipt.read_text())
    identities = validate_inventory(receipts['collected'], receipts['executed'], junit)
    report.update(identities=identities, passed=len(identities))


def publish_reports(directory, report):
    resource = {key:report[key] for key in ('identity','image','complete','exit_code','pytest_exit_code',
        'error','cleanup_error','external_database_accepted')}
    resource.update(report['resource'])
    run_directory = report.get('run_directory')
    if run_directory:
        write_json(Path(run_directory)/'native-resource.json', resource)
        write_json(Path(run_directory)/'native.json', report)
    write_json(directory/'native-resource.json', resource)
    # Publish the authoritative success marker last, after cleanup and receipts.
    write_json(directory/'native.json', report)


def main(arguments=None):
    arguments = sys.argv[1:] if arguments is None else arguments
    directory = Path(arguments[0]).resolve() if arguments else Path(os.environ.get(
        'TEMPO_LEARNING_EVIDENCE_DIR', str(ROOT/'test-results/learning-review-224'))).resolve()
    identity = 'tempo224-' + secrets.token_hex(8)
    report = {'identity':identity,'image':None,'complete':False,'exit_code':1,
              'process':{'pid':os.getpid(),'pgid':os.getpgid(0)},
              'pytest_exit_code':None,'collection_exit_code':None,'error':None,'cleanup_error':None,
              'external_database_accepted':False,'commands':[],'runtime':sys.version,
              'resource':{'creation_attempted':False,'container_removed':False,
                          'container_absent':None,'cleanup_verified':True}}
    validated = False
    try:
        if directory.exists():
            # Preserve previous summaries, while invalidating their current status
            # even when this invocation is rejected before allocating resources.
            history = directory/'history'/identity
            for name in ('native.json','native-resource.json','native.xml'):
                previous = directory/name
                if previous.exists():
                    history.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(previous, history/name)
            publish_reports(directory, report)
        if len(arguments) > 1:
            raise ValueError('Expected at most one evidence directory')
        external_junit = junit_destination(os.environ.get('PYTEST_ADDOPTS', ''), allow_junit=True)
        directory.mkdir(parents=True, exist_ok=True)
        run_directory = directory/'runs'/identity
        run_directory.mkdir(parents=True)
        report.update(run_directory=str(run_directory), external_junit=str(external_junit) if external_junit else None,
            revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip())
        publish_reports(directory, report)
        report['image'] = docker('image','inspect','postgres:18.6-trixie','--format','{{.Id}}').stdout.strip()
        if matching_containers(identity):
            raise RuntimeError('Generated test container identity already exists; refusing reuse')
        password = secrets.token_urlsafe(32)
        with tempfile.TemporaryDirectory(prefix='tempo224-secret-') as secret_directory:
            secret_file = Path(secret_directory)/'postgres.env'
            secret_file.write_text('POSTGRES_PASSWORD='+password+'\n')
            secret_file.chmod(0o600)
            report['resource']['creation_attempted'] = True
            docker('run','--detach','--name',identity,'--label','tempo.owner='+identity,
                   '--label','tempo.disposable=true','--memory','384m','--cpus','1',
                   '--pids-limit','256','--tmpfs','/var/lib/postgresql:rw,size=512m',
                   '--publish','127.0.0.1::5432','--env-file',str(secret_file),report['image'],
                   'postgres','-c','shared_buffers=32MB','-c','max_connections=32')
        report['resource']['observed_caps'] = json.loads(docker('container','inspect',identity,'--format',
            '{"cpu_nanos":{{.HostConfig.NanoCpus}},"memory_bytes":{{.HostConfig.Memory}}}').stdout)
        assert report['resource']['observed_caps'] == {'cpu_nanos':1_000_000_000,'memory_bytes':384*1024*1024}
        publish_reports(directory, report)
        environment = provision_owned_cluster(identity, password)
        run_required_tests(environment, run_directory, external_junit, report)
        validated = True
    except Exception as error:
        report['error'] = f'{type(error).__name__}: {error}'
    finally:
        try:
            cleanup_container(identity, report['resource'])
        except Exception as error:
            report['cleanup_error'] = f'{type(error).__name__}: {error}'
        report['complete'] = validated and report['error'] is None and report['cleanup_error'] is None and report['resource']['cleanup_verified']
        report['exit_code'] = 0 if report['complete'] else 1
        if directory.exists():
            publish_reports(directory, report)
    for key in ('error','cleanup_error'):
        if report[key]:
            print(report[key], file=sys.stderr)
    return report['exit_code']


if __name__ == '__main__':
    raise SystemExit(main())
