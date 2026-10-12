"""Audit-only disposable runtime owner; no live-data writes or production edits."""
import argparse, datetime, json, os, pathlib, secrets, shutil, subprocess, time, urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[3]
EVIDENCE = pathlib.Path('/Users/andy/tempo/test-results/audit-218-2026-10-12')
STATE = ROOT / '.audit-218-state'
PROJECT = 'tempo-pg-regressions-21800-a2182026'
SERVICES = ['postgres', 'redis', 'schema', 'api', 'foreground-worker', 'background-worker', 'background-scheduler', 'web']
ENV = dict(os.environ, TEMPO_PG_TEST_SECRETS=str(STATE), TEMPO_PG_TEST_PORT='4183')
COMPOSE = ['docker', 'compose', '-p', PROJECT, '-f', str(ROOT/'docker-compose.postgres.test.yml'), '-f', str(STATE/'override.json')]

def run(args, *, check=True, timeout=600, output=None):
    result = subprocess.run(args, cwd=ROOT, env=ENV, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    if output:
        (EVIDENCE/output).write_text(result.stdout)
    if check and result.returncode:
        raise RuntimeError(f'{args[:5]} exited {result.returncode}: {result.stdout[-3000:]}')
    return result

def compose(*args, **kwargs):
    return run(COMPOSE+list(args), **kwargs)

def http(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={'X-Tempo-Work-Class':'background'}), timeout=3) as response:
        return json.load(response)

def ready(url, seconds=120):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        try:
            return http(url)
        except Exception:
            time.sleep(.5)
    raise RuntimeError('Readiness deadline exceeded: '+url)

def prepare():
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    if STATE.exists():
        raise RuntimeError('Audit state exists; inspect ownership before reuse')
    if run(['docker','ps','-aq','--filter',f'label=com.docker.compose.project={PROJECT}']).stdout.strip():
        raise RuntimeError('Audit project already exists')
    STATE.mkdir(mode=0o700)
    for role in ['admin','reader','writer']:
        password=secrets.token_hex(24)
        (STATE/(role+'_password')).write_text(password+'\n')
        user='postgres' if role=='admin' else 'tempo_'+role
        (STATE/(role+'_pgpass')).write_text(f'postgres:5432:*:{user}:{password}\n')
    for path in STATE.iterdir(): path.chmod(0o600)
    override={'services':{}}
    for name in ['schema','api','foreground-worker','background-worker','background-scheduler']:
        override['services'][name]={'volumes':[str(ROOT/'docs/architecture/audit-218')+':/audit:ro']}
    override['services']['api']['ports']=['127.0.0.1:4182:8000']
    override['services']['background-worker'].update({'cpus':1,'command':['python','/audit/tasks.py']})
    for name in ['api','foreground-worker','background-worker']:
        override['services'][name]['environment']={'TEMPO_TRAINING_TIMINGS':'0'}
    (STATE/'override.json').write_text(json.dumps(override))
    compose('build','schema','api','foreground-worker','background-worker','background-scheduler','web',output='build.log')
    # Images are built before interruption. No disposable application is started here.
    (EVIDENCE/'prepared.json').write_text(json.dumps({'project':PROJECT,'revision':run(['git','rev-parse','HEAD']).stdout.strip(),'override_sha256':__import__('hashlib').sha256((STATE/'override.json').read_bytes()).hexdigest(),'services':SERVICES},indent=2))

def inspect_live():
    identifiers=run(['docker','ps','-q','--filter','label=com.docker.compose.project=tempo']).stdout.split()
    if not identifiers: raise RuntimeError('Expected running live study stack')
    raw=json.loads(run(['docker','inspect',*identifiers]).stdout)
    safe=[{'id':item['Id'],'name':item['Name'],'image':item['Image'],'service':item['Config']['Labels']['com.docker.compose.service'],
           'revision':item['Config']['Labels'].get('org.opencontainers.image.revision'),
           'compose_files':item['Config']['Labels'].get('com.docker.compose.project.config_files'),
           'mounts':[{'type':m['Type'],'source':m['Source'],'destination':m['Destination'],'name':m.get('Name')} for m in item['Mounts']]} for item in raw]
    for item in safe:
        if not item['compose_files'] or not all(pathlib.Path(p).exists() for p in item['compose_files'].split(',')):
            raise RuntimeError('Captured deployment configuration missing')
    (EVIDENCE/'live-before.json').write_text(json.dumps(safe,indent=2))
    (EVIDENCE/'live-health-before.json').write_text(json.dumps(http('http://127.0.0.1:8000/api/health')))
    return safe

def restore(live):
    dependencies=[item['id'] for item in live if item['service'] in ['postgres','redis']]
    run(['docker','start',*dependencies],output='live-dependencies-start.log')
    deadline=time.monotonic()+120
    while time.monotonic()<deadline:
        current=json.loads(run(['docker','inspect',*dependencies]).stdout)
        if all(item['State'].get('Health',{}).get('Status')=='healthy' for item in current): break
        time.sleep(.5)
    else: raise RuntimeError('Live dependencies failed readiness')
    run(['docker','start',*[item['id'] for item in live if item['service'] not in ['postgres','redis']]],output='live-applications-start.log')
    health=ready('http://127.0.0.1:8000/api/health')
    if health.get('test_instance'): raise RuntimeError('Live runtime unexpectedly disposable')
    (EVIDENCE/'live-health-after.json').write_text(json.dumps(health))
    # Inspection uses background-class reads and never issues a queue head.
    api_id=next(item['id'] for item in live if item['service']=='api')
    query="import json; from app.postgres_store import connection;\nwith connection(read_only=True) as db: print(json.dumps({'queue_read_ok': True, 'queue_rows': db.execute('SELECT COUNT(*) FROM daily_queue').fetchone()[0]}))"
    run(['docker','exec',api_id,'python','-c',query],output='live-readiness-after.json')
    after=json.loads(run(['docker','inspect',*[item['id'] for item in live]]).stdout)
    for expected,actual in zip(live,after):
        assert expected['image']==actual['Image'] and actual['State']['Running']
    (EVIDENCE/'live-restored.json').write_text(json.dumps({'identical_container_ids':True,'identical_image_ids':True,'all_running':True}))

def measure():
    other=run(['docker','ps','--format','{{.Names}}']).stdout.splitlines()
    if any((name.startswith('tempo-pg-regressions-') or name.startswith('tempo-audit')) and not name.startswith(PROJECT+'-') for name in other):
        raise RuntimeError('Other disposable runtime active; defer uncontaminated measurements')
    live=inspect_live()
    started=datetime.datetime.now(datetime.timezone.utc).isoformat()
    interruption=time.monotonic()
    try:
        apps=[item['id'] for item in live if item['service'] not in ['postgres','redis']]
        run(['docker','stop','--time','60',*apps],output='live-applications-stop.log',timeout=180)
        run(['docker','stop','--time','60',*[item['id'] for item in live if item['service'] in ['postgres','redis']]],output='live-dependencies-stop.log',timeout=180)
        compose('up','-d',*SERVICES,output='audit-up.log')
        assert ready('http://127.0.0.1:4182/api/health')['test_instance']
        compose('run','--rm','--no-deps','schema','python','/audit/fixture.py','seed',output='seed.log')
        result=run(['node',str(ROOT/'docs/architecture/audit-218/measure.mjs')],check=False,timeout=7200,output='measurement.log')
        if result.returncode: raise RuntimeError('Measurement failed; inspect measurement.log')
        # Worker characterization needs no server stack; remove competing CPU load.
        compose('logs','--no-color',output='http-runtime.log')
        compose('down','--volumes','--remove-orphans',output='http-down.log')
        ENV.update(TEMPO_INVESTIGATION_DIR=str(EVIDENCE/'browser-worker'), TEMPO_INVESTIGATION_SAMPLES='200',
                   TEMPO_INVESTIGATION_SCENARIO='audit', TEMPO_INVESTIGATION_WORKLOAD='high-distinctness-10000')
        run(['node',str(ROOT/'docs/architecture/audit-218/worker.mjs')],timeout=3600,output='browser-worker.log')
    finally:
        compose('logs','--no-color',output='runtime.log',check=False)
        compose('down','--volumes','--remove-orphans',output='audit-down.log',check=False,timeout=180)
        restore(live)
        (EVIDENCE/'interruption.json').write_text(json.dumps({'started_utc':started,'restored_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'seconds':time.monotonic()-interruption},indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('action',choices=['prepare','measure'])
    globals()[parser.parse_args().action]()
