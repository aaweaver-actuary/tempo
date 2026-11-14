import json

from app.services.repertoire_conflicts import _conflicts_from_snapshot

START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def test_conflict_fast_position_key_preserves_en_passant_distinction():
    with_en_passant = "rnbqkbnr/1pp1pppp/p7/3pP3/8/8/PPPP1PPP/RNBQKBNR w KQkq d6 0 3"
    without_en_passant = with_en_passant.replace(" d6 ", " - ")
    moves = (
        (with_en_passant, "e5d6"),
        (with_en_passant, "g1f3"),
        (without_en_passant, "g1f3"),
        (without_en_passant, "f1e2"),
    )
    snapshot = tuple(
        (
            f"line-{index}",
            "repertoire",
            f"Line {index}",
            "white",
            fen,
            json.dumps([move]),
        )
        for index, (fen, move) in enumerate(moves)
    )
    conflicts = _conflicts_from_snapshot(snapshot)
    assert len(conflicts) == 2
    assert {conflict["fen"] for conflict in conflicts} == {
        " ".join(with_en_passant.split()[:4]),
        " ".join(without_en_passant.split()[:4]),
    }
    assert {
        tuple(move["uci"] for move in conflict["moves"]) for conflict in conflicts
    } == {
        ("e5d6", "g1f3"),
        ("f1e2", "g1f3"),
    }


def test_authored_studies_conflict_policy_imports_without_database_or_sibling_domains():
    import subprocess
    import sys
    from pathlib import Path

    probe = r"""
from importlib.abc import MetaPathFinder
import sys
sys.path.insert(0, sys.argv[1])
class RejectInfrastructure(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if (fullname.startswith(('tempo_', 'psycopg', 'sqlite3', 'redis', 'celery', 'fastapi')) or
                (fullname.startswith('app.') and not
                 (fullname == 'app.studies' or fullname == 'app.studies.repertoire_conflicts'))):
            raise AssertionError('Authored Studies imported outside its boundary: ' + fullname)
sys.meta_path.insert(0, RejectInfrastructure())
from app.studies.repertoire_conflicts import conflicts_from_snapshot
assert conflicts_from_snapshot(()) == ()
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", probe, str(Path(__file__).resolve().parents[1])],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
