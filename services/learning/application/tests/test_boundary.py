"""Fresh-process import boundary, including deferred application execution."""
import subprocess
import sys


def test_import_and_explicit_registration_need_no_legacy_runtime():
    source = '''
import sys
from importlib.abc import MetaPathFinder
class RejectServices(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'app','fastapi','celery','redis','psycopg','httpx','chess','fsrs','torch','onnxruntime','openai'}:
            raise AssertionError(fullname)
sys.meta_path.insert(0, RejectServices())
import tempo_learning_review as review
assert not any(name == 'app' or name.startswith('app.') for name in sys.modules)
handlers = {}
review.register_review_commands(lambda name, handler: handlers.setdefault(name,handler), submit=lambda:None,reconcile=lambda:None)
assert sorted(handlers) == ['cards.review','cards.review.reconcile']
assert 'site-packages' in review.__file__
'''
    result = subprocess.run([sys.executable,"-I","-c",source],capture_output=True,text=True,timeout=15)
    assert result.returncode == 0,result.stderr
