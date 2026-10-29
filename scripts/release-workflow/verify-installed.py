"""Focused installed readback; never issue a new queue attempt or review."""
import json, sys, urllib.request, hashlib, time
from pathlib import Path

root=Path(sys.argv[1]).resolve()
operation=json.loads((root/'release-operation.json').read_text())
assert operation['phase']=='ready'
assert not operation['migration_required'] or operation['preservation_verified']
prepared=json.loads((root/'prepared-session.json').read_text())
url=prepared['target']['webUrl']
head=json.loads((root/'queue-head-readonly.json').read_text())
health={}
for path in ['/api/health','/api/settings','/api/tactics/progress','/api/product/game-review/games','/api/repertoires']:
 started=time.monotonic()
 with urllib.request.urlopen(url+path,timeout=10) as response:
  raw=response.read(2_000_000);data=json.loads(raw)
  health[path]={'status':response.status,'seconds':round(time.monotonic()-started,3),'bytes':len(raw)}
  if path=='/api/health':assert data['status']=='ok' and data['storage']=='postgresql' and not data['test_instance']
  if path=='/api/product/game-review/games':health[path]['game_count']=len(data['games'])
  if path=='/api/repertoires':health[path]['repertoire_count']=len(data['repertoires'])
  if path=='/api/settings':health[path]['study_only_mode']=data['study_only_mode']
  if path=='/api/settings' and (root/'preservation-before.json').exists():assert data['study_only_mode']==json.loads((root/'preservation-before.json').read_text())['study_only_mode']
assets=json.loads((root/'artifact-verification.json').read_text())
with urllib.request.urlopen(url+'/',timeout=5) as response:
 assert hashlib.sha256(response.read()).hexdigest()==assets['web_index_sha256']
health['web_artifact_hash_matches']=True
queue=[]
health['queue_head_readonly']=head
health['queue_check_blocker']='Queue GET may issue state after a concurrent head change; retained read-only database head probe only'
out={'health':health,'ordinary_queue_load_reload':queue,'no_user_move_review_or_replay':True}
(root/'installed-recovery.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps(out,indent=2))
