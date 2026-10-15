"""Regression: fresh backend assembly installs Learning without service startup."""
import json
from pathlib import Path
import subprocess
import sys


def test_backend_learning_distribution_and_command_import_need_no_source_or_services():
    backend = Path(__file__).resolve().parents[1]
    probe = '''
import importlib, importlib.metadata as metadata, json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
def reject_connections(event, arguments):
    if event in {'socket.connect', 'socket.bind', 'sqlite3.connect'}:
        raise AssertionError('Import initialized a service: '+event)
sys.addaudithook(reject_connections)
for distribution, module_name, version in [
    ('tempo-learning-review','tempo_learning_review','0.1.0'),
    ('tempo-scheduling','tempo_scheduling','0.2.0'),
    ('tempo-chess-core','tempo_chess_core','0.1.0')]:
    module = importlib.import_module(module_name)
    installed = metadata.distribution(distribution)
    assert installed.version == version
    origin = Path(module.__file__).resolve()
    assert origin.is_relative_to(Path(sys.prefix).resolve())
    assert origin == Path(installed.locate_file(module_name+'/__init__.py')).resolve()
    direct_url = json.loads(installed.read_text('direct_url.json'))
    assert not direct_url.get('dir_info', {}).get('editable', False)
    print(json.dumps({'distribution':distribution,'version':version,'origin':str(origin),'direct_url':direct_url}))
from app import review_commands, command_gateway
from app.services import learning_review_adapter
assert command_gateway._handlers['cards.review'] is review_commands.submit_review
assert command_gateway._handlers['cards.review.reconcile'] is review_commands.reconcile_review
assert 'app.main' not in sys.modules
'''
    result = subprocess.run([sys.executable, '-I', '-c', probe, str(backend)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    origins = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(origins) == 3
    print(result.stdout, end="")
