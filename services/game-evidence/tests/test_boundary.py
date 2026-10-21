import subprocess
import sys


def test_installed_import_never_initializes_optional_domains_or_network():
    guard="""
import sys
def forbid(event,args):
    if event in {'socket.connect','socket.bind','subprocess.Popen','sqlite3.connect'}:
        raise AssertionError(event)
sys.addaudithook(forbid)
import tempo_game_evidence
assert not any(name.split('.')[0] in {'app','tempo_learning_review','tempo_scheduling','celery','redis'} for name in sys.modules)
"""
    result=subprocess.run([sys.executable,"-I","-c",guard],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
