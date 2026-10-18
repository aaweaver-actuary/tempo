"""Installed-path isolation that deliberately honors PYTHONHASHSEED."""
import json
import os
import subprocess
import sys


def run_python(arguments, *, env=None, timeout=20):
    environment={key:value for key,value in (os.environ if env is None else env).items()
        if not key.startswith('PYTHON') or key=='PYTHONHASHSEED'}
    environment.pop('VIRTUAL_ENV',None)
    return subprocess.run([sys.executable,'-s','-P','-B',*map(str,arguments)],
        env=environment,capture_output=True,text=True,timeout=timeout)


def assert_fixed_seeds_honored():
    probe='''
import json, site, sys
import tempo_ranking_evaluation as package
assert sys.flags.ignore_environment == 0
assert sys.flags.safe_path and sys.flags.no_user_site and not site.ENABLE_USER_SITE
assert 'site-packages' in package.__file__
assert not any(path == '' or '/src' in path or '/backend' in path for path in sys.path)
print(json.dumps(hash('tempo-fixed-seed-proof')))
'''
    hashes={}
    for seed in ('1','22'):
        results=[]
        for _ in range(2):
            environment=dict(os.environ,PYTHONHASHSEED=seed,PYTHONPATH='/nonexistent/source',
                PYTHONHOME='/nonexistent/home',PYTHONUSERBASE='/nonexistent/user')
            completed=run_python(['-c',probe],env=environment)
            assert completed.returncode==0,completed.stderr
            results.append(json.loads(completed.stdout))
        assert results[0]==results[1],('fixed seed ignored',seed,results)
        hashes[seed]=results[0]
    assert hashes['1']!=hashes['22'],'different requested seeds had no observable effect'
