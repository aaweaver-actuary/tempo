"""Emit every changed path and its deletion/retention disposition as CSV."""
import csv,subprocess,sys
from pathlib import Path
root=Path(__file__).resolve().parents[2]
base,end=sys.argv[1:3]
rows=subprocess.check_output(['git','diff','--name-status','--find-renames=80%',base,end],cwd=root,text=True).splitlines()
writer=csv.writer(sys.stdout);writer.writerow(['status','old_path','new_path','classification','reason'])
for row in rows:
 fields=row.split('\t');status=fields[0];old=fields[1] if status[0]!='A' else '';new=fields[-1] if status[0]!='D' else '';p=new or old
 if p.startswith('docs/architecture/reduction-pass2/') or p.startswith('tools/reduction/'):
  kind,why='audit','Reproducible baseline, dependency census, path dispositions or bounded verification evidence'
 elif p.startswith('docs/architecture/schema-history/') or old.startswith('database/tempo_legacy_database/schema/'):
  kind,why='historical_schema','Preserve table/field dictionary outside retired runtime package'
 elif '/ddl/' in p or '/ddl/' in old:
  kind,why='schema_history_or_forward_migration','Isolate preserved upgrade SQL; schema 64 records paused retired identities without changing source rows'
 elif p.startswith('database/tempo_storage/'):
  kind,why='native_required_storage','Selected concrete native PostgreSQL operation; retained SQL/locks with alternate-driver and optional-feature paths removed'
 elif old.startswith('database/tempo_legacy_database/'):
  kind,why='legacy_runtime_removed','Retire shared runtime; retained SQL selected into concrete native owner, optional interfaces removed'
 elif any(part in p for part in ['game-ingestion','game-classification','repertoire-comparison','game-actions','analysis-jobs','learning/remediation','tempo_remediation_database']):
  kind,why='retired_dependency_closure','Remove optional provider, analysis, classification, feedback or remediation dependency; historical schema inclusion preserved separately'
 elif 'recombination' in p or 'recombination' in old:
  kind,why='recombination_retired','Stop automatic joining/splitting/projection; preserve generated exercises, schedules and history as frozen ordinary exercises'
 elif ('test' in p or 'test' in old) and status[0]=='D':
  kind,why='retired_exclusive_test','Remove proof/fixture for retired feature or obsolete compatibility contract; retained mixed boundaries kept or extracted'
 elif ('test' in p or 'test' in old):
  kind,why='retained_verification','Adapt retained proof to concrete native or current owner boundaries; preserve meaningful assertions'
 elif status[0]=='D':
  kind,why='retired_feature_or_dead_framework','Remove automatic feedback/provider/proposal/authoring/diagnostic closure, obsolete interface, registration or dead protocol'
 elif p.startswith('backend/app/games/') or p.startswith('app/features/game-review/'):
  kind,why='minimal_game_review','Replace automatic feedback orchestration with PGN replay, current authored comparison and first-departure display'
 elif p.startswith('backend/app/study/position_learning/') or p=='backend/app/repertoire/position_study_source.py':
  kind,why='application_ownership','Move source projection, immutable issuance and grading decisions out of SQL storage; fixed native operations remain in storage'
 elif p.startswith(('scripts/','Dockerfile','docker-compose','backend/Dockerfile','components.json','tools/component-build','backend/pyproject','backend/uv.lock','backend/requirements')):
  kind,why='registration_build_or_deployment','Remove retired dependencies/collector and update native storage paths; preserve backup and guarded schema transitions'
 elif p.startswith(('docs/','SPEC.md','database/README.md','backend/app/README.md')):
  kind,why='current_documentation','Describe reduced source ownership, deliberate behavior changes, preserved historical data and unverified installation'
 else:
  kind,why='retained_consumer_simplified','Rewire concrete native/current owners and remove optional feature calls, gates, settings or protocol branches'
 writer.writerow([status,old,new,kind,why])
