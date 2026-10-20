import json
import os
from pathlib import Path
import subprocess
import sys


def test_installed_ingester_works_with_comparer_classifier_actions_and_game_evidence_absent(tmp_path):
    code = '''
import importlib.abc, importlib.util, sys
blocked = ("tempo_game_evidence", "tempo_game_comparison", "tempo_repertoire_comparison",
           "tempo_game_classification", "tempo_game_actions", "tempo_learning_runtime", "backend")
for name in blocked:
    assert importlib.util.find_spec(name) is None, name + " unexpectedly installed"
class NoDownstream(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == prefix or fullname.startswith(prefix + ".") for prefix in blocked):
            raise AssertionError("Unexpected downstream import: " + fullname)
sys.meta_path.insert(0, NoDownstream())
from tempo_game_ingestion import BytesInput, PgnGameIngester
raw = b'[White "Learner"]\\n[Black "Opponent"]\\n[Result "1-0"]\\n\\n1. e4 e5 1-0\\n'
result = PgnGameIngester().ingest(BytesInput(raw), workspace_id="w", game_id="g", game_revision=1,
    source_id="s", source_revision=1, player="Learner")
assert result.moves == ["e2e4", "e7e5"]
from datetime import datetime, timedelta, timezone
import json
from tempo_game_ingestion.lichess import PublicResponse, fetch_lichess_games
start = datetime(2026, 10, 1, tzinfo=timezone.utc)
class PublicFixture:
    calls = 0
    def get(self, url, **options):
        self.calls += 1
        assert url.startswith("https://lichess.org/api/games/user/learner?")
        return PublicResponse(200, json.dumps({"id":"abcd1234", "createdAt":int(start.timestamp()*1000), "pgn":raw.decode()}).encode())
transport = PublicFixture()
batch = fetch_lichess_games(transport, workspace_id="w", username="Learner", since=start,
    until=start+timedelta(days=1), limit=1, game_revision=1, source_revision=1)
assert len(batch.games) == 1 and batch.limit_reached and transport.calls == 1
assert batch.games[0].source_id == "lichess:abcd1234"
print(result.model_dump_json())
'''
    environment = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME"}}
    completed = subprocess.run([sys.executable, "-I", "-c", code], cwd=tmp_path,
        env=environment, capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["game_id"] == "g"


def test_installed_file_cli_emits_only_canonical_game_without_changing_source(tmp_path):
    path = tmp_path / "input.pgn"
    path.write_text('[White "Learner"]\n[Black "Opponent"]\n[Result "1-0"]\n\n1. d4 d5 1-0\n')
    original = path.read_bytes()
    arguments = [str(path), "--workspace-id", "w", "--game-id", "g", "--game-revision", "1",
        "--source-id", "local-file", "--source-revision", "1", "--player", "Learner"]
    cli = Path(sys.executable).parent / "tempo-game-ingest"
    completed = subprocess.run([str(cli), *arguments], cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    assert json.loads(completed.stdout)["moves"] == ["d2d4", "d7d5"]
    assert path.read_bytes() == original
    assert set(tmp_path.iterdir()) == {path}


def test_file_cli_reports_invalid_input_without_emitting_success_json(tmp_path):
    path = tmp_path / "input.pgn"
    path.write_text('invalid unfinished source')
    completed = subprocess.run([sys.executable, "-I", "-m", "tempo_game_ingestion", str(path),
        "--workspace-id", "w", "--game-id", "g", "--game-revision", "1", "--source-id", "s",
        "--source-revision", "1", "--player", "Learner"], cwd=tmp_path,
        capture_output=True, text=True, timeout=10)
    assert completed.returncode == 1
    assert completed.stdout == ""
    assert "Game ingestion failed" in completed.stderr
