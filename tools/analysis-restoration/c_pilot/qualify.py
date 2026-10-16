"""Source-removed installed pilot consumers on the existing Python matrix."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--c-artifacts',type=Path,required=True);parser.add_argument('--b-client',type=Path,required=True);parser.add_argument('--requirements',type=Path,required=True);parser.add_argument('evidence',type=Path)
    args=parser.parse_args();source=Path(__file__).resolve().parent;destination=args.evidence.resolve();destination.mkdir(parents=True,exist_ok=True)
    environment={key:value for key,value in os.environ.items() if not key.startswith(('TEMPO_','PG','PYTHON','VIRTUAL_ENV','PYTEST_'))}
    environment.update(UV_PYTHON_DOWNLOADS='never',PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',PYTHONNOUSERSITE='1')
    report={'complete':False,'commands':[],'cases':{},'artifacts':{},'B_client_sha256':hashlib.sha256(args.b_client.read_bytes()).hexdigest()}
    def run(command,cwd,label):
        command=[str(item) for item in command];result=subprocess.run(command,cwd=cwd,env=environment,capture_output=True,text=True)
        report['commands'].append({'label':label,'argv':command,'exit_code':result.returncode});(destination/(label+'.log')).write_text(result.stdout+result.stderr)
        if result.returncode:raise RuntimeError(label+'_failed')
    try:
        with tempfile.TemporaryDirectory(prefix='tempo-C-pilot-artifacts-') as temporary:
            root=Path(temporary);build=root/'build';shutil.copytree(source,build,ignore=shutil.ignore_patterns('.venv','__pycache__','.pytest_cache','test-results','dist'))
            run([sys.executable,'-m','build','--no-isolation','--outdir',destination],build,'build');shutil.rmtree(build);report['build_source_removed']=True
            dependencies=[args.c_artifacts/name for name in ('tempo_chess_core-0.1.0-py3-none-any.whl','tempo_analysis_jobs_contracts-1.0.0-py3-none-any.whl','tempo_intelligence-0.1.0-py3-none-any.whl')]
            report['C_dependency_artifacts']={path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in dependencies}
            for version in ('3.12','3.14'):
                for kind,filename in (('wheel','tempo_analysis_pilot-0.1.0-py3-none-any.whl'),('sdist','tempo_analysis_pilot-0.1.0.tar.gz')):
                    label='python-'+version+'-'+kind;consumer=root/label;consumer.mkdir();shutil.copytree(source/'tests',consumer/'tests',ignore=shutil.ignore_patterns('__pycache__'))
                    run(['uv','venv','--python',version,consumer/'.venv'],root,label+'-venv');python=consumer/'.venv/bin/python';artifact=destination/filename
                    report['artifacts'][filename]=hashlib.sha256(artifact.read_bytes()).hexdigest()
                    run(['uv','pip','install','--python',python,'--offline','--no-build-isolation','-r',args.requirements,*dependencies,args.b_client,artifact],consumer,label+'-install')
                    run([python,'-I','-c',"from pathlib import Path;import tempo_analysis_pilot;assert 'site-packages' in Path(tempo_analysis_pilot.__file__).parts;assert not (Path.cwd()/'src').exists()"],consumer,label+'-guard')
                    junit=destination/(label+'.xml');run([python,'-I','-m','pytest','tests','--import-mode=importlib','-q','--junitxml='+str(junit)],consumer,label+'-tests')
                    cases=ET.parse(junit).getroot().findall('.//testcase')
                    if not cases or any(case.find(tag) is not None for case in cases for tag in ('failure','error','skipped')):raise RuntimeError('missing_or_failed_cases')
                    report['cases'][label]=[case.attrib for case in cases]
            report['complete']=True
    except Exception as error:report['error']=str(error) if isinstance(error,RuntimeError) else type(error).__name__
    finally:
        report['temporary_removed']=True;(destination/'artifacts.json').write_text(json.dumps(report,indent=2)+'\n')
    return 0 if report['complete'] else 1

if __name__=='__main__':raise SystemExit(main())
