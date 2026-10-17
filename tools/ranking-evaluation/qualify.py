"""Bounded component-local qualification; no services or shared runner changes."""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
STAGES = []
DEADLINE = None


def bounded(command, cwd, timeout=180, environment=None):
    environment = dict(os.environ if environment is None else environment)
    for name in ('PYTHONPATH','PYTHONHOME','PYTEST_ADDOPTS','VIRTUAL_ENV'):
        environment.pop(name, None)
    environment.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PYTHONDONTWRITEBYTECODE='1', UV_OFFLINE='1', UV_CACHE_DIR=str(Path(tempfile.gettempdir())/'tempo-evidence-qualification-cache'))
    started = time.monotonic()
    if DEADLINE is not None:
        timeout = min(timeout, max(0.01, DEADLINE-started))
    def limits():
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (120,120))
        if sys.platform.startswith('linux'):
            resource.setrlimit(resource.RLIMIT_AS, (1024**3,1024**3))
    try:
        completed = subprocess.run(command, cwd=cwd, env=environment, text=True, capture_output=True,
                                   timeout=timeout, preexec_fn=limits)
    except subprocess.TimeoutExpired as error:
        STAGES.append(dict(command=list(map(str,command)),cwd=str(cwd),status='timeout',seconds=time.monotonic()-started))
        raise RuntimeError('Bounded command timed out') from error
    stage = dict(command=list(map(str,command)),cwd=str(cwd),seconds=time.monotonic()-started,
                 returncode=completed.returncode,stdout=completed.stdout,stderr=completed.stderr)
    STAGES.append(stage)
    if completed.returncode:
        raise RuntimeError(json.dumps(stage))
    return completed.stdout


def static_boundary(root):
    metadata=tomllib.loads((root/'pyproject.toml').read_text())
    namespace=next((root/'src').iterdir()).name
    allowed={'chess','pydantic','pydantic_core','tempo_evidence_math',namespace}|set(sys.stdlib_module_names)
    forbidden={'app','sqlite3','psycopg','psycopg_pool','redis','httpx','requests','fastapi','celery','fsrs','tempo_scheduling','subprocess','multiprocessing','random','socket','os'}
    files=list((root/'src'/namespace).rglob('*.py'))
    for file in files:
        tree=ast.parse(file.read_text())
        for node in ast.walk(tree):
            names=[]
            if isinstance(node,ast.Import): names=[item.name for item in node.names]
            elif isinstance(node,ast.ImportFrom) and not node.level: names=[node.module or '']
            for name in names:
                owned_file_stat = namespace == 'tempo_ranking_evaluation' and file.name == 'cli.py' and isinstance(node,ast.ImportFrom) and node.module == 'os' and all(alias.name == 'fstat' for alias in node.names)
                if owned_file_stat:
                    continue
                if name.split('.')[0] not in allowed or name.split('.')[0] in forbidden:
                    raise ValueError(f'Forbidden/undeclared import: {file}:{node.lineno}: {name}')
            if isinstance(node,ast.Attribute) and node.attr in {'now','today','import_module','exec_module','load_module'}:
                raise ValueError(f'Forbidden clock/loader: {file}:{node.lineno}')
            if isinstance(node,ast.Name) and node.id in {'eval','exec','__import__'}:
                raise ValueError(f'Forbidden dynamic loader: {file}:{node.lineno}')
    expected=set()
    for file in (root/'tests').rglob('test_*.py'):
        expected.update(node.name for node in ast.walk(ast.parse(file.read_text())) if isinstance(node,ast.FunctionDef) and node.name.startswith('test_'))
    inventory=(root/'REGRESSIONS.md').read_text()
    if not expected or any('`'+name+'`' not in inventory for name in expected):
        raise ValueError('Missing/empty component-local regression registration')
    return namespace, metadata


def suite(root, python, report, junit):
    namespace=next((root/'src').iterdir()).name if (root/'src').exists() else json.loads((root/'qualification/config.json').read_text())['namespace']
    cases=report.with_suffix('.cases.json')
    bounded([str(python),'-I','-B',str(root/'qualification/guarded_pytest.py'),namespace,str(cases),
             str(root/'tests'),'--import-mode=importlib','-q','--junitxml='+str(junit),'-c',str(root/'pyproject.toml')],root)
    data=json.loads(cases.read_text())
    if not data['selected'] or sorted(data['selected'])!=sorted(data['passed']) or data['failed'] or data['skipped']:
        raise ValueError('Zero/missing/failed/skipped required cases')
    tree=ET.parse(junit)
    actual=[node for node in tree.iter('testcase')]
    if len(actual)!=len(data['selected']):raise ValueError('JUnit and collection identity mismatch')
    return data


def fingerprint(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def artifacts(options):
    global DEADLINE
    DEADLINE = time.monotonic()+900
    namespace,metadata=static_boundary(ROOT)
    destination=Path(options.artifacts).resolve();destination.mkdir(parents=True,exist_ok=False)
    wheelhouse=Path(options.wheelhouse).resolve()
    manifests=[];cases=[]
    with tempfile.TemporaryDirectory(prefix='tempo-evidence-artifacts-') as temporary:
        temporary=Path(temporary)
        build=temporary/'build'
        shutil.copytree(ROOT,build,ignore=shutil.ignore_patterns('dist','.venv','__pycache__','.pytest_cache','*.egg-info'))
        # Only declared component inputs; no checkout-level data is copied.
        bounded([sys.executable,'-m','build','--no-isolation'],build)
        for artifact in (build/'dist').iterdir():shutil.copy2(artifact,destination/artifact.name)
        sdist=next((build/'dist').glob('*.tar.gz'))
        unpack=temporary/'sdist';unpack.mkdir()
        with tarfile.open(sdist) as archive:archive.extractall(unpack,filter='data')
        rebuilt=next(unpack.iterdir())
        bounded([sys.executable,'-m','build','--wheel','--no-isolation'],rebuilt)
        rebuilt_wheel=next((rebuilt/'dist').glob('*.whl'))
        shutil.copy2(rebuilt_wheel,destination/('sdist-'+rebuilt_wheel.name))
        wheels=[next((build/'dist').glob('*.whl')),rebuilt_wheel]
        # Freeze artifact hashes before execution.
        manifests=[dict(file=p.name,sha256=fingerprint(p)) for p in destination.iterdir()]
        # Execution never has the component source directory available.
        shutil.rmtree(build/'src');shutil.rmtree(rebuilt/'src')
        for index,interpreter in enumerate(options.python):
            for kind,wheel in zip(('wheel','sdist-wheel'),wheels):
                consumer=temporary/f'consumer-{index}-{kind}';consumer.mkdir()
                environment=consumer/'venv'
                bounded(['uv','venv','--python',interpreter,str(environment)],consumer)
                python=environment/'bin/python'
                # Resolve only exact locked versions, from prebuilt local artifacts.
                requirements=[]
                for line in (ROOT/'requirements-dev.lock').read_text().splitlines():
                    if line and not line.startswith((' ','#','--')):
                        requirements.append(line.split(' \\')[0])
                bounded(['uv','pip','install','--python',str(python),'--no-index','--find-links',str(wheelhouse),*requirements,str(wheel)],consumer)
                for item in ('tests','qualification','pyproject.toml','qualify.py'):
                    source=rebuilt/item
                    shutil.copytree(source,consumer/item) if source.is_dir() else shutil.copy2(source,consumer/item)
                result=suite(consumer,python,consumer/'report.json',consumer/'junit.xml')
                case_dest=destination/f'cases-{index}-{kind}.json'
                case_dest.write_text(json.dumps(result,indent=2))
                shutil.copy2(consumer/'junit.xml',destination/f'junit-{index}-{kind}.xml')
                cases.append(dict(interpreter=bounded([str(python),'--version'],consumer).strip(),artifact=kind,passed=len(result['passed'])))
                if namespace=='tempo_ranking_evaluation':
                    bounded([str(environment/'bin/tempo-evaluate-opening-rankings'),'corpus','--scenario','italian-common','--top-k','2'],consumer)
    return dict(artifacts=manifests,matrix=cases,dependency_artifacts=[dict(file=p.name,sha256=fingerprint(p)) for p in sorted(wheelhouse.glob('*.whl'))])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    modes=parser.add_subparsers(dest='mode',required=True)
    run=modes.add_parser('run');run.add_argument('--timeout',type=int,default=180);run.add_argument('command',nargs=argparse.REMAINDER)
    test=modes.add_parser('suite');test.add_argument('--report',required=True);test.add_argument('--junitxml','--junit-xml')
    artifact=modes.add_parser('artifacts');artifact.add_argument('--python',action='append',required=True);artifact.add_argument('--wheelhouse',required=True);artifact.add_argument('--report',required=True);artifact.add_argument('--artifacts',required=True)
    options=parser.parse_args();result={'complete':False,'limits':{'cpu_seconds':120,'child_wall_seconds':180,'parallelism':1,'memory_mib':1024 if sys.platform.startswith('linux') else None,'memory_note':'macOS hard memory limit not claimed' if sys.platform=='darwin' else ''}}
    if options.mode=='run':
        command=options.command[1:] if options.command[:1]==['--'] else options.command
        if not command:parser.error('A command is required')
        print(bounded(command,ROOT,options.timeout),end='');return 0
    report=Path(options.report).resolve();report.parent.mkdir(parents=True,exist_ok=True)
    try:
        static_boundary(ROOT)
        if options.mode=='suite':
            result['cases']=suite(ROOT,Path(sys.executable),report,Path(options.junitxml or str(report.with_suffix('.xml'))))
        else:result.update(artifacts(options))
        result['complete']=True
    except Exception as error:
        result['error']=str(error)
    result['stages']=STAGES
    result['inputs']={str(p.relative_to(ROOT)):fingerprint(p) for p in ROOT.rglob('*') if p.is_file() and not any(part in {'dist','.venv','__pycache__','.pytest_cache'} for part in p.relative_to(ROOT).parts)}
    report.write_text(json.dumps(result,indent=2))
    print(json.dumps({'complete':result['complete'],'report':str(report),'error':result.get('error')}))
    return 0 if result['complete'] else 1


if __name__=='__main__':raise SystemExit(main())
