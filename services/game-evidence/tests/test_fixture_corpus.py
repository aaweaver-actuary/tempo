import hashlib
import json
from pathlib import Path

from tempo_game_evidence import ComparisonSnapshot,compare_game,normalize_pgn,replay_positions


def test_source_bound_report_fixtures_match_actual_pgn_comparison():
    root=Path(__file__).resolve().parents[1]/"fixtures"
    corpus=json.loads((root/"corpus.json").read_text())
    assert hashlib.sha256((root/"snapshot.json").read_bytes()).hexdigest()==corpus["snapshot_sha256"]
    snapshot=ComparisonSnapshot.model_validate_json((root/"snapshot.json").read_bytes())
    for case in corpus["cases"]:
        raw=(root/case["source"]).read_bytes()
        expected_bytes=(root/case["report"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest()==case["source_sha256"]
        assert hashlib.sha256(expected_bytes).hexdigest()==case["report_sha256"]
        expected=json.loads(expected_bytes)
        game=normalize_pgn(raw,"Learner")
        game.update(workspace_id="fixture",game_id=expected["game"]["game_id"],revision=1)
        assert compare_game(game,replay_positions(game),snapshot)==expected["findings"]
