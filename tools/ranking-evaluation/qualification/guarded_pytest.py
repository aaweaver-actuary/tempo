"""Fresh installed-process guards and exact collection/execution accounting."""
import importlib.abc
import json
import os
from pathlib import Path
import socket
import sys

namespace,report,*arguments=sys.argv[1:]
blocked={'app','backend','sqlite3','psycopg','psycopg_pool','redis','httpx','requests','fastapi','celery','fsrs','tempo_scheduling','tempo_intelligence','torch','transformers'}

class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname.split('.')[0] in blocked:raise AssertionError('Forbidden runtime import: '+fullname)

sys.meta_path.insert(0,Guard())
def reject_socket(*args,**kwargs):raise AssertionError('Network denied')
socket.socket.connect=reject_socket
socket.create_connection=reject_socket
socket.socket.bind=reject_socket


def audit(event,args):
    if event not in {'open','socket.connect','socket.bind','sqlite3.connect','subprocess.Popen','os.system','os.fork','os.posix_spawn'}:
        return
    frame=sys._getframe(1)
    modules=set()
    while frame:
        modules.add(frame.f_globals.get('__name__',''))
        frame=frame.f_back
    owned=any(name.startswith(namespace) for name in modules)
    if event in {'socket.connect','socket.bind','sqlite3.connect'}:raise AssertionError('Service initialization denied')
    if owned and event in {'subprocess.Popen','os.system','os.fork','os.posix_spawn'}:
        raise AssertionError('Library process initialization denied')
    if event=='open' and owned and namespace=='tempo_evidence_math':
        path=str(Path(str(args[0])).resolve())
        roots=tuple(str(Path(prefix).resolve())+'/' for prefix in (sys.prefix,sys.base_prefix))
        module_read = any(name in modules for name in {'_frozen_importlib_external','importlib._bootstrap_external'}) and path.endswith(('.py','.pyc','.so','.pyd')) and path.startswith(roots)
        metadata_read = 'importlib.metadata' in modules and '.dist-info/' in path and path.startswith(str(Path(sys.prefix).resolve()))
        platform_read = '_osx_support' in modules and path == '/System/Library/CoreServices/SystemVersion.plist'
        if not (module_read or metadata_read or platform_read):
            raise AssertionError('Math filesystem I/O denied: '+path)

sys.addaudithook(audit)
# Import via a literal root API; no checkout paths are added.
if namespace=='tempo_evidence_math':
    import tempo_evidence_math as package
else:
    import tempo_ranking_evaluation as package
assert 'site-packages' in package.__file__,package.__file__
assert not any('/backend' in entry or '/src' in entry for entry in sys.path)
import pytest
os.environ["TEMPO_RUNTIME_GUARD"]="1"

class Cases:
    def __init__(self):self.selected=[];self.passed=[];self.failed=[];self.skipped=[]
    def pytest_collection_finish(self,session):self.selected=[item.nodeid for item in session.items]
    def pytest_runtest_logreport(self,report):
        if report.failed:self.failed.append(report.nodeid)
        elif report.skipped:self.skipped.append(report.nodeid)
        elif report.when=='call':self.passed.append(report.nodeid)

cases=Cases()
code=pytest.main(arguments,plugins=[cases])
Path(report).write_text(json.dumps(vars(cases)))
raise SystemExit(code)
