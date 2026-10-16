"""Reuse F2 build and owning runner, selecting exactly one C optional-off case."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
ROOT=Path(__file__).resolve().parents[4]
INTEGRATION=ROOT/'services/learning/runtime/integration'
sys.path.insert(0,str(INTEGRATION))
from run_postgres import main as learning_owned_proof


def main():
    if len(sys.argv)==1 and os.environ.get('TEMPO_C_STUDY_EVIDENCE') and os.environ.get('TEMPO_C_OPTIONAL_STOP_RECEIPT'):sys.argv.extend([os.environ['TEMPO_C_STUDY_EVIDENCE'],os.environ['TEMPO_C_OPTIONAL_STOP_RECEIPT']])
    if len(sys.argv)!=3:raise SystemExit('usage: run_study_off.py ABSOLUTE_EVIDENCE ABSOLUTE_C_STOP_RECEIPT')
    destination=Path(sys.argv[1]).resolve();receipt=Path(sys.argv[2]).resolve()
    document=json.loads(receipt.read_text());assert document['server_stopped'] and document['component']=='Intelligence'
    destination.mkdir(parents=True,exist_ok=True);build=Path(tempfile.mkdtemp(prefix='tempo-C-study-build-'))
    environment={key:value for key,value in os.environ.items() if key in {'PATH','HOME','TMPDIR','TMP','TEMP','LANG','CI','PLAYWRIGHT_BROWSERS_PATH','NODE_EXTRA_CA_CERTS'} or key.startswith('LC_')}
    environment.update(TEMPO_BROWSER_BUILD_DIR=str(build),TZ='America/New_York')
    try:
        process=subprocess.run(['npm','run','build:study'],cwd=ROOT,env=environment,capture_output=True,text=True)
        (destination/'build.log').write_text(process.stdout+process.stderr)
        if process.returncode:raise RuntimeError('F2_study_build_failed')
        (destination/'assets.json').write_text(json.dumps({str(path.relative_to(build)):hashlib.sha256(path.read_bytes()).hexdigest() for path in build.rglob('*') if path.is_file()},indent=2))
        # The owner accepts one evidence argument; explicit test selection comes through its API.
        sys.argv=[sys.argv[0],str(destination)]
        learning_owned_proof([Path(__file__).parent/'study/test_study_off.py'],{'TEMPO_LEARNING_STUDY_ASSETS':str(build),'TEMPO_C_OPTIONAL_STOP_RECEIPT':str(receipt)},[Path(__file__).parent,ROOT/'app',ROOT/'static',ROOT/'scripts/prove-study-learning.mjs'],evidence_directory=destination)
        result=json.loads((destination/'native.json').read_text())
        assert result['complete']
    finally:
        shutil.rmtree(build);(destination/'build-cleanup.json').write_text(json.dumps({'removed':not build.exists()})+'\n')

if __name__=='__main__':main()
