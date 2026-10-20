"""Installed comparer proof without ingester, provider, DB or action implementations."""
from importlib import metadata
import json
from pathlib import Path
import subprocess
import sys

from .test_comparison import game, route, snapshot


def test_installed_closure_needs_no_ingester_or_optional_infrastructure():
    forbidden = {"tempo-game-ingestion", "tempo-game-evidence", "tempo-learning-runtime", "tempo-content", "psycopg", "httpx"}
    for distribution in forbidden:
        try:
            metadata.distribution(distribution)
        except metadata.PackageNotFoundError:
            continue
        raise AssertionError(f"Unexpected independent comparer dependency: {distribution}")
    script = '''
import sys
from importlib.abc import MetaPathFinder
class Refuse(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'tempo_game_ingestion','tempo_game_ingester','tempo_game_evidence','tempo_learning_runtime','tempo_content','tempo_game_actions','psycopg','httpx','sqlite3','requests'}:
            raise AssertionError('Forbidden comparer dependency: '+fullname)
sys.meta_path.insert(0,Refuse())
def no_connection(event,args):
    if event in {'socket.connect','sqlite3.connect'}: raise AssertionError('Unexpected infrastructure: '+event)
sys.addaudithook(no_connection)
from tempo_repertoire_comparison import CurrentRepertoireComparer, RouteFrontier
print(CurrentRepertoireComparer.__name__)
'''
    result = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


def test_installed_file_cli_compares_and_refuses_output_overwrite(tmp_path):
    game_path, snapshot_path, output = [tmp_path / name for name in ("game.json", "snapshot.json", "report.json")]
    game_path.write_text(game(["e2e4", "c7c5"]).model_dump_json())
    snapshot_path.write_text(snapshot([route(["e2e4", "e7e5"])]).model_dump_json())
    command = [sys.executable, "-I", "-m", "tempo_repertoire_comparison.cli", "--game", str(game_path), "--snapshot", str(snapshot_path), "--output", str(output)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    original = output.read_bytes()
    report = json.loads(original)
    assert report["first_difference"]["side"] == "opponent"
    assert report["snapshot_captured_at"] == "2026-10-20T12:00:00Z"
    retry = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert retry.returncode == 2 and output.read_bytes() == original


def test_oversized_file_is_rejected_before_output_creation(tmp_path):
    from tempo_repertoire_comparison.cli import MAX_INPUT_BYTES, main
    huge, other, output = [tmp_path / name for name in ("huge.json", "snapshot.json", "report.json")]
    huge.write_bytes(b" " * (MAX_INPUT_BYTES + 1))
    other.write_text(snapshot([]).model_dump_json())
    import pytest
    with pytest.raises(SystemExit) as error:
        main(["--game", str(huge), "--snapshot", str(other), "--output", str(output)])
    assert error.value.code == 2 and not output.exists()
