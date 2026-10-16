"""Manual installed A export -> installed C acceptance and bounded work."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
from .inputs import normalize


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    if len(sys.argv)>1 and sys.argv[1]=='work':
        work_parser=argparse.ArgumentParser(description='Explicit bounded B execution through installed Intelligence composition')
        work_parser.add_argument('run_id');work_parser.add_argument('--profile',type=Path,required=True);work_parser.add_argument('--max-jobs',type=int,default=1)
        args=work_parser.parse_args(sys.argv[2:])
        from tempo_intelligence import Configuration,run_analysis_work
        from .stockfish_client import connect
        print(json.dumps(run_analysis_work(Configuration.environment(),args.run_id,args.max_jobs,client=connect(args.profile))))
        return
    parser.add_argument('--config',type=Path,required=True);parser.add_argument('--evidence',type=Path,required=True)
    options=parser.parse_args();config=json.loads(options.config.read_text())
    allowed={'schema_version','workspace_id','a_export_directory','game_ids','intelligence_command','run_id','position_budget','engine_profile','max_jobs'}
    if set(config)-allowed or config.get('schema_version')!=1:raise ValueError('invalid_pilot_configuration')
    destination=options.evidence.resolve();destination.mkdir(parents=True,exist_ok=True)
    manifest,pages=normalize(config['a_export_directory'],workspace=config['workspace_id'],selected_game_ids=config['game_ids'])
    (destination/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    for page in pages:(destination/(page['page_id']+'.json')).write_text(json.dumps(page,indent=2)+'\n')
    command=config['intelligence_command']
    if not isinstance(command,list) or not command or any(not isinstance(value,str) for value in command):raise ValueError('explicit_installed_cli_required')
    commands=[]
    def invoke(label,args):
        argv=command+['analysis']+args
        result=subprocess.run(argv,capture_output=True,text=True)
        commands.append({'label':label,'argv':argv,'exit_code':result.returncode})
        if result.returncode:raise RuntimeError(label+'_failed')
        value=json.loads(result.stdout);(destination/(label+'.json')).write_text(json.dumps(value,indent=2)+'\n');return value
    invoke('accepted-input',['accept-input','--manifest',str(destination/'manifest.json'),'--pages-directory',str(destination)])
    request={'schema_version':1,'workspace_id':config['workspace_id'],'run_id':config['run_id'],'revision':1,'input_id':manifest['input_id'],'input_revision':manifest['revision'],'position_budget':config.get('position_budget',250)}
    (destination/'run-request.json').write_text(json.dumps(request)+'\n')
    invoke('run',['run','--file',str(destination/'run-request.json')])
    work=['work',config['run_id'],'--max-jobs',str(config.get('max_jobs',1))]
    if config.get('engine_profile'):
        from tempo_intelligence import Configuration,run_analysis_work
        from .stockfish_client import connect
        worker_result=run_analysis_work(Configuration.environment(),config['run_id'],config.get('max_jobs',1),client=connect(config['engine_profile']))
        (destination/'engine-work.json').write_text(json.dumps(worker_result,indent=2)+'\n')
    invoke('work',work);report=invoke('report',['report',config['run_id']]);remaining=invoke('remaining',['remaining',config['run_id']])
    summary={'schema_version':1,'C1':'report-produced','C2':'requires exact installed A/B artifact receipt; work output retained','C3':'unverified' if manifest['producer_manifest']['provenance']['kind']=='synthetic' else 'explicit scoped export consumed; inspect exclusions/scan completion','source_manifest_digest':manifest['source_artifact_digest'],'selected_game_ids':config['game_ids'],'position_budget':request['position_budget'],'unprocessed_count':report.get('unprocessed_count'),'commands':commands}
    (destination/'pilot.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary))

if __name__=='__main__':main()
