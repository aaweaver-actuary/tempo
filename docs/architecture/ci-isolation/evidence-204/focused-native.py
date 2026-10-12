"""Audit-only disposable runner using unchanged production Compose and proofs."""
import json
import os
import pathlib
import secrets
import shutil
import subprocess
import sys
import tempfile
import time

root = pathlib.Path(sys.argv[1]).resolve()
evidence = pathlib.Path(sys.argv[2]).resolve()
evidence.mkdir(parents=True, exist_ok=True)
project = 'tempo-a1-204-' + secrets.token_hex(5)
secret_directory = pathlib.Path(tempfile.mkdtemp(prefix='.tempo-a1-204-secrets-', dir=root))
secret_directory.chmod(0o700)
passwords = {role: secrets.token_hex(24) for role in ('admin','reader','writer')}
for role,password in passwords.items():
    user = 'postgres' if role == 'admin' else 'tempo_' + role
    for filename,value in [(role+'_password',password+'\n'),(role+'_pgpass',f'postgres:5432:*:{user}:{password}\n')]:
        path=secret_directory/filename
        path.write_text(value)
        path.chmod(0o600)
environment = dict(os.environ, TEMPO_PG_TEST_SECRETS=str(secret_directory), TEMPO_PG_TEST_PORT='0', TEMPO_PG_MAINTENANCE_IMAGE=project+'-maintenance')
compose=['docker','compose','-p',project,'-f','docker-compose.postgres.test.yml']
record={'project':project,'source_revision':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),'started_at':time.time(),'commands':[],'teardown':compose+['down','--rmi','local','-v'],'status':'running'}
def save():
    (evidence/'native-provenance.json').write_text(json.dumps(record,indent=2)+'\n')
def run(arguments,allow_failure=False):
    started=time.monotonic()
    completed=subprocess.run(arguments,cwd=root,env=environment)
    record['commands'].append({'command':arguments,'seconds':round(time.monotonic()-started,3),'exit_code':completed.returncode})
    save()
    if completed.returncode and not allow_failure: raise RuntimeError('Audit native command failed')
try:
    save()
    for resource,args in [('containers',['ps','-aq']),('volumes',['volume','ls','-q']),('networks',['network','ls','-q'])]:
        existing=subprocess.check_output(['docker',*args,'--filter','label=com.docker.compose.project='+project],text=True).strip()
        if existing: raise RuntimeError('Project unexpectedly already owns '+resource)
    run(compose+['build','schema','api','foreground-worker','background-worker'])
    run(compose+['up','-d','--wait','postgres','redis'])
    run(compose+['run','--rm','--no-deps','schema'])
    run(compose+['up','-d','--no-deps','--wait','api','foreground-worker','background-worker'])
    # Match the regular runner's background_workloads consumer isolation.
    run(compose+['stop','background-worker'])
    proof = "import sys; sys.path.insert(0,'/source/scripts'); import check_postgres_queue_attempt_recovery as p; p.test_postgres_scheduling_adapter_effects_rollback_with_receipt_failure(); p.test_postgres_scheduling_concurrent_replay_preserves_one_history_and_queue_effect()"
    run(compose+['run','--rm','--no-deps','-e','TEMPO_REDIS_URL=redis://redis:6379/0','schema','python','-c',proof])
    from uuid import uuid4
    run(compose+['run','--rm','--no-deps','-e','TEMPO_REDIS_URL=redis://redis:6379/0','schema','python','/source/scripts/check_postgres_training_transition.py','proof','training-transition-'+str(uuid4())])
    record['status']='passed'
except BaseException as error:
    record['status']='failed'; record['error']=str(error)
finally:
    containers=subprocess.run(['docker','ps','-aq','--filter','label=com.docker.compose.project='+project],capture_output=True,text=True)
    ids=containers.stdout.split()
    if ids:
        details=json.loads(subprocess.check_output(['docker','inspect',*ids],text=True))
        record['resources']=[{'id':item['Id'],'image':item['Image'],'created':item['Created'],'name':item['Name'],'mounts':[{'type':m['Type'],'name':m.get('Name'),'source':m['Source'],'destination':m['Destination']} for m in item['Mounts']]} for item in details]
    logs=subprocess.run(compose+['logs','--no-color','--tail','200'],capture_output=True,text=True,cwd=root,env=environment)
    (evidence/'native-container-logs.txt').write_text(logs.stdout+logs.stderr)
    run(compose+['down','--rmi','local','-v'],allow_failure=True)
    record['remaining']={}
    for resource,args in [('containers',['ps','-aq']),('volumes',['volume','ls','-q']),('networks',['network','ls','-q'])]:
        result=subprocess.run(['docker',*args,'--filter','label=com.docker.compose.project='+project],capture_output=True,text=True)
        record['remaining'][resource]={'exit_code':result.returncode,'identifiers':result.stdout.split()}
    shutil.rmtree(secret_directory)
    record['finished_at']=time.time();save()
if record['status']!='passed' or any(v['exit_code'] or v['identifiers'] for v in record['remaining'].values()): sys.exit(1)
