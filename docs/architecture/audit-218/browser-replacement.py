"""Replace three interrupted browser pairs; never pool the excluded trials."""
import runtime,datetime,time
runtime.EVIDENCE=runtime.EVIDENCE/'browser-replacement'
runtime.EVIDENCE.mkdir(exist_ok=True)
assert not any(name.startswith(('tempo-audit','tempo-pg-regressions-')) for name in runtime.run(['docker','ps','--format','{{.Names}}']).stdout.splitlines())
live=runtime.inspect_live();started=datetime.datetime.now(datetime.timezone.utc).isoformat();wall=time.monotonic()
try:
 runtime.run(['docker','stop','--time','60',*[item['id'] for item in live if item['service'] not in ['postgres','redis']]],output='live-applications-stop.log',timeout=180)
 runtime.run(['docker','stop','--time','60',*[item['id'] for item in live if item['service'] in ['postgres','redis']]],output='live-dependencies-stop.log',timeout=180)
 runtime.ENV.update(TEMPO_INVESTIGATION_DIR=str(runtime.EVIDENCE/'worker'),TEMPO_INVESTIGATION_SAMPLES='3',TEMPO_INVESTIGATION_SCENARIO='foreground',TEMPO_INVESTIGATION_WORKLOAD='high-distinctness-10000')
 runtime.run(['node',str(runtime.ROOT/'docs/architecture/audit-218/worker.mjs')],timeout=240,output='worker.log')
finally:
 runtime.restore(live)
 (runtime.EVIDENCE/'interruption.json').write_text(runtime.json.dumps({'started_utc':started,'restored_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'seconds':time.monotonic()-wall},indent=2))
