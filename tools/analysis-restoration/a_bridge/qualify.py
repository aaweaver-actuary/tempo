"""Source-removed wheel/sdist qualification for the database-free operator."""
import hashlib
import json
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent


def main():
    evidence = Path(sys.argv[1]).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    report = {'complete': False, 'commands': [], 'artifacts': {}, 'consumers': {}}
    environment = {key: value for key, value in os.environ.items() if not key.startswith(('PYTHON', 'TEMPO_', 'PG', 'PYTEST_', 'VIRTUAL_ENV'))}
    environment['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'

    def run(arguments, directory, label, expected=0):
        report['commands'].append({'label': label, 'argv': list(map(str, arguments))})
        process = subprocess.run(list(map(str, arguments)), cwd=directory, env=environment, capture_output=True, text=True)
        (evidence / (label + '.log')).write_text(process.stdout + process.stderr)
        if process.returncode != expected:
            raise RuntimeError(label + ' failed; inspect its retained log')
        return process.stdout

    try:
        report['revision'] = run(['git', 'rev-parse', 'HEAD'], ROOT, 'revision').strip()
        report['source_inputs'] = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in ROOT.rglob('*') if path.is_file() and not any(part in {'.venv', '__pycache__', 'dist', '.pytest_cache'} for part in path.parts)}
        with tempfile.TemporaryDirectory(prefix='tempo-a-bridge-artifacts-') as temporary:
            workspace = Path(temporary)
            source = workspace / 'producer'
            source.mkdir()
            for name in ('pyproject.toml', 'uv.lock', 'src', 'tests', 'fixtures', 'REGRESSIONS.md'):
                item = ROOT / name
                shutil.copytree(item, source / name, ignore=shutil.ignore_patterns('__pycache__')) if item.is_dir() else shutil.copy2(item, source / name)
            artifacts = evidence / 'distributions'
            artifacts.mkdir(exist_ok=True)
            run([sys.executable, '-m', 'build', '--no-isolation', '--outdir', artifacts], source, 'build')
            requirements = workspace / 'requirements.txt'
            requirements.write_text(run(['uv', 'export', '--locked', '--no-emit-project', '--no-hashes', '--no-header'], ROOT, 'requirements'))
            shutil.rmtree(source)
            report['producer_source_removed'] = not source.exists()
            for artifact in artifacts.iterdir():
                report['artifacts'][artifact.name] = hashlib.sha256(artifact.read_bytes()).hexdigest()
            for version in ('3.12', '3.14'):
                for format_name, pattern in (('wheel', '*.whl'), ('sdist', '*.tar.gz')):
                    label = version + '-' + format_name
                    consumer = workspace / label
                    consumer.mkdir()
                    run(['uv', 'venv', '--python', version, consumer / '.venv'], workspace, label + '-venv')
                    python = consumer / '.venv/bin/python'
                    run(['uv', 'pip', 'install', '--python', python, '-r', requirements], consumer, label + '-dependencies')
                    run(['uv', 'pip', 'install', '--python', python, '--no-build-isolation', next(artifacts.glob(pattern))], consumer, label + '-install')
                    shutil.copytree(ROOT / 'tests', consumer / 'tests', ignore=shutil.ignore_patterns('__pycache__'))
                    run([python, '-I', '-c', "import tempo_a_bridge; assert 'site-packages' in tempo_a_bridge.__file__"], consumer, label + '-guard')
                    collected = run([python, '-I', '-m', 'pytest', 'tests', '--collect-only', '-q'], consumer, label + '-collection')
                    identities = [line for line in collected.splitlines() if '::' in line]
                    junit = evidence / (label + '.xml')
                    run([python, '-I', '-m', 'pytest', 'tests', '-q', '--junitxml=' + str(junit)], consumer, label + '-tests')
                    cases = ET.parse(junit).getroot().findall('.//testcase')
                    if not cases or len(identities) != len(cases) or len(set(identities)) != len(identities) or any(case.find(tag) is not None for case in cases for tag in ('failure', 'error', 'skipped')):
                        raise RuntimeError('Missing, duplicate, skipped or failed owning cases')
                    run([python, '-I', '-m', 'pytest', 'tests', '-q', '-k', 'zero_selection_probe_no_such_case'], consumer, label + '-zero', expected=5)
                    run([consumer / '.venv/bin/tempo-a-bridge', '--help'], consumer, label + '-cli')
                    report['consumers'][label] = {'passed': len(cases), 'identities': identities, 'zero_selection_exit': 5}
        report['complete'] = True
    except Exception as error:
        report['error'] = str(error)
    (evidence / 'artifacts.json').write_text(json.dumps(report, indent=2) + '\n')
    return 0 if report['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
