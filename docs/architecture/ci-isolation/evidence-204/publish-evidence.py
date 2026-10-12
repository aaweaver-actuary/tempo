"""Preserve evidence on a separate durable ref without editing the feature tree."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

checkout = Path(sys.argv[1]).resolve()
evidence = Path(sys.argv[2]).resolve()
branch = 'codex/issue-204-scheduling-evidence'
def git(*arguments, env=None, data=None):
    return subprocess.check_output(['git',*arguments],cwd=checkout,env=env,input=data,text=True).strip()
feature_head = git('rev-parse','HEAD')
prior = subprocess.run(['git','rev-parse','--verify',f'refs/heads/{branch}'],cwd=checkout,capture_output=True,text=True)
parent = prior.stdout.strip() if prior.returncode == 0 else feature_head
# Never snapshot live writers; their completed logs are added at final publication.
excluded = set(json.loads(sys.argv[3])) if len(sys.argv) > 3 else set()
files = sorted(p for p in evidence.rglob('*') if p.is_file() and not p.is_symlink()
               and p.name not in excluded and p.name != 'manifest.json'
               and '__pycache__' not in p.parts)
manifest = [{'path':str(p.relative_to(evidence)),'bytes':p.stat().st_size,
             'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in files]
(evidence/'manifest.json').write_text(json.dumps({'feature_head':feature_head,'files':manifest},indent=2)+'\n')
files.append(evidence/'manifest.json')
with tempfile.TemporaryDirectory(prefix='tempo-204-evidence-index-') as temporary:
    environment = dict(os.environ,GIT_INDEX_FILE=str(Path(temporary)/'index'))
    git('read-tree',feature_head,env=environment)
    for path in files:
        blob = git('hash-object','-w',str(path))
        target = 'docs/architecture/ci-isolation/evidence-204/'+str(path.relative_to(evidence))
        git('update-index','--add','--cacheinfo',f'100644,{blob},{target}',env=environment)
    tree = git('write-tree',env=environment)
    commit = git('commit-tree',tree,'-p',parent,data='Preserve A1 scheduling qualification evidence and reproduction inputs\n')
    git('update-ref',f'refs/heads/{branch}',commit)
print(json.dumps({'branch':branch,'commit':commit,'feature_head':feature_head,'files':len(files)}))
