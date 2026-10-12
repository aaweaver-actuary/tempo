"""Native cold-publication and Redis error-surface follow-up; isolated data only."""
import datetime,json,pathlib,time,urllib.request,urllib.error,uuid
import runtime

runtime.EVIDENCE=runtime.EVIDENCE/'native-fault-probe'
runtime.EVIDENCE.mkdir(exist_ok=True)

def schema(code):
    result=runtime.compose('run','--rm','--no-deps','-e','TEMPO_REDIS_URL=redis://redis:6379/0','schema','python','-c',code)
    return result.stdout.strip().splitlines()[-1]

def request(path,payload=None,operation=None,method=None):
    headers={'Connection':'close','Content-Type':'application/json'}
    if operation:headers['Idempotency-Key']=operation
    body=json.dumps(payload).encode() if payload is not None else None
    req=urllib.request.Request('http://127.0.0.1:4182/api/'+path,data=body,headers=headers,method=method)
    started=time.monotonic()
    try:
        with urllib.request.urlopen(req,timeout=15) as response:
            return {'status':response.status,'body':response.read().decode(),'milliseconds':(time.monotonic()-started)*1000}
    except urllib.error.HTTPError as error:
        return {'status':error.code,'body':error.read().decode(),'milliseconds':(time.monotonic()-started)*1000}

def value(response):
    assert response['status']==200,response
    return json.loads(response['body'])

def saved_state():
    return json.loads(schema("import psycopg,json;\nwith psycopg.connect('postgresql://postgres@postgres:5432/tempo') as db:\n row=db.execute(\"SELECT p.state,p.refresh_pending,t.state,t.attempt_count FROM queue_projections p JOIN background_tasks t ON t.deduplication_key='audit-cold-218' WHERE p.queue_date='2099-10-12'\").fetchone(); print(json.dumps(row))"))

live=runtime.inspect_live()
started=datetime.datetime.now(datetime.timezone.utc).isoformat()
wall=time.monotonic()
findings={}
try:
    other=runtime.run(['docker','ps','--format','{{.Names}}']).stdout.splitlines()
    assert not any(name.startswith(('tempo-audit','tempo-pg-regressions-')) for name in other),'Other audit stack active'
    runtime.run(['docker','stop','--time','60',*[item['id'] for item in live if item['service'] not in ['postgres','redis']]],output='live-stop-applications.log',timeout=180)
    runtime.run(['docker','stop','--time','60',*[item['id'] for item in live if item['service'] in ['postgres','redis']]],output='live-stop-dependencies.log',timeout=180)
    runtime.compose('up','-d',*runtime.SERVICES,output='up.log')
    assert runtime.ready('http://127.0.0.1:4182/api/health')['test_instance']
    runtime.compose('run','--rm','--no-deps','schema','python','/audit/fixture.py','seed',output='seed.log')
    # Native queue/receipt data are synthetic and publication uses the production slice.
    task=schema("import sys;sys.path.insert(0,'/source/backend');from app.celery_app import celery_app;print(celery_app.send_task('audit.cpu',args=[120],queue='background').id)")
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        if runtime.compose('exec','-T','redis','redis-cli','GET','audit:running:'+task).stdout.strip():break
        time.sleep(.05)
    else:raise RuntimeError('CPU injection never started')
    schema("import sys,os;sys.path.insert(0,'/source/backend');os.environ['TEMPO_DATABASE_WRITE_URL']='postgresql://postgres@postgres:5432/tempo';os.environ['TEMPO_DATABASE_READ_URL']='postgresql://postgres@postgres:5432/tempo';from app import postgres_store;from app.services.durable_tasks import enqueue_task;from app.services.queue_refresh_wakeup import wake_queue_refresh;\nwith postgres_store.connection() as db: db.execute(\"INSERT INTO queue_projections(queue_date,state,generation,refresh_pending) VALUES('2099-10-12','refreshing',0,1)\");\nenqueue_task('daily_queue','audit-cold-218',{'queue_date':'2099-10-12','_queue_phase':'publish_projection'},priority=0);wake_queue_refresh();print('queued')")
    before=time.monotonic()
    first=saved_state()
    # A foreground review still succeeds while the essential publication waits.
    queue=value(request('queue/window?limit=2'));card=queue['cards'][0]
    operation=uuid.uuid4().hex
    payload={'outcome':'correct','queue_entry_id':card['queue_entry_id'],'expected_revision':card['revision'],'attempt_id':'audit-'+operation}
    review=request('cards/'+card['id']+'/review',payload,operation)
    assert value(review)['persisted']
    deadline=before+30.5
    while time.monotonic()<deadline:
        assert saved_state()[0]!='ready'
        time.sleep(.25)
    still_pending=saved_state()
    findings['cold_publication']={'initial':first,'after_30_seconds':still_pending,'blocked_seconds':time.monotonic()-before,'foreground_review':review,'injected_task':task,'classification':'Synthetic admitted CPU occupancy, real daily_queue publish slice; not attribution to a specific production handler'}
    cancelled=time.monotonic()
    schema(f"import sys;sys.path.insert(0,'/source/backend');from app.celery_app import celery_app;celery_app.control.revoke('{task}',terminate=True,signal='SIGTERM');print('revoked')")
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        if saved_state()[0]=='ready':break
        time.sleep(.05)
    else:raise RuntimeError('Essential publication did not recover')
    findings['cold_publication']['recovered_state']=saved_state()
    findings['cold_publication']['revoke_to_ready_wall_ms']=(time.monotonic()-cancelled)*1000
    queue=value(request('queue/window?limit=2'));card=queue['cards'][0];operation=uuid.uuid4().hex
    payload={'outcome':'correct','queue_entry_id':card['queue_entry_id'],'expected_revision':card['revision'],'attempt_id':'audit-'+operation}
    runtime.compose('stop','redis',output='redis-stop.log')
    try:
        outage=request('cards/'+card['id']+'/review',payload,operation)
        findings['redis_http_outage']=outage
        assert outage['status']>=500 and 'persisted' not in outage['body']
        # Separate 200-observation refused-admission cohort; real socket I/O, no mock.
        admission=schema("import sys,os,json,time;sys.path.insert(0,'/source/backend');from app.services.redis_admission_gate import foreground_lease;rows=[];\nfor index in range(200):\n started=time.monotonic()\n try:\n  with foreground_lease(): raise AssertionError('Unexpected admission')\n except Exception as error: rows.append({'index':index,'error_class':type(error).__name__,'milliseconds':(time.monotonic()-started)*1000})\nprint(json.dumps(rows))")
        (runtime.EVIDENCE/'raw-refused-admission.json').write_text(admission)
        # Capture failure diagnostics before any container recreation.
        runtime.compose('logs','--no-color','api',output='redis-api-error.log')
    finally:runtime.compose('start','redis',output='redis-start.log')
    deadline=time.monotonic()+45
    while time.monotonic()<deadline:
        recovered=request('cards/'+card['id']+'/review',payload,operation)
        if recovered['status']==200:break
        time.sleep(.1)
    assert value(recovered)['persisted']
    replay=request('cards/'+card['id']+'/review',payload,uuid.uuid4().hex)
    assert value(replay)==value(recovered)
    findings['redis_recovery']={'response':recovered,'logical_replay':replay}
    runtime.compose('run','--rm','--no-deps','schema','python','/audit/fixture.py','verify',output='verify.log')
    (runtime.EVIDENCE/'findings.json').write_text(json.dumps(findings,indent=2))
finally:
    runtime.compose('logs','--no-color',check=False,output='runtime.log')
    runtime.compose('down','--volumes','--remove-orphans',check=False,output='down.log',timeout=180)
    runtime.restore(live)
    (runtime.EVIDENCE/'interruption.json').write_text(json.dumps({'started_utc':started,'restored_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'seconds':time.monotonic()-wall},indent=2))
