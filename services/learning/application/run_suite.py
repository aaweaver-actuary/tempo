"""Complete Learning-owned component suites with the shared JUnit contract."""
import argparse
from contextlib import nullcontext
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

from qualification import controlled_environment, junit_destination, validate_inventory

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def suite_inputs(suite):
    if suite == 'application':
        return HERE, [HERE/'tests']
    return ROOT, [HERE/'integration/test_qualification_runners.py',
                  ROOT/'backend/tests/test_learning_package_installation.py']


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('suite', choices=('application', 'tooling'))
    parser.add_argument('--junitxml', '--junit-xml', action='append', default=[])
    options = parser.parse_args(arguments)
    if len(options.junitxml) > 1:
        parser.error('Only one JUnit destination is supported')
    try:
        junit_destination(os.environ.get('PYTEST_ADDOPTS', ''))
        if options.junitxml and not options.junitxml[0]:
            raise ValueError('JUnit destination must be a nonempty path')
    except ValueError as error:
        parser.error(str(error))
    external_junit = Path(options.junitxml[0]).resolve() if options.junitxml else None
    environment = controlled_environment()
    environment.pop('PYTHONPATH', None)
    evidence_root = os.environ.get('TEMPO_LEARNING_EVIDENCE_DIR')
    workspace = (nullcontext(str(Path(evidence_root).resolve()/('component-'+options.suite)/uuid.uuid4().hex))
                 if evidence_root else tempfile.TemporaryDirectory(prefix='tempo-learning-suite-'))
    with workspace as directory:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        junit = external_junit or directory/'cases.xml'
        junit.parent.mkdir(parents=True, exist_ok=True)
        cwd, paths = suite_inputs(options.suite)
        flags = ['-c',str(HERE/'pyproject.toml'),'-o','addopts=','-p','no:cacheprovider','--rootdir',str(cwd),
                 *[str(path) for path in paths]]
        receipts = {}
        for stage, extra in (('collected',['--collect-only','-q']),
                             ('executed',['-q','--junitxml='+str(junit)])):
            receipt = directory/(stage+'.json')
            command = [sys.executable,str(HERE/'qualification.py'),str(receipt),*flags,*extra]
            result = subprocess.run(command, cwd=cwd, env=environment, capture_output=True, text=True)
            (directory/(stage+'.log')).write_text(result.stdout+result.stderr)
            print(result.stdout, end='')
            print(result.stderr, end='', file=sys.stderr)
            if result.returncode:
                print(f'{options.suite}: {stage} failed with exit {result.returncode}', file=sys.stderr)
                return 1
            receipts[stage] = json.loads(receipt.read_text())
        try:
            identities = validate_inventory(receipts['collected'], receipts['executed'], junit)
        except (OSError, ValueError, RuntimeError) as error:
            print(f'{options.suite}: incomplete qualification: {error}', file=sys.stderr)
            return 1
        print(json.dumps({'suite':options.suite,'complete':True,'passed':len(identities),
                          'identities':identities}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
